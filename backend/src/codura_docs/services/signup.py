"""Self-service organisation signup and email flows (ADR-0020, FR-49, FR-52, SEC-18)."""

from __future__ import annotations

import re
import unicodedata
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from codura_docs.ids import new_id
from codura_docs.passwords import hash_password
from codura_docs.services import auth as auth_svc
from codura_docs.services import email_flows

RESERVED_SLUGS = frozenset(
    {
        "admin",
        "api",
        "app",
        "c",
        "demo",
        "platform",
        "signup",
        "login",
        "www",
        "widget",
        "static",
        "help",
        "support",
        "billing",
        "codura",
    }
)
# A small blocklist of throwaway domains; extend in production (SEC-18).
DISPOSABLE_DOMAINS = frozenset(
    {
        "mailinator.com",
        "10minutemail.com",
        "guerrillamail.com",
        "tempmail.com",
        "yopmail.com",
        "trashmail.com",
        "sharklasers.com",
    }
)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SIGNUP_THROTTLE = auth_svc.Throttle(limit=5, window_s=3600)
RESEND_THROTTLE = auth_svc.Throttle(limit=3, window_s=3600)


class SignupError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:40]
    return slug or "org"


def validate_password(password: str) -> None:
    if len(password) < 10:
        raise SignupError("weak_password", "Password must be at least 10 characters")


def validate_email(email: str) -> str:
    email = email.strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 254:
        raise SignupError("invalid_email", "Enter a valid email address")
    if email.rsplit("@", 1)[1] in DISPOSABLE_DOMAINS:
        raise SignupError("disposable_email", "Please use a work or personal email address")
    return email


async def _unique_slug(session: AsyncSession, base: str) -> str:
    slug = base if base not in RESERVED_SLUGS else f"{base}-org"
    for n in range(0, 50):
        candidate = slug if n == 0 else f"{slug}-{n + 1}"
        taken = (
            await session.execute(text("SELECT 1 FROM tenants WHERE slug = :s"), {"s": candidate})
        ).first()
        if not taken:
            return candidate
    return f"{slug}-{new_id('tnt')[-6:].lower()}"


async def signup(
    session: AsyncSession, *, base_url: str, org_name: str, name: str, email: str, password: str
) -> str | None:
    """Create Tenant + Tenant Admin. Returns the tenant id, or None if the email already exists
    (the caller responds identically either way: no account enumeration)."""
    org_name = org_name.strip()
    if not 2 <= len(org_name) <= 120:
        raise SignupError("invalid_org_name", "Organisation name must be 2–120 characters")
    email = validate_email(email)
    validate_password(password)
    exists = (
        await session.execute(text("SELECT 1 FROM users WHERE email = :e"), {"e": email})
    ).first()
    if exists:
        return None
    user_id, tenant_id = new_id("usr"), new_id("tnt")
    await session.execute(
        text("INSERT INTO users (id, email, name, password_hash) VALUES (:id, :e, :n, :h)"),
        {"id": user_id, "e": email, "n": name.strip()[:120], "h": hash_password(password)},
    )
    slug = await _unique_slug(session, slugify(org_name))
    await session.execute(
        text("SELECT signup_create_tenant(:t, :s, :n, :u)"),
        {"t": tenant_id, "s": slug, "n": org_name, "u": user_id},
    )
    token = await auth_svc.issue_auth_token(session, user_id, "verify_email", timedelta(hours=24))
    await email_flows.verification_email(session, base_url, email, token)
    return tenant_id


async def verify_email(session: AsyncSession, token: str) -> bool:
    user_id = await auth_svc.consume_auth_token(session, token, "verify_email")
    if user_id is None:
        return False
    await session.execute(
        text("UPDATE users SET email_verified_at = now() WHERE id = :u"), {"u": user_id}
    )
    await session.execute(text("SELECT activate_tenants_of_admin(:u)"), {"u": user_id})
    return True


async def resend_verification(session: AsyncSession, base_url: str, email: str) -> None:
    row = (
        await session.execute(
            text("SELECT id FROM users WHERE email = :e AND email_verified_at IS NULL"),
            {"e": email.strip().lower()},
        )
    ).first()
    if row is not None:
        token = await auth_svc.issue_auth_token(
            session, row.id, "verify_email", timedelta(hours=24)
        )
        await email_flows.verification_email(session, base_url, email.strip().lower(), token)


async def forgot_password(session: AsyncSession, base_url: str, email: str) -> None:
    row = (
        await session.execute(
            text("SELECT id FROM users WHERE email = :e"), {"e": email.strip().lower()}
        )
    ).first()
    if row is not None:
        token = await auth_svc.issue_auth_token(
            session, row.id, "reset_password", timedelta(hours=1)
        )
        await email_flows.reset_email(session, base_url, email.strip().lower(), token)


async def reset_password(session: AsyncSession, token: str, password: str) -> bool:
    validate_password(password)
    user_id = await auth_svc.consume_auth_token(session, token, "reset_password")
    if user_id is None:
        return False
    await session.execute(
        text(
            "UPDATE users SET password_hash = :h, email_verified_at = COALESCE(email_verified_at, now()) "
            "WHERE id = :u"
        ),
        {"h": hash_password(password), "u": user_id},
    )
    await session.execute(text("DELETE FROM sessions WHERE user_id = :u"), {"u": user_id})
    return True
