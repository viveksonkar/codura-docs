"""Platform Admin use cases: the ONLY cross-Tenant code (ADR-0014, ADR-0019, FR-48, FR-75).

Runs as the ``codura_platform`` role; every mutating action is written to ``platform_audit``.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from codura_docs.db.session import rowcount
from codura_docs.ids import new_id
from codura_docs.passwords import hash_password
from codura_docs.services import auth as auth_svc


async def audit(
    session: AsyncSession, actor: str, action: str, tenant_id: str | None, details: dict[str, Any]
) -> None:
    await session.execute(
        text(
            "INSERT INTO platform_audit (id, actor_user_id, action, target_tenant_id, details) "
            "VALUES (:id, :a, :act, :t, CAST(:d AS jsonb))"
        ),
        {
            "id": new_id("aud"),
            "a": actor,
            "act": action,
            "t": tenant_id,
            "d": json.dumps(details, default=str),
        },
    )


async def list_tenants(session: AsyncSession, q: str | None = None) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                text(
                    "SELECT t.id, t.slug, t.name, t.status, coalesce((SELECT p.code FROM subscriptions s JOIN plan_versions v ON v.id = s.plan_version_id JOIN plans p ON p.id = v.plan_id WHERE s.tenant_id = t.id), 'free')::text AS plan_code, t.model_profile_override, t.created_at, "
                    "(SELECT count(*) FROM tenant_members m WHERE m.tenant_id = t.id) AS members, "
                    "(SELECT count(*) FROM documents d WHERE d.tenant_id = t.id) AS documents, "
                    "(SELECT coalesce(sum(quantity), 0) FROM usage_counters u WHERE u.tenant_id = t.id AND u.meter = 'answer' "
                    " AND u.period_start = date_trunc('month', now())::date) AS answers_this_month, "
                    "(SELECT coalesce(sum(cogs_micros), 0) FROM usage_counters u WHERE u.tenant_id = t.id "
                    " AND u.period_start = date_trunc('month', now())::date) AS cogs_micros_this_month "
                    "FROM tenants t WHERE (CAST(:q AS text) IS NULL OR t.name ILIKE '%' || CAST(:q AS text) || '%' OR t.slug ILIKE '%' || CAST(:q AS text) || '%') "
                    "ORDER BY t.created_at DESC LIMIT 200"
                ),
                {"q": q},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def get_tenant(session: AsyncSession, tenant_id: str) -> dict[str, Any] | None:
    items = [t for t in await list_tenants(session) if t["id"] == tenant_id]
    if not items:
        return None
    admins = (
        (
            await session.execute(
                text(
                    "SELECT u.email, m.role FROM tenant_members m JOIN users u ON u.id = m.user_id WHERE m.tenant_id = :t "
                    "ORDER BY m.role, u.email"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    return items[0] | {"members_list": [dict(a) for a in admins]}


async def update_tenant(
    session: AsyncSession,
    actor: str,
    tenant_id: str,
    *,
    status: str | None,
    model_profile_override: str | None,
    clear_override: bool,
) -> bool:
    if model_profile_override:
        exists = (
            await session.execute(
                text(
                    "SELECT 1 FROM model_profiles WHERE id = :id AND status = 'active' AND tenant_id IS NULL"
                ),
                {"id": model_profile_override},
            )
        ).first()
        if not exists:
            raise ValueError("unknown or inactive model profile")
    result = await session.execute(
        text(
            "UPDATE tenants SET status = COALESCE(:s, status), "
            "model_profile_override = CASE WHEN :clear THEN NULL ELSE COALESCE(:m, model_profile_override) END, "
            "updated_at = now() WHERE id = :t"
        ),
        {"s": status, "m": model_profile_override, "clear": clear_override, "t": tenant_id},
    )
    if rowcount(result):
        await audit(
            session,
            actor,
            "tenant.update",
            tenant_id,
            {
                "status": status,
                "model_profile_override": model_profile_override,
                "clear": clear_override,
            },
        )
    return bool(rowcount(result))


async def delete_tenant(session: AsyncSession, actor: str, tenant_id: str) -> bool:
    await audit(session, actor, "tenant.delete", tenant_id, {})
    return bool(
        rowcount(await session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": tenant_id}))
    )


async def list_model_profiles(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                text(
                    "SELECT id, tenant_id, provider, model, base_url, params, license, is_local, status, "
                    "price_in_per_mtok_micros, price_out_per_mtok_micros, credentials_enc IS NOT NULL AS has_credentials, "
                    "(SELECT value #>> '{}' FROM system_settings WHERE key = 'default_model_profile') = id AS is_default "
                    "FROM model_profiles ORDER BY tenant_id NULLS FIRST, id"
                )
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def list_audit(session: AsyncSession, limit: int = 200) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                text(
                    "SELECT a.id, u.email AS actor, a.action, a.target_tenant_id, a.details, a.created_at "
                    "FROM platform_audit a LEFT JOIN users u ON u.id = a.actor_user_id ORDER BY a.created_at DESC LIMIT :l"
                ),
                {"l": limit},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def invite_platform_admin(
    session: AsyncSession, actor: str, email: str, base_url: str
) -> str:
    """Create (or promote) a Platform Admin; they set a password through the reset flow."""
    from codura_docs.services import email_flows

    row = (
        await session.execute(text("SELECT id FROM users WHERE email = :e"), {"e": email})
    ).first()
    if row:
        user_id = row.id
        await session.execute(
            text("UPDATE users SET is_platform_admin = true WHERE id = :u"), {"u": user_id}
        )
    else:
        user_id = new_id("usr")
        await session.execute(
            text(
                "INSERT INTO users (id, email, name, password_hash, is_platform_admin) VALUES (:id, :e, '', :h, true)"
            ),
            {"id": user_id, "e": email, "h": hash_password(auth_svc.new_token())},
        )
    token = await auth_svc.issue_auth_token(session, user_id, "reset_password", timedelta(days=2))
    await email_flows.reset_email(session, base_url, email, token)
    await audit(session, actor, "platform_admin.invite", None, {"email": email})
    return str(user_id)
