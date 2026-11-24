"""Basic Tenant insights (FR-72 basic). Tenant Admins and Tenant Users; current Tenant only."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from codura_docs.api.deps import TenantAccess, require_tenant
from codura_docs.db.session import tenant_session
from codura_docs.services import insights

router = APIRouter(prefix="/api/admin/insights", tags=["insights"])
READ = Depends(require_tenant("user"))


@router.get("/summary")
async def summary(
    request: Request, a: TenantAccess = READ, days: int = Query(default=30, ge=1, le=365)
) -> dict[str, Any]:
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        return await insights.summary(s, request.app.state.settings, a.tenant_id, days=days)


@router.get("/unanswered")
async def unanswered(
    request: Request,
    a: TenantAccess = READ,
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        return {"items": await insights.unanswered(s, days=days, limit=limit)}
