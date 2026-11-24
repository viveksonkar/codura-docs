"""Authentication and authorisation dependencies (SEC-01, SEC-15, ADR-0014, ADR-0020).

The Tenant comes ONLY from credentials: the session's current Tenant (membership re-verified on
every request) or a Secret Key. Tenant ids in paths/bodies are never trusted.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from codura_docs.api.errors import ApiError
from codura_docs.config import Settings
from codura_docs.context import tenant_id_var
from codura_docs.crypto import Keyring
from codura_docs.db.session import global_session, tenant_session
from codura_docs.registry import Registry
from codura_docs.services import auth as auth_svc
from codura_docs.tenancy import TenantContext

SESSION_COOKIE = "codura_session"
CSRF_HEADER = "x-csrf-token"
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
Role = Literal["admin", "user"]


def _role(value: str) -> Role:
    return "admin" if value == "admin" else "user"


@dataclass(frozen=True)
class Principal:
    user_id: str | None
    email: str
    name: str
    is_platform_admin: bool
    via: Literal["session", "secret_key"]
    session_id: str | None
    csrf_token: str | None
    current_tenant_id: str | None
    email_verified: bool
    key_tenant_id: str | None = None


@dataclass(frozen=True)
class TenantAccess:
    principal: Principal
    ctx: TenantContext
    role: Role
    tenant_status: str

    @property
    def tenant_id(self) -> str:
        return self.ctx.tenant_id


def engine(request: Request) -> AsyncEngine:
    return request.app.state.engine  # type: ignore[no-any-return]


def settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def registry(request: Request) -> Registry:
    return request.app.state.registry  # type: ignore[no-any-return]


def keyring(request: Request) -> Keyring:
    return request.app.state.keyring  # type: ignore[no-any-return]


async def _principal(request: Request) -> Principal | None:
    eng: AsyncEngine = request.app.state.engine
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if not token.startswith("sk_"):
            raise ApiError(401, "unauthenticated", "Admin API requires a session or a Secret Key")
        from codura_docs.services.channel_keys import resolve_key

        async with global_session(eng) as s:
            key = await resolve_key(s, token)
        if key is None or key.kind != "secret" or key.revoked or key.tenant_status == "suspended":
            raise ApiError(401, "unauthenticated", "Invalid or revoked key")
        return Principal(
            None,
            "api-key",
            "Secret Key",
            False,
            "secret_key",
            None,
            None,
            key.tenant_id,
            True,
            key_tenant_id=key.tenant_id,
        )
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        return None
    async with global_session(eng) as s:
        info = await auth_svc.load_session(s, cookie)
    if info is None:
        return None
    if request.method in UNSAFE and request.headers.get(CSRF_HEADER) != info.csrf_token:
        raise ApiError(403, "csrf_failed", "Missing or invalid CSRF token")
    return Principal(
        info.user_id,
        info.email,
        info.name,
        info.is_platform_admin,
        "session",
        info.session_id,
        info.csrf_token,
        info.current_tenant_id,
        info.email_verified,
    )


async def require_user(request: Request) -> Principal:
    principal = await _principal(request)
    if principal is None:
        raise ApiError(401, "unauthenticated", "Login required")
    return principal


async def require_platform(principal: Principal = Depends(require_user)) -> Principal:
    if not principal.is_platform_admin or principal.via != "session":
        raise ApiError(403, "forbidden", "Platform Admin only")
    return principal


def require_tenant(
    role: Role = "user", *, active: bool = False
) -> Callable[..., Awaitable[TenantAccess]]:
    """Resolve the Tenant from credentials. ``role='admin'`` for every Tenant write (ADR-0020)."""

    async def dependency(
        request: Request, principal: Principal = Depends(require_user)
    ) -> TenantAccess:
        eng: AsyncEngine = request.app.state.engine
        if principal.via == "secret_key":
            assert principal.key_tenant_id
            access = TenantAccess(
                principal, TenantContext(principal.key_tenant_id), "admin", "active"
            )
        else:
            tenant_id = principal.current_tenant_id
            if not tenant_id or principal.user_id is None:
                raise ApiError(403, "no_tenant", "Select an organisation first")
            async with global_session(eng) as s:
                ms = {m.tenant_id: m for m in await auth_svc.memberships(s, principal.user_id)}
            member = ms.get(tenant_id)
            if member is None:  # membership revoked since login
                raise ApiError(403, "forbidden", "You are not a member of this organisation")
            access = TenantAccess(
                principal, TenantContext(tenant_id), _role(member.role), member.tenant_status
            )
        if access.tenant_status == "suspended":
            raise ApiError(403, "tenant_suspended", "This organisation is suspended")
        if role == "admin" and access.role != "admin":
            raise ApiError(403, "forbidden", "Tenant Admin role required")
        if active and access.tenant_status != "active":
            raise ApiError(403, "email_not_verified", "Verify your email to unlock this action")
        tenant_id_var.set(access.tenant_id)
        return access

    return dependency


async def tenant_db(request: Request, access: TenantAccess) -> AsyncIterator[AsyncSession]:
    async with tenant_session(request.app.state.engine, access.ctx) as s:
        yield s
