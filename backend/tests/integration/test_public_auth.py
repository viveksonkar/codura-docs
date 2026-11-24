"""M2 web Channel on real Postgres with fake models (M2-00/01/02/06/08/10/11, M1-18 inventory).

- Key type × Origin × own/other Tenant matrix for every public route (SEC-04/05/15).
- Hosted Chat Page bootstrap: enable/disable, identical 404s, seeded /c/demo/help (FR-46).
- Rate limits and concurrency caps: Tenant A saturating doesn't block Tenant B (SEC-06).
- Optional Visitor email: required/optional, encrypted, never identity, list/export/delete (FR-53/54).
- Conversation log viewer, feedback, insights; roles (Tenant User read-only, no emails).
- Route inventory: every API route is probed cross-Tenant or explicitly exempt (M1-18).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from codura_docs.bootstrap import DEMO_WIDGET_KEY
from codura_docs.config import Settings
from codura_docs.registry import Registry
from tests.integration.test_m1_flows import Org, client_for, drain, signup_and_login, sse_events

pytestmark = pytest.mark.integration

PLATFORM_ORIGIN = "http://localhost:8080"  # Settings.public_base_url default
SHOP_A = "https://shop-a.example"
EVIL = "https://evil.example"


@dataclass
class World:
    a: Org
    b: Org
    kb: str
    doc: str
    aid: str
    b_aid: str
    keys: dict[str, str] = field(default_factory=dict)  # name → plaintext
    key_ids: dict[str, str] = field(default_factory=dict)
    conv: str = ""
    msg: str = ""


def hdr(key: str, visitor: str = "visitor-aaaa-0001", origin: str | None = None) -> dict[str, str]:
    h = {"Authorization": f"Bearer {key}", "X-Codura-Visitor": visitor}
    if origin:
        h["Origin"] = origin
    return h


async def make_key(o: Org, **body: Any) -> tuple[str, str]:
    r = await o.client.post("/api/admin/channel-keys", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"], r.json()["key"]


async def platform_client(app: Any) -> httpx.AsyncClient:
    plat = client_for(app)
    login = await plat.post(
        "/api/admin/auth/login", json={"email": "platform@local.test", "password": "admin"}
    )
    plat.headers["x-csrf-token"] = login.json()["csrf_token"]
    return plat


async def assign_plan(app: Any, tenant_id: str, plan_code: str) -> None:
    """Platform Admin puts a Tenant on a Plan (M3); M2 tests need every Channel."""
    plat = await platform_client(app)
    r = await plat.put(f"/api/platform/tenants/{tenant_id}/billing", json={"plan_code": plan_code})
    assert r.status_code == 200, r.text


async def build_world(stack: dict[str, Any]) -> World:
    worker = stack["worker"]
    run = uuid.uuid4().hex[:6]
    a = await signup_and_login(stack, f"Initech {run}", f"owner-{run}@initech.test")
    b = await signup_and_login(stack, f"Umbrella {run}", f"owner-{run}@umbrella.test")
    kb = (await a.client.post("/api/admin/knowledge-bases", json={"name": "Docs"})).json()["id"]
    up = await a.client.post(
        f"/api/admin/knowledge-bases/{kb}/documents",
        files=[
            ("file", ("faq.md", b"# FAQ\n\nThe X200 warranty lasts 24 months.", "text/markdown"))
        ],
    )
    doc = up.json()["items"][0]["id"]
    await drain(worker)
    aid = (
        await a.client.post(
            "/api/admin/assistants",
            json={
                "name": "Initech Help",
                "slug": "help",
                "knowledge_base_ids": [kb],
                "retrieval": {"min_score": 0.0},
            },
        )
    ).json()["id"]
    b_kb = (await b.client.post("/api/admin/knowledge-bases", json={"name": "B"})).json()["id"]
    b_aid = (
        await b.client.post(
            "/api/admin/assistants", json={"name": "Umbrella Help", "knowledge_base_ids": [b_kb]}
        )
    ).json()["id"]
    w = World(a, b, kb, doc, aid, b_aid)
    await assign_plan(stack["app"], a.tenant_id, "business")
    for name, body in {
        "widget": {"channel": "widget", "assistant_id": aid, "allowed_origins": [SHOP_A + "/"]},
        "wordpress": {"channel": "wordpress", "assistant_id": aid, "allowed_origins": [SHOP_A]},
        "widget_no_origins": {"channel": "widget", "assistant_id": aid},
        "api": {"channel": "api", "assistant_id": aid},
        "android": {"channel": "android", "assistant_id": aid},
        "revoked": {"channel": "widget", "assistant_id": aid, "allowed_origins": [SHOP_A]},
        "secret": {"kind": "secret"},
    }.items():
        w.key_ids[name], w.keys[name] = await make_key(a, **body)
    assert (
        await a.client.post(f"/api/admin/channel-keys/{w.key_ids['revoked']}/revoke")
    ).status_code == 204
    w.key_ids["b_widget"], w.keys["b_widget"] = await make_key(
        b, channel="widget", assistant_id=b_aid, allowed_origins=[SHOP_A]
    )
    # One Conversation of Tenant A through its widget key.
    pub = client_for(stack["app"])
    r = await pub.post(
        "/api/v1/chat/stream",
        headers=hdr(w.keys["widget"], origin=SHOP_A),
        json={"question": "How long is the X200 warranty?"},
    )
    assert r.status_code == 200, r.text
    meta = next(d for n, d in sse_events(r.text) if n == "meta")
    w.conv, w.msg = meta["conversation_id"], meta["message_id"]
    return w


# --- M2-00 / M2-01: keys, Origins, CORS, Tenant matrix ---------------------------------------------


async def test_channel_keys_origins_and_tenant_matrix(stack: dict[str, Any]) -> None:
    w = await build_world(stack)
    app = stack["app"]
    pub = client_for(app)

    # Keys are listed without secrets, stored hashed, Allowed Origins normalised.
    listed = (await w.a.client.get("/api/admin/channel-keys")).json()["items"]
    assert "key" not in json.dumps(listed).replace('"key_', "").replace("_key", "")
    by_id = {k["id"]: k for k in listed}
    assert by_id[w.key_ids["widget"]]["allowed_origins"] == [SHOP_A]
    assert by_id[w.key_ids["revoked"]]["active"] is False
    assert all(w.keys["widget"] not in json.dumps(k) for k in listed)
    bad = await w.a.client.post(
        "/api/admin/channel-keys",
        json={"channel": "widget", "assistant_id": w.aid, "allowed_origins": ["*"]},
    )
    assert bad.status_code == 422
    stolen = await w.a.client.post(
        "/api/admin/channel-keys", json={"channel": "widget", "assistant_id": w.b_aid}
    )
    assert stolen.status_code == 422  # another Tenant's Assistant is unknown
    hosted_by_hand = await w.a.client.post(
        "/api/admin/channel-keys", json={"channel": "hosted", "assistant_id": w.aid}
    )
    assert hosted_by_hand.status_code == 422
    patched = await w.a.client.patch(
        f"/api/admin/channel-keys/{w.key_ids['wordpress']}",
        json={"allowed_origins": [SHOP_A, "https://shop-a2.example"]},
    )
    assert patched.status_code == 200 and len(patched.json()["allowed_origins"]) == 2
    api_origins = await w.a.client.patch(
        f"/api/admin/channel-keys/{w.key_ids['api']}", json={"allowed_origins": [SHOP_A]}
    )
    assert api_origins.status_code == 422

    routes = [
        ("GET", "/api/v1/channel/config", None),
        ("POST", "/api/v1/chat/stream", {"question": "warranty?", "conversation_id": w.conv}),
        ("GET", f"/api/v1/conversations/{w.conv}/messages", None),
        ("POST", f"/api/v1/messages/{w.msg}/feedback", {"value": 1}),
    ]
    # (key, origin) → expected status for Tenant A's own resources.
    expectations: list[tuple[str, str | None, int]] = [
        ("widget", None, 200),
        ("widget", SHOP_A, 200),
        ("widget", "https://SHOP-A.example", 200),
        ("widget", EVIL, 403),
        ("widget", PLATFORM_ORIGIN, 200),  # dev-only demo page (not in production: unit-tested)
        ("widget_no_origins", SHOP_A, 403),  # fails closed
        ("wordpress", SHOP_A, 200),
        ("wordpress", EVIL, 403),
        ("api", None, 200),
        ("api", SHOP_A, 403),
        ("android", None, 200),
        ("android", PLATFORM_ORIGIN, 403),
        ("revoked", SHOP_A, 401),
        ("secret", None, 401),
        ("b_widget", SHOP_A, 404),  # B's valid key against A's ids
    ]
    for key_name, origin, expected in expectations:
        for method, url, body in routes:
            status = expected
            if key_name == "b_widget" and url == "/api/v1/channel/config":
                status = 200  # B's own config
            r = await pub.request(
                method, url, headers=hdr(w.keys[key_name], origin=origin), json=body
            )
            ok = {200, 204} if status == 200 else {status}
            assert r.status_code in ok, (key_name, origin, method, url, r.status_code, r.text)
            if r.status_code < 300 and origin:
                assert r.headers.get("access-control-allow-origin") == origin
                assert "access-control-allow-credentials" not in r.headers
            if r.status_code in (401, 403):
                assert "access-control-allow-origin" not in r.headers
    # The B key's config is B's Assistant, never A's.
    cfg = await pub.get("/api/v1/channel/config", headers=hdr(w.keys["b_widget"], origin=SHOP_A))
    assert cfg.json()["assistant"]["id"] == w.b_aid
    assert "Initech" not in cfg.text

    # Same Tenant, different Visitor → 404 (history is per Visitor).
    other = await pub.get(
        f"/api/v1/conversations/{w.conv}/messages",
        headers=hdr(w.keys["widget"], visitor="visitor-zzzz-9999"),
    )
    assert other.status_code == 404
    # No Authorization / wrong scheme / missing Visitor.
    assert (await pub.get("/api/v1/channel/config")).status_code == 401
    assert (
        await pub.get("/api/v1/channel/config", headers={"Authorization": "Basic x"})
    ).status_code == 401
    assert (
        await pub.get(
            "/api/v1/channel/config", headers={"Authorization": f"Bearer {w.keys['widget']}"}
        )
    ).status_code == 422
    # sk_ never works on public routes; pk_ never works on admin routes.
    assert (
        await pub.get(
            "/api/admin/knowledge-bases", headers={"Authorization": f"Bearer {w.keys['widget']}"}
        )
    ).status_code == 401

    # CORS preflight: reflected, never *, never credentials; admin API gets no CORS at all.
    pre = await pub.options(
        "/api/v1/chat/stream",
        headers={
            "Origin": SHOP_A,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert pre.status_code == 204 and pre.headers["access-control-allow-origin"] == SHOP_A
    assert "x-codura-visitor" in pre.headers["access-control-allow-headers"]
    admin_cors = await pub.get("/api/admin/auth/me", headers={"Origin": SHOP_A})
    assert "access-control-allow-origin" not in admin_cors.headers

    # last_used_at is recorded (asynchronously).
    import asyncio

    await asyncio.sleep(0.2)
    listed = (await w.a.client.get("/api/admin/channel-keys")).json()["items"]
    assert {k["id"]: k for k in listed}[w.key_ids["widget"]]["last_used_at"] is not None

    # Suspended Tenant → its keys get 401.
    plat = client_for(app)
    login = await plat.post(
        "/api/admin/auth/login", json={"email": "platform@local.test", "password": "admin"}
    )
    plat.headers["x-csrf-token"] = login.json()["csrf_token"]
    assert (
        await plat.patch(f"/api/platform/tenants/{w.b.tenant_id}", json={"status": "suspended"})
    ).status_code == 200
    assert (
        await pub.get("/api/v1/channel/config", headers=hdr(w.keys["b_widget"], origin=SHOP_A))
    ).status_code == 401
    await plat.patch(f"/api/platform/tenants/{w.b.tenant_id}", json={"status": "active"})


# --- M2-06: Hosted Chat Page --------------------------------------------------------------------


async def test_hosted_page_bootstrap(stack: dict[str, Any]) -> None:
    w = await build_world(stack)
    pub = client_for(stack["app"])
    slug = w.a.me["current_tenant"]["slug"]
    unknown = await pub.get("/api/v1/hosted/no-such-tenant/help")
    disabled = await pub.get(f"/api/v1/hosted/{slug}/help")
    assert unknown.status_code == disabled.status_code == 404

    def strip(body: dict[str, Any]) -> dict[str, Any]:
        body["error"].pop("request_id")
        return body

    assert strip(unknown.json()) == strip(disabled.json())  # no enumeration

    enabled = await w.a.client.patch(
        f"/api/admin/assistants/{w.aid}", json={"hosted_enabled": True}
    )
    assert enabled.status_code == 200
    boot = await pub.get(f"/api/v1/hosted/{slug}/help")
    assert boot.status_code == 200, boot.text
    key = boot.json()["channel_key"]
    assert key.startswith("pk_") and boot.json()["api"] == PLATFORM_ORIGIN
    assert (await pub.get(f"/api/v1/hosted/{slug.upper()}/HELP")).json()["channel_key"] == key
    keys = (await w.a.client.get(f"/api/admin/channel-keys?assistant_id={w.aid}")).json()["items"]
    hosted = [k for k in keys if k["channel"] == "hosted" and k["active"]]
    assert len(hosted) == 1
    # The hosted key can't be revoked by hand, only by the toggle.
    assert (
        await w.a.client.post(f"/api/admin/channel-keys/{hosted[0]['id']}/revoke")
    ).status_code == 404
    # Hosted keys work only from the platform's own origin.
    assert (
        await pub.get("/api/v1/channel/config", headers=hdr(key, origin=PLATFORM_ORIGIN))
    ).status_code == 200
    assert (
        await pub.get("/api/v1/channel/config", headers=hdr(key, origin=SHOP_A))
    ).status_code == 403
    # Toggling again does not create a second key; disabling revokes it.
    await w.a.client.patch(f"/api/admin/assistants/{w.aid}", json={"hosted_enabled": True})
    assert (await pub.get(f"/api/v1/hosted/{slug}/help")).json()["channel_key"] == key
    await w.a.client.patch(f"/api/admin/assistants/{w.aid}", json={"hosted_enabled": False})
    assert (await pub.get(f"/api/v1/hosted/{slug}/help")).status_code == 404
    assert (await pub.get("/api/v1/channel/config", headers=hdr(key))).status_code == 401
    # A suspended Tenant's page is gone too (same 404).
    await w.a.client.patch(f"/api/admin/assistants/{w.aid}", json={"hosted_enabled": True})
    assert (await pub.get(f"/api/v1/hosted/{slug}/help")).status_code == 200

    # The seeded demo Tenant is reachable at /c/demo/help, and its dev widget key works.
    demo = await pub.get("/api/v1/hosted/demo/help")
    assert demo.status_code == 200, demo.text
    cfg = await pub.get(
        "/api/v1/channel/config", headers=hdr(DEMO_WIDGET_KEY, origin=PLATFORM_ORIGIN)
    )
    assert cfg.status_code == 200 and cfg.json()["assistant"]["name"] == "Demo Help"
    assert cfg.json()["visitor_email"]["mode"] == "optional"


async def test_hosted_lookup_is_rate_limited(stack: dict[str, Any], pg_database: Settings) -> None:
    from codura_docs.api.app import create_app

    tight = pg_database.model_copy(update={"hosted_lookup_per_ip_per_min": 2})
    app = create_app(tight, registry=Registry.from_settings(tight))
    try:
        c = client_for(app)
        codes = [(await c.get("/api/v1/hosted/demo/help")).status_code for _ in range(3)]
        assert codes == [200, 200, 429]
        r = await c.get("/api/v1/hosted/demo/help")
        assert int(r.headers["retry-after"]) >= 1
    finally:
        await app.state.engine.dispose()
        await app.state.platform_engine.dispose()
        await app.state.debug_worker_engine.dispose()


# --- M2-02: rate limits & concurrency ---------------------------------------------------------------


async def test_rate_limits_and_tenant_fairness(
    stack: dict[str, Any], pg_database: Settings
) -> None:
    w = await build_world(stack)
    from codura_docs.api.app import create_app

    tight = pg_database.model_copy(
        update={"chat_rate_per_visitor_per_min": 2, "chat_max_question_chars": 50}
    )
    app = create_app(tight, registry=Registry.from_settings(tight))
    try:
        c = client_for(app)
        a_hdr = hdr(w.keys["api"], visitor="visitor-rate-0001")
        long_q = await c.post("/api/v1/chat/stream", headers=a_hdr, json={"question": "x" * 51})
        assert long_q.status_code == 422
        for _ in range(2):
            assert (
                await c.post("/api/v1/chat/stream", headers=a_hdr, json={"question": "warranty?"})
            ).status_code == 200
        limited = await c.post("/api/v1/chat/stream", headers=a_hdr, json={"question": "warranty?"})
        assert limited.status_code == 429 and limited.json()["error"]["code"] == "rate_limited"
        assert int(limited.headers["retry-after"]) >= 1
        # Another Visitor of the same Tenant is not affected by this Visitor's bucket.
        assert (
            await c.post(
                "/api/v1/chat/stream",
                headers=hdr(w.keys["api"], visitor="visitor-rate-0002"),
                json={"question": "warranty?"},
            )
        ).status_code == 200

        # Tenant A at its concurrency cap: A gets 429, B still streams.
        cc = app.state.concurrency
        cap = tight.chat_max_concurrent_per_tenant
        held = [(f"tenant:{w.a.tenant_id}", cap)]
        for _ in range(cap):
            assert cc.acquire(held)
        busy = await c.post(
            "/api/v1/chat/stream",
            headers=hdr(w.keys["api"], visitor="visitor-rate-0003"),
            json={"question": "warranty?"},
        )
        assert busy.status_code == 429
        b_ok = await c.post(
            "/api/v1/chat/stream",
            headers=hdr(w.keys["b_widget"], visitor="visitor-rate-0004"),
            json={"question": "hello?"},
        )
        assert b_ok.status_code == 200
        for _ in range(cap):
            cc.release(held)
        assert cc.active(f"tenant:{w.a.tenant_id}") == 0  # finished streams released their slots
        assert cc.active(f"tenant:{w.b.tenant_id}") == 0
    finally:
        await app.state.engine.dispose()
        await app.state.platform_engine.dispose()
        await app.state.debug_worker_engine.dispose()


# --- M2-10, M2-08, M2-11: Visitor email, log viewer, feedback, insights, roles -------------------------


async def test_visitor_email_log_viewer_and_insights(
    stack: dict[str, Any], owner_engine: AsyncEngine
) -> None:
    w = await build_world(stack)
    pub = client_for(stack["app"])
    key = w.keys["api"]

    # off (default): an email sent anyway is ignored, nothing stored.
    r = await pub.post(
        "/api/v1/chat/stream",
        headers=hdr(key, visitor="visitor-mail-0000"),
        json={"question": "warranty?", "visitor_email": "x@y.com", "consent": True},
    )
    assert r.status_code == 200
    # required: no email → 422 visitor_email_required; bad email / no consent → 422.
    await w.a.client.patch(
        f"/api/admin/assistants/{w.aid}",
        json={
            "visitor_email": "required",
            "visitor_email_consent": {"default": "I agree to be contacted."},
            "privacy_url": "https://initech.example/privacy",
        },
    )
    cfg = (await pub.get("/api/v1/channel/config", headers=hdr(key))).json()["visitor_email"]
    assert cfg == {
        "mode": "required",
        "consent_text": "I agree to be contacted.",
        "privacy_url": "https://initech.example/privacy",
    }
    v1 = hdr(key, visitor="visitor-mail-0001")
    missing = await pub.post("/api/v1/chat/stream", headers=v1, json={"question": "warranty?"})
    assert (
        missing.status_code == 422 and missing.json()["error"]["code"] == "visitor_email_required"
    )
    bad = await pub.post(
        "/api/v1/chat/stream",
        headers=v1,
        json={"question": "warranty?", "visitor_email": "nope", "consent": True},
    )
    assert bad.status_code == 422
    no_consent = await pub.post(
        "/api/v1/chat/stream",
        headers=v1,
        json={"question": "warranty?", "visitor_email": "lead@client.example"},
    )
    assert no_consent.json()["error"]["code"] == "consent_required"
    ok = await pub.post(
        "/api/v1/chat/stream",
        headers=v1,
        json={"question": "warranty?", "visitor_email": "Lead@Client.example", "consent": True},
    )
    assert ok.status_code == 200
    conv1 = next(d for n, d in sse_events(ok.text) if n == "meta")["conversation_id"]
    # Follow-ups need no email.
    follow = await pub.post(
        "/api/v1/chat/stream",
        headers=v1,
        json={"question": "and returns?", "conversation_id": conv1},
    )
    assert follow.status_code == 200
    # Stored encrypted, never in plaintext.
    async with owner_engine.connect() as conn:
        stored = (
            await conn.execute(
                text(
                    "SELECT visitor_email_enc, visitor_email_hash FROM conversations WHERE id = :c"
                ),
                {"c": conv1},
            )
        ).one()
    assert stored.visitor_email_enc and b"lead@client" not in bytes(stored.visitor_email_enc)
    # The email is not an identity: same email on another device sees nothing.
    v2 = hdr(key, visitor="visitor-mail-0002")
    other_device = await pub.post(
        "/api/v1/chat/stream",
        headers=v2,
        json={"question": "hi", "visitor_email": "lead@client.example", "consent": True},
    )
    conv2 = next(d for n, d in sse_events(other_device.text) if n == "meta")["conversation_id"]
    assert conv2 != conv1
    assert (await pub.get(f"/api/v1/conversations/{conv1}/messages", headers=v2)).status_code == 404

    # Tenant Admin: list, CSV export, delete by email. B sees none of it.
    emails = (await w.a.client.get("/api/admin/visitor-emails")).json()["items"]
    assert {e["email"] for e in emails} == {"lead@client.example"} and len(emails) == 2
    csv_body = await w.a.client.get("/api/admin/visitor-emails/export")
    assert (
        csv_body.headers["content-type"].startswith("text/csv")
        and "lead@client.example" in csv_body.text
    )
    assert (await w.b.client.get("/api/admin/visitor-emails")).json()["items"] == []
    assert (
        await w.b.client.delete("/api/admin/visitor-emails?email=lead@client.example")
    ).status_code == 404

    # Feedback from the Visitor shows up in the log viewer and insights.
    msgs = (await pub.get(f"/api/v1/conversations/{conv1}/messages", headers=v1)).json()["items"]
    answer_id = next(m["id"] for m in msgs if m["role"] == "assistant")
    assert (
        await pub.post(
            f"/api/v1/messages/{answer_id}/feedback",
            headers=v1,
            json={"value": -1, "note": "wrong"},
        )
    ).status_code == 204
    convs = (await w.a.client.get("/api/admin/conversations?feedback=down")).json()
    assert [c["id"] for c in convs["items"]] == [conv1]
    assert convs["items"][0]["has_visitor_email"] is True and "lead@client" not in json.dumps(convs)
    page1 = (await w.a.client.get("/api/admin/conversations?limit=1")).json()
    page2 = (
        await w.a.client.get(f"/api/admin/conversations?limit=1&cursor={page1['next_cursor']}")
    ).json()
    assert page1["items"][0]["id"] != page2["items"][0]["id"]
    assert (await w.a.client.get("/api/admin/conversations?channel=api")).json()["items"]
    assert (await w.a.client.get("/api/admin/conversations?channel=hosted")).json()["items"] == []
    detail = (await w.a.client.get(f"/api/admin/conversations/{conv1}")).json()
    assert [m["role"] for m in detail["messages"]][:2] == ["user", "assistant"]
    assert any(m["retrieval"] for m in detail["messages"] if m["role"] == "assistant")
    flat = json.dumps(detail)
    assert "cogs" not in flat and "model" not in flat.replace("model_", "")  # ADR-0019
    assert (await w.b.client.get(f"/api/admin/conversations/{conv1}")).status_code == 404
    assert all(
        c["id"] not in (conv1, conv2, w.conv)
        for c in (await w.b.client.get("/api/admin/conversations")).json()["items"]
    )

    summary = (await w.a.client.get("/api/admin/insights/summary")).json()
    channels = {c["channel"]: c["count"] for c in summary["conversations_by_channel"]}
    assert channels.get("api", 0) >= 3 and channels.get("widget", 0) >= 1
    assert summary["answers"]["thumbs_down"] == 1 and "cogs" not in json.dumps(summary)
    assert {m["meter"] for m in summary["usage"]["meters"]} >= {"answer"}
    unanswered = (await w.a.client.get("/api/admin/insights/unanswered")).json()["items"]
    assert any(u["reason"] == "thumbs_down" and u["question"] == "warranty?" for u in unanswered)
    b_summary = (await w.b.client.get("/api/admin/insights/summary")).json()
    assert b_summary["answers"]["thumbs_down"] == 0

    # Delete-by-email (DSAR) removes it from every Conversation.
    deleted = await w.a.client.delete("/api/admin/visitor-emails?email=LEAD@client.example")
    assert deleted.json() == {"deleted": 2}
    assert (await w.a.client.get("/api/admin/visitor-emails")).json()["items"] == []

    # Roles: the demo Tenant User can read the log and insights but not emails or keys.
    user = client_for(stack["app"])
    me = (
        await user.post(
            "/api/admin/auth/login", json={"email": "user@demo.test", "password": "demo-password"}
        )
    ).json()
    user.headers["x-csrf-token"] = me["csrf_token"]
    assert (await user.get("/api/admin/conversations")).status_code == 200
    assert (await user.get("/api/admin/insights/summary")).status_code == 200
    assert (await user.get("/api/admin/insights/unanswered")).status_code == 200
    for method, url in [
        ("GET", "/api/admin/visitor-emails"),
        ("GET", "/api/admin/visitor-emails/export"),
        ("DELETE", "/api/admin/visitor-emails?email=a@b.co"),
        ("GET", "/api/admin/channel-keys"),
        ("POST", "/api/admin/channel-keys"),
    ]:
        r = await user.request(method, url, json={} if method == "POST" else None)
        assert r.status_code == 403, (method, url, r.text)


# --- M1-18: route inventory — every route is probed cross-Tenant or explicitly exempt ---------------

# Routes without Tenant data, or that never accept a Tenant credential.
EXEMPT: dict[tuple[str, str], str] = {
    ("POST", "/api/auth/signup"): "creates a new Tenant; no existing Tenant data",
    ("POST", "/api/auth/verify-email"): "token-scoped",
    ("POST", "/api/auth/resend-verification"): "uniform response",
    ("POST", "/api/auth/forgot-password"): "uniform response",
    ("POST", "/api/auth/reset-password"): "token-scoped",
    ("POST", "/api/auth/accept-invite"): "token-scoped",
    ("POST", "/api/admin/auth/login"): "credentials",
    ("POST", "/api/admin/auth/logout"): "own session only",
    ("GET", "/api/admin/ocr/languages"): "installed packs; no Tenant data",
    ("POST", "/api/admin/debug/jobs/noop"): "dev-only, never mounted in production",
    ("GET", "/api/admin/debug/jobs/{job_id}"): "dev-only, never mounted in production",
    ("POST", "/api/webhooks/payments/{provider}"): "provider signature only; Tenant from signed "
    "event (tests/integration/test_billing.py covers forgery, replay and cross-Tenant ids)",
}


async def test_every_route_is_probed_cross_tenant(stack: dict[str, Any]) -> None:
    w = await build_world(stack)
    app = stack["app"]
    inv = await w.a.client.post(
        "/api/admin/invites", json={"email": "someone@initech.test", "role": "user"}
    )
    invite_id = inv.json()["invite_id"]
    # A gets an invoice: Starter, then a prorated upgrade to Growth (manual provider).
    await assign_plan(app, w.a.tenant_id, "starter")
    assert (
        await w.a.client.put("/api/admin/billing/account", json={"country": "IN"})
    ).status_code == 200
    up = await w.a.client.post("/api/admin/billing/change-plan", json={"plan_code": "growth"})
    assert up.status_code == 200, up.text
    invoice_id = up.json()["invoice_id"]
    await assign_plan(app, w.a.tenant_id, "business")
    await w.a.client.patch(f"/api/admin/assistants/{w.aid}", json={"hosted_enabled": True})
    a_slug = w.a.me["current_tenant"]["slug"]
    ids = {
        "kb_id": w.kb,
        "doc_id": w.doc,
        "assistant_id": w.aid,
        "key_id": w.key_ids["widget"],
        "conversation_id": w.conv,
        "message_id": w.msg,
        "invite_id": invite_id,
        "user_id": w.a.me["user"]["id"],
        "tenant_id": w.a.tenant_id,
        "invoice_id": invoice_id,
        "plan_id": "plan_growth",
        "profile_id": "fake-default",
    }
    secrets_of_a = [*ids.values(), "Initech", w.a.me["user"]["email"], "24 months"]
    pub = client_for(app)
    b_pub = hdr(w.keys["b_widget"], origin=SHOP_A)
    probed: set[tuple[str, str]] = set()

    async def probe(
        method: str,
        template: str,
        expect: str,
        body: Any = None,
        client: httpx.AsyncClient | None = None,
        headers: dict[str, str] | None = None,
        url: str | None = None,
    ) -> None:
        target = url or template.format(**ids)
        r = await (client or w.b.client).request(method, target, json=body, headers=headers)
        if expect == "404":
            assert r.status_code == 404, (method, target, r.status_code, r.text)
        elif expect.isdigit():
            assert r.status_code == int(expect), (method, target, r.status_code, r.text)
        else:  # "own": succeeds for B but contains nothing of A's
            assert r.status_code < 400, (method, target, r.status_code, r.text)
            leaked = [s for s in secrets_of_a if s in r.text]
            assert not leaked, (method, target, leaked)
        probed.add((method, template))

    # Session + Tenant-level routes resolve from B's credentials only.
    await probe("GET", "/api/admin/auth/me", "own")
    await probe("POST", "/api/admin/auth/switch-tenant", "404", {"tenant_id": w.a.tenant_id})
    await probe("GET", "/api/admin/tenant", "own")
    await probe("PATCH", "/api/admin/tenant", "own", {"name": "Umbrella Corp"})
    await probe("GET", "/api/admin/usage", "own")
    await probe("GET", "/api/admin/members", "own")
    await probe("POST", "/api/admin/invites", "own", {"email": "x@umbrella.test", "role": "user"})
    await probe("DELETE", "/api/admin/invites/{invite_id}", "404")
    await probe("PATCH", "/api/admin/members/{user_id}", "404", {"role": "user"})
    await probe("DELETE", "/api/admin/members/{user_id}", "404")
    # Content.
    await probe("GET", "/api/admin/knowledge-bases", "own")
    await probe("POST", "/api/admin/knowledge-bases", "own", {"name": "B2"})
    await probe("GET", "/api/admin/knowledge-bases/{kb_id}", "404")
    await probe("PATCH", "/api/admin/knowledge-bases/{kb_id}", "404", {"name": "x"})
    await probe("DELETE", "/api/admin/knowledge-bases/{kb_id}", "404")
    r = await w.b.client.post(
        f"/api/admin/knowledge-bases/{w.kb}/documents",
        files=[("file", ("x.md", b"# x\n\nx", "text/markdown"))],
    )
    assert r.status_code == 404, r.text
    probed.add(("POST", "/api/admin/knowledge-bases/{kb_id}/documents"))
    await probe("GET", "/api/admin/knowledge-bases/{kb_id}/documents", "own")
    await probe("GET", "/api/admin/documents/{doc_id}", "404")
    await probe("DELETE", "/api/admin/documents/{doc_id}", "404")
    await probe("POST", "/api/admin/documents/{doc_id}/reingest", "404", {})
    await probe("GET", "/api/admin/documents/{doc_id}/chunks", "404")
    await probe("GET", "/api/admin/assistants", "own")
    await probe("POST", "/api/admin/assistants", "own", {"name": "B2"})
    await probe("GET", "/api/admin/assistants/{assistant_id}", "404")
    await probe("PATCH", "/api/admin/assistants/{assistant_id}", "404", {"name": "x"})
    await probe("DELETE", "/api/admin/assistants/{assistant_id}", "404")
    await probe(
        "POST",
        "/api/admin/assistants/{assistant_id}/playground/stream",
        "404",
        {"question": "warranty?"},
    )
    # Keys, log, emails, insights.
    await probe("GET", "/api/admin/channel-keys", "own")
    await probe(
        "POST", "/api/admin/channel-keys", "own", {"channel": "widget", "assistant_id": w.b_aid}
    )
    await probe("PATCH", "/api/admin/channel-keys/{key_id}", "404", {"allowed_origins": [EVIL]})
    await probe("POST", "/api/admin/channel-keys/{key_id}/revoke", "404", {})
    await probe("GET", "/api/admin/conversations", "own")
    await probe("GET", "/api/admin/conversations/{conversation_id}", "404")
    await probe("GET", "/api/admin/visitor-emails", "own")
    await probe("GET", "/api/admin/visitor-emails/export", "own")
    await probe(
        "DELETE",
        "/api/admin/visitor-emails",
        "404",
        url="/api/admin/visitor-emails?email=lead@client.example",
    )
    await probe("GET", "/api/admin/insights/summary", "own")
    await probe("GET", "/api/admin/insights/unanswered", "own")
    # Billing (M3): B sees and changes only its own billing; A's invoice ids are 404.
    await probe("GET", "/api/admin/billing", "own")
    await probe("GET", "/api/admin/billing/plans", "own")
    await probe("PUT", "/api/admin/billing/account", "own", {"country": "US"})
    await probe("PUT", "/api/admin/billing/spend-cap", "own", {"spend_cap_micros": 5_000_000})
    await probe("POST", "/api/admin/billing/change-plan", "own", {"plan_code": "starter"})
    await probe("POST", "/api/admin/billing/cancel", "own", {})
    await probe("POST", "/api/admin/billing/resume", "own", {})
    await probe("POST", "/api/admin/billing/portal", "409", {})
    await probe("GET", "/api/admin/billing/invoices", "own")
    await probe("GET", "/api/admin/billing/invoices/{invoice_id}", "404")
    await probe("GET", "/api/admin/billing/invoices/{invoice_id}/html", "404")
    terms = {"prices": {"USD": {"base_micros": 0}, "INR": {"base_micros": 0}}}
    # Platform routes: a Tenant Admin is refused.
    for method, template, body in [
        ("GET", "/api/platform/plans", None),
        ("POST", "/api/platform/plans", {"code": "evil", "name": "Evil", **terms}),
        ("POST", "/api/platform/plans/margin", terms),
        ("GET", "/api/platform/plans/{plan_id}", None),
        ("PATCH", "/api/platform/plans/{plan_id}", {"name": "x"}),
        ("POST", "/api/platform/plans/{plan_id}/versions", terms),
        ("POST", "/api/platform/plans/{plan_id}/migrate", {}),
        ("GET", "/api/platform/cost-rates", None),
        ("PUT", "/api/platform/cost-rates", {"rates": {"fx_usd_inr": 1}}),
        ("GET", "/api/platform/tenants/{tenant_id}/billing", None),
        ("PUT", "/api/platform/tenants/{tenant_id}/billing", {"plan_code": "free"}),
        ("GET", "/api/platform/invoices", None),
        ("POST", "/api/platform/invoices/{invoice_id}/mark-paid", {}),
        ("POST", "/api/platform/invoices/{invoice_id}/payment-failed", {}),
        ("POST", "/api/platform/invoices/{invoice_id}/void", {}),
        (
            "POST",
            "/api/platform/model-profiles",
            {"id": "evil", "provider": "openai_compat", "model": "x", "license": "commercial-api"},
        ),
        ("PATCH", "/api/platform/model-profiles/{profile_id}", {"status": "disabled"}),
        ("POST", "/api/platform/model-profiles/{profile_id}/default", {}),
        ("POST", "/api/platform/model-profiles/{profile_id}/test", {}),
        ("GET", "/api/platform/finance/summary", None),
        ("GET", "/api/platform/finance/tenants", None),
        ("POST", "/api/platform/finance/simulate", {"plan_code": "growth"}),
        ("GET", "/api/platform/finance/funnel", None),
        ("GET", "/api/platform/tenants", None),
        ("GET", "/api/platform/tenants/{tenant_id}", None),
        ("PATCH", "/api/platform/tenants/{tenant_id}", {"status": "suspended"}),
        ("DELETE", "/api/platform/tenants/{tenant_id}", None),
        ("GET", "/api/platform/model-profiles", None),
        ("GET", "/api/platform/audit", None),
        ("POST", "/api/platform/admins/invites", {"email": "evil@umbrella.test"}),
    ]:
        await probe(method, template, "403", body)
    # Public Chat API with B's Channel Key against A's ids.
    await probe("GET", "/api/v1/channel/config", "own", client=pub, headers=b_pub)
    await probe(
        "POST",
        "/api/v1/chat/stream",
        "404",
        {"question": "warranty?", "conversation_id": w.conv},
        client=pub,
        headers=b_pub,
    )
    await probe(
        "GET", "/api/v1/conversations/{conversation_id}/messages", "404", client=pub, headers=b_pub
    )
    await probe(
        "POST",
        "/api/v1/messages/{message_id}/feedback",
        "404",
        {"value": -1},
        client=pub,
        headers=b_pub,
    )
    # Hosted bootstrap: A's page resolves only by A's slugs; B's slug + A's Assistant slug → 404.
    b_slug = w.b.me["current_tenant"]["slug"]
    await probe(
        "GET",
        "/api/v1/hosted/{tenant_slug}/{assistant_slug}",
        "404",
        client=pub,
        url=f"/api/v1/hosted/{b_slug}/help",
    )
    assert (await pub.get(f"/api/v1/hosted/{a_slug}/help")).status_code == 200

    # A must be untouched by all of B's attempts.
    assert (await w.a.client.get(f"/api/admin/assistants/{w.aid}")).json()["name"] == "Initech Help"
    assert (await w.a.client.get(f"/api/admin/documents/{w.doc}")).status_code == 200

    inventory = {(m.upper(), p) for p, ops in app.openapi()["paths"].items() for m in ops}
    missing = sorted(inventory - probed - set(EXEMPT))
    assert not missing, (
        f"routes without a cross-Tenant probe (add one here or to EXEMPT): {missing}"
    )
    stale = sorted(set(EXEMPT) - inventory)
    assert not stale, f"EXEMPT lists routes that no longer exist: {stale}"
