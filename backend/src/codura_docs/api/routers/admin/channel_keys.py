"""Channel Keys and Secret Keys management (FR-34, SEC-04, ADR-0012, ADR-0025). Tenant Admin only."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from codura_docs.api.deps import TenantAccess, require_tenant
from codura_docs.api.errors import ApiError
from codura_docs.db.session import tenant_session
from codura_docs.services import channel_keys

router = APIRouter(prefix="/api/admin", tags=["channel-keys"])
ADMIN = Depends(require_tenant("admin"))
ADMIN_ACTIVE = Depends(require_tenant("admin", active=True))


class KeyIn(BaseModel):
    kind: Literal["channel", "secret"] = "channel"
    name: str = Field(default="", max_length=80)
    # `hosted` keys are system-managed via the Assistant's hosted toggle (ADR-0025).
    channel: Literal["widget", "wordpress", "android", "api"] | None = "widget"
    assistant_id: str | None = Field(default=None, max_length=64)
    allowed_origins: list[str] = Field(default_factory=list, max_length=20)


class OriginsIn(BaseModel):
    allowed_origins: list[str] = Field(max_length=20)


def _error(exc: channel_keys.KeyServiceError) -> ApiError:
    return ApiError(exc.status, exc.code, str(exc))


@router.get("/channel-keys")
async def list_keys(
    request: Request, assistant_id: str | None = None, a: TenantAccess = ADMIN
) -> dict[str, Any]:
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        return {"items": await channel_keys.list_keys(s, assistant_id=assistant_id)}


@router.post("/channel-keys", status_code=201)
async def create_key(
    body: KeyIn, request: Request, a: TenantAccess = ADMIN_ACTIVE
) -> dict[str, Any]:
    try:
        async with tenant_session(request.app.state.engine, a.ctx) as s:
            key_id, plaintext = await channel_keys.create_key(
                s,
                a.tenant_id,
                kind=body.kind,
                name=body.name,
                channel=body.channel if body.kind == "channel" else None,
                assistant_id=body.assistant_id if body.kind == "channel" else None,
                allowed_origins=body.allowed_origins,
            )
            item = await channel_keys.get_key(s, key_id)
    except channel_keys.KeyServiceError as exc:
        raise _error(exc) from exc
    return (item or {}) | {
        "key": plaintext,
        "note": "Copy this key now: it is shown only once.",
    }


@router.patch("/channel-keys/{key_id}")
async def update_key(
    key_id: str, body: OriginsIn, request: Request, a: TenantAccess = ADMIN
) -> dict[str, Any]:
    try:
        async with tenant_session(request.app.state.engine, a.ctx) as s:
            item = await channel_keys.update_origins(s, key_id, body.allowed_origins)
    except channel_keys.KeyServiceError as exc:
        raise _error(exc) from exc
    if item is None:
        raise ApiError(404, "not_found", "Key not found")
    return item


@router.post("/channel-keys/{key_id}/revoke", status_code=204)
async def revoke_key(key_id: str, request: Request, a: TenantAccess = ADMIN) -> None:
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        if not await channel_keys.revoke_key(s, key_id):
            raise ApiError(404, "not_found", "Key not found (hosted keys follow the hosted toggle)")
