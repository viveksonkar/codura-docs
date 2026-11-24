"""M0-03: RLS isolation, RLS coverage, last-admin rule, bootstrap idempotency, embedding guard."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from codura_docs.bootstrap import run_bootstrap
from codura_docs.config import EmbeddingConfig, Settings
from codura_docs.db.guards import EmbeddingModelMismatch, check_embedding_guard
from codura_docs.db.rls import RLS_EXEMPT
from codura_docs.db.session import global_session, tenant_session
from codura_docs.ids import new_id
from codura_docs.tenancy import TenantContext

pytestmark = pytest.mark.integration


async def _make_tenant(owner: AsyncEngine, admin_email: str) -> tuple[str, str]:
    tenant_id, user_id = new_id("tnt"), new_id("usr")
    async with owner.begin() as conn:
        await conn.execute(
            text("INSERT INTO tenants (id, slug, name, status) VALUES (:id, :slug, :n, 'active')"),
            {"id": tenant_id, "slug": tenant_id.lower(), "n": admin_email},
        )
        await conn.execute(
            text("INSERT INTO users (id, email, password_hash) VALUES (:id, :e, 'x')"),
            {"id": user_id, "e": admin_email},
        )
        await conn.execute(
            text("INSERT INTO tenant_members (tenant_id, user_id, role) VALUES (:t, :u, 'admin')"),
            {"t": tenant_id, "u": user_id},
        )
    return tenant_id, user_id


async def test_rls_isolates_tenants_and_fails_closed(
    owner_engine: AsyncEngine, app_engine: AsyncEngine
) -> None:
    a, _ = await _make_tenant(owner_engine, f"a-{new_id('usr')}@x.test")
    b, _ = await _make_tenant(owner_engine, f"b-{new_id('usr')}@x.test")

    async with tenant_session(app_engine, TenantContext(a)) as s:
        rows = (await s.execute(text("SELECT tenant_id FROM tenant_members"))).scalars().all()
    assert set(rows) == {a}

    async with tenant_session(app_engine, TenantContext(b)) as s:
        rows = (await s.execute(text("SELECT tenant_id FROM tenant_members"))).scalars().all()
        assert a not in rows
        # Writing into another tenant is rejected by WITH CHECK.
        with pytest.raises(DBAPIError):
            await s.execute(
                text(
                    "INSERT INTO invites (id, tenant_id, email, role, token_hash, expires_at) "
                    "VALUES (:id, :t, 'x@y.test', 'user', :h, now())"
                ),
                {"id": new_id("inv"), "t": a, "h": new_id("tok")},
            )

    async with global_session(app_engine) as s:
        count = (await s.execute(text("SELECT count(*) FROM tenant_members"))).scalar_one()
    assert count == 0  # no tenant context ⇒ nothing visible


async def test_app_role_is_not_privileged(app_engine: AsyncEngine) -> None:
    async with app_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
        assert row == (False, False)
        with pytest.raises(DBAPIError):
            await conn.execute(text("SELECT * FROM platform_audit"))


async def test_every_tenant_table_has_forced_rls_policy(owner_engine: AsyncEngine) -> None:
    async with owner_engine.connect() as conn:
        rows = (
            await conn.execute(
                text("""
            SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                   EXISTS (SELECT 1 FROM pg_policies p
                           WHERE p.tablename = c.relname AND p.policyname = 'tenant_isolation')
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r'
              AND EXISTS (SELECT 1 FROM information_schema.columns col
                          WHERE col.table_schema = 'public' AND col.table_name = c.relname
                            AND col.column_name = 'tenant_id')""")
            )
        ).all()
    checked = [r for r in rows if r[0] not in RLS_EXEMPT]
    assert checked, "expected tenant tables"
    missing = [r[0] for r in checked if not (r[1] and r[2] and r[3])]
    assert missing == [], f"tenant tables without forced RLS policy: {missing}"


async def test_tenant_keeps_last_admin_but_can_be_deleted(owner_engine: AsyncEngine) -> None:
    tenant_id, user_id = await _make_tenant(owner_engine, f"solo-{new_id('usr')}@x.test")
    with pytest.raises(DBAPIError, match="at least one admin"):
        async with owner_engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE tenant_members SET role = 'user' WHERE tenant_id = :t AND user_id = :u"
                ),
                {"t": tenant_id, "u": user_id},
            )
    async with owner_engine.begin() as conn:
        await conn.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": tenant_id})
        left = (
            await conn.execute(
                text("SELECT count(*) FROM tenant_members WHERE tenant_id = :t"), {"t": tenant_id}
            )
        ).scalar_one()
    assert left == 0


def test_bootstrap_is_idempotent_and_seeds(pg_database: Settings) -> None:
    run_bootstrap(pg_database)  # second run must be a no-op
    engine = create_engine(pg_database.database_owner_url)
    with engine.connect() as conn:
        admins = conn.execute(
            text("SELECT count(*) FROM users WHERE is_platform_admin")
        ).scalar_one()
        demo_roles = conn.execute(
            text(
                "SELECT array_agg(m.role ORDER BY m.role) FROM tenant_members m "
                "JOIN tenants t ON t.id = m.tenant_id WHERE t.slug = 'demo'"
            )
        ).scalar_one()
        embedding = conn.execute(
            text("SELECT value FROM system_settings WHERE key = 'embedding'")
        ).scalar_one()
    engine.dispose()
    assert admins == 1
    assert demo_roles == ["admin", "user"]
    assert embedding == {"provider": "fake", "model": "bge-m3", "dimension": 1024}


async def test_embedding_guard(app_engine: AsyncEngine) -> None:
    await check_embedding_guard(app_engine, EmbeddingConfig(provider="fake", model="bge-m3"))
    with pytest.raises(EmbeddingModelMismatch, match="fixed"):
        await check_embedding_guard(
            app_engine, EmbeddingConfig(provider="fake", model="nomic-embed-text")
        )
