"""Platform Console API (/api/platform): Platform Admins only (ADR-0014, ADR-0019, FR-48).

Uses ``app.state.platform_engine`` (role ``codura_platform``): the only engine whose RLS policies
allow cross-Tenant reads. Every write is audited by the platform service.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncEngine

from codura_docs.api.deps import Principal, require_platform
from codura_docs.api.errors import ApiError
from codura_docs.db.session import global_session
from codura_docs.platform import service as platform

router = APIRouter(prefix="/api/platform", tags=["platform"])
PLATFORM = Depends(require_platform)


def _engine(request: Request) -> AsyncEngine:
    return request.app.state.platform_engine  # type: ignore[no-any-return]


class TenantPatch(BaseModel):
    status: Literal["active", "suspended"] | None = None
    model_profile_override: str | None = None
    clear_model_override: bool = False


class AdminInvite(BaseModel):
    email: str = Field(max_length=254)


@router.get("/tenants")
async def tenants(
    request: Request, q: str | None = None, _: Principal = PLATFORM
) -> dict[str, Any]:
    async with global_session(_engine(request)) as s:
        return {"items": await platform.list_tenants(s, q)}


@router.get("/tenants/{tenant_id}")
async def tenant(tenant_id: str, request: Request, _: Principal = PLATFORM) -> dict[str, Any]:
    async with global_session(_engine(request)) as s:
        item = await platform.get_tenant(s, tenant_id)
    if item is None:
        raise ApiError(404, "not_found", "Tenant not found")
    return item


@router.patch("/tenants/{tenant_id}")
async def patch_tenant(
    tenant_id: str, body: TenantPatch, request: Request, p: Principal = PLATFORM
) -> dict[str, Any]:
    assert p.user_id
    try:
        async with global_session(_engine(request)) as s:
            ok = await platform.update_tenant(
                s,
                p.user_id,
                tenant_id,
                status=body.status,
                model_profile_override=body.model_profile_override,
                clear_override=body.clear_model_override,
            )
    except ValueError as exc:
        raise ApiError(422, "validation_error", str(exc)) from exc
    if not ok:
        raise ApiError(404, "not_found", "Tenant not found")
    return await tenant(tenant_id, request, p)


@router.delete("/tenants/{tenant_id}", status_code=204)
async def delete_tenant(tenant_id: str, request: Request, p: Principal = PLATFORM) -> None:
    assert p.user_id
    async with global_session(_engine(request)) as s:
        if not await platform.delete_tenant(s, p.user_id, tenant_id):
            raise ApiError(404, "not_found", "Tenant not found")


@router.get("/audit")
async def audit(request: Request, _: Principal = PLATFORM) -> dict[str, Any]:
    async with global_session(_engine(request)) as s:
        return {"items": await platform.list_audit(s)}


@router.post("/admins/invites", status_code=201)
async def invite_admin(
    body: AdminInvite, request: Request, p: Principal = PLATFORM
) -> dict[str, str]:
    assert p.user_id
    async with global_session(_engine(request)) as s:
        user_id = await platform.invite_platform_admin(
            s, p.user_id, body.email.strip().lower(), request.app.state.settings.public_base_url
        )
    return {"user_id": user_id}
