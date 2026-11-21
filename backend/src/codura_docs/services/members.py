"""User management inside one Tenant (FR-50, FR-51): invite, change role, remove."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from codura_docs.db.session import rowcount
from codura_docs.ids import new_id
from codura_docs.passwords import hash_password
from codura_docs.services import auth as auth_svc
from codura_docs.services import email_flows


class MemberError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


async def list_members(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                text(
                    "SELECT m.user_id, u.email, u.name, m.role, m.created_at, u.last_login_at "
                    "FROM tenant_members m JOIN users u ON u.id = m.user_id ORDER BY u.email"
                )
            )
        )
        .mappings()
        .all()
    )
    pending = (
        (
            await session.execute(
                text(
                    "SELECT id, email, role, expires_at FROM invites WHERE accepted_at IS NULL AND expires_at > now() "
                    "ORDER BY created_at DESC"
                )
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) | {"status": "active"} for r in rows] + [
        {
            "invite_id": p["id"],
            "email": p["email"],
            "role": p["role"],
            "status": "invited",
            "expires_at": p["expires_at"],
        }
        for p in pending
    ]


async def invite(
    session: AsyncSession,
    *,
    tenant_id: str,
    tenant_name: str,
    base_url: str,
    email: str,
    role: str,
    invited_by: str | None,
    seat_limit: int,
) -> str:
    seats = (await session.execute(text("SELECT count(*) FROM tenant_members"))).scalar_one()
    if seats >= seat_limit:
        raise MemberError("seat_limit", f"Your plan allows {seat_limit} users")
    already = (
        await session.execute(
            text(
                "SELECT 1 FROM tenant_members m JOIN users u ON u.id = m.user_id WHERE u.email = :e"
            ),
            {"e": email},
        )
    ).first()
    if already:
        raise MemberError("already_member", "This person is already a member")
    token = auth_svc.new_token()
    invite_id = new_id("inv")
    await session.execute(
        text(
            "INSERT INTO invites (id, tenant_id, email, role, token_hash, expires_at, created_by) "
            "VALUES (:id, :t, :e, :r, :h, :x, :b)"
        ),
        {
            "id": invite_id,
            "t": tenant_id,
            "e": email,
            "r": role,
            "h": auth_svc.sha256(token),
            "x": datetime.now(UTC) + timedelta(days=7),
            "b": invited_by,
        },
    )
    await email_flows.invite_email(session, base_url, email, tenant_name, role, token)
    return invite_id


async def accept_invite(
    session: AsyncSession, token: str, *, name: str, password: str
) -> tuple[str, str] | None:
    """Accept an invite as a new user, or as the existing user with that email (password checked)."""
    email = (
        await session.execute(text("SELECT invite_email(:h)"), {"h": auth_svc.sha256(token)})
    ).scalar()
    if not email:
        return None
    user = (
        await session.execute(text("SELECT id FROM users WHERE email = :e"), {"e": email})
    ).first()
    if user is None:
        user_id = new_id("usr")
        await session.execute(
            text(
                "INSERT INTO users (id, email, email_verified_at, name, password_hash) "
                "VALUES (:id, :e, now(), :n, :h)"
            ),
            {"id": user_id, "e": email, "n": name.strip()[:120], "h": hash_password(password)},
        )
    else:
        user_id = await auth_svc.authenticate(session, email, password) or ""
        if not user_id:
            raise MemberError(
                "wrong_password", "This email already has an account: enter its password"
            )
    row = (
        await session.execute(
            text("SELECT * FROM accept_invite(:h, :u)"), {"h": auth_svc.sha256(token), "u": user_id}
        )
    ).first()
    return (user_id, row.tenant_id) if row else None


async def change_role(session: AsyncSession, user_id: str, role: str) -> bool:
    try:
        result = await session.execute(
            text("UPDATE tenant_members SET role = :r WHERE user_id = :u"),
            {"r": role, "u": user_id},
        )
    except DBAPIError as exc:
        if "at least one admin" in str(exc):
            raise MemberError(
                "last_admin", "An organisation must keep at least one Tenant Admin"
            ) from exc
        raise
    return bool(rowcount(result))


async def remove(session: AsyncSession, user_id: str) -> bool:
    try:
        result = await session.execute(
            text("DELETE FROM tenant_members WHERE user_id = :u"), {"u": user_id}
        )
    except DBAPIError as exc:
        if "at least one admin" in str(exc):
            raise MemberError(
                "last_admin", "An organisation must keep at least one Tenant Admin"
            ) from exc
        raise
    return bool(rowcount(result))


async def revoke_invite(session: AsyncSession, invite_id: str) -> bool:
    result = await session.execute(
        text("DELETE FROM invites WHERE id = :id AND accepted_at IS NULL"), {"id": invite_id}
    )
    return bool(rowcount(result))
