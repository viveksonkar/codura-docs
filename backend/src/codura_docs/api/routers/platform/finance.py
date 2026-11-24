"""Platform finance & growth API (FR-69, FR-73; billing.md §9; M3-09). Platform Admins only."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncEngine

from codura_docs.api.deps import Principal, require_platform
from codura_docs.db.session import global_session
from codura_docs.platform import finance

router = APIRouter(prefix="/api/platform/finance", tags=["platform-finance"])
PLATFORM = Depends(require_platform)


def _engine(request: Request) -> AsyncEngine:
    return request.app.state.platform_engine  # type: ignore[no-any-return]


@router.get("/summary")
async def summary(
    request: Request, days: int = Query(default=30, ge=1, le=366), _: Principal = PLATFORM
) -> dict[str, Any]:
    async with global_session(_engine(request)) as s:
        return await finance.summary(s, days)


@router.get("/tenants")
async def tenants(
    request: Request, days: int = Query(default=30, ge=1, le=366), _: Principal = PLATFORM
) -> dict[str, Any]:
    async with global_session(_engine(request)) as s:
        items = await finance.tenants(s, days)
    return {"items": items, "unprofitable": [t for t in items if t["unprofitable"]]}


class SimulateIn(BaseModel):
    plan_code: str = Field(min_length=1, max_length=40)
    base_micros: dict[str, int] = Field(default_factory=dict)
    included_answers: int | None = Field(default=None, ge=0)


@router.post("/simulate")
async def simulate(body: SimulateIn, request: Request, _: Principal = PLATFORM) -> dict[str, Any]:
    async with global_session(_engine(request)) as s:
        return await finance.simulate(
            s, body.plan_code, base_micros=body.base_micros, included_answers=body.included_answers
        )


@router.get("/funnel")
async def funnel(
    request: Request, days: int = Query(default=90, ge=1, le=366), _: Principal = PLATFORM
) -> dict[str, Any]:
    async with global_session(_engine(request)) as s:
        return await finance.funnel(s, days)
