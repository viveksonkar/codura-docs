"""Baseline: extensions, roles, platform + tenancy tables, jobs (specs/db.md §1–3, M0-03).

Revision ID: 0001_baseline
Revises:
Create Date: 2026-11-19
"""

from __future__ import annotations

from alembic import op

from codura_docs.db.rls import APP_ROLES, PLATFORM_ROLE, grant_sql, tenant_rls_sql

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

ALL_ROLES = (*APP_ROLES, PLATFORM_ROLE)


def upgrade() -> None:
    for ext in ("vector", "pgcrypto", "citext"):
        op.execute(f"CREATE EXTENSION IF NOT EXISTS {ext}")

    # Roles are cluster-wide; created NOLOGIN here, given LOGIN + password by bootstrap.
    for role in ALL_ROLES:
        op.execute(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') "
            f"THEN CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS; END IF; END $$"
        )
        op.execute(f"GRANT USAGE ON SCHEMA public TO {role}")

    op.execute("""
        CREATE TABLE system_settings (
            key        text PRIMARY KEY,
            value      jsonb NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now()
        )""")

    op.execute("""
        CREATE TABLE tenants (
            id               text PRIMARY KEY,
            slug             citext NOT NULL UNIQUE,
            name             text NOT NULL,
            status           text NOT NULL DEFAULT 'pending_verification'
                             CHECK (status IN ('pending_verification','active','past_due','suspended')),
            plan_code        text NOT NULL DEFAULT 'trial',
            ocr_languages    text NOT NULL DEFAULT 'eng',
            default_language text NOT NULL DEFAULT 'en',
            created_at       timestamptz NOT NULL DEFAULT now(),
            updated_at       timestamptz NOT NULL DEFAULT now()
        )""")

    op.execute("""
        CREATE TABLE users (
            id                text PRIMARY KEY,
            email             citext NOT NULL UNIQUE,
            email_verified_at timestamptz,
            name              text NOT NULL DEFAULT '',
            password_hash     text NOT NULL,
            is_platform_admin boolean NOT NULL DEFAULT false,
            created_at        timestamptz NOT NULL DEFAULT now(),
            last_login_at     timestamptz
        )""")

    op.execute("""
        CREATE TABLE tenant_members (
            tenant_id  text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            user_id    text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role       text NOT NULL CHECK (role IN ('admin','user')),
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, user_id)
        )""")
    op.execute("CREATE INDEX tenant_members_user ON tenant_members (user_id)")

    # A Tenant always keeps at least one Tenant Admin (ADR-0020). Skipped when the tenant itself
    # is being deleted (cascade).
    op.execute("""
        CREATE FUNCTION ensure_tenant_has_admin() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        BEGIN
            IF OLD.role = 'admin' AND (TG_OP = 'DELETE' OR NEW.role <> 'admin') THEN
                IF EXISTS (SELECT 1 FROM tenants WHERE id = OLD.tenant_id)
                   AND NOT EXISTS (SELECT 1 FROM tenant_members
                                   WHERE tenant_id = OLD.tenant_id AND role = 'admin'
                                     AND user_id <> OLD.user_id) THEN
                    RAISE EXCEPTION 'tenant % must keep at least one admin', OLD.tenant_id
                        USING ERRCODE = 'check_violation';
                END IF;
            END IF;
            RETURN COALESCE(NEW, OLD);
        END $$""")
    op.execute("""
        CREATE TRIGGER tenant_members_keep_admin
        BEFORE UPDATE OR DELETE ON tenant_members
        FOR EACH ROW EXECUTE FUNCTION ensure_tenant_has_admin()""")

    op.execute("""
        CREATE TABLE invites (
            id          text PRIMARY KEY,
            tenant_id   text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            email       citext NOT NULL,
            role        text NOT NULL CHECK (role IN ('admin','user')),
            token_hash  text NOT NULL UNIQUE,
            expires_at  timestamptz NOT NULL,
            accepted_at timestamptz,
            created_by  text REFERENCES users(id) ON DELETE SET NULL,
            created_at  timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id)
        )""")

    op.execute("""
        CREATE TABLE auth_tokens (
            id         text PRIMARY KEY,
            user_id    text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            purpose    text NOT NULL CHECK (purpose IN ('verify_email','reset_password')),
            token_hash text NOT NULL UNIQUE,
            expires_at timestamptz NOT NULL,
            used_at    timestamptz,
            created_at timestamptz NOT NULL DEFAULT now()
        )""")

    op.execute("""
        CREATE TABLE platform_audit (
            id               text PRIMARY KEY,
            actor_user_id    text REFERENCES users(id) ON DELETE SET NULL,
            action           text NOT NULL,
            target_tenant_id text,
            details          jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at       timestamptz NOT NULL DEFAULT now()
        )""")
    op.execute("""
        CREATE FUNCTION platform_audit_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'platform_audit is append-only'; END $$""")
    op.execute("""
        CREATE TRIGGER platform_audit_no_change BEFORE UPDATE OR DELETE ON platform_audit
        FOR EACH ROW EXECUTE FUNCTION platform_audit_append_only()""")

    op.execute("""
        CREATE TABLE jobs (
            id           text PRIMARY KEY,
            tenant_id    text REFERENCES tenants(id) ON DELETE CASCADE,
            kind         text NOT NULL,
            payload      jsonb NOT NULL DEFAULT '{}'::jsonb,
            status       text NOT NULL DEFAULT 'queued'
                         CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
            attempts     int NOT NULL DEFAULT 0,
            max_attempts int NOT NULL DEFAULT 3,
            run_after    timestamptz NOT NULL DEFAULT now(),
            locked_by    text,
            locked_until timestamptz,
            last_error   text,
            created_at   timestamptz NOT NULL DEFAULT now(),
            updated_at   timestamptz NOT NULL DEFAULT now(),
            finished_at  timestamptz
        )""")
    op.execute("CREATE INDEX jobs_claim ON jobs (run_after) WHERE status = 'queued'")
    op.execute("CREATE INDEX jobs_running_lease ON jobs (locked_until) WHERE status = 'running'")

    # --- RLS on tenant tables -----------------------------------------------------------------
    for table in ("tenant_members", "invites"):
        for stmt in tenant_rls_sql(table):
            op.execute(stmt)

    # --- Grants (least privilege) -------------------------------------------------------------
    op.execute(grant_sql("system_settings", "SELECT", ALL_ROLES))
    op.execute(grant_sql("alembic_version", "SELECT", ALL_ROLES))
    op.execute(grant_sql("tenants", "SELECT", APP_ROLES))
    op.execute(grant_sql("tenants", "SELECT, INSERT, UPDATE, DELETE", (PLATFORM_ROLE,)))
    op.execute(grant_sql("users", "SELECT, INSERT, UPDATE", ALL_ROLES))
    for table in ("tenant_members", "invites"):
        op.execute(grant_sql(table, "SELECT, INSERT, UPDATE, DELETE", ALL_ROLES))
    op.execute(grant_sql("auth_tokens", "SELECT, INSERT, UPDATE, DELETE", ("codura_app",)))
    op.execute(grant_sql("platform_audit", "SELECT, INSERT", (PLATFORM_ROLE,)))
    op.execute(grant_sql("jobs", "INSERT", ("codura_app", PLATFORM_ROLE)))
    op.execute(grant_sql("jobs", "SELECT, INSERT, UPDATE", ("codura_worker",)))


def downgrade() -> None:
    for table in (
        "jobs",
        "platform_audit",
        "auth_tokens",
        "invites",
        "tenant_members",
        "users",
        "tenants",
        "system_settings",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS ensure_tenant_has_admin()")
    op.execute("DROP FUNCTION IF EXISTS platform_audit_append_only()")
