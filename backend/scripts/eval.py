"""Retrieval/answer quality report on the golden set (M1-13, NFR-10), against a running stack.

    cd backend && uv run python scripts/eval.py [--base http://localhost:8080] [--json report.json]

Creates a throwaway organisation through the real signup flow (Mailpit), ingests the golden
documents, asks every question and reports hit@1/hit@k (cited document), answer contains,
answer-language accuracy and fallback precision, overall and per language. Reporting only in M1;
CI gating thresholds arrive in M5.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx
import yaml

GOLDEN = Path(__file__).resolve().parents[1] / "tests" / "eval" / "golden.yaml"


def sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for block in body.strip().split("\n\n"):
        lines = [ln for ln in block.split("\n") if ln and not ln.startswith(":")]
        if lines:
            name = next(ln[7:] for ln in lines if ln.startswith("event: "))
            out.append(
                (name, json.loads("".join(ln[6:] for ln in lines if ln.startswith("data: "))))
            )
    return out


def setup_org(c: httpx.Client, mailpit: str) -> None:
    email, password = f"eval-{uuid.uuid4().hex[:8]}@example.test", "eval-password-123"
    c.post(
        "/api/auth/signup", json={"org_name": "Eval Org", "email": email, "password": password}
    ).raise_for_status()
    for _ in range(30):
        msgs = httpx.get(f"{mailpit}/api/v1/search", params={"query": f"to:{email}"}).json()[
            "messages"
        ]
        if msgs:
            body = httpx.get(f"{mailpit}/api/v1/message/{msgs[0]['ID']}").json()["Text"]
            c.post(
                "/api/auth/verify-email",
                json={"token": re.search(r"token=([\w\-]+)", body).group(1)},
            )  # type: ignore[union-attr]
            break
        time.sleep(1)
    me = c.post("/api/admin/auth/login", json={"email": email, "password": password}).json()
    c.headers["x-csrf-token"] = me["csrf_token"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8080")
    ap.add_argument("--mailpit", default="http://localhost:8025")
    ap.add_argument("--json", dest="json_out")
    args = ap.parse_args()
    golden = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    c = httpx.Client(base_url=args.base, timeout=180)
    setup_org(c, args.mailpit)

    kb = c.post("/api/admin/knowledge-bases", json={"name": "Golden"}).json()
    files = [
        ("file", (d["filename"], d["text"].encode(), "text/plain")) for d in golden["documents"]
    ]
    c.post(f"/api/admin/knowledge-bases/{kb['id']}/documents", files=files).raise_for_status()
    titles: dict[str, str] = {}
    for _ in range(120):
        docs = c.get(f"/api/admin/knowledge-bases/{kb['id']}/documents").json()["items"]
        if all(d["status"] == "ready" for d in docs):
            titles = {d["filename"]: d["title"] for d in docs}
            break
        time.sleep(1)
    asst = c.post(
        "/api/admin/assistants", json={"name": "Eval", "knowledge_base_ids": [kb["id"]]}
    ).json()

    rows = []
    for item in golden["questions"]:
        t = time.time()
        events = sse(
            c.post(
                f"/api/admin/assistants/{asst['id']}/playground/stream",
                json={"question": item["q"]},
            ).text
        )
        answer = "".join(d["text"] for n, d in events if n == "delta")
        done = next(d for n, d in events if n == "done")
        language = next(d for n, d in events if n == "meta")["language"]
        cited = [x["title"] for x in next((d["items"] for n, d in events if n == "citations"), [])]
        expected = titles.get(item.get("doc", ""))
        rows.append(
            {
                "q": item["q"],
                "lang": item["lang"],
                "answer": answer.strip(),
                "latency_s": round(time.time() - t, 2),
                "fallback": done["finish_reason"] == "fallback",
                "expect_fallback": bool(item.get("fallback")),
                "hit1": bool(expected and cited[:1] == [expected]),
                "hitk": bool(expected and expected in cited),
                "contains": all(s.lower() in answer.lower() for s in item.get("contains", [])),
                "lang_ok": language == item["lang"],
                "is_doc_q": bool(expected),
                "injection": bool(item.get("fallback_or_refusal")),
            }
        )

    def rate(xs: list[bool]) -> float:
        return round(sum(xs) / len(xs), 3) if xs else 1.0

    doc_q = [r for r in rows if r["is_doc_q"]]
    fb_q = [r for r in rows if r["expect_fallback"]]
    report: dict[str, Any] = {
        "questions": len(rows),
        "hit@1": rate([r["hit1"] for r in doc_q]),
        "hit@k": rate([r["hitk"] for r in doc_q]),
        "answer_contains": rate([r["contains"] for r in doc_q]),
        "language_accuracy": rate([r["lang_ok"] for r in rows]),
        "fallback_precision": rate([r["fallback"] for r in fb_q]),
        "false_fallbacks": sum(r["fallback"] for r in doc_q),
        "injection_leaks": sum(
            1 for r in rows if r["injection"] and "grounding" in r["answer"].lower()
        ),
        "p50_latency_s": sorted(r["latency_s"] for r in rows)[len(rows) // 2],
        "per_language": {},
    }
    by_lang: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in doc_q:
        by_lang[r["lang"]].append(r)
    report["per_language"] = {
        k: {
            "n": len(v),
            "hit@k": rate([r["hitk"] for r in v]),
            "contains": rate([r["contains"] for r in v]),
        }
        for k, v in by_lang.items()
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    for r in rows:
        if (r["is_doc_q"] and not (r["hitk"] and r["contains"])) or (
            r["expect_fallback"] and not r["fallback"]
        ):
            print(f"MISS [{r['lang']}] {r['q']}\n     -> {r['answer'][:160]}")
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"report": report, "rows": rows}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
