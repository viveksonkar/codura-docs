"""M1 end-to-end on real Postgres with fake models: signup → upload/OCR → answer → metering → roles,
platform controls, trial limit, and the cross-Tenant probe suite (M1-14, M1-18, SEC-02/09/15)."""

from __future__ import annotations

import io
import json
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pypdfium2 as pdfium
import pytest
from docx import Document as DocxDocument
from PIL import Image
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from codura_docs.api.app import create_app
from codura_docs.config import Settings
from codura_docs.registry import Registry
from codura_docs.worker.runner import Worker

pytestmark = pytest.mark.integration


# --- helpers ---------------------------------------------------------------------------------------


def sse_events(body: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = [ln for ln in block.split("\n") if ln and not ln.startswith(":")]
        if not lines:
            continue
        name = next(ln[7:] for ln in lines if ln.startswith("event: "))
        data = json.loads("".join(ln[6:] for ln in lines if ln.startswith("data: ")))
        events.append((name, data))
    return events


def docx_bytes(heading: str, body: str) -> bytes:
    doc = DocxDocument()
    doc.add_heading(heading, level=1)
    doc.add_paragraph(body)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), "white").save(buf, format="PNG")
    return buf.getvalue()


def blank_pdf_bytes(pages: int = 2) -> bytes:
    pdf = pdfium.PdfDocument.new()
    for _ in range(pages):
        pdf.new_page(595, 842)
    buf = io.BytesIO()
    pdf.save(buf)
    pdf.close()
    return buf.getvalue()


class Org:
    def __init__(self, client: httpx.AsyncClient, me: dict[str, Any]) -> None:
        self.client = client
        self.me = me
        self.client.headers["x-csrf-token"] = me["csrf_token"]

    @property
    def tenant_id(self) -> str:
        return str(self.me["current_tenant"]["id"])


@pytest.fixture
async def stack(pg_database: Settings) -> AsyncIterator[dict[str, Any]]:
    registry = Registry.from_settings(pg_database)
    app = create_app(pg_database, registry=registry)
    worker = Worker(pg_database, registry, app.state.debug_worker_engine, app.state.engine)
    async with app.router.lifespan_context(app):
        yield {"app": app, "registry": registry, "worker": worker}
    await app.state.platform_engine.dispose()
    await app.state.debug_worker_engine.dispose()


