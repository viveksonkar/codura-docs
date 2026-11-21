"""Login/logout/me/switch-tenant (SEC-01) and public signup + email flows (ADR-0020, SEC-18)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from codura_docs.api.deps import SESSION_COOKIE, Principal, require_user
from codura_docs.api.errors import ApiError
from codura_docs.db.session import global_session
from codura_docs.services import auth as auth_svc
from codura_docs.services import members as members_svc
from codura_docs.services import signup as signup_svc

router = APIRouter(prefix="/api/admin/auth", tags=["auth"])
public = APIRouter(prefix="/api/auth", tags=["signup"])
UNIFORM = {
    "status": "ok",
    "message": "If the details are valid, check your email for the next step.",
}


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)


class SwitchIn(BaseModel):
    tenant_id: str


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _set_cookie(request: Request, response: Response, token: str) -> None:
    settings = request.app.state.settings
    secure = (
        settings.cookie_secure
        if settings.cookie_secure is not None
        else settings.public_base_url.startswith("https")
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )


async def _me(
    request: Request,
    user_id: str,
    session_tenant: str | None,
    csrf: str,
    platform: bool,
    email: str,
    name: str,
    verified: bool,
) -> dict[str, Any]:
    async with global_session(request.app.state.engine) as s:
        ms = await auth_svc.memberships(s, user_id)
    current = next((m for m in ms if m.tenant_id == session_tenant), None)
    return {
        "user": {
            "id": user_id,
            "email": email,
            "name": name,
            "is_platform_admin": platform,
            "email_verified": verified,
        },
        "csrf_token": csrf,
        "current_tenant": None
        if current is None
        else {
            "id": current.tenant_id,
            "name": current.tenant_name,
            "slug": current.tenant_slug,
            "role": current.role,
            "status": current.tenant_status,
        },
        "tenants": [
            {
                "id": m.tenant_id,
                "name": m.tenant_name,
                "slug": m.tenant_slug,
                "role": m.role,
                "status": m.tenant_status,
            }
            for m in ms
        ],
    }


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response) -> dict[str, Any]:
    email = body.email.strip().lower()
    throttle_key = f"{email}|{_client_ip(request)}"
    if not auth_svc.LOGIN_THROTTLE.hit(throttle_key):
        raise ApiError(
            429,
            "rate_limited",
            "Too many login attempts. Try again in 15 minutes.",
            headers={"Retry-After": "900"},
        )
    settings = request.app.state.settings
    async with global_session(request.app.state.engine) as s:
        user_id = await auth_svc.authenticate(s, email, body.password)
        if user_id is None:
            raise ApiError(401, "unauthenticated", "Wrong email or password")
        ms = await auth_svc.memberships(s, user_id)
        tenant_id = ms[0].tenant_id if ms else None
        token, csrf = await auth_svc.create_session(
            s, user_id, tenant_id, settings.session_ttl_hours
        )
    auth_svc.LOGIN_THROTTLE.reset(throttle_key)
    _set_cookie(request, response, token)
    async with global_session(request.app.state.engine) as s:
        info = await auth_svc.load_session(s, token)
    assert info is not None
    return await _me(
        request,
        info.user_id,
        info.current_tenant_id,
        csrf,
        info.is_platform_admin,
        info.email,
        info.name,
        info.email_verified,
    )


@router.post("/logout", status_code=204)
async def logout(
    request: Request, response: Response, _: Principal = Depends(require_user)
) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        async with global_session(request.app.state.engine) as s:
            await auth_svc.delete_session(s, token)
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me")
async def me(request: Request, p: Principal = Depends(require_user)) -> dict[str, Any]:
    if p.user_id is None:
        raise ApiError(403, "forbidden", "Secret Keys have no user profile")
    return await _me(
        request,
        p.user_id,
        p.current_tenant_id,
        p.csrf_token or "",
        p.is_platform_admin,
        p.email,
        p.name,
        p.email_verified,
    )


@router.post("/switch-tenant")
async def switch_tenant(
    body: SwitchIn, request: Request, p: Principal = Depends(require_user)
) -> dict[str, Any]:
    if p.user_id is None or p.session_id is None:
        raise ApiError(403, "forbidden", "Sessions only")
    async with global_session(request.app.state.engine) as s:
        if body.tenant_id not in {m.tenant_id for m in await auth_svc.memberships(s, p.user_id)}:
            raise ApiError(404, "not_found", "Organisation not found")
        await auth_svc.set_current_tenant(s, p.session_id, body.tenant_id)
    return await _me(
        request,
        p.user_id,
        body.tenant_id,
        p.csrf_token or "",
        p.is_platform_admin,
        p.email,
        p.name,
        p.email_verified,
    )


# --- Public: signup and email flows --------------------------------------------------------------


class SignupIn(BaseModel):
    org_name: str = Field(min_length=2, max_length=120)
    name: str = Field(default="", max_length=120)
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=200)


class TokenIn(BaseModel):
    token: str = Field(max_length=200)


class EmailIn(BaseModel):
    email: str = Field(max_length=254)


class ResetIn(BaseModel):
    token: str = Field(max_length=200)
    password: str = Field(max_length=200)


class AcceptInviteIn(BaseModel):
    token: str = Field(max_length=200)
    name: str = Field(default="", max_length=120)
    password: str = Field(max_length=200)


@public.post("/signup", status_code=202)
async def signup(body: SignupIn, request: Request) -> dict[str, str]:
    settings = request.app.state.settings
    if not settings.signup_enabled:
        raise ApiError(403, "signup_disabled", "Self-service signup is disabled")
    if not signup_svc.SIGNUP_THROTTLE.hit(_client_ip(request)):
        raise ApiError(
            429,
            "rate_limited",
            "Too many signups from this address",
            headers={"Retry-After": "3600"},
        )
    try:
        async with global_session(request.app.state.engine) as s:
            await signup_svc.signup(
                s,
                base_url=settings.public_base_url,
                org_name=body.org_name,
                name=body.name,
                email=body.email,
                password=body.password,
            )
    except signup_svc.SignupError as exc:
        raise ApiError(422, exc.code, str(exc)) from exc
    return UNIFORM  # identical whether or not the email already existed (no enumeration)


@public.post("/verify-email")
async def verify_email(body: TokenIn, request: Request) -> dict[str, str]:
    async with global_session(request.app.state.engine) as s:
        ok = await signup_svc.verify_email(s, body.token)
    if not ok:
        raise ApiError(400, "invalid_token", "This link is invalid or has expired")
    return {"status": "verified"}


@public.post("/resend-verification", status_code=202)
async def resend(body: EmailIn, request: Request) -> dict[str, str]:
    if signup_svc.RESEND_THROTTLE.hit(f"{body.email.lower()}|{_client_ip(request)}"):
        async with global_session(request.app.state.engine) as s:
            await signup_svc.resend_verification(
                s, request.app.state.settings.public_base_url, body.email
            )
    return UNIFORM


@public.post("/forgot-password", status_code=202)
async def forgot(body: EmailIn, request: Request) -> dict[str, str]:
    if signup_svc.RESEND_THROTTLE.hit(f"reset|{body.email.lower()}|{_client_ip(request)}"):
        async with global_session(request.app.state.engine) as s:
            await signup_svc.forgot_password(
                s, request.app.state.settings.public_base_url, body.email
            )
    return UNIFORM


@public.post("/reset-password")
async def reset(body: ResetIn, request: Request) -> dict[str, str]:
    try:
        async with global_session(request.app.state.engine) as s:
            ok = await signup_svc.reset_password(s, body.token, body.password)
    except signup_svc.SignupError as exc:
        raise ApiError(422, exc.code, str(exc)) from exc
    if not ok:
        raise ApiError(400, "invalid_token", "This link is invalid or has expired")
    return {"status": "password_changed"}


@public.post("/accept-invite")
async def accept_invite(
    body: AcceptInviteIn, request: Request, response: Response
) -> dict[str, Any]:
    settings = request.app.state.settings
    try:
        if len(body.password) < 10:
            raise members_svc.MemberError(
                "weak_password", "Password must be at least 10 characters"
            )
        async with global_session(request.app.state.engine) as s:
            result = await members_svc.accept_invite(
                s, body.token, name=body.name, password=body.password
            )
            if result is None:
                raise ApiError(400, "invalid_token", "This invitation is invalid or has expired")
            user_id, tenant_id = result
            token, csrf = await auth_svc.create_session(
                s, user_id, tenant_id, settings.session_ttl_hours
            )
    except members_svc.MemberError as exc:
        raise ApiError(422, exc.code, str(exc)) from exc
    _set_cookie(request, response, token)
    async with global_session(request.app.state.engine) as s:
        info = await auth_svc.load_session(s, token)
    assert info is not None
    return await _me(
        request, user_id, tenant_id, csrf, info.is_platform_admin, info.email, info.name, True
    )
