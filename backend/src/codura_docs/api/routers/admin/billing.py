"""Tenant billing (FR-68; specs/billing.md §9). Reads: Tenant Users. Changes: Tenant Admin only.

Reads run on the app engine (RLS, SELECT-only on billing tables); every billing write runs on
``app.state.billing_engine`` inside the caller's tenant_session (ADR-0026 §4). Responses carry
quantities and prices, never COGS or model names (ADR-0019).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from codura_docs import metering
from codura_docs.api.deps import TenantAccess, require_tenant
from codura_docs.api.errors import ApiError
from codura_docs.billing import ledger, subscriptions
from codura_docs.billing.entitlements import REGISTRY, for_tenant, prices_for
from codura_docs.billing.money import currency_for_country
from codura_docs.db.session import tenant_session
from codura_docs.metering import quotas
from codura_docs.ports.payments import PaymentProviderError

router = APIRouter(prefix="/api/admin/billing", tags=["billing"])
READ = Depends(require_tenant("user"))
ADMIN = Depends(require_tenant("admin"))


def _billing(request: Request) -> AsyncEngine:
    return request.app.state.billing_engine  # type: ignore[no-any-return]


def _return_url(request: Request) -> str:
    return f"{request.app.state.settings.public_base_url}/billing"


def _actor(a: TenantAccess) -> str:
    return a.principal.user_id or "secret_key"


@router.get("")
async def overview(request: Request, a: TenantAccess = READ) -> dict[str, Any]:
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        terms = await for_tenant(s, a.tenant_id)
        meters = await metering.usage_summary(s, request.app.state.settings, a.tenant_id)
        account = await subscriptions.account(s, a.tenant_id)
        invoices = await ledger.list_invoices(s, limit=12)
        notices = await quotas.notices(s, a.tenant_id)
        spend = await quotas.overage_spend_micros(s, terms, quotas.period_of(), _now())
    provider = request.app.state.registry.payments()
    return {
        **terms.public(),
        "period_start": str(terms.period_start),
        "meters": meters,
        "notices": notices,
        "overage_spend_micros": spend,
        "account": _account_out(account),
        "invoices": invoices,
        "provider": {"name": provider.name, "portal": provider.name != "manual"},
    }


def _now() -> Any:
    from datetime import UTC, datetime

    return datetime.now(UTC)


def _account_out(account: dict[str, Any] | None) -> dict[str, Any] | None:
    if not account:
        return None
    return {
        k: account.get(k)
        for k in ("country", "currency", "billing_email", "company_name", "tax_id", "address")
    } | {"currency_locked": account.get("currency_locked_at") is not None}


@router.get("/plans")
async def plans(request: Request, a: TenantAccess = READ) -> dict[str, Any]:
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        terms = await for_tenant(s, a.tenant_id)
        rows = (
            await s.execute(
                text(
                    "SELECT p.code, p.name, p.description, p.is_free, v.id AS version_id, v.included, "
                    "v.entitlements, v.overage_enabled FROM plans p JOIN plan_versions v ON v.id = p.current_version_id "
                    "WHERE p.status = 'active' AND p.is_public ORDER BY p.sort_order"
                )
            )
        ).all()
        items = []
        for r in rows:
            prices = await prices_for(s, r.version_id)
            shown = {terms.currency: prices[terms.currency]} if terms.currency in prices else prices
            items.append(
                {
                    "code": r.code,
                    "name": r.name,
                    "description": r.description,
                    "is_free": r.is_free,
                    "included": r.included,
                    "entitlements": {
                        k: (r.entitlements or {}).get(k, spec.default)
                        for k, spec in REGISTRY.items()
                    },
                    "overage_enabled": r.overage_enabled,
                    "prices": {
                        cur: {"base_micros": base, "overage_micros": over}
                        for cur, (base, over) in shown.items()
                    },
                    "current": r.code == terms.plan_code and not terms.trialing,
                }
            )
    return {"items": items, "currency": terms.currency}


class AccountIn(BaseModel):
    country: str = Field(pattern=r"^[A-Za-z]{2}$")
    billing_email: str | None = Field(
        default=None, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    )
    company_name: str | None = Field(default=None, max_length=200)
    tax_id: str | None = Field(default=None, max_length=40)
    address: dict[str, str] = Field(default_factory=dict, max_length=8)


@router.put("/account")
async def put_account(body: AccountIn, request: Request, a: TenantAccess = ADMIN) -> dict[str, Any]:
    if any(len(v) > 200 for v in body.address.values()):
        raise ApiError(422, "validation_error", "Address fields are limited to 200 characters")
    async with tenant_session(_billing(request), a.ctx) as s:
        account = await subscriptions.upsert_account(
            s,
            a.tenant_id,
            country=body.country,
            billing_email=body.billing_email,
            company_name=body.company_name,
            tax_id=body.tax_id,
            address=body.address,
            provider=request.app.state.registry.payments().name,
        )
    out = _account_out(account)
    assert out is not None
    return out | {"currency": currency_for_country(body.country)}


class ChangePlanIn(BaseModel):
    plan_code: str = Field(min_length=1, max_length=40)


@router.post("/change-plan")
async def change_plan(
    body: ChangePlanIn, request: Request, a: TenantAccess = ADMIN
) -> dict[str, Any]:
    provider = request.app.state.registry.payments()
    try:
        async with tenant_session(_billing(request), a.ctx) as s:
            result = await subscriptions.change_plan(
                s,
                provider,
                a.tenant_id,
                body.plan_code,
                actor=_actor(a),
                return_url=_return_url(request),
            )
    except PaymentProviderError as exc:
        raise ApiError(
            502, "payment_provider_error", "The payment provider is unavailable"
        ) from exc
    return {
        "applied": result.applied,
        "kind": result.kind,
        "redirect_url": result.redirect_url,
        "invoice_id": result.invoice_id,
    }


@router.post("/cancel")
async def cancel(request: Request, a: TenantAccess = ADMIN) -> dict[str, bool]:
    async with tenant_session(_billing(request), a.ctx) as s:
        await subscriptions.cancel(s, a.tenant_id, actor=_actor(a))
    return {"cancel_at_period_end": True}


@router.post("/resume")
async def resume(request: Request, a: TenantAccess = ADMIN) -> dict[str, bool]:
    async with tenant_session(_billing(request), a.ctx) as s:
        await subscriptions.cancel(s, a.tenant_id, actor=_actor(a), resume=True)
    return {"cancel_at_period_end": False}


class SpendCapIn(BaseModel):
    spend_cap_micros: int | None = Field(default=None, ge=0, le=10**15)


@router.put("/spend-cap")
async def spend_cap(body: SpendCapIn, request: Request, a: TenantAccess = ADMIN) -> dict[str, Any]:
    async with tenant_session(_billing(request), a.ctx) as s:
        await subscriptions.set_spend_cap(s, a.tenant_id, body.spend_cap_micros, actor=_actor(a))
    return {"spend_cap_micros": body.spend_cap_micros}


@router.post("/portal")
async def portal(request: Request, a: TenantAccess = ADMIN) -> dict[str, str]:
    provider = request.app.state.registry.payments()
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        account = await subscriptions.account(s, a.tenant_id)
    if (
        not account
        or not account.get("provider_customer_id")
        or account.get("provider") != provider.name
    ):
        raise ApiError(
            409, "not_available", "Payment methods are managed by our team for this account"
        )
    try:
        url = await provider.customer_portal_url(
            account["provider_customer_id"], _return_url(request)
        )
    except PaymentProviderError as exc:
        raise ApiError(
            502, "payment_provider_error", "The payment provider is unavailable"
        ) from exc
    if not url:
        raise ApiError(
            409, "not_available", "Payment methods are managed by our team for this account"
        )
    return {"url": url}


@router.get("/invoices")
async def invoices(request: Request, a: TenantAccess = READ) -> dict[str, Any]:
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        return {"items": await ledger.list_invoices(s, limit=100)}


@router.get("/invoices/{invoice_id}")
async def invoice(invoice_id: str, request: Request, a: TenantAccess = READ) -> dict[str, Any]:
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        item = await ledger.get_invoice(s, invoice_id)
    if item is None:
        raise ApiError(404, "not_found", "Invoice not found")
    return item


@router.get("/invoices/{invoice_id}/html", response_class=HTMLResponse)
async def invoice_html(invoice_id: str, request: Request, a: TenantAccess = READ) -> HTMLResponse:
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        item = await ledger.get_invoice(s, invoice_id)
        account = await subscriptions.account(s, a.tenant_id)
        name = (
            await s.execute(text("SELECT name FROM tenants WHERE id = :t"), {"t": a.tenant_id})
        ).scalar()
    if item is None:
        raise ApiError(404, "not_found", "Invoice not found")
    return HTMLResponse(
        ledger.render_html(item, tenant_name=str(name or ""), account=account),
        headers={
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
            "X-Content-Type-Options": "nosniff",
        },
    )
