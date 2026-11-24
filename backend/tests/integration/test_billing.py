"""M3 exit verification on real Postgres (M3-13): Plans, quotas, upgrades in INR/USD, invoices,
reconciliation, webhooks (Stripe, mocked HTTPS), past-due, margin floor, finance, Custom LLM."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from codura_docs import metering
from codura_docs.adapters.payments.stripe import StripePaymentProvider, sign
from codura_docs.api.app import create_app
from codura_docs.billing import rollups, subscriptions
from codura_docs.billing.money import month_start, next_month, proration_minor
from codura_docs.config import Settings
from codura_docs.db.session import tenant_session
from codura_docs.metering import quotas
from codura_docs.registry import Registry
from codura_docs.tenancy import TenantContext
from codura_docs.worker.runner import Worker
from tests.contract.test_payment_contract import FakeStripe
from tests.integration.test_m1_flows import (
    Org,
    ask,
    client_for,
    drain,
    signup_and_login,
    sse_events,
)
from tests.integration.test_public_auth import assign_plan, hdr, make_key, platform_client

pytestmark = pytest.mark.integration
WEBHOOK_SECRET = "whsec_it"


async def new_org(stack: dict[str, Any], name: str) -> Org:
    run = uuid.uuid4().hex[:6]
    return await signup_and_login(stack, f"{name} {run}", f"owner-{run}@{name.lower()}.test")


async def make_assistant(o: Org, worker: Worker) -> str:
    kb = (await o.client.post("/api/admin/knowledge-bases", json={"name": "Docs"})).json()["id"]
    await o.client.post(
        f"/api/admin/knowledge-bases/{kb}/documents",
        files=[
            ("file", ("faq.md", b"# FAQ\n\nThe X200 warranty lasts 24 months.", "text/markdown"))
        ],
    )
    await drain(worker)
    r = await o.client.post(
        "/api/admin/assistants",
        json={"name": "Help", "knowledge_base_ids": [kb], "retrieval": {"min_score": 0.0}},
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def finish(events: list[tuple[str, dict[str, Any]]]) -> str:
    return str(next(d for n, d in events if n == "done")["finish_reason"])


# --- Write boundary + Trial ------------------------------------------------------------------------


async def test_app_role_cannot_write_billing_and_trial_is_created(
    stack: dict[str, Any], app_engine: AsyncEngine
) -> None:
    a = await new_org(stack, "Acme")
    overview = (await a.client.get("/api/admin/billing")).json()
    assert overview["status"] == "trialing"
    assert overview["plan"]["code"] == "free" and overview["trial_plan"] == "growth"
    assert overview["included"]["answer"] == 5000  # Growth limits during the Trial
    assert "cogs" not in json.dumps(overview).lower()
    async with tenant_session(app_engine, TenantContext(a.tenant_id)) as s:
        own = (await s.execute(text("SELECT count(*) FROM subscriptions"))).scalar()
        assert own == 1  # RLS: only its own Subscription is visible
    for sql in (
        "UPDATE subscriptions SET plan_version_id = 'plv_business_1'",
        "INSERT INTO invoices (id, tenant_id, number, kind, period_start, currency, status, provider, "
        "idempotency_key) VALUES ('bill_x', :t, 'X-1', 'period', now(), 'USD', 'paid', 'manual', 'k')",
        "UPDATE plan_prices SET base_micros = 0",
        "INSERT INTO tenant_billing_overrides (tenant_id, entitlements) VALUES (:t, '{\"custom_llm\": true}')",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with tenant_session(app_engine, TenantContext(a.tenant_id)) as s:
                await s.execute(text(sql), {"t": a.tenant_id})


# --- Upgrade (INR, manual), invoices, currency lock ----------------------------------------------------


async def test_upgrade_in_inr_with_proration_and_manual_payment(stack: dict[str, Any]) -> None:
    app = stack["app"]
    a = await new_org(stack, "Bharat")
    blocked = await a.client.post("/api/admin/billing/change-plan", json={"plan_code": "starter"})
    assert blocked.json()["error"]["code"] == "billing_account_required"
    acct = await a.client.put(
        "/api/admin/billing/account",
        json={
            "country": "in",
            "billing_email": "accounts@bharat.test",
            "company_name": "Bharat Pvt Ltd",
        },
    )
    assert acct.json()["currency"] == "INR"
    plans = (await a.client.get("/api/admin/billing/plans")).json()
    starter = next(p for p in plans["items"] if p["code"] == "starter")
    assert list(starter["prices"]) == ["INR"]
    assert starter["prices"]["INR"]["base_micros"] == 1999_000_000

    up = await a.client.post("/api/admin/billing/change-plan", json={"plan_code": "starter"})
    assert up.status_code == 200, up.text
    assert up.json()["applied"] and up.json()["kind"] == "upgrade"
    inv = (await a.client.get(f"/api/admin/billing/invoices/{up.json()['invoice_id']}")).json()
    assert inv["currency"] == "INR" and inv["status"] == "open" and inv["kind"] == "proration"
    assert inv["total_minor"] == proration_minor(0, 1999_000_000, datetime.now(UTC).date())
    overview = (await a.client.get("/api/admin/billing")).json()
    assert overview["plan"]["code"] == "starter" and overview["status"] == "active"
    html = await a.client.get(f"/api/admin/billing/invoices/{inv['id']}/html")
    assert "₹" in html.text and "Bharat Pvt Ltd" in html.text
    assert "default-src 'none'" in html.headers["content-security-policy"]

    # Currency is locked after the first invoice.
    moved = await a.client.put("/api/admin/billing/account", json={"country": "US"})
    assert moved.json()["error"]["code"] == "currency_locked"
    # A Tenant User can read billing but not change it.
    plat = await platform_client(app)
    paid = await plat.post(f"/api/platform/invoices/{inv['id']}/mark-paid", json={"method": "upi"})
    assert paid.status_code == 200
    detail = (await a.client.get(f"/api/admin/billing/invoices/{inv['id']}")).json()
    assert (
        detail["status"] == "paid" and detail["payments"][0]["amount_minor"] == inv["total_minor"]
    )
    assert (
        await plat.post(f"/api/platform/invoices/{inv['id']}/mark-paid", json={})
    ).status_code == 200
    again = (await a.client.get(f"/api/admin/billing/invoices/{inv['id']}")).json()
    assert len(again["payments"]) == 1  # idempotent

    # Downgrade is scheduled for the period roll, cancel/resume toggle.
    down = await a.client.post("/api/admin/billing/change-plan", json={"plan_code": "free"})
    assert down.json()["kind"] == "downgrade"
    assert (await a.client.get("/api/admin/billing")).json()["pending_plan"] == "free"
    assert (await a.client.post("/api/admin/billing/cancel")).status_code == 200
    assert (await a.client.get("/api/admin/billing")).json()["cancel_at_period_end"] is True
    assert (await a.client.post("/api/admin/billing/resume")).status_code == 200


# --- Quota hard stop on Free + concurrency ---------------------------------------------------------------


async def test_free_plan_hard_stop_and_no_overshoot_under_concurrency(
    stack: dict[str, Any], worker_engine: AsyncEngine
) -> None:
    app, worker = stack["app"], stack["worker"]
    a = await new_org(stack, "Freebie")
    aid = await make_assistant(a, worker)
    await assign_plan(app, a.tenant_id, "free")
    plat = await platform_client(app)
    await plat.put(f"/api/platform/tenants/{a.tenant_id}/billing", json={"included": {"answer": 2}})
    results = [finish(await ask(a, aid, "How long is the warranty?")) for _ in range(3)]
    assert results[:2] != ["limit", "limit"] and results[2] == "limit"
    usage = (await a.client.get("/api/admin/usage")).json()
    answer = next(m for m in usage["meters"] if m["meter"] == "answer")
    assert answer["used"] == 2 and answer["limit"] == 2
    assert usage["notices"] == [{"meter": "answer", "threshold": 100}]  # highest reached
    await drain(worker)
    subjects = [m.subject for m in stack["registry"].email().sent if m.to.startswith("owner-")]
    assert any("80%" in x for x in subjects) and any("100%" in x for x in subjects)

    # 20 parallel admissions against 5 remaining: exactly 5 reservations win.
    await plat.put(f"/api/platform/tenants/{a.tenant_id}/billing", json={"included": {"answer": 7}})

    async def one() -> bool:
        async with tenant_session(worker_engine, TenantContext(a.tenant_id)) as s:
            return (await quotas.admit(s, a.tenant_id, "answer")).allowed

    wins = await asyncio.gather(*(one() for _ in range(20)))
    assert sum(wins) == 5


# --- Overage, spend cap, period roll, reconciliation (USD) ---------------------------------------------


async def test_period_invoice_matches_ledger_and_reconciles(
    stack: dict[str, Any], owner_engine: AsyncEngine, worker_engine: AsyncEngine
) -> None:
    app = stack["app"]
    a = await new_org(stack, "Globex")
    await a.client.put("/api/admin/billing/account", json={"country": "US"})
    await assign_plan(app, a.tenant_id, "starter")
    plat = await platform_client(app)
    await plat.put(f"/api/platform/tenants/{a.tenant_id}/billing", json={"included": {"answer": 2}})
    today = datetime.now(UTC).date()
    last = month_start(month_start(today) - timedelta(days=1))
    # Last month: 5 answers (3 over), 2 OCR pages, one duplicate retry; the period started then.
    async with tenant_session(worker_engine, TenantContext(a.tenant_id)) as s:
        for i in range(5):
            await s.execute(
                text(
                    "INSERT INTO usage_events (id, tenant_id, meter, quantity, cogs_micros, idempotency_key, "
                    "occurred_at, model_profile_id) VALUES (:id, :t, 'answer', 1, 2500, :k, :at, 'fake-default')"
                ),
                {
                    "id": f"ue_{uuid.uuid4().hex}",
                    "t": a.tenant_id,
                    "k": f"m{i}",
                    "at": datetime.combine(last, datetime.min.time(), tzinfo=UTC)
                    + timedelta(days=3, hours=i),
                },
            )
        await s.execute(
            text(
                "INSERT INTO usage_counters (tenant_id, meter, period_start, quantity, cogs_micros) "
                "VALUES (:t, 'answer', :p, 5, 12500)"
            ),
            {"t": a.tenant_id, "p": last},
        )
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE subscriptions SET period_start = :p WHERE tenant_id = :t"),
            {"p": last, "t": a.tenant_id},
        )
    provider = stack["registry"].payments()
    async with tenant_session(worker_engine, TenantContext(a.tenant_id)) as s:
        issued = await subscriptions.roll_period(s, provider, a.tenant_id)
        assert len(issued) == 1
        lines = (
            await s.execute(
                text(
                    "SELECT kind, meter, quantity, unit_micros, amount_minor FROM invoice_lines WHERE invoice_id = :i ORDER BY sort"
                ),
                {"i": issued[0]},
            )
        ).all()
        assert [(r.kind, r.meter) for r in lines] == [("base", None), ("overage", "answer")]
        assert lines[0].amount_minor == 2900  # $29.00 Starter, next period in advance
        assert float(lines[1].quantity) == 3 and lines[1].unit_micros == 20_000
        assert lines[1].amount_minor == 6  # 3 × $0.02, from rollups
        assert await rollups.reconcile(s, a.tenant_id, last) == []
        assert await subscriptions.roll_period(s, provider, a.tenant_id) == []  # idempotent
        period = (
            await s.execute(
                text("SELECT period_start FROM subscriptions WHERE tenant_id = :t"),
                {"t": a.tenant_id},
            )
        ).scalar()
        assert period == month_start(today)

    # Spend cap: overage stops at the cap (Starter $0.02/answer → cap of 1 answer).
    await plat.put(f"/api/platform/tenants/{a.tenant_id}/billing", json={"included": {"answer": 0}})
    assert (
        await a.client.put("/api/admin/billing/spend-cap", json={"spend_cap_micros": 20_000})
    ).status_code == 200
    async with tenant_session(worker_engine, TenantContext(a.tenant_id)) as s:
        first = await quotas.admit(s, a.tenant_id, "answer")
        assert first.allowed and first.reason == "overage"
        await metering.record(
            s,
            a.tenant_id,
            "answer",
            1,
            cogs_micros=1,
            idempotency_key="cap1",
            reservation_id=first.reservation_id,
        )
    async with tenant_session(worker_engine, TenantContext(a.tenant_id)) as s:
        second = await quotas.admit(s, a.tenant_id, "answer")
        assert not second.allowed and second.reason == "spend_cap_reached"


# --- Stripe: checkout webhook, dedupe, forgery, past-due ----------------------------------------------------


async def test_stripe_checkout_webhooks_and_past_due(
    pg_database: Settings, owner_engine: AsyncEngine, worker_engine: AsyncEngine
) -> None:
    fake = FakeStripe()
    stripe = StripePaymentProvider(
        "sk_test_x",
        WEBHOOK_SECRET,
        api_base="https://api.stripe.test",
        transport=httpx.MockTransport(fake),
    )
    registry = Registry(pg_database, pg_database.load_models(), overrides={"payments": stripe})
    app = create_app(pg_database, registry=registry)
    worker = Worker(pg_database, registry, app.state.debug_worker_engine, app.state.engine)
    stack = {"app": app, "registry": registry, "worker": worker}
    async with app.router.lifespan_context(app):
        a = await new_org(stack, "Stripey")
        aid = await make_assistant(a, worker)
        await a.client.put("/api/admin/billing/account", json={"country": "DE"})
        _, widget_key = await make_key(a, channel="widget", assistant_id=aid)
        up = (
            await a.client.post("/api/admin/billing/change-plan", json={"plan_code": "growth"})
        ).json()
        assert up["applied"] is False and up["redirect_url"].startswith(
            "https://checkout.stripe.test/"
        )
        assert (await a.client.get("/api/admin/billing")).json()["status"] == "trialing"  # not yet
        checkout_form = next(f for p, f, _ in fake.calls if p == "/v1/checkout/sessions")
        assert checkout_form["line_items[0][price_data][currency]"] == ["usd"]
        customer = next(f for p, f, _ in fake.calls if p == "/v1/customers")
        assert customer["address[country]"] == ["DE"]

        pub = client_for(app)

        def event(evt_id: str, kind: str, obj: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
            body = json.dumps({"id": evt_id, "type": kind, "data": {"object": obj}}).encode()
            return body, {
                "Stripe-Signature": sign(WEBHOOK_SECRET, body, int(time.time())),
                "content-type": "application/json",
            }

        obj = {
            "customer": "cus_1",
            "client_reference_id": a.tenant_id,
            "payment_intent": "pi_1",
            "amount_total": 1,
            "currency": "usd",
            "metadata": {"tenant_id": a.tenant_id, "invoice_id": up["invoice_id"]},
        }
        body, headers = event("evt_checkout", "checkout.session.completed", obj)
        forged = await pub.post(
            "/api/webhooks/payments/stripe",
            content=body,
            headers={**headers, "Stripe-Signature": "t=1,v1=00"},
        )
        assert forged.status_code == 400
        assert (
            await pub.post("/api/webhooks/payments/manual", content=body, headers=headers)
        ).status_code == 404
        r = await pub.post("/api/webhooks/payments/stripe", content=body, headers=headers)
        assert r.json() == {"status": "processed"}, r.text
        replay = await pub.post("/api/webhooks/payments/stripe", content=body, headers=headers)
        assert replay.json() == {"status": "duplicate"}
        overview = (await a.client.get("/api/admin/billing")).json()
        assert overview["plan"]["code"] == "growth" and overview["status"] == "active"
        inv = (await a.client.get(f"/api/admin/billing/invoices/{up['invoice_id']}")).json()
        assert inv["status"] == "paid" and len(inv["payments"]) == 1

        # Another Tenant's invoice id in signed metadata never touches it (RLS + customer mapping).
        b = await new_org(stack, "Other")
        spoof, sh = event(
            "evt_spoof",
            "invoice.payment_failed",
            {
                "customer": "cus_404",
                "metadata": {"tenant_id": b.tenant_id, "invoice_id": up["invoice_id"]},
            },
        )
        assert (
            await pub.post("/api/webhooks/payments/stripe", content=spoof, headers=sh)
        ).status_code == 200
        assert (await a.client.get(f"/api/admin/billing/invoices/{up['invoice_id']}")).json()[
            "status"
        ] == "paid"

        # Payment failure → grace → past_due: chat says "unavailable", writes get 402, billing works.
        fail, fh = event(
            "evt_fail",
            "invoice.payment_failed",
            {
                "id": "in_x",
                "customer": "cus_1",
                "metadata": {"tenant_id": a.tenant_id, "invoice_id": up["invoice_id"]},
            },
        )
        assert (await pub.post("/api/webhooks/payments/stripe", content=fail, headers=fh)).json()[
            "status"
        ] == "processed"
        async with owner_engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE subscriptions SET payment_failed_at = now() - interval '8 days' WHERE tenant_id = :t"
                ),
                {"t": a.tenant_id},
            )
        async with tenant_session(worker_engine, TenantContext(a.tenant_id)) as s:
            await subscriptions.roll_period(s, stripe, a.tenant_id)
        chat = await pub.post(
            "/api/v1/chat/stream", headers=hdr(widget_key), json={"question": "warranty?"}
        )
        assert finish(sse_events(chat.text)) == "unavailable"
        playground = await a.client.post(
            f"/api/admin/assistants/{aid}/playground/stream", json={"question": "warranty?"}
        )
        assert playground.status_code == 402
        blocked = await a.client.post("/api/admin/knowledge-bases", json={"name": "x"})
        assert blocked.status_code == 402 and blocked.json()["error"]["code"] == "payment_required"
        assert (await a.client.get("/api/admin/billing")).json()["status"] == "past_due"
        paid, ph = event(
            "evt_paid",
            "invoice.paid",
            {
                "id": "in_x",
                "customer": "cus_1",
                "amount_paid": 1,
                "currency": "usd",
                "payment_intent": "pi_2",
                "metadata": {"tenant_id": a.tenant_id, "invoice_id": up["invoice_id"]},
            },
        )
        assert (await pub.post("/api/webhooks/payments/stripe", content=paid, headers=ph)).json()[
            "status"
        ] == "processed"
        assert (await a.client.get("/api/admin/billing")).json()["status"] == "active"
        assert (
            await a.client.post("/api/admin/knowledge-bases", json={"name": "y"})
        ).status_code == 201
        portal = await a.client.post("/api/admin/billing/portal")
        assert portal.json()["url"].startswith("https://billing.stripe.test/")
    for engine in ("platform_engine", "billing_engine", "debug_worker_engine"):
        await getattr(app.state, engine).dispose()


# --- Plan editor: margin floor, versions, immutability ---------------------------------------------------


async def test_plan_editor_margin_floor_and_versions(
    stack: dict[str, Any], owner_engine: AsyncEngine
) -> None:
    plat = await platform_client(stack["app"])
    cheap = {
        "code": f"cheap-{uuid.uuid4().hex[:4]}",
        "name": "Cheap",
        "included": {"answer": 1000, "page_ocr": 500},
        "overage_enabled": True,
        "prices": {
            "USD": {
                "base_micros": 1_000_000,
                "overage_micros": {"answer": 10_000, "page_ocr": 10_000},
            },
            "INR": {
                "base_micros": 80_000_000,
                "overage_micros": {"answer": 800_000, "page_ocr": 800_000},
            },
        },
    }
    refused = await plat.post("/api/platform/plans", json=cheap)
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "below_margin_floor"
    assert refused.json()["error"]["details"]["margin"]["currencies"]["USD"]["base_ok"] is False
    leader = await plat.post("/api/platform/plans", json={**cheap, "loss_leader": True})
    assert leader.status_code == 201, leader.text
    audit = (await plat.get("/api/platform/audit")).json()["items"]
    assert any(e["action"] == "plan.version.loss_leader" for e in audit)
    unknown = await plat.post(
        "/api/platform/plans/margin", json={**cheap, "entitlements": {"teleport": True}}
    )
    assert unknown.status_code == 422

    # Growth v1 is in use → immutable in the DB; edits create v2 and become current.
    with pytest.raises(DBAPIError, match="immutable"):
        async with owner_engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE plan_prices SET base_micros = 1 WHERE plan_version_id = 'plv_growth_1'"
                )
            )
    growth = (await plat.get("/api/platform/plans/plan_growth")).json()
    body = {
        "included": growth["included"],
        "entitlements": growth["entitlements"],
        "overage_enabled": True,
        "prices": {
            c: {"base_micros": p["base_micros"] + 1_000_000, "overage_micros": p["overage_micros"]}
            for c, p in growth["prices"].items()
        },
    }
    v2 = await plat.post("/api/platform/plans/plan_growth/versions", json=body)
    assert v2.status_code == 201, v2.text
    assert v2.json()["version"] == 2 and v2.json()["current_version_id"] == v2.json()["version_id"]
    moved = await plat.post("/api/platform/plans/plan_growth/migrate")
    assert moved.status_code == 200


# --- Finance: margins and the unprofitable flag ---------------------------------------------------------


async def test_finance_flags_unprofitable_tenant(
    stack: dict[str, Any], worker_engine: AsyncEngine
) -> None:
    app = stack["app"]
    a = await new_org(stack, "Costly")
    await a.client.put("/api/admin/billing/account", json={"country": "US"})
    await a.client.post("/api/admin/billing/change-plan", json={"plan_code": "starter"})
    async with tenant_session(worker_engine, TenantContext(a.tenant_id)) as s:
        await s.execute(
            text(
                "INSERT INTO usage_rollups (tenant_id, day, meter, model_profile_id, quantity, cogs_micros, events) "
                "VALUES (:t, :d, 'answer', 'fake-default', 50000, 500000000, 50000)"
            ),
            {"t": a.tenant_id, "d": date.today()},
        )
    plat = await platform_client(app)
    rows = (await plat.get("/api/platform/finance/tenants")).json()
    mine = next(t for t in rows["items"] if t["tenant_id"] == a.tenant_id)
    assert mine["unprofitable"] and any("answer" in c for c in mine["causes"])
    summary = (await plat.get("/api/platform/finance/summary")).json()
    assert summary["cogs_usd_micros"] >= 500_000_000 and summary["mrr_usd_micros"] >= 29_000_000
    assert any(m["model_profile_id"] == "fake-default" for m in summary["by_model"])
    funnel = (await plat.get("/api/platform/finance/funnel")).json()
    assert funnel["signups"] >= funnel["verified"] >= funnel["activated"] >= 0
    sim = (
        await plat.post(
            "/api/platform/finance/simulate",
            json={"plan_code": "starter", "base_micros": {"USD": 999_000_000}},
        )
    ).json()
    assert sim["tenants"] >= 1


# --- Custom LLM ---------------------------------------------------------------------------------------------------


async def test_custom_llm_answers_metered_and_hidden(
    stack: dict[str, Any], owner_engine: AsyncEngine
) -> None:
    app, worker = stack["app"], stack["worker"]
    a = await new_org(stack, "Ownmodel")
    aid = await make_assistant(a, worker)
    async with (
        owner_engine.begin() as conn
    ):  # a `fake` provider stands in for the Tenant's endpoint
        await conn.execute(
            text(
                "INSERT INTO model_profiles (id, tenant_id, provider, model, license, is_local) "
                "VALUES (:id, :t, 'fake', 'tenant-model', 'apache-2.0', true)"
            ),
            {"id": f"custom-{a.tenant_id[-6:].lower()}", "t": a.tenant_id},
        )
    plat = await platform_client(app)
    # A Plan's default Model Profile answers (how premium Plans use the Anthropic profile).
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO model_profiles (id, provider, model, license, is_local) "
                "VALUES ('fake-premium', 'fake', 'premium-model', 'apache-2.0', true) ON CONFLICT DO NOTHING"
            )
        )
    code = f"premium-{uuid.uuid4().hex[:4]}"
    created = await plat.post(
        "/api/platform/plans",
        json={
            "code": code,
            "name": "Premium",
            "is_public": False,
            "loss_leader": True,
            "default_model_profile_id": "fake-premium",
            "included": {"answer": 100},
            "prices": {"USD": {"base_micros": 499_000_000}, "INR": {"base_micros": 39_999_000_000}},
        },
    )
    assert created.status_code == 201, created.text
    await assign_plan(app, a.tenant_id, code)
    assert finish(await ask(a, aid, "warranty?")) != "limit"
    on = await plat.put(
        f"/api/platform/tenants/{a.tenant_id}/billing", json={"entitlements": {"custom_llm": True}}
    )
    assert on.json()["custom_llm_profiles"]
    events = await ask(a, aid, "How long is the warranty?")
    assert "tenant-model" not in json.dumps(events)
    async with owner_engine.connect() as conn:
        meters = (
            await conn.execute(
                text(
                    "SELECT meter, model_profile_id FROM usage_events WHERE tenant_id = :t AND meter LIKE 'answer%' ORDER BY occurred_at"
                ),
                {"t": a.tenant_id},
            )
        ).all()
    assert meters[0].meter == "answer" and meters[0].model_profile_id == "fake-premium"
    assert meters[-1].meter == "answer_custom_llm"
    assert meters[-1].model_profile_id.startswith("custom-")
    tenant_view = json.dumps((await a.client.get("/api/admin/billing")).json())
    assert "custom-" not in tenant_view and "fake" not in tenant_view


# --- Scheduler + rollup Jobs -------------------------------------------------------------------------------------


async def test_scheduler_enqueues_once_and_jobs_run(stack: dict[str, Any]) -> None:
    worker: Worker = stack["worker"]
    now = datetime(2031, 3, 1, 1, 5, tzinfo=UTC)
    assert await worker.schedule_once(now) == 4
    assert await worker.schedule_once(now) == 0
    await drain(worker)
    assert next_month(date(2031, 12, 1)) == date(2032, 1, 1)
