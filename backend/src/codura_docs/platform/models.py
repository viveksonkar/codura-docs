"""Model Profile management (FR-26/27/28/29/70; SEC-11, SEC-17; ADR-0018/0019; M3-10, M3-11).

Platform profiles (``tenant_id`` NULL) and Tenant-scoped Custom LLM profiles. Credentials are
write-only: encrypted with ``crypto.py`` and never returned. The licence allowlist is enforced
on every save. Non-local profiles are flagged together with the Plans and Tenants that use them.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from codura_docs.config import ConfigError
from codura_docs.crypto import Keyring
from codura_docs.ids import is_id
from codura_docs.platform.service import audit
from codura_docs.ports.chat import ChatMessage, ChatParams, ModelProfile
from codura_docs.ports.errors import ProviderError
from codura_docs.registry import Registry, is_local_url, validate_profile_license
from codura_docs.services.model_resolution import COLUMNS, profile_from_row

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,47}$")
PROVIDERS = ("ollama", "openai_compat", "anthropic")
LICENSES = ("apache-2.0", "mit", "commercial-api")


class ModelError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


async def list_profiles(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                text(
                    "SELECT m.id, m.tenant_id, t.name AS tenant_name, m.provider, m.model, m.base_url, m.params, "
                    "m.license, m.is_local, m.status, m.price_in_per_mtok_micros, m.price_out_per_mtok_micros, "
                    "m.credentials_enc IS NOT NULL AS has_credentials, m.updated_at, "
                    "(SELECT value #>> '{}' FROM system_settings WHERE key = 'default_model_profile') = m.id AS is_default, "
                    "ARRAY(SELECT p.code::text FROM plans p JOIN plan_versions v ON v.id = p.current_version_id "
                    "      WHERE v.default_model_profile_id = m.id ORDER BY p.sort_order) AS plans, "
                    "ARRAY(SELECT tt.name FROM tenants tt WHERE tt.model_profile_override = m.id ORDER BY tt.name) AS override_tenants "
                    "FROM model_profiles m LEFT JOIN tenants t ON t.id = m.tenant_id "
                    "ORDER BY m.tenant_id NULLS FIRST, m.id"
                )
            )
        )
        .mappings()
        .all()
    )
    out = []
    for r in rows:
        item = dict(r)
        # SEC-11: remote providers receive Tenant content; show who would be affected.
        item["egress"] = not r["is_local"]
        out.append(item)
    return out


def _validate(body: dict[str, Any], *, creating: bool) -> None:
    if creating and not _ID.match(str(body.get("id") or "")):
        raise ModelError(422, "validation_error", "id: 2-48 lowercase letters, digits or '-'")
    if "provider" in body and body["provider"] not in PROVIDERS:
        raise ModelError(422, "validation_error", f"provider must be one of {', '.join(PROVIDERS)}")
    if "license" in body and body["license"] not in LICENSES:
        raise ModelError(
            422, "validation_error", "license must be apache-2.0, mit or commercial-api"
        )
    url = body.get("base_url")
    if url and not re.match(r"^https?://[^\s]+$", str(url)):
        raise ModelError(422, "validation_error", "base_url must be an http(s) URL")
    for key in ("price_in_per_mtok_micros", "price_out_per_mtok_micros"):
        if key in body and (not isinstance(body[key], int) or body[key] < 0):
            raise ModelError(422, "validation_error", f"{key} must be a non-negative integer")
    params = body.get("params")
    if params is not None and not isinstance(params, dict):
        raise ModelError(422, "validation_error", "params must be an object")


def _profile(row: dict[str, Any], api_key: str | None) -> ModelProfile:
    params = row.get("params") or {}
    if isinstance(params, str):
        params = json.loads(params)
    return ModelProfile(
        id=str(row["id"]),
        provider=row["provider"],
        model=str(row["model"]),
        license=row["license"],
        is_local=bool(row["is_local"]),
        base_url=row.get("base_url"),
        api_key=api_key,
        temperature=float(params.get("temperature", 0.1)),
        max_output_tokens=int(params.get("max_output_tokens", 800)),
        disable_thinking=bool(params.get("disable_thinking", False)),
        price_in_per_mtok_micros=int(row.get("price_in_per_mtok_micros") or 0),
        price_out_per_mtok_micros=int(row.get("price_out_per_mtok_micros") or 0),
        extra={
            k: v
            for k, v in params.items()
            if k not in {"temperature", "max_output_tokens", "disable_thinking"}
        },
    )


def _is_local(provider: str, base_url: str | None) -> bool:
    return provider == "ollama" or is_local_url(base_url)


async def create(session: AsyncSession, keyring: Keyring, actor: str, body: dict[str, Any]) -> str:
    _validate(body, creating=True)
    tenant_id = body.get("tenant_id")
    if tenant_id is not None:
        if not is_id("tnt", tenant_id):
            raise ModelError(422, "validation_error", "tenant_id is invalid")
        exists = (
            await session.execute(text("SELECT 1 FROM tenants WHERE id = :t"), {"t": tenant_id})
        ).first()
        if not exists:
            raise ModelError(404, "not_found", "Tenant not found")
    if (
        await session.execute(
            text("SELECT 1 FROM model_profiles WHERE id = :id"), {"id": body["id"]}
        )
    ).first():
        raise ModelError(409, "conflict", "A Model Profile with this id exists")
    provider = str(body.get("provider") or "openai_compat")
    base_url = body.get("base_url")
    row = {
        "id": body["id"],
        "provider": provider,
        "model": str(body.get("model") or ""),
        "license": body.get("license") or "commercial-api",
        "is_local": _is_local(provider, base_url),
        "base_url": base_url,
        "params": body.get("params") or {},
        "price_in_per_mtok_micros": int(body.get("price_in_per_mtok_micros") or 0),
        "price_out_per_mtok_micros": int(body.get("price_out_per_mtok_micros") or 0),
    }
    if not row["model"]:
        raise ModelError(422, "validation_error", "model is required")
    try:
        validate_profile_license(_profile(row, None))  # ADR-0018 allowlist
    except ConfigError as exc:
        raise ModelError(422, "license_not_allowed", str(exc)) from exc
    creds = body.get("api_key")
    await session.execute(
        text(
            "INSERT INTO model_profiles (id, tenant_id, provider, model, base_url, params, license, is_local, "
            "credentials_enc, price_in_per_mtok_micros, price_out_per_mtok_micros, status) VALUES (:id, :t, :p, :m, "
            ":u, CAST(:params AS jsonb), :l, :loc, :c, :pin, :pout, :st)"
        ),
        {
            "id": row["id"],
            "t": tenant_id,
            "p": provider,
            "m": row["model"],
            "u": base_url,
            "params": json.dumps(row["params"]),
            "l": row["license"],
            "loc": row["is_local"],
            "c": keyring.encrypt(str(creds), aad=f"model_profiles:{row['id']}".encode())
            if creds
            else None,
            "pin": row["price_in_per_mtok_micros"],
            "pout": row["price_out_per_mtok_micros"],
            "st": body.get("status") or "active",
        },
    )
    await audit(
        session,
        actor,
        "model_profile.create",
        tenant_id,
        {
            "id": row["id"],
            "provider": provider,
            "is_local": row["is_local"],
            "credentials": bool(creds),
        },
    )
    return str(row["id"])


async def update(
    session: AsyncSession, keyring: Keyring, actor: str, profile_id: str, body: dict[str, Any]
) -> bool:
    _validate(body, creating=False)
    current = (
        (
            await session.execute(
                text("SELECT * FROM model_profiles WHERE id = :id FOR UPDATE"), {"id": profile_id}
            )
        )
        .mappings()
        .first()
    )
    if current is None:
        return False
    merged = dict(current) | {
        k: v
        for k, v in body.items()
        if k
        in {
            "provider",
            "model",
            "base_url",
            "params",
            "license",
            "price_in_per_mtok_micros",
            "price_out_per_mtok_micros",
            "status",
        }
    }
    if merged.get("status") not in ("active", "disabled"):
        raise ModelError(422, "validation_error", "status must be active or disabled")
    merged["is_local"] = _is_local(str(merged["provider"]), merged.get("base_url"))
    try:
        validate_profile_license(_profile(merged, None))
    except ConfigError as exc:
        raise ModelError(422, "license_not_allowed", str(exc)) from exc
    if merged["status"] == "disabled":
        is_default = (
            await session.execute(
                text(
                    "SELECT 1 FROM system_settings WHERE key = 'default_model_profile' AND value #>> '{}' = :id"
                ),
                {"id": profile_id},
            )
        ).first()
        if is_default:
            raise ModelError(409, "conflict", "The platform default profile can't be disabled")
    creds_sql = ""
    params: dict[str, Any] = {
        "id": profile_id,
        "p": merged["provider"],
        "m": merged["model"],
        "u": merged.get("base_url"),
        "params": json.dumps(merged.get("params") or {}),
        "l": merged["license"],
        "loc": merged["is_local"],
        "pin": int(merged["price_in_per_mtok_micros"] or 0),
        "pout": int(merged["price_out_per_mtok_micros"] or 0),
        "st": merged["status"],
    }
    if body.get("clear_credentials"):
        creds_sql = ", credentials_enc = NULL"
    elif body.get("api_key"):
        creds_sql = ", credentials_enc = :c"
        params["c"] = keyring.encrypt(
            str(body["api_key"]), aad=f"model_profiles:{profile_id}".encode()
        )
    await session.execute(
        text(
            "UPDATE model_profiles SET provider = :p, model = :m, base_url = :u, params = CAST(:params AS jsonb), "
            f"license = :l, is_local = :loc, price_in_per_mtok_micros = :pin, price_out_per_mtok_micros = :pout, "
            f"status = :st{creds_sql}, updated_at = now() WHERE id = :id"
        ),
        params,
    )
    await audit(
        session,
        actor,
        "model_profile.update",
        current["tenant_id"],
        {
            "id": profile_id,
            "fields": sorted(k for k in body if k != "api_key"),
            "credentials": bool(body.get("api_key")),
        },
    )
    return True


async def set_default(session: AsyncSession, actor: str, profile_id: str) -> None:
    ok = (
        await session.execute(
            text(
                "SELECT 1 FROM model_profiles WHERE id = :id AND tenant_id IS NULL AND status = 'active'"
            ),
            {"id": profile_id},
        )
    ).first()
    if not ok:
        raise ModelError(
            422, "validation_error", "Only an active platform profile can be the default"
        )
    await session.execute(
        text(
            "INSERT INTO system_settings (key, value) VALUES ('default_model_profile', to_jsonb(CAST(:id AS text))) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ),
        {"id": profile_id},
    )
    await audit(session, actor, "model_profile.default", None, {"id": profile_id})


async def test(
    session: AsyncSession, keyring: Keyring, registry: Registry, profile_id: str
) -> dict[str, Any]:
    """Health check + one short completion. Returns timing and a short sample, never credentials."""
    row = (
        await session.execute(
            text(f"SELECT {COLUMNS} FROM model_profiles WHERE id = :id"), {"id": profile_id}
        )
    ).first()
    if row is None:
        raise ModelError(404, "not_found", "Model Profile not found")
    profile = profile_from_row(row, keyring)
    started = time.perf_counter()
    try:
        chat = registry.chat(profile)
        await chat.health()
        reply, done = await chat.complete(
            [ChatMessage("user", "Reply with the single word: ready")],
            ChatParams(temperature=0.0, max_output_tokens=16),
        )
    except (ProviderError, ConfigError) as exc:
        return {
            "ok": False,
            "error": type(exc).__name__,
            "message": str(exc)[:300],
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
    return {
        "ok": True,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "sample": reply.strip()[:80],
        "usage": {
            "prompt_tokens": done.usage.prompt_tokens,
            "completion_tokens": done.usage.completion_tokens,
        },
    }
