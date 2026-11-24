"""Platform Model Profile API (FR-26…29, SEC-11, SEC-17; M3-10, M3-11). Platform Admins only.

Credentials are write-only (`api_key` in, `has_credentials` out). A `tenant_id` makes the profile
that Tenant's Custom LLM; it answers only while the Tenant has the `custom_llm` Entitlement.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncEngine

from codura_docs.api.deps import Principal, require_platform
from codura_docs.api.errors import ApiError
from codura_docs.db.session import global_session
from codura_docs.platform import models

router = APIRouter(prefix="/api/platform/model-profiles", tags=["platform-models"])
PLATFORM = Depends(require_platform)


def _engine(request: Request) -> AsyncEngine:
    return request.app.state.platform_engine  # type: ignore[no-any-return]


def _err(exc: models.ModelError) -> ApiError:
    return ApiError(exc.status, exc.code, exc.message)


class ProfileIn(BaseModel):
    id: str = Field(min_length=2, max_length=48)
    tenant_id: str | None = Field(default=None, max_length=64)
    provider: Literal["ollama", "openai_compat", "anthropic"]
    model: str = Field(min_length=1, max_length=200)
    base_url: str | None = Field(default=None, max_length=500)
    params: dict[str, Any] = Field(default_factory=dict)
    license: Literal["apache-2.0", "mit", "commercial-api"]
    price_in_per_mtok_micros: int = Field(default=0, ge=0)
    price_out_per_mtok_micros: int = Field(default=0, ge=0)
    api_key: str | None = Field(default=None, max_length=500, repr=False)
    status: Literal["active", "disabled"] = "active"


class ProfilePatch(BaseModel):
    provider: Literal["ollama", "openai_compat", "anthropic"] | None = None
    model: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = Field(default=None, max_length=500)
    params: dict[str, Any] | None = None
    license: Literal["apache-2.0", "mit", "commercial-api"] | None = None
    price_in_per_mtok_micros: int | None = Field(default=None, ge=0)
    price_out_per_mtok_micros: int | None = Field(default=None, ge=0)
    api_key: str | None = Field(default=None, max_length=500, repr=False)
    clear_credentials: bool = False
    status: Literal["active", "disabled"] | None = None


@router.get("")
async def list_profiles(request: Request, _: Principal = PLATFORM) -> dict[str, Any]:
    async with global_session(_engine(request)) as s:
        return {"items": await models.list_profiles(s)}


@router.post("", status_code=201)
async def create_profile(
    body: ProfileIn, request: Request, p: Principal = PLATFORM
) -> dict[str, Any]:
    assert p.user_id
    try:
        async with global_session(_engine(request)) as s:
            pid = await models.create(s, request.app.state.keyring, p.user_id, body.model_dump())
    except models.ModelError as exc:
        raise _err(exc) from exc
    return {"id": pid}


@router.patch("/{profile_id}")
async def patch_profile(
    profile_id: str, body: ProfilePatch, request: Request, p: Principal = PLATFORM
) -> dict[str, Any]:
    assert p.user_id
    try:
        async with global_session(_engine(request)) as s:
            ok = await models.update(
                s,
                request.app.state.keyring,
                p.user_id,
                profile_id,
                body.model_dump(exclude_none=True),
            )
    except models.ModelError as exc:
        raise _err(exc) from exc
    if not ok:
        raise ApiError(404, "not_found", "Model Profile not found")
    return {"id": profile_id}


@router.post("/{profile_id}/default")
async def make_default(
    profile_id: str, request: Request, p: Principal = PLATFORM
) -> dict[str, str]:
    assert p.user_id
    try:
        async with global_session(_engine(request)) as s:
            await models.set_default(s, p.user_id, profile_id)
    except models.ModelError as exc:
        raise _err(exc) from exc
    return {"default_model_profile": profile_id}


@router.post("/{profile_id}/test")
async def run_profile_test(
    profile_id: str, request: Request, _: Principal = PLATFORM
) -> dict[str, Any]:
    try:
        async with global_session(_engine(request)) as s:
            return await models.test(
                s, request.app.state.keyring, request.app.state.registry, profile_id
            )
    except models.ModelError as exc:
        raise _err(exc) from exc
