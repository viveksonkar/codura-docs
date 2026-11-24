"""Subscription lifecycle (FR-63; billing.md §7; specs/billing.md §6). Every transition is audited.

All writes take a session opened on the billing engine (``codura_worker``) inside a
``tenant_session``, or the platform engine for Platform Admin actions (ADR-0026 §4).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from codura_docs.billing import ledger, rollups
from codura_docs.billing.entitlements import (
    INCLUDED,
    SHARED_ALLOWANCE,
    Terms,
    billing_settings,
    for_tenant,
    prices_for,
)
from codura_docs.billing.money import (
    Currency,
    currency_for_country,
    month_start,
    next_month,
    proration_minor,
)
from codura_docs.ids import new_id
from codura_docs.ports.payments import (
    BillingAccount,
    CheckoutRequest,
    PaymentProvider,
)


class BillingError(Exception):
    def __init__(
        self, status: int, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details or {}


@dataclass(frozen=True)
class ChangeResult:
    applied: bool
    kind: str  # upgrade | downgrade | none
    redirect_url: str | None = None
    invoice_id: str | None = None


async def _event(
    session: AsyncSession,
    tenant_id: str,
    action: str,
    actor: str,
    *,
    from_version: str | None = None,
    to_version: str | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    await session.execute(
        text(
            "INSERT INTO subscription_events (id, tenant_id, subscription_id, action, from_plan_version_id, "
            "to_plan_version_id, from_status, to_status, actor, details) VALUES (:id, :t, :s, :a, :fv, :tv, "
            ":fs, :ts, :actor, CAST(:d AS jsonb))"
        ),
        {
            "id": new_id("sev"),
            "t": tenant_id,
            "s": f"sub_{tenant_id}",
            "a": action,
            "fv": from_version,
            "tv": to_version,
            "fs": from_status,
            "ts": to_status,
            "actor": actor,
            "d": json.dumps(details or {}, default=str),
        },
    )


_SUB = "SELECT * FROM subscriptions WHERE tenant_id = :t"
_SUB_LOCK = _SUB + " FOR UPDATE"


async def subscription(session: AsyncSession, tenant_id: str, *, lock: bool = False) -> Any:
    return (
        await session.execute(
            text(_SUB_LOCK if lock else _SUB),
            {"t": tenant_id},
        )
    ).first()


async def ensure_subscription(session: AsyncSession, tenant_id: str, actor: str = "system") -> Any:
    """Tenants created before M3 or by tools get Free (no Trial) on first billing access."""
    sub = await subscription(session, tenant_id, lock=True)
    if sub is not None:
        return sub
    cfg = await billing_settings(session)
    version = (
        await session.execute(
            text("SELECT current_version_id FROM plans WHERE code = :c"),
            {"c": cfg["free_plan_code"]},
        )
    ).scalar()
    await session.execute(
        text(
            "INSERT INTO subscriptions (id, tenant_id, plan_version_id, status, period_start) "
            "VALUES (:id, :t, :v, 'active', :p) ON CONFLICT (tenant_id) DO NOTHING"
        ),
        {
            "id": f"sub_{tenant_id}",
            "t": tenant_id,
            "v": version,
            "p": month_start(datetime.now(UTC).date()),
        },
    )
    await _event(session, tenant_id, "created", actor, to_version=version, to_status="active")
    return await subscription(session, tenant_id, lock=True)


# --- Billing account (country → currency) -----------------------------------------------------------


async def account(session: AsyncSession, tenant_id: str) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text("SELECT * FROM billing_accounts WHERE tenant_id = :t"), {"t": tenant_id}
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def upsert_account(
    session: AsyncSession,
    tenant_id: str,
    *,
    country: str,
    billing_email: str | None,
    company_name: str | None,
    tax_id: str | None,
    address: dict[str, str],
    provider: str,
) -> dict[str, Any]:
    country = country.upper()
    currency = currency_for_country(country)
    current = await account(session, tenant_id)
    if current and current["currency_locked_at"] and current["currency"] != currency:
        raise BillingError(
            409,
            "currency_locked",
            f"Billing currency is {current['currency']} since your first invoice; contact support to change it",
        )
    await session.execute(
        text(
            "INSERT INTO billing_accounts (tenant_id, country, currency, billing_email, company_name, tax_id, "
            "address, provider) VALUES (:t, :c, :cur, :e, :n, :tax, CAST(:a AS jsonb), :p) "
            "ON CONFLICT (tenant_id) DO UPDATE SET country = EXCLUDED.country, currency = EXCLUDED.currency, "
            "billing_email = EXCLUDED.billing_email, company_name = EXCLUDED.company_name, "
            "tax_id = EXCLUDED.tax_id, address = EXCLUDED.address, updated_at = now()"
        ),
        {
            "t": tenant_id,
            "c": country,
            "cur": currency,
            "e": billing_email,
            "n": company_name,
            "tax": tax_id,
            "a": json.dumps(address),
            "p": provider,
        },
    )
    result = await account(session, tenant_id)
    assert result is not None
    return result


async def _require_account(session: AsyncSession, tenant_id: str) -> dict[str, Any]:
    acct = await account(session, tenant_id)
    if not acct or not acct["currency"]:
        raise BillingError(
            409,
            "billing_account_required",
            "Add your billing country and email before choosing a paid plan",
        )
    return acct


async def _customer(
    session: AsyncSession, provider: PaymentProvider, tenant_id: str, acct: dict[str, Any]
) -> str:
    name = (
        await session.execute(text("SELECT name FROM tenants WHERE id = :t"), {"t": tenant_id})
    ).scalar()
    same_provider = acct["provider"] == provider.name
    customer_id = await provider.ensure_customer(
        BillingAccount(
            tenant_id=tenant_id,
            name=str(acct["company_name"] or name or tenant_id),
            email=acct["billing_email"],
            country=str(acct["country"]),
            currency=acct["currency"],
            customer_id=acct["provider_customer_id"] if same_provider else None,
        )
    )
    if customer_id != acct["provider_customer_id"] or not same_provider:
        await session.execute(
            text(
                "UPDATE billing_accounts SET provider = :p, provider_customer_id = :c, updated_at = now() "
                "WHERE tenant_id = :t"
            ),
            {"p": provider.name, "c": customer_id, "t": tenant_id},
        )
    return customer_id


async def _lock_currency(session: AsyncSession, tenant_id: str) -> None:
    await session.execute(
        text(
            "UPDATE billing_accounts SET currency_locked_at = COALESCE(currency_locked_at, now()) WHERE tenant_id = :t"
        ),
        {"t": tenant_id},
    )


# --- Plan catalogue lookups -----------------------------------------------------------------------------


async def plan_version_for(session: AsyncSession, plan_code: str, *, public_only: bool) -> Any:
    row = (
        await session.execute(
            text(
                "SELECT p.id AS plan_id, p.code, p.name, p.is_free, p.is_public, p.status, v.id AS version_id, "
                "v.included FROM plans p JOIN plan_versions v ON v.id = p.current_version_id WHERE p.code = :c"
            ),
            {"c": plan_code},
        )
    ).first()
    if row is None or row.status != "active" or (public_only and not row.is_public):
        raise BillingError(404, "not_found", "Plan not found")
    return row


async def base_micros(session: AsyncSession, version_id: str, currency: Currency) -> int:
    prices = await prices_for(session, version_id)
    if currency not in prices:
        raise BillingError(409, "price_missing", f"This plan has no {currency} price yet")
    return prices[currency][0]


def _included(row: Any) -> dict[str, int]:
    value = row.included if isinstance(row.included, dict) else json.loads(row.included or "{}")
    return {k: int(v) for k, v in value.items()}


async def _over_new_limits(
    session: AsyncSession, tenant_id: str, included: dict[str, int]
) -> dict[str, Any]:
    from codura_docs.metering.quotas import stock_usage

    stock = await stock_usage(session, tenant_id)
    return {
        key: {"used": stock[key], "limit": included.get(key, default)}
        for key, (kind, default) in INCLUDED.items()
        if kind == "stock" and stock[key] > included.get(key, default)
    }


# --- Tenant actions ------------------------------------------------------------------------------------


async def change_plan(
    session: AsyncSession,
    provider: PaymentProvider,
    tenant_id: str,
    plan_code: str,
    *,
    actor: str,
    return_url: str,
    today: date | None = None,
) -> ChangeResult:
    today = today or datetime.now(UTC).date()
    sub = await ensure_subscription(session, tenant_id, actor)
    terms = await for_tenant(session, tenant_id)
    target = await plan_version_for(session, plan_code, public_only=True)
    current_code = terms.plan_code
    if target.code == current_code and not terms.trialing:
        if (
            sub.pending_plan_version_id or sub.cancel_at_period_end
        ):  # "switch back" undoes a pending change
            await session.execute(
                text(
                    "UPDATE subscriptions SET pending_plan_version_id = NULL, cancel_at_period_end = false, "
                    "updated_at = now() WHERE tenant_id = :t"
                ),
                {"t": tenant_id},
            )
            await _event(session, tenant_id, "pending_change_cleared", actor)
            return ChangeResult(True, "none")
        raise BillingError(409, "already_on_plan", "You are already on this plan")
    if target.is_free:
        return await _schedule(session, tenant_id, sub, target, actor)
    acct = await _require_account(session, tenant_id)
    currency: Currency = acct["currency"]
    new_base = await base_micros(session, target.version_id, currency)
    old_base = (
        0
        if terms.trialing or terms.is_free
        else await base_micros(session, sub.plan_version_id, currency)
    )
    if new_base < old_base:
        return await _schedule(session, tenant_id, sub, target, actor)
    over = await _over_new_limits(session, tenant_id, _included(target))
    if over:
        raise BillingError(
            409,
            "over_new_limits",
            "Reduce your usage before switching to this plan",
            {"over": over},
        )
    amount = proration_minor(old_base, new_base, today)
    lines = [
        ledger.Line(
            "proration_charge",
            f"{target.name} plan, {today:%B %Y} (prorated from {today:%d %b})",
            Decimal(1),
            0,
            amount_minor=amount,
        )
    ]
    customer_id = await _customer(session, provider, tenant_id, acct)
    issued = await ledger.issue(
        session,
        tenant_id=tenant_id,
        kind="proration",
        period_start=month_start(today),
        currency=currency,
        lines=lines,
        idempotency_key=f"upgrade:{target.version_id}:{today.isoformat()}:{sub.plan_version_id}",
        provider=provider.name,
        subscription_id=sub.id,
        status="draft",
    )
    if provider.immediate_upgrades:
        await apply_upgrade(session, tenant_id, issued.id, target.version_id, actor=actor)
        await ledger.open_draft(session, issued.id)
        if issued.total_minor > 0:
            ref = await provider.report_invoice(
                await ledger.draft_for_provider(session, issued.id, customer_id)
            )
            await ledger.set_external(session, issued.id, ref)
        return ChangeResult(True, "upgrade", invoice_id=issued.id)
    checkout = await provider.create_checkout(
        CheckoutRequest(
            tenant_id=tenant_id,
            customer_id=customer_id,
            currency=currency,
            amount_minor=issued.total_minor,
            description=lines[0].description,
            plan_version_id=target.version_id,
            invoice_id=issued.id,
            success_url=f"{return_url}?checkout=success",
            cancel_url=f"{return_url}?checkout=cancelled",
        )
    )
    await _event(
        session,
        tenant_id,
        "checkout_started",
        actor,
        to_version=target.version_id,
        details={"invoice_id": issued.id, "checkout": checkout.id},
    )
    return ChangeResult(False, "upgrade", redirect_url=checkout.url, invoice_id=issued.id)


async def _schedule(
    session: AsyncSession, tenant_id: str, sub: Any, target: Any, actor: str
) -> ChangeResult:
    """Downgrades (and moving to Free) apply at the period roll, if the stock limits allow it."""
    over = await _over_new_limits(session, tenant_id, _included(target))
    if over:
        raise BillingError(
            409,
            "over_new_limits",
            "Reduce your usage before switching to this plan",
            {"over": over},
        )
    if target.is_free and sub.status == "trialing":
        raise BillingError(409, "already_on_plan", "Your trial ends on Free automatically")
    await session.execute(
        text(
            "UPDATE subscriptions SET pending_plan_version_id = :v, cancel_at_period_end = false, "
            "updated_at = now() WHERE tenant_id = :t"
        ),
        {"v": target.version_id, "t": tenant_id},
    )
    await _event(
        session,
        tenant_id,
        "downgrade_scheduled",
        actor,
        from_version=sub.plan_version_id,
        to_version=target.version_id,
    )
    return ChangeResult(True, "downgrade")


async def apply_upgrade(
    session: AsyncSession, tenant_id: str, invoice_id: str, plan_version_id: str, *, actor: str
) -> bool:
    """Switch the Subscription now (manual: on request; Stripe: on checkout.completed). Idempotent."""
    sub = await ensure_subscription(session, tenant_id, actor)
    if sub.plan_version_id == plan_version_id and sub.status == "active":
        return False
    await session.execute(
        text(
            "UPDATE subscriptions SET plan_version_id = :v, status = 'active', trial_ends_at = NULL, "
            "pending_plan_version_id = NULL, cancel_at_period_end = false, updated_at = now() "
            "WHERE tenant_id = :t"
        ),
        {"v": plan_version_id, "t": tenant_id},
    )
    await _lock_currency(session, tenant_id)
    await _event(
        session,
        tenant_id,
        "upgraded",
        actor,
        from_version=sub.plan_version_id,
        to_version=plan_version_id,
        from_status=sub.status,
        to_status="active",
        details={"invoice_id": invoice_id},
    )
    return True


async def cancel(
    session: AsyncSession, tenant_id: str, *, actor: str, resume: bool = False
) -> None:
    sub = await ensure_subscription(session, tenant_id, actor)
    terms = await for_tenant(session, tenant_id)
    if not resume and (terms.is_free or terms.trialing):
        raise BillingError(409, "nothing_to_cancel", "There is no paid plan to cancel")
    await session.execute(
        text(
            "UPDATE subscriptions SET cancel_at_period_end = :c, pending_plan_version_id = NULL, "
            "updated_at = now() WHERE tenant_id = :t"
        ),
        {"c": not resume, "t": tenant_id},
    )
    await _event(
        session,
        tenant_id,
        "resumed" if resume else "cancel_scheduled",
        actor,
        from_version=sub.plan_version_id,
    )


async def set_spend_cap(
    session: AsyncSession, tenant_id: str, cap_micros: int | None, *, actor: str
) -> None:
    await ensure_subscription(session, tenant_id, actor)
    await session.execute(
        text(
            "UPDATE subscriptions SET spend_cap_micros = :c, updated_at = now() WHERE tenant_id = :t"
        ),
        {"c": cap_micros, "t": tenant_id},
    )
    await _event(
        session, tenant_id, "spend_cap_set", actor, details={"spend_cap_micros": cap_micros}
    )


# --- Platform Admin -------------------------------------------------------------------------------------


async def assign_plan(session: AsyncSession, tenant_id: str, plan_code: str, *, actor: str) -> None:
    """Immediate, no proration (deals, Enterprise, support). Non-public Plans allowed."""
    sub = await ensure_subscription(session, tenant_id, actor)
    target = await plan_version_for(session, plan_code, public_only=False)
    await session.execute(
        text(
            "UPDATE subscriptions SET plan_version_id = :v, status = CASE WHEN status = 'past_due' THEN status "
            "ELSE 'active' END, trial_ends_at = NULL, pending_plan_version_id = NULL, cancel_at_period_end = false, "
            "updated_at = now() WHERE tenant_id = :t"
        ),
        {"v": target.version_id, "t": tenant_id},
    )
    await _event(
        session,
        tenant_id,
        "plan_assigned",
        actor,
        from_version=sub.plan_version_id,
        to_version=target.version_id,
    )


# --- Payments -----------------------------------------------------------------------------------------------


async def payment_failed(session: AsyncSession, tenant_id: str, *, actor: str) -> None:
    await session.execute(
        text(
            "UPDATE subscriptions SET payment_failed_at = COALESCE(payment_failed_at, now()), updated_at = now() "
            "WHERE tenant_id = :t"
        ),
        {"t": tenant_id},
    )
    await _event(session, tenant_id, "payment_failed", actor)


async def payment_succeeded(session: AsyncSession, tenant_id: str, *, actor: str) -> None:
    sub = await subscription(session, tenant_id, lock=True)
    if sub is None:
        return
    open_failed = (
        await session.execute(
            text(
                "SELECT count(*) FROM invoices WHERE tenant_id = :t AND status IN ('open','uncollectible') "
                "AND due_at < now()"
            ),
            {"t": tenant_id},
        )
    ).scalar()
    if int(open_failed or 0) > 0:
        return  # still owes an overdue invoice
    if sub.payment_failed_at is not None or sub.status == "past_due":
        await session.execute(
            text(
                "UPDATE subscriptions SET payment_failed_at = NULL, status = CASE WHEN status = 'past_due' "
                "THEN 'active' ELSE status END, updated_at = now() WHERE tenant_id = :t"
            ),
            {"t": tenant_id},
        )
        await session.execute(
            text(
                "UPDATE tenants SET status = 'active', updated_at = now() WHERE id = :t AND status = 'past_due'"
            ),
            {"t": tenant_id},
        )
        await _event(
            session,
            tenant_id,
            "payment_recovered",
            actor,
            from_status=sub.status,
            to_status="active",
        )


# --- Period roll (Job) -------------------------------------------------------------------------------------


async def _overage_lines(
    session: AsyncSession, terms: Terms, period: date, currency: Currency
) -> list[ledger.Line]:
    """Arrears: included allowance is consumed by platform answers first, then Custom LLM answers."""
    if not terms.overage_enabled:
        return []
    totals = await rollups.period_totals(session, terms.tenant_id, period)
    lines: list[ledger.Line] = []
    for allowance, meters in SHARED_ALLOWANCE.items():
        remaining = Decimal(terms.limit(allowance))
        for meter in meters:
            used = totals.get(meter, Decimal(0))
            covered = min(used, remaining)
            remaining -= covered
            over = used - covered
            price = terms.overage_price(meter)
            if over > 0 and price > 0:
                label = {
                    "answer": "answers",
                    "answer_custom_llm": "Custom LLM answers",
                    "page_ocr": "OCR pages",
                }[meter]
                lines.append(
                    ledger.Line(
                        "overage",
                        f"Overage: {over:f} {label}, {period:%B %Y}",
                        over,
                        price,
                        meter=meter,
                    )
                )
    return lines


async def roll_period(
    session: AsyncSession, provider: PaymentProvider, tenant_id: str, *, now: datetime | None = None
) -> list[str]:
    """Close every finished month: overage (arrears) + next base (advance) on one invoice. Idempotent."""
    now = now or datetime.now(UTC)
    this_month = month_start(now.date())
    sub = await subscription(session, tenant_id, lock=True)
    issued: list[str] = []
    if sub is None:
        return issued
    cfg = await billing_settings(session)
    # Trial ends → Free limits (billing.md §7).
    if sub.status == "trialing" and sub.trial_ends_at is not None and sub.trial_ends_at <= now:
        await session.execute(
            text(
                "UPDATE subscriptions SET status = 'active', trial_ends_at = NULL, updated_at = now() WHERE tenant_id = :t"
            ),
            {"t": tenant_id},
        )
        await _event(
            session, tenant_id, "trial_ended", "system", from_status="trialing", to_status="active"
        )
        sub = await subscription(session, tenant_id, lock=True)
    while sub.period_start < this_month:
        ended = sub.period_start
        new_period = next_month(ended)
        end_of_ended = datetime.combine(new_period, datetime.min.time(), tzinfo=UTC) - timedelta(
            seconds=1
        )
        await rollups.rollup_range(session, tenant_id, ended, new_period - timedelta(days=1))
        terms = await for_tenant(session, tenant_id, now=end_of_ended)
        next_version = sub.pending_plan_version_id or sub.plan_version_id
        if sub.cancel_at_period_end:
            next_version = (
                await session.execute(
                    text("SELECT current_version_id FROM plans WHERE code = :c"),
                    {"c": cfg["free_plan_code"]},
                )
            ).scalar()
        acct = await account(session, tenant_id)
        currency: Currency | None = acct["currency"] if acct else None
        lines: list[ledger.Line] = []
        if currency:
            lines += await _overage_lines(session, terms, ended, currency)
            nv = (
                await session.execute(
                    text(
                        "SELECT p.name, p.is_free FROM plan_versions v JOIN plans p ON p.id = v.plan_id WHERE v.id = :v"
                    ),
                    {"v": next_version},
                )
            ).one()
            if not nv.is_free:
                base = await base_micros(session, str(next_version), currency)
                lines.insert(
                    0, ledger.Line("base", f"{nv.name} plan, {new_period:%B %Y}", Decimal(1), base)
                )
        if currency and lines and sum(line.amount for line in lines) > 0:
            inv = await ledger.issue(
                session,
                tenant_id=tenant_id,
                kind="period",
                period_start=new_period,
                currency=currency,
                lines=lines,
                idempotency_key=f"period:{new_period:%Y-%m}",
                provider=provider.name,
                subscription_id=sub.id,
            )
            if inv.created:
                await _lock_currency(session, tenant_id)
                assert acct is not None
                customer_id = await _customer(session, provider, tenant_id, acct)
                ref = await provider.report_invoice(
                    await ledger.draft_for_provider(session, inv.id, customer_id)
                )
                await ledger.set_external(session, inv.id, ref)
            issued.append(inv.id)
        await session.execute(
            text(
                "UPDATE subscriptions SET period_start = :p, plan_version_id = :v, pending_plan_version_id = NULL, "
                "cancel_at_period_end = false, updated_at = now() WHERE tenant_id = :t"
            ),
            {"p": new_period, "v": next_version, "t": tenant_id},
        )
        await _event(
            session,
            tenant_id,
            "period_rolled",
            "system",
            from_version=sub.plan_version_id,
            to_version=str(next_version),
            details={"period": new_period.isoformat(), "invoices": issued},
        )
        sub = await subscription(session, tenant_id, lock=True)
    # Payment failed: grace, then past_due (console read-only, Channels unavailable).
    grace = timedelta(days=int(cfg["grace_days"]))
    if (
        sub.payment_failed_at is not None
        and sub.status != "past_due"
        and sub.payment_failed_at + grace <= now
    ):
        await session.execute(
            text(
                "UPDATE subscriptions SET status = 'past_due', updated_at = now() WHERE tenant_id = :t"
            ),
            {"t": tenant_id},
        )
        await session.execute(
            text(
                "UPDATE tenants SET status = 'past_due', updated_at = now() WHERE id = :t AND status = 'active'"
            ),
            {"t": tenant_id},
        )
        await _event(
            session,
            tenant_id,
            "past_due",
            "system",
            from_status=sub.status,
            to_status="past_due",
        )
    return issued
