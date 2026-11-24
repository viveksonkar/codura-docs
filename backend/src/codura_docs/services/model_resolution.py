"""Which Model Profile answers a Tenant's question (ADR-0019).

Order: Tenant Custom LLM (M3, entitlement-gated) → Platform Admin override → platform default.
Tenants never choose. The resolved profile id is stored on each Message.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from codura_docs.crypto import Keyring
from codura_docs.ports.chat import ModelProfile


class NoModelProfile(RuntimeError):
    """The platform has no active profile to answer with."""


def profile_from_row(row: Any, keyring: Keyring | None) -> ModelProfile:
    params: dict[str, Any] = (
        row.params if isinstance(row.params, dict) else json.loads(row.params or "{}")
    )
    api_key = None
    if row.credentials_enc and keyring is not None:
        api_key = keyring.decrypt_str(
            bytes(row.credentials_enc), aad=f"model_profiles:{row.id}".encode()
        )
    extra = {
        k: v
        for k, v in params.items()
        if k not in {"temperature", "max_output_tokens", "disable_thinking"}
    }
    return ModelProfile(
        id=row.id,
        provider=row.provider,
        model=row.model,
        license=row.license,
        is_local=row.is_local,
        base_url=row.base_url,
        api_key=api_key,
        temperature=float(params.get("temperature", 0.1)),
        max_output_tokens=int(params.get("max_output_tokens", 800)),
        disable_thinking=bool(params.get("disable_thinking", False)),
        price_in_per_mtok_micros=int(row.price_in_per_mtok_micros),
        price_out_per_mtok_micros=int(row.price_out_per_mtok_micros),
        extra=extra,
    )


_COLUMNS = (
    "id, provider, model, base_url, params, license, is_local, credentials_enc, "
    "price_in_per_mtok_micros, price_out_per_mtok_micros"
)


async def resolve_model(session: AsyncSession, tenant_id: str, keyring: Keyring) -> ModelProfile:
    override = (
        await session.execute(
            text("SELECT model_profile_override FROM tenants WHERE id = :t"), {"t": tenant_id}
        )
    ).scalar()
    default = (
        await session.execute(
            text("SELECT value FROM system_settings WHERE key = 'default_model_profile'")
        )
    ).scalar()
    for candidate in (override, default):
        if not candidate:
            continue
        row = (
            await session.execute(
                text(
                    f"SELECT {_COLUMNS} FROM model_profiles WHERE id = :id AND status = 'active' "
                    "AND tenant_id IS NULL"
                ),
                {"id": candidate},
            )
        ).first()
        if row is not None:
            return profile_from_row(row, keyring)
    raise NoModelProfile("no active model profile is configured")