def client_for(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def drain(worker: Worker) -> None:
    for _ in range(200):
        if not await worker.run_one():
            return


def last_token(registry: Registry, to: str) -> str:
    sent = registry.email().sent  # type: ignore[attr-defined]
    body = next(m.text for m in reversed(sent) if m.to == to)
    match = re.search(r"token=([A-Za-z0-9_\-]+)", body)
    assert match, body
    return match.group(1)


async def signup_and_login(stack: dict[str, Any], org: str, email: str) -> Org:
    app, registry, worker = stack["app"], stack["registry"], stack["worker"]
    client = client_for(app)
    r = await client.post(
        "/api/auth/signup",
        json={
            "org_name": org,
            "name": "Owner",
            "email": email,
            "password": "correct-horse-battery",
        },
    )
    assert r.status_code == 202, r.text
    dup = await client.post(
        "/api/auth/signup",
        json={"org_name": org, "name": "X", "email": email, "password": "correct-horse-battery"},
    )
    assert dup.json() == r.json()  # no account enumeration
    await drain(worker)  # sends the verification email (fake sender)
    login = await client.post(
        "/api/admin/auth/login", json={"email": email, "password": "correct-horse-battery"}
    )
    assert login.status_code == 200
    assert login.json()["current_tenant"]["status"] == "pending_verification"
    o = Org(client, login.json())
    blocked = await client.post("/api/admin/knowledge-bases", json={"name": "KB"})
    assert blocked.json()["error"]["code"] == "email_not_verified"
    assert (
        await client.post("/api/auth/verify-email", json={"token": last_token(registry, email)})
    ).status_code == 200
    o.me = (await client.get("/api/admin/auth/me")).json()
    assert o.me["current_tenant"]["status"] == "active"
    return o


async def ask(
    o: Org, assistant_id: str, question: str, conversation_id: str | None = None
) -> list[tuple[str, dict[str, Any]]]:
    r = await o.client.post(
        f"/api/admin/assistants/{assistant_id}/playground/stream",
        json={"question": question, "conversation_id": conversation_id},
    )
    assert r.status_code == 200, r.text
    return sse_events(r.text)


# --- the flow ------------------------------------------------------------------------------------------


async def test_m1_end_to_end_and_isolation(
    stack: dict[str, Any], owner_engine: AsyncEngine
) -> None:
    app, registry, worker = stack["app"], stack["registry"], stack["worker"]
    a = await signup_and_login(stack, "Acme Industries", "owner@acme.test")
    b = await signup_and_login(stack, "Globex", "owner@globex.test")

    # CSRF is enforced for cookie sessions.
    no_csrf = client_for(app)
    no_csrf.cookies = a.client.cookies
    assert (await no_csrf.post("/api/admin/knowledge-bases", json={"name": "x"})).json()["error"][
        "code"
    ] == "csrf_failed"

    # --- Upload: Markdown, DOCX, image (OCR) and a scanned PDF (OCR) -----------------------------------
    kb = (await a.client.post("/api/admin/knowledge-bases", json={"name": "Manuals"})).json()
    files = [
        (
            "file",
            (
                "x200.md",
                b"# X200 Warranty\n\nThe X200 warranty lasts 24 months from purchase.\n\n"
                b"## Reset\n\nHold the power button for ten seconds to reset the X200.",
                "text/markdown",
            ),
        ),
        (
            "file",
            (
                "policy.docx",
                docx_bytes("Returns", "Returns are accepted within 30 days with a receipt."),
                "application/octet-stream",
            ),
        ),
        ("file", ("photo.png", png_bytes(), "image/png")),
        ("file", ("scan.pdf", blank_pdf_bytes(), "application/pdf")),
        ("file", ("evil.exe", b"MZ\x90\x00\x03\x00\x00\x00binary", "application/octet-stream")),
    ]
    up = await a.client.post(f"/api/admin/knowledge-bases/{kb['id']}/documents", files=files)
    assert up.status_code == 201, up.text
    items = up.json()["items"]
    assert [i["status"] for i in items] == ["pending", "pending", "pending", "pending", "rejected"]
    again = await a.client.post(
        f"/api/admin/knowledge-bases/{kb['id']}/documents", files=[files[0]]
    )
    assert again.json()["items"][0]["status"] == "duplicate"
    await drain(worker)
    docs = (await a.client.get(f"/api/admin/knowledge-bases/{kb['id']}/documents")).json()["items"]
    by_name = {d["filename"]: d for d in docs}
    assert all(d["status"] == "ready" for d in docs), [
        (d["filename"], d["status"], d["error"]) for d in docs
    ]
    assert by_name["scan.pdf"]["ocr_pages"] == 2 and by_name["photo.png"]["ocr_pages"] == 1
    assert by_name["x200.md"]["chunk_count"] >= 1
    chunks = (await a.client.get(f"/api/admin/documents/{by_name['x200.md']['id']}/chunks")).json()[
        "items"
    ]
    assert any("24 months" in c["text"] for c in chunks)

    # --- Assistant + grounded answer with citations + metering -------------------------------------------
    asst = await a.client.post(
        "/api/admin/assistants",
        json={"name": "Help", "knowledge_base_ids": [kb["id"]], "retrieval": {"min_score": 0.0}},
    )
    assert asst.status_code == 201, asst.text
    assert "model" not in json.dumps(asst.json()).lower().replace("model_", "")  # no model exposed
    aid = asst.json()["id"]
    events = await ask(a, aid, "How long is the X200 warranty?")
    names = [e[0] for e in events]
    assert names[0] == "meta" and names[-1] == "done" and "citations" in names and "delta" in names
    assert "debug" in names
    citations = next(d for n, d in events if n == "citations")["items"]
    assert citations and citations[0]["title"]
    done = next(d for n, d in events if n == "done")
    assert done["finish_reason"] in {"stop", "length"}
    conv = next(d for n, d in events if n == "meta")["conversation_id"]
    follow = await ask(a, aid, "And how do I reset it?", conversation_id=conv)
    assert next(d for n, d in follow if n == "meta")["conversation_id"] == conv
    usage = (await a.client.get("/api/admin/usage")).json()["meters"]
    used = {m["meter"]: m["used"] for m in usage}
    assert used["answer"] == 2 and used["page_ingested"] >= 4 and used["page_ocr"] == 3
    assert "cogs" not in json.dumps(usage)

    # --- Roles: invite a read-only Tenant User --------------------------------------------------------------
    inv = await a.client.post(
        "/api/admin/invites", json={"email": "viewer@acme.test", "role": "user"}
    )
    assert inv.status_code == 201, inv.text
    await drain(worker)
    viewer = client_for(app)
    acc = await viewer.post(
        "/api/auth/accept-invite",
        json={
            "token": last_token(registry, "viewer@acme.test"),
            "name": "Viewer",
            "password": "viewer-password-1",
        },
    )
    assert acc.status_code == 200, acc.text
    v = Org(viewer, acc.json())
    assert v.me["current_tenant"]["role"] == "user"
    assert (await viewer.get(f"/api/admin/knowledge-bases/{kb['id']}/documents")).status_code == 200
    for method, url, body in [
        ("POST", "/api/admin/knowledge-bases", {"name": "nope"}),
        ("DELETE", f"/api/admin/documents/{by_name['x200.md']['id']}", None),
        ("PATCH", f"/api/admin/assistants/{aid}", {"name": "hacked"}),
        ("POST", "/api/admin/invites", {"email": "x@acme.test", "role": "admin"}),
        ("GET", "/api/admin/channel-keys", None),
    ]:
        r = await viewer.request(method, url, json=body)
        assert r.status_code == 403, (method, url, r.text)
    assert [n for n, _ in await ask(v, aid, "What is the return policy?")][
        -1
    ] == "done"  # Playground allowed
    last_admin = await a.client.patch(
        f"/api/admin/members/{a.me['user']['id']}", json={"role": "user"}
    )
    assert last_admin.json()["error"]["code"] == "last_admin"

    # --- Cross-Tenant probe suite: B must never reach A ------------------------------------------------------
    a_doc = by_name["x200.md"]["id"]
    for method, url in [
        ("GET", f"/api/admin/knowledge-bases/{kb['id']}"),
        ("GET", f"/api/admin/documents/{a_doc}"),
        ("GET", f"/api/admin/documents/{a_doc}/chunks"),
        ("DELETE", f"/api/admin/documents/{a_doc}"),
        ("GET", f"/api/admin/assistants/{aid}"),
        ("PATCH", f"/api/admin/assistants/{aid}"),
        ("POST", f"/api/admin/assistants/{aid}/playground/stream"),
    ]:
        body = (
            {"question": "warranty?"}
            if "playground" in url
            else ({"name": "x"} if method == "PATCH" else None)
        )
        r = await b.client.request(method, url, json=body)
        assert r.status_code == 404, (method, url, r.status_code, r.text)
    assert (await b.client.get(f"/api/admin/knowledge-bases/{kb['id']}/documents")).json()[
        "items"
    ] == []
    assert (await b.client.get("/api/admin/knowledge-bases")).json()["items"] == []
    spoof = await b.client.post("/api/admin/auth/switch-tenant", json={"tenant_id": a.tenant_id})
    assert spoof.status_code == 404
    b_kb = (await b.client.post("/api/admin/knowledge-bases", json={"name": "B"})).json()
    bad = await b.client.post(
        "/api/admin/assistants", json={"name": "Steal", "knowledge_base_ids": [kb["id"]]}
    )
    assert bad.status_code == 422  # cannot attach another Tenant's Knowledge Base
    b_asst = (
        await b.client.post(
            "/api/admin/assistants",
            json={"name": "B", "knowledge_base_ids": [b_kb["id"]], "retrieval": {"min_score": 0.0}},
        )
    ).json()
    b_answer = await ask(b, b_asst["id"], "How long is the X200 warranty?")
    assert next(d for n, d in b_answer if n == "done")["finish_reason"] == "fallback"
    assert next(d for n, d in b_answer if n == "citations")["items"] == []
    b_conv_probe = await b.client.post(
        f"/api/admin/assistants/{b_asst['id']}/playground/stream",
        json={"question": "hi", "conversation_id": conv},
    )
    assert b_conv_probe.status_code == 404

    # Public Chat API: a Channel Key resolves to exactly one Tenant + Assistant.
    key = (
        await a.client.post("/api/admin/channel-keys", json={"channel": "api", "assistant_id": aid})
    ).json()["key"]
    pub = client_for(app)
    hdr = {"Authorization": f"Bearer {key}", "X-Codura-Visitor": "visitor-0001"}
    cfg = await pub.get("/api/v1/channel/config", headers=hdr)
    assert cfg.status_code == 200 and cfg.json()["assistant"]["id"] == aid
    stream = await pub.post(
        "/api/v1/chat/stream", headers=hdr, json={"question": "How long is the warranty?"}
    )
    pub_conv = next(d for n, d in sse_events(stream.text) if n == "meta")["conversation_id"]
    assert (
        await pub.get(f"/api/v1/conversations/{pub_conv}/messages", headers=hdr)
    ).status_code == 200
    other_visitor = hdr | {"X-Codura-Visitor": "visitor-0002"}
    assert (
        await pub.get(f"/api/v1/conversations/{pub_conv}/messages", headers=other_visitor)
    ).status_code == 404
    assert (await pub.get(f"/api/v1/conversations/{conv}/messages", headers=hdr)).status_code == 404
    sk = (await a.client.post("/api/admin/channel-keys", json={"kind": "secret"})).json()["key"]
    assert (
        await pub.get("/api/v1/channel/config", headers={**hdr, "Authorization": f"Bearer {sk}"})
    ).status_code == 401
    as_key = await pub.get("/api/admin/knowledge-bases", headers={"Authorization": f"Bearer {sk}"})
    assert [k["id"] for k in as_key.json()["items"]] == [kb["id"]]  # sk_ is scoped to Tenant A only

    # --- Platform Admin: override model, suspend a Tenant, audit -----------------------------------------------
    plat = client_for(app)
    login = await plat.post(
        "/api/admin/auth/login", json={"email": "platform@local.test", "password": "admin"}
    )
    plat.headers["x-csrf-token"] = login.json()["csrf_token"]
    assert (await a.client.get("/api/platform/tenants")).status_code == 403
    tenants = (await plat.get("/api/platform/tenants")).json()["items"]
    assert {a.tenant_id, b.tenant_id} <= {t["id"] for t in tenants}
    assert (
        await plat.patch(
            f"/api/platform/tenants/{a.tenant_id}", json={"model_profile_override": "nope"}
        )
    ).status_code == 422
    ok = await plat.patch(
        f"/api/platform/tenants/{a.tenant_id}", json={"model_profile_override": "fake-default"}
    )
    assert ok.json()["model_profile_override"] == "fake-default"
    assert (
        await plat.patch(f"/api/platform/tenants/{b.tenant_id}", json={"status": "suspended"})
    ).status_code == 200
    assert (await b.client.get("/api/admin/knowledge-bases")).json()["error"][
        "code"
    ] == "tenant_suspended"
    audit = (await plat.get("/api/platform/audit")).json()["items"]
    assert {e["action"] for e in audit} >= {"tenant.update"}

    # --- Usage Events are idempotent and carry COGS fields -----------------------------------------------------
    async with owner_engine.connect() as conn:
        n_events = (
            await conn.execute(
                text("SELECT count(*) FROM usage_events WHERE tenant_id = :t AND meter = 'answer'"),
                {"t": a.tenant_id},
            )
        ).scalar_one()
        counter = (
            await conn.execute(
                text(
                    "SELECT quantity FROM usage_counters WHERE tenant_id = :t AND meter = 'answer'"
                ),
                {"t": a.tenant_id},
            )
        ).scalar_one()
    assert float(counter) == n_events >= 4

    # --- Trial limit: answers stop without an LLM call --------------------------------------------------------
    limited_settings = registry.settings.model_copy(update={"trial_answers": int(n_events)})
    limited = create_app(limited_settings, registry=Registry.from_settings(limited_settings))
    async with limited.router.lifespan_context(limited):
        lc = client_for(limited)
        lc.cookies = a.client.cookies
        lc.headers["x-csrf-token"] = a.me["csrf_token"]
        r = await lc.post(
            f"/api/admin/assistants/{aid}/playground/stream", json={"question": "warranty?"}
        )
        assert next(d for n, d in sse_events(r.text) if n == "done")["finish_reason"] == "limit"
    await limited.state.platform_engine.dispose()
    await limited.state.debug_worker_engine.dispose()
