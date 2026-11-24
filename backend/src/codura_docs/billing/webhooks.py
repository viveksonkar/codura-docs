"""Payment webhooks: verified, deduplicated by event id, and limited to payment status (FR-67, SEC-20).

Flow: ``verify_webhook`` (bad signature → rejected) → claim ``payment_events`` row (a processed or
ignored duplicate is a no-op) → resolve the Tenant from the signed event (customer mapping first)
→ apply in that Tenant's RLS-scoped transaction → mark processed. A failure leaves the event
``failed`` so the provider's retry reprocesses it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from codura_docs.billing import ledger, subscriptions
from codura_docs.db.session import global_session, tenant_session
from codura_docs.ids import is_id
from codura_docs.ports.payments import PaymentProvider, ProviderEvent
from codura_docs.tenancy import TenantContext

Outcome = Literal["processed", "duplicate", "ignored"]


async def handle(
    billing_engine: AsyncEngine, provider: PaymentProvider, headers: Mapping[str, str], body: bytes
) -> Outcome:
    event = provider.verify_webhook(headers, body)  # raises WebhookVerificationError
    digest = hashlib.sha256(body).hexdigest()
    async with global_session(billing_engine) as s:
        claimed = (
            await s.execute(
                text(
                    "INSERT INTO payment_events (provider, event_id, event_type, payload_sha256) "
                    "VALUES (:p, :e, :t, :h) ON CONFLICT (provider, event_id) DO NOTHING RETURNING event_id"
                ),
                {"p": provider.name, "e": event.id, "t": event.raw_type, "h": digest},
            )
        ).first()
        if claimed is None:
            status = (
                await s.execute(
                    text(
                        "SELECT status FROM payment_events WHERE provider = :p AND event_id = :e FOR UPDATE"
                    ),
                    {"p": provider.name, "e": event.id},
                )
            ).scalar()
            if status in ("processed", "ignored"):
                return "duplicate"
        tenant_id = await _tenant_for(s, provider.name, event)
    if event.kind == "ignored" or tenant_id is None:
        await _finish(billing_engine, provider.name, event.id, "ignored", None)
        return "ignored"
    try:
        async with tenant_session(billing_engine, TenantContext(tenant_id)) as s:
            await _apply(s, provider, tenant_id, event)
    except Exception as exc:
        await _finish(billing_engine, provider.name, event.id, "failed", type(exc).__name__)
        raise
    await _finish(billing_engine, provider.name, event.id, "processed", None)
    return "processed"


async def _tenant_for(s: AsyncSession, provider: str, event: ProviderEvent) -> str | None:
    if event.customer_id:
        mapped = (
            await s.execute(
                text("SELECT billing_tenant_for_customer(:p, :c)"),
                {"p": provider, "c": event.customer_id},
            )
        ).scalar()
        if mapped:
            return str(mapped)
    # Signed metadata we set ourselves at checkout / invoice creation.
    return event.tenant_id if is_id("tnt", event.tenant_id) else None


async def _apply(
    s: AsyncSession, provider: PaymentProvider, tenant_id: str, event: ProviderEvent
) -> None:
    actor = f"webhook:{provider.name}"
    invoice_id = event.invoice_id
    if not invoice_id and event.provider_invoice_id:
        invoice_id = (
            await s.execute(
                text("SELECT id FROM invoices WHERE provider = :p AND provider_invoice_id = :i"),
                {"p": provider.name, "i": event.provider_invoice_id},
            )
        ).scalar()
    if not invoice_id:
        return
    inv = (
        await s.execute(
            text("SELECT id, kind, status FROM invoices WHERE id = :id"), {"id": invoice_id}
        )
    ).first()
    if inv is None:  # RLS: another Tenant's invoice id is invisible here
        return
    if event.kind == "checkout_completed" and inv.kind == "proration":
        version = (
            await s.execute(
                text(
                    "SELECT to_plan_version_id FROM subscription_events WHERE tenant_id = :t "
                    "AND action = 'checkout_started' AND details->>'invoice_id' = :i "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"t": tenant_id, "i": invoice_id},
            )
        ).scalar()
        if version:
            await subscriptions.apply_upgrade(s, tenant_id, invoice_id, str(version), actor=actor)
        await ledger.open_draft(s, invoice_id)
        await ledger.mark_paid(
            s,
            invoice_id,
            provider=provider.name,
            provider_payment_id=event.provider_payment_id,
            method="card",
            recorded_by=None,
            amount_minor=event.amount_minor,
        )
    elif event.kind == "invoice_paid":
        await ledger.mark_paid(
            s,
            invoice_id,
            provider=provider.name,
            provider_payment_id=event.provider_payment_id,
            method="card",
            recorded_by=None,
            amount_minor=event.amount_minor,
        )
        await subscriptions.payment_succeeded(s, tenant_id, actor=actor)
    elif event.kind == "invoice_payment_failed":
        await ledger.record_failed_payment(
            s, invoice_id, provider=provider.name, provider_payment_id=event.provider_payment_id
        )
        await subscriptions.payment_failed(s, tenant_id, actor=actor)


async def _finish(
    engine: AsyncEngine, provider: str, event_id: str, status: str, error: str | None
) -> None:
    async with global_session(engine) as s:
        await s.execute(
            text(
                "UPDATE payment_events SET status = :s, error = :e, processed_at = now() "
                "WHERE provider = :p AND event_id = :id"
            ),
            {"s": status, "e": error, "p": provider, "id": event_id},
        )
