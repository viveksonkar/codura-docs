"""Live end-to-end check against a running stack with REAL models, OCR and email (M1 exit, M1-14).

    cd backend && uv run python scripts/live_e2e.py [--base http://localhost:8080]

Signs up a fresh organisation (email verified through Mailpit), uploads an English manual, a Hindi
policy and an image containing text (OCR), then asks questions in English and Hindi and an unrelated
one. Prints answers, citations and timings; exits non-zero if an expectation fails.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
import uuid

import httpx
from PIL import Image, ImageDraw, ImageFont

MANUAL = """# X200 Coffee Machine Manual

## Warranty
The X200 comes with a warranty of 24 months from the date of purchase. The warranty covers the pump and heater.

## Descaling
Descale the X200 every 3 months using the Codura descaling tablets. Run two empty cycles afterwards.
"""
HINDI_POLICY = """वापसी नीति

उत्पाद खरीद के 30 दिनों के भीतर रसीद के साथ लौटाए जा सकते हैं। खुले हुए उपभोग्य सामान वापस नहीं लिए जाते।
"""
IMAGE_TEXT = [
    "SHIPPING NOTICE",
    "Orders ship within 2 business days.",
    "Express delivery costs 15 EUR.",
]


def image_bytes() -> bytes:
    img = Image.new("RGB", (1400, 500), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=48)
    for i, line in enumerate(IMAGE_TEXT):
        draw.text((60, 60 + i * 110), line, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def sse(body: str) -> list[tuple[str, dict]]:
    out = []
    for block in body.strip().split("\n\n"):
        lines = [ln for ln in block.split("\n") if ln and not ln.startswith(":")]
        if lines:
            name = next(ln[7:] for ln in lines if ln.startswith("event: "))
            out.append(
                (name, json.loads("".join(ln[6:] for ln in lines if ln.startswith("data: "))))
            )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8080")
    ap.add_argument("--mailpit", default="http://localhost:8025")
    args = ap.parse_args()
    failures: list[str] = []
    c = httpx.Client(base_url=args.base, timeout=180)
    email = f"live-{uuid.uuid4().hex[:8]}@example.test"
    password = "live-test-password-1"

    r = c.post(
        "/api/auth/signup",
        json={"org_name": "Live Test Org", "name": "Live", "email": email, "password": password},
    )
    assert r.status_code == 202, r.text
    token = None
    for _ in range(30):
        msgs = httpx.get(f"{args.mailpit}/api/v1/search", params={"query": f"to:{email}"}).json()[
            "messages"
        ]
        if msgs:
            text = httpx.get(f"{args.mailpit}/api/v1/message/{msgs[0]['ID']}").json()["Text"]
            token = re.search(r"token=([A-Za-z0-9_\-]+)", text).group(1)  # type: ignore[union-attr]
            break
        time.sleep(1)
    assert token, "verification email not received"
    assert c.post("/api/auth/verify-email", json={"token": token}).status_code == 200
    me = c.post("/api/admin/auth/login", json={"email": email, "password": password}).json()
    c.headers["x-csrf-token"] = me["csrf_token"]
    print(f"signed up + verified: {me['current_tenant']['name']} ({me['current_tenant']['slug']})")

    c.patch("/api/admin/tenant", json={"ocr_languages": "eng+hin"})
    kb = c.post("/api/admin/knowledge-bases", json={"name": "Support"}).json()
    files = [
        ("file", ("x200-manual.md", MANUAL.encode(), "text/markdown")),
        ("file", ("returns-hi.txt", HINDI_POLICY.encode(), "text/plain")),
        ("file", ("shipping-notice.png", image_bytes(), "image/png")),
    ]
    up = c.post(f"/api/admin/knowledge-bases/{kb['id']}/documents", files=files)
    assert up.status_code == 201, up.text
    t0 = time.time()
    while True:
        docs = c.get(f"/api/admin/knowledge-bases/{kb['id']}/documents").json()["items"]
        if all(d["status"] in ("ready", "failed") for d in docs):
            break
        if time.time() - t0 > 300:
            failures.append("ingestion timed out")
            break
        time.sleep(2)
    for d in docs:
        print(
            f"  {d['filename']:<22} {d['status']:<7} lang={d['language']} pages={d['page_count']} "
            f"ocr_pages={d['ocr_pages']} chunks={d['chunk_count']} {d['error'] or ''}"
        )
        if d["status"] != "ready":
            failures.append(f"{d['filename']} not ready: {d['error']}")
    print(f"ingestion: {time.time() - t0:.1f}s")

    asst = c.post(
        "/api/admin/assistants", json={"name": "Support Bot", "knowledge_base_ids": [kb["id"]]}
    ).json()
    cases = [
        ("How long is the warranty on the X200?", "en", ["24"], False),
        ("X200 की वारंटी कितने महीने की है?", "hi", ["24"], False),
        ("How many days do I have to return a product?", "en", ["30"], False),
        ("How much does express delivery cost?", "en", ["15"], False),
        ("What is the capital of France?", "en", [], True),
    ]
    for question, lang, must, expect_fallback in cases:
        t = time.time()
        events = sse(
            c.post(
                f"/api/admin/assistants/{asst['id']}/playground/stream", json={"question": question}
            ).text
        )
        answer = "".join(d["text"] for n, d in events if n == "delta")
        meta = next(d for n, d in events if n == "meta")
        done = next(d for n, d in events if n == "done")
        cites = next((d["items"] for n, d in events if n == "citations"), [])
        print(
            f"\nQ ({meta['language']}): {question}\nA: {answer.strip()}\n   -> {done['finish_reason']}, "
            f"{time.time() - t:.1f}s, citations: {[x['title'] for x in cites]}"
        )
        if meta["language"] != lang:
            failures.append(f"language for {question!r}: {meta['language']} != {lang}")
        if expect_fallback != (done["finish_reason"] == "fallback"):
            failures.append(f"fallback expectation failed for {question!r}")
        for needle in must:
            if needle not in answer:
                failures.append(f"{needle!r} missing from answer to {question!r}")
    usage = c.get("/api/admin/usage").json()["meters"]
    print("\nusage:", {m["meter"]: m["used"] for m in usage})
    if failures:
        print("\nFAILURES:\n  " + "\n  ".join(failures))
        return 1
    print("\nlive e2e: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
