"""Channel Keys (pk_) and Secret Keys (sk_): random, stored hashed, shown once (SEC-04, ADR-0012).

Hosted keys are system-managed and also stored encrypted, so the Hosted Chat Page can fetch its
key (ADR-0025).
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from codura_docs.crypto import Keyring
from codura_docs.db.session import rowcount
from codura_docs.ids import new_id
from codura_docs.services.auth import sha256

Channel = Literal["hosted", "widget", "wordpress", "android", "api"]
ORIGIN_CHANNELS = frozenset({"widget", "wordpress"})
MAX_ORIGINS = 20
_LABEL = r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
_ORIGIN = re.compile(rf"^https?://{_LABEL}(?:\.{_LABEL})*(?::\d{{1,5}})?$")
_KEY_COLUMNS = (
    "id, kind, prefix, name, channel, assistant_id, allowed_origins, revoked_at, last_used_at, "
    "created_at"
)


class KeyServiceError(ValueError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code = status, code


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


def normalize_origin(value: str) -> str:
    """'https://Shop.Example.com/' -> 'https://shop.example.com'. Raises on anything else."""
    origin = value.strip().rstrip("/").lower()
    if origin.startswith("https://") and origin.endswith(":443"):
        origin = origin[:-4]
    if origin.startswith("http://") and origin.endswith(":80"):
        origin = origin[:-3]
    if not _ORIGIN.match(origin):
        raise KeyServiceError(
            422, "validation_error", f"Not an origin (scheme://host[:port]): {value!r}"
        )
    return origin


def normalize_origins(values: list[str]) -> list[str]:
    if len(values) > MAX_ORIGINS:
        raise KeyServiceError(422, "validation_error", f"At most {MAX_ORIGINS} Allowed Origins")
    out: list[str] = []
    for value in values:
        origin = normalize_origin(value)
        if origin not in out:
            out.append(origin)
    return out


def hosted_aad(tenant_id: str) -> bytes:
    return f"hosted_key:{tenant_id}".encode()


async def create_key(
    session: AsyncSession,
    tenant_id: str,
    *,
    kind: Literal["channel", "secret"],
    name: str = "",
    channel: Channel | None = None,
    assistant_id: str | None = None,
    allowed_origins: list[str] | None = None,
    keyring: Keyring | None = None,
) -> tuple[str, str]:
    """Returns (key_id, plaintext key). Only hosted keys keep an encrypted copy (ADR-0025)."""
    if kind == "channel" and (channel is None or assistant_id is None):
        raise KeyServiceError(
            422, "validation_error", "Channel keys need a channel and an assistant"
        )
    if kind == "channel":
        exists = (
            await session.execute(
                text("SELECT 1 FROM assistants WHERE id = :a"), {"a": assistant_id}
            )
        ).first()
        if exists is None:  # another Tenant's Assistant is invisible under RLS: same as unknown
            raise KeyServiceError(422, "validation_error", "Unknown assistant")
    origins = normalize_origins(allowed_origins or []) if channel in ORIGIN_CHANNELS else []
    plaintext = generate(kind)
    encrypted = None
    if channel == "hosted":
        if keyring is None:
            raise KeyServiceError(422, "validation_error", "Hosted keys are system-managed")
        encrypted = keyring.encrypt(plaintext, aad=hosted_aad(tenant_id))
    key_id = new_id("key")
    await session.execute(
        text(
            "INSERT INTO api_keys (id, tenant_id, kind, prefix, key_hash, name, channel, "
            "assistant_id, allowed_origins, public_key_enc) "
            "VALUES (:id, :t, :k, :p, :h, :n, :c, :a, :o, :pe)"
        ),
        {
            "id": key_id,
            "t": tenant_id,
            "k": kind,
            "p": plaintext[:10],
            "h": sha256(plaintext),
            "n": name,
            "c": channel if kind == "channel" else None,
            "a": assistant_id if kind == "channel" else None,
            "o": origins,
            "pe": encrypted,
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


def _public(row: Any) -> dict[str, Any]:
    return dict(row) | {"active": row["revoked_at"] is None}


async def list_keys(
    session: AsyncSession, *, assistant_id: str | None = None, include_revoked: bool = True
) -> list[dict[str, Any]]:
    where: list[str] = []
    params: dict[str, Any] = {}
    if assistant_id:
        where.append("assistant_id = :a")
        params["a"] = assistant_id
    if not include_revoked:
        where.append("revoked_at IS NULL")
    sql = f"SELECT {_KEY_COLUMNS} FROM api_keys"
    if where:
        sql += " WHERE " + " AND ".join(where)
    rows = (await session.execute(text(sql + " ORDER BY created_at DESC"), params)).mappings()
    return [_public(r) for r in rows.all()]


async def get_key(session: AsyncSession, key_id: str) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text(f"SELECT {_KEY_COLUMNS} FROM api_keys WHERE id = :id"), {"id": key_id}
            )
        )
        .mappings()
        .first()
    )
    return _public(row) if row else None


async def update_origins(
    session: AsyncSession, key_id: str, origins: list[str]
) -> dict[str, Any] | None:
    key = await get_key(session, key_id)
    if key is None:
        return None
    if key["channel"] not in ORIGIN_CHANNELS:
        raise KeyServiceError(
            422, "validation_error", "Only widget and wordpress keys have Allowed Origins"
        )
    await session.execute(
        text("UPDATE api_keys SET allowed_origins = :o WHERE id = :id"),
        {"o": normalize_origins(origins), "id": key_id},
    )
    return await get_key(session, key_id)


async def revoke_key(session: AsyncSession, key_id: str, *, allow_hosted: bool = False) -> bool:
    """Revoke a key. Hosted keys follow the Assistant's hosted toggle instead (ADR-0025)."""
    guard = "" if allow_hosted else " AND channel IS DISTINCT FROM 'hosted'"
    result = await session.execute(
        text(
            f"UPDATE api_keys SET revoked_at = now() WHERE id = :id AND revoked_at IS NULL{guard}"
        ),
        {"id": key_id},
    )
    return bool(rowcount(result))


async def sync_hosted_key(
    session: AsyncSession, keyring: Keyring, tenant_id: str, assistant_id: str, enabled: bool
) -> None:
    """Enabling the Hosted Chat Page creates its hosted key; disabling revokes it (M2-00)."""
    active = (
        await session.execute(
            text(
                "SELECT id FROM api_keys WHERE assistant_id = :a AND channel = 'hosted' "
                "AND revoked_at IS NULL"
            ),
            {"a": assistant_id},
        )
    ).first()
    if enabled and active is None:
        await create_key(
            session,
            tenant_id,
            kind="channel",
            name="Hosted Chat Page",
            channel="hosted",
            assistant_id=assistant_id,
            keyring=keyring,
        )
    elif not enabled and active is not None:
        await revoke_key(session, active.id, allow_hosted=True)


async def touch(session: AsyncSession, key_id: str) -> None:
    """Throttled in SQL to one write per key per minute."""
    await session.execute(text("SELECT touch_api_key(:k)"), {"k": key_id})
