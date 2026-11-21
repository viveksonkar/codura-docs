"""Sessions, login throttling, memberships and single-use tokens (SEC-01, ADR-0020)."""

from __future__ import annotations

import hashlib
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from codura_docs.ids import new_id
from codura_docs.passwords import hash_password, needs_rehash, verify_password

# A dummy hash so unknown emails cost the same time as wrong passwords (no user enumeration).
_DUMMY_HASH = hash_password("timing-equaliser")


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class SessionInfo:
    session_id: str
    user_id: str
    email: str
    name: str
    is_platform_admin: bool
    current_tenant_id: str | None
    csrf_token: str
    email_verified: bool


@dataclass(frozen=True)
class Membership:
    tenant_id: str
    role: str
    tenant_name: str
    tenant_slug: str
    tenant_status: str


class Throttle:
    """In-memory sliding window (single api replica in MVP; SEC-06 moves this behind an interface)."""

    def __init__(self, limit: int, window_s: float) -> None:
        self.limit, self.window = limit, window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str) -> bool:
        """Record an attempt; return False when the key is over its limit."""
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)


LOGIN_THROTTLE = Throttle(limit=8, window_s=15 * 60)


async def authenticate(session: AsyncSession, email: str, password: str) -> str | None:
    row = (
        await session.execute(
            text("SELECT id, password_hash FROM users WHERE email = :e"), {"e": email}
        )
    ).first()
    if row is None:
        verify_password(_DUMMY_HASH, password)
        return None
    if not verify_password(row.password_hash, password):
        return None
    if needs_rehash(row.password_hash):
        await session.execute(
            text("UPDATE users SET password_hash = :h WHERE id = :id"),
            {"h": hash_password(password), "id": row.id},
        )
    await session.execute(
        text("UPDATE users SET last_login_at = now() WHERE id = :id"), {"id": row.id}
    )
    return str(row.id)


async def memberships(session: AsyncSession, user_id: str) -> list[Membership]:
    rows = (await session.execute(text("SELECT * FROM user_memberships(:u)"), {"u": user_id})).all()
    return [
        Membership(r.tenant_id, r.role, r.tenant_name, r.tenant_slug, r.tenant_status) for r in rows
    ]


async def create_session(
    session: AsyncSession, user_id: str, tenant_id: str | None, ttl_hours: int
) -> tuple[str, str]:
    token, csrf = new_token(), new_token()
    await session.execute(
        text(
            "INSERT INTO sessions (id, user_id, current_tenant_id, csrf_token, expires_at) "
            "VALUES (:id, :u, :t, :c, :exp)"
        ),
        {
            "id": sha256(token),
            "u": user_id,
            "t": tenant_id,
            "c": csrf,
            "exp": datetime.now(UTC) + timedelta(hours=ttl_hours),
        },
    )
    return token, csrf


async def load_session(session: AsyncSession, token: str) -> SessionInfo | None:
    row = (
        await session.execute(
            text(
                "SELECT s.id, s.user_id, s.current_tenant_id, s.csrf_token, u.email, u.name, "
                "u.is_platform_admin, u.email_verified_at IS NOT NULL AS verified "
                "FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.id = :id AND s.expires_at > now()"
            ),
            {"id": sha256(token)},
        )
    ).first()
    if row is None:
        return None
    return SessionInfo(
        row.id,
        row.user_id,
        row.email,
        row.name,
        row.is_platform_admin,
        row.current_tenant_id,
        row.csrf_token,
        row.verified,
    )


async def set_current_tenant(session: AsyncSession, session_id: str, tenant_id: str | None) -> None:
    await session.execute(
        text("UPDATE sessions SET current_tenant_id = :t, last_seen_at = now() WHERE id = :id"),
        {"t": tenant_id, "id": session_id},
    )


async def delete_session(session: AsyncSession, token: str) -> None:
    await session.execute(text("DELETE FROM sessions WHERE id = :id"), {"id": sha256(token)})


async def issue_auth_token(
    session: AsyncSession, user_id: str, purpose: str, ttl: timedelta
) -> str:
    token = new_token()
    await session.execute(
        text(
            "INSERT INTO auth_tokens (id, user_id, purpose, token_hash, expires_at) VALUES (:id, :u, :p, :h, :e)"
        ),
        {
            "id": new_id("tok"),
            "u": user_id,
            "p": purpose,
            "h": sha256(token),
            "e": datetime.now(UTC) + ttl,
        },
    )
    return token


async def consume_auth_token(session: AsyncSession, token: str, purpose: str) -> str | None:
    row = (
        await session.execute(
            text(
                "UPDATE auth_tokens SET used_at = now() WHERE token_hash = :h AND purpose = :p "
                "AND used_at IS NULL AND expires_at > now() RETURNING user_id"
            ),
            {"h": sha256(token), "p": purpose},
        )
    ).first()
    return str(row.user_id) if row else None
