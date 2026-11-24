"""Platform billing API: Plans (versioned), cost rates, Tenant billing, invoices (M3-02/05/10/11).

Platform Admins only; ``codura_platform`` engine; every write audited.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncEngine

from codura_docs.api.deps import Principal, require_platform
from codura_docs.api.errors import ApiError
from codura_docs.db.session import global_session
from codura_docs.platform import plans, tenant_billing

router = APIRouter(prefix="/api/platform", tags=["platform-billing"])
PLATFORM = Depends(require_platform)


def _engine(request: Request) -> AsyncEngine:
    return request.app.state.platform_engine  # type: ignore[no-any-return]


def _err(exc: plans.PlanError) -> ApiError:
    return ApiError(exc.status, exc.code, exc.message, details=exc.details)


class PriceIn(BaseModel):
    base_micros: int = Field(ge=0)
    overage_micros: dict[str, int] = Field(default_factory=dict)


class TermsIn(BaseModel):
    included: dict[str, int] = Field(default_factory=dict)
    entitlements: dict[str, Any] = Field(default_factory=dict)
    overage_enabled: bool = False
    default_model_profile_id: str | None = None
    target_margin_pct: float = Field(default=70, ge=0, lt=100)
    loss_leader: bool = False
    prices: dict[str, PriceIn]


class PlanIn(TermsIn):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    sort_order: int = 0
    is_public: bool = True
    is_free: bool = False


class PlanPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    sort_order: int | None = None
    is_public: bool | None = None
    status: Literal["active", "archived"] | None = None


@router.get("/plans")
async def list_plans(request: Request, _: Principal = PLATFORM) -> dict[str, Any]:
    async with global_session(_engine(request)) as s:
        return {"items": await plans.list_plans(s), "registry": plans.registry_view()}


@router.post("/plans", status_code=201)
async def create_plan(body: PlanIn, request: Request, p: Principal = PLATFORM) -> dict[str, Any]:
    assert p.user_id
    try:
        async with global_session(_engine(request)) as s:
            plan_id = await plans.create_plan(s, p.user_id, body.model_dump())
    except plans.PlanError as exc:
        raise _err(exc) from exc
    return await get_plan(plan_id, request, p)


@router.get("/plans/{plan_id}")
async def get_plan(plan_id: str, request: Request, _: Principal = PLATFORM) -> dict[str, Any]:
    async with global_session(_engine(request)) as s:
        item = await plans.get_plan(s, plan_id)
    if item is None:
        raise ApiError(404, "not_found", "Plan not found")
    return item


@router.patch("/plans/{plan_id}")
async def patch_plan(
    plan_id: str, body: PlanPatch, request: Request, p: Principal = PLATFORM
) -> dict[str, Any]:
    assert p.user_id
    try:
        async with global_session(_engine(request)) as s:
            ok = await plans.update_plan(s, p.user_id, plan_id, body.model_dump(exclude_none=True))
    except plans.PlanError as exc:
        raise _err(exc) from exc
    if not ok:
        raise ApiError(404, "not_found", "Plan not found")
    return await get_plan(plan_id, request, p)


@router.post("/plans/{plan_id}/versions", status_code=201)
async def new_version(
    plan_id: str, body: TermsIn, request: Request, p: Principal = PLATFORM
) -> dict[str, Any]:
    assert p.user_id
    try:
        async with global_session(_engine(request)) as s:
            version_id = await plans.new_version(s, p.user_id, plan_id, body.model_dump())
    except plans.PlanError as exc:
        raise _err(exc) from exc
    return {"version_id": version_id} | await get_plan(plan_id, request, p)


@router.post("/plans/{plan_id}/migrate")
async def migrate(plan_id: str, request: Request, p: Principal = PLATFORM) -> dict[str, int]:
    assert p.user_id
    async with global_session(_engine(request)) as s:
        return {"moved": await plans.migrate(s, p.user_id, plan_id)}


@router.post("/plans/margin")
async def margin(body: TermsIn, request: Request, _: Principal = PLATFORM) -> dict[str, Any]:
    """Live margin calculator for the editor (nothing is saved)."""
    try:
        terms = plans.parse_terms(body.model_dump())
    except plans.PlanError as exc:
        raise _err(exc) from exc
    async with global_session(_engine(request)) as s:
        return await plans.margin_report(s, terms)


class RatesIn(BaseModel):
    rates: dict[str, int]


@router.get("/cost-rates")
async def cost_rates(request: Request, _: Principal = PLATFORM) -> dict[str, Any]:
    async with global_session(_engine(request)) as s:
        return {"items": await plans.list_rates(s)}


@router.put("/cost-rates")
async def put_cost_rates(
    body: RatesIn, request: Request, p: Principal = PLATFORM
) -> dict[str, Any]:
    assert p.user_id
    try:
        async with global_session(_engine(request)) as s:
            await plans.set_rates(s, p.user_id, body.rates)
    except plans.PlanError as exc:
        raise _err(exc) from exc
    return await cost_rates(request, p)


class TenantBillingIn(BaseModel):
    plan_code: str | None = Field(default=None, max_length=40)
    included: dict[str, int] | None = None
    entitlements: dict[str, Any] | None = None
    overage_enabled: bool | None = None
    clear_overage_override: bool = False
    note: str | None = Field(default=None, max_length=500)


@router.get("/tenants/{tenant_id}/billing")
async def tenant_billing_view(
    tenant_id: str, request: Request, _: Principal = PLATFORM
) -> dict[str, Any]:
    async with global_session(_engine(request)) as s:
        item = await tenant_billing.view(s, tenant_id)
    if item is None:
        raise ApiError(404, "not_found", "Tenant not found")
    return item


@router.put("/tenants/{tenant_id}/billing")
async def tenant_billing_update(
    tenant_id: str, body: TenantBillingIn, request: Request, p: Principal = PLATFORM
) -> dict[str, Any]:
    assert p.user_id
    async with global_session(_engine(request)) as s:
        if await tenant_billing.view(s, tenant_id) is None:
            raise ApiError(404, "not_found", "Tenant not found")
        await tenant_billing.update(
            s,
            p.user_id,
            tenant_id,
            plan_code=body.plan_code,
            included=body.included,
            entitlements=body.entitlements,
            overage_enabled=body.overage_enabled,
            clear_overage=body.clear_overage_override,
            note=body.note,
        )
    return await tenant_billing_view(tenant_id, request, p)


@router.get("/invoices")
async def invoices(
    request: Request, status: str | None = None, _: Principal = PLATFORM
) -> dict[str, Any]:
    async with global_session(_engine(request)) as s:
        return {"items": await tenant_billing.list_invoices(s, status)}


class MarkPaidIn(BaseModel):
    method: str = Field(default="bank_transfer", max_length=40)
    note: str = Field(default="", max_length=500)


@router.post("/invoices/{invoice_id}/mark-paid")
async def mark_paid(
    invoice_id: str, body: MarkPaidIn, request: Request, p: Principal = PLATFORM
) -> dict[str, bool]:
    assert p.user_id
    async with global_session(_engine(request)) as s:
        ok = await tenant_billing.mark_paid(
            s, p.user_id, invoice_id, method=body.method, note=body.note
        )
    if not ok:
        raise ApiError(404, "not_found", "Invoice not found")
    return {"paid": True}


@router.post("/invoices/{invoice_id}/payment-failed")
async def payment_failed(
    invoice_id: str, request: Request, p: Principal = PLATFORM
) -> dict[str, bool]:
    assert p.user_id
    async with global_session(_engine(request)) as s:
        if not await tenant_billing.mark_failed(s, p.user_id, invoice_id):
            raise ApiError(404, "not_found", "Invoice not found")
    return {"payment_failed": True}


@router.post("/invoices/{invoice_id}/void")
async def void(invoice_id: str, request: Request, p: Principal = PLATFORM) -> dict[str, bool]:
    assert p.user_id
    async with global_session(_engine(request)) as s:
        if not await tenant_billing.void(s, p.user_id, invoice_id):
            raise ApiError(409, "conflict", "Only open or draft invoices can be voided")
    return {"void": True}
