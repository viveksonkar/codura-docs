"""Tenant settings, members, usage and Channel/Secret keys (FR-31, FR-37/50/51, FR-34)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from codura_docs import metering
from codura_docs.api.deps import TenantAccess, require_tenant
from codura_docs.api.errors import ApiError
from codura_docs.db.session import tenant_session
from codura_docs.domain.language import ocr_languages_valid
from codura_docs.ports.language import SUPPORTED_LANGUAGES
from codura_docs.services import channel_keys, members

router = APIRouter(prefix="/api/admin", tags=["tenant"])
READ = Depends(require_tenant("user"))
ADMIN = Depends(require_tenant("admin"))
ADMIN_ACTIVE = Depends(require_tenant("admin", active=True))


def _ocr_languages(request: Request) -> frozenset[str]:
    try:
        return request.app.state.registry.ocr().available_languages  # type: ignore[no-any-return]
    except Exception:
        return frozenset()


class TenantPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    ocr_languages: str | None = Field(default=None, max_length=100)
    default_language: str | None = None


@router.get("/tenant")
async def get_tenant(request: Request, a: TenantAccess = READ) -> dict[str, Any]:
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        row = (
            (
                await s.execute(
                    text(
                        "SELECT id, slug, name, status, plan_code, ocr_languages, default_language, "
                        "created_at FROM tenants WHERE id = :t"
                    ),
                    {"t": a.tenant_id},
                )
            )
            .mappings()
            .one()
        )
    return dict(row) | {"role": a.role}


@router.patch("/tenant")
async def patch_tenant(
    body: TenantPatch, request: Request, a: TenantAccess = ADMIN
) -> dict[str, Any]:
    if body.ocr_languages is not None and not ocr_languages_valid(
        body.ocr_languages, _ocr_languages(request)
    ):
        raise ApiError(422, "validation_error", "Unknown or uninstalled OCR language")
    if body.default_language is not None and body.default_language not in SUPPORTED_LANGUAGES:
        raise ApiError(422, "validation_error", "Unsupported default language")
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        await s.execute(
            text(
                "UPDATE tenants SET name = COALESCE(:n, name), ocr_languages = COALESCE(:o, ocr_languages), "
                "default_language = COALESCE(:d, default_language), updated_at = now() WHERE id = :t"
            ),
            {"n": body.name, "o": body.ocr_languages, "d": body.default_language, "t": a.tenant_id},
        )
    return await get_tenant(request, a)


@router.get("/ocr/languages")
async def ocr_languages(request: Request, _: TenantAccess = READ) -> dict[str, Any]:
    return {
        "installed": sorted(_ocr_languages(request)),
        "supported": {code: {"name": n, "ocr": t} for code, (n, t) in SUPPORTED_LANGUAGES.items()},
    }


@router.get("/usage")
async def usage(request: Request, a: TenantAccess = READ) -> dict[str, Any]:
    """Quantities only: never COGS or model names (ADR-0019)."""
    settings = request.app.state.settings
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        meters = await metering.usage_summary(s, settings, a.tenant_id)
    return {"plan": "trial", "period_start": str(metering.period_start()), "meters": meters}


# --- Members -------------------------------------------------------------------------------------


class InviteIn(BaseModel):
    email: str = Field(max_length=254)
    role: Literal["admin", "user"] = "user"


class RoleIn(BaseModel):
    role: Literal["admin", "user"]


@router.get("/members")
async def list_members(request: Request, a: TenantAccess = READ) -> dict[str, Any]:
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        return {"items": await members.list_members(s)}


@router.post("/invites", status_code=201)
async def invite(
    body: InviteIn, request: Request, a: TenantAccess = ADMIN_ACTIVE
) -> dict[str, Any]:
    settings = request.app.state.settings
    try:
        async with tenant_session(request.app.state.engine, a.ctx) as s:
            org = (
                await s.execute(text("SELECT name FROM tenants WHERE id = :t"), {"t": a.tenant_id})
            ).scalar_one()
            invite_id = await members.invite(
                s,
                tenant_id=a.tenant_id,
                tenant_name=org,
                base_url=settings.public_base_url,
                email=body.email.strip().lower(),
                role=body.role,
                invited_by=a.principal.user_id,
                seat_limit=settings.trial_seats,
            )
    except members.MemberError as exc:
        raise ApiError(409, exc.code, str(exc)) from exc
    return {"invite_id": invite_id}


@router.delete("/invites/{invite_id}", status_code=204)
async def revoke_invite(invite_id: str, request: Request, a: TenantAccess = ADMIN) -> None:
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        if not await members.revoke_invite(s, invite_id):
            raise ApiError(404, "not_found", "Invitation not found")


@router.patch("/members/{user_id}")
async def change_role(
    user_id: str, body: RoleIn, request: Request, a: TenantAccess = ADMIN
) -> dict[str, str]:
    try:
        async with tenant_session(request.app.state.engine, a.ctx) as s:
            if not await members.change_role(s, user_id, body.role):
                raise ApiError(404, "not_found", "Member not found")
    except members.MemberError as exc:
        raise ApiError(409, exc.code, str(exc)) from exc
    return {"user_id": user_id, "role": body.role}


@router.delete("/members/{user_id}", status_code=204)
async def remove_member(user_id: str, request: Request, a: TenantAccess = ADMIN) -> None:
    try:
        async with tenant_session(request.app.state.engine, a.ctx) as s:
            if not await members.remove(s, user_id):
                raise ApiError(404, "not_found", "Member not found")
    except members.MemberError as exc:
        raise ApiError(409, exc.code, str(exc)) from exc


# --- Keys (M2-00 completes management; available now for the Playground/public API) -----------------


class KeyIn(BaseModel):
    kind: Literal["channel", "secret"] = "channel"
    name: str = Field(default="", max_length=80)
    channel: Literal["hosted", "widget", "wordpress", "android", "api"] | None = "widget"
    assistant_id: str | None = None
    allowed_origins: list[str] = Field(default_factory=list, max_length=20)


@router.get("/channel-keys")
async def list_keys(request: Request, a: TenantAccess = ADMIN) -> dict[str, Any]:
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        return {"items": await channel_keys.list_keys(s)}


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
    except ValueError as exc:
        raise ApiError(422, "validation_error", str(exc)) from exc
    return {"id": key_id, "key": plaintext, "note": "Copy this key now: it is shown only once."}


@router.post("/channel-keys/{key_id}/revoke", status_code=204)
async def revoke_key(key_id: str, request: Request, a: TenantAccess = ADMIN) -> None:
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        if not await channel_keys.revoke_key(s, key_id):
            raise ApiError(404, "not_found", "Key not found")
