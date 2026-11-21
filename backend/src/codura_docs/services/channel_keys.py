"""Channel Keys (pk_) and Secret Keys (sk_): random, stored hashed, shown once (SEC-04, ADR-0012)."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from codura_docs.db.session import rowcount
from codura_docs.ids import new_id
from codura_docs.services.auth import sha256

Channel = Literal["hosted", "widget", "wordpress", "android", "api"]


@dataclass(frozen=True)
class ResolvedKey:
    key_id: str
    tenant_id: str
    kind: str
    channel: str | None
    assistant_id: str | None
    allowed_origins: list[str]
    revoked: bool
    tenant_status: str


def generate(kind: Literal["channel", "secret"]) -> str:
    return ("pk_" if kind == "channel" else "sk_") + secrets.token_hex(24)


async def create_key(
    session: AsyncSession,
    tenant_id: str,
    *,
    kind: Literal["channel", "secret"],
    name: str = "",
    channel: Channel | None = None,
    assistant_id: str | None = None,
    allowed_origins: list[str] | None = None,
) -> tuple[str, str]:
    """Returns (key_id, plaintext key). The plaintext is never stored."""
    if kind == "channel" and (channel is None or assistant_id is None):
        raise ValueError("channel keys need a channel and an assistant")
    plaintext = generate(kind)
    key_id = new_id("key")
    await session.execute(
        text(
            "INSERT INTO api_keys (id, tenant_id, kind, prefix, key_hash, name, channel, assistant_id, allowed_origins) "
            "VALUES (:id, :t, :k, :p, :h, :n, :c, :a, :o)"
        ),
        {
            "id": key_id,
            "t": tenant_id,
            "k": kind,
            "p": plaintext[:10],
            "h": sha256(plaintext),
            "n": name,
            "c": channel,
            "a": assistant_id,
            "o": allowed_origins or [],
        },
    )
    return key_id, plaintext


async def resolve_key(session: AsyncSession, plaintext: str) -> ResolvedKey | None:
    row = (
        await session.execute(text("SELECT * FROM resolve_api_key(:h)"), {"h": sha256(plaintext)})
    ).first()
    if row is None:
        return None
    return ResolvedKey(
        row.key_id,
        row.tenant_id,
        row.kind,
        row.channel,
        row.assistant_id,
        list(row.allowed_origins or []),
        row.revoked,
        row.tenant_status,
    )


async def list_keys(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                text(
                    "SELECT id, kind, prefix, name, channel, assistant_id, allowed_origins, revoked_at, "
                    "last_used_at, created_at FROM api_keys ORDER BY created_at DESC"
                )
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def revoke_key(session: AsyncSession, key_id: str) -> bool:
    result = await session.execute(
        text("UPDATE api_keys SET revoked_at = now() WHERE id = :id AND revoked_at IS NULL"),
        {"id": key_id},
    )
    return bool(rowcount(result))
