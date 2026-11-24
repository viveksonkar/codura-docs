"""Platform Admin billing actions on one Tenant, plus manual invoice collection (FR-63, FR-66).

Runs on the platform engine (cross-Tenant reads by design). Every write is audited in
``platform_audit`` as well as ``subscription_events``.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from codura_docs.billing import ledger, subscriptions
from codura_docs.billing.entitlements import InvalidTerms, for_tenant, validate
from codura_docs.platform.service import audit


async def view(session: AsyncSession, tenant_id: str) -> dict[str, Any] | None:
    exists = (
        await session.execute(text("SELECT 1 FROM tenants WHERE id = :t"), {"t": tenant_id})
    ).first()
    if not exists:
        return None
    terms = await for_tenant(session, tenant_id)
    override = (
        (
            await session.execute(
                text(
                    "SELECT included, entitlements, overage_enabled, note, updated_at FROM tenant_billing_overrides WHERE tenant_id = :t"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .first()
    )
    events = (
        (
            await session.execute(
                text(
                    "SELECT action, from_plan_version_id, to_plan_version_id, from_status, to_status, actor, details, created_at "
                    "FROM subscription_events WHERE tenant_id = :t ORDER BY created_at DESC LIMIT 50"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    invoices = (
        (
            await session.execute(
                text(
                    "SELECT id, number, kind, period_start, currency, status, total_minor, issued_at, due_at, paid_at "
                    "FROM invoices WHERE tenant_id = :t ORDER BY created_at DESC LIMIT 50"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    profiles = (
        (
            await session.execute(
                text(
                    "SELECT id, provider, model, base_url, status, credentials_enc IS NOT NULL AS has_credentials "
                    "FROM model_profiles WHERE tenant_id = :t ORDER BY id"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    return {
        "terms": terms.public() | {"plan_version_id": terms.plan_version_id},
        "account": await subscriptions.account(session, tenant_id),
        "override": dict(override) if override else None,
        "events": [dict(e) for e in events],
        "invoices": [dict(i) for i in invoices],
        "custom_llm_profiles": [dict(p) for p in profiles],
    }


async def update(
    session: AsyncSession,
    actor: str,
    tenant_id: str,
    *,
    plan_code: str | None,
    included: dict[str, int] | None,
    entitlements: dict[str, Any] | None,
    overage_enabled: bool | None,
    clear_overage: bool,
    note: str | None,
) -> None:
    if plan_code:
        await subscriptions.assign_plan(session, tenant_id, plan_code, actor=actor)
    if (
        included is not None
        or entitlements is not None
        or overage_enabled is not None
        or clear_overage
        or note is not None
    ):
        try:
            validate(entitlements or {}, included or {})
        except InvalidTerms as exc:
            raise subscriptions.BillingError(422, "validation_error", str(exc)) from exc
        current = (
            await session.execute(
                text(
                    "SELECT included, entitlements, overage_enabled, note FROM tenant_billing_overrides WHERE tenant_id = :t"
                ),
                {"t": tenant_id},
            )
        ).first()
        inc = included if included is not None else (current.included if current else {})
        ent = (
            entitlements if entitlements is not None else (current.entitlements if current else {})
        )
        ov = (
            None
            if clear_overage
            else (
                overage_enabled
                if overage_enabled is not None
                else (current.overage_enabled if current else None)
            )
        )
        await session.execute(
            text(
                "INSERT INTO tenant_billing_overrides (tenant_id, included, entitlements, overage_enabled, note, updated_by) "
                "VALUES (:t, CAST(:i AS jsonb), CAST(:e AS jsonb), :o, :n, :by) ON CONFLICT (tenant_id) DO UPDATE SET "
                "included = EXCLUDED.included, entitlements = EXCLUDED.entitlements, "
                "overage_enabled = EXCLUDED.overage_enabled, note = EXCLUDED.note, "
                "updated_by = EXCLUDED.updated_by, updated_at = now()"
            ),
            {
                "t": tenant_id,
                "i": json.dumps(inc if isinstance(inc, dict) else json.loads(inc)),
                "e": json.dumps(ent if isinstance(ent, dict) else json.loads(ent)),
                "o": ov,
                "n": note if note is not None else (current.note if current else ""),
                "by": actor,
            },
        )
    await audit(
        session,
        actor,
        "tenant.billing",
        tenant_id,
        {
            "plan_code": plan_code,
            "included": included,
            "entitlements": entitlements,
            "overage_enabled": overage_enabled,
        },
    )


async def list_invoices(session: AsyncSession, status: str | None) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                text(
                    "SELECT i.id, i.tenant_id, t.name AS tenant_name, i.number, i.kind, i.period_start, i.currency, "
                    "i.status, i.total_minor, i.issued_at, i.due_at, i.paid_at, i.provider FROM invoices i "
                    "JOIN tenants t ON t.id = i.tenant_id WHERE (CAST(:s AS text) IS NULL OR i.status = :s) "
                    "AND i.status <> 'draft' ORDER BY i.created_at DESC LIMIT 200"
                ),
                {"s": status},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def mark_paid(
    session: AsyncSession, actor: str, invoice_id: str, *, method: str, note: str
) -> bool:
    tenant_id = (
        await session.execute(
            text("SELECT tenant_id FROM invoices WHERE id = :id"), {"id": invoice_id}
        )
    ).scalar()
    if tenant_id is None:
        return False
    ok = await ledger.mark_paid(
        session,
        invoice_id,
        provider="manual",
        provider_payment_id=None,
        method=method,
        recorded_by=actor,
        note=note,
    )
    if ok:
        await subscriptions.payment_succeeded(session, str(tenant_id), actor=actor)
        await audit(
            session,
            actor,
            "invoice.mark_paid",
            str(tenant_id),
            {"invoice_id": invoice_id, "method": method},
        )
    return ok


async def mark_failed(session: AsyncSession, actor: str, invoice_id: str) -> bool:
    tenant_id = (
        await session.execute(
            text("SELECT tenant_id FROM invoices WHERE id = :id"), {"id": invoice_id}
        )
    ).scalar()
    if tenant_id is None:
        return False
    await subscriptions.payment_failed(session, str(tenant_id), actor=actor)
    await audit(
        session, actor, "invoice.payment_failed", str(tenant_id), {"invoice_id": invoice_id}
    )
    return True


async def void(session: AsyncSession, actor: str, invoice_id: str) -> bool:
    tenant_id = (
        await session.execute(
            text("SELECT tenant_id FROM invoices WHERE id = :id"), {"id": invoice_id}
        )
    ).scalar()
    ok = tenant_id is not None and await ledger.void(session, invoice_id)
    if ok:
        await audit(session, actor, "invoice.void", str(tenant_id), {"invoice_id": invoice_id})
    return bool(ok)
