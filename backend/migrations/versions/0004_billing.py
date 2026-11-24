"""M3 billing: Plans, Subscriptions, quotas, rollups, invoices, payments (specs/billing.md, ADR-0026).

Revision ID: 0004_billing
Revises: 0003_web_channel
Create Date: 2026-11-24
"""

from __future__ import annotations

import json

from alembic import op

from codura_docs.db.rls import APP_ROLES, PLATFORM_ROLE, grant_sql, tenant_rls_sql

revision = "0004_billing"
down_revision = "0003_web_channel"
branch_labels = None
depends_on = None

ALL = (*APP_ROLES, PLATFORM_ROLE)
WORKER = "codura_worker"
APP = "codura_app"

# Tenant tables (forced RLS). codura_app reads; billing writes use codura_worker (ADR-0026 §4).
BILLING_TABLES = (
    "billing_accounts",
    "subscriptions",
    "subscription_events",
    "tenant_billing_overrides",
    "usage_rollups",
    "invoices",
    "invoice_lines",
    "payments",
)
# Metering helpers the request path writes itself (same transaction as the Usage Event).
METERING_TABLES = ("quota_reservations", "quota_notices")

# Seed catalogue (billing.md §5). Every value is editable in the Plan editor; prices in micros.
_U = 1_000_000
SEED_PLANS: list[dict[str, object]] = [
    {
        "code": "free",
        "name": "Free",
        "sort": 0,
        "free": True,
        "public": True,
        "included": {
            "answer": 100,
            "page_ocr": 50,
            "pages_stored": 200,
            "storage_mb": 100,
            "seats": 2,
            "assistants": 1,
        },
        "ent": {
            "channels": ["hosted", "widget"],
            "remove_branding": False,
            "visitor_email": "off",
            "analytics_retention_days": 7,
            "custom_llm": False,
            "max_output_tokens": 500,
            "max_input_tokens": 4000,
            "context_token_budget": 2000,
            "max_concurrent_answers": 2,
        },
        "overage": False,
        "loss_leader": True,
        "prices": {"USD": (0, {}), "INR": (0, {})},
    },
    {
        "code": "starter",
        "name": "Starter",
        "sort": 1,
        "free": False,
        "public": True,
        "included": {
            "answer": 1000,
            "page_ocr": 500,
            "pages_stored": 2000,
            "storage_mb": 1024,
            "seats": 3,
            "assistants": 2,
        },
        "ent": {
            "channels": ["hosted", "widget", "api"],
            "remove_branding": False,
            "visitor_email": "optional",
            "analytics_retention_days": 30,
            "custom_llm": False,
            "max_output_tokens": 700,
            "max_input_tokens": 6000,
            "context_token_budget": 3000,
            "max_concurrent_answers": 3,
        },
        "overage": True,
        "loss_leader": False,
        "prices": {
            "USD": (29 * _U, {"answer": 20_000, "page_ocr": 10_000, "answer_custom_llm": 5_000}),
            "INR": (
                1999 * _U,
                {"answer": 1_500_000, "page_ocr": 800_000, "answer_custom_llm": 400_000},
            ),
        },
    },
    {
        "code": "growth",
        "name": "Growth",
        "sort": 2,
        "free": False,
        "public": True,
        "included": {
            "answer": 5000,
            "page_ocr": 3000,
            "pages_stored": 10000,
            "storage_mb": 5120,
            "seats": 10,
            "assistants": 5,
        },
        "ent": {
            "channels": ["hosted", "widget", "api", "wordpress"],
            "remove_branding": True,
            "visitor_email": "required",
            "analytics_retention_days": 90,
            "custom_llm": False,
            "max_output_tokens": 800,
            "max_input_tokens": 6000,
            "context_token_budget": 3000,
            "max_concurrent_answers": 6,
        },
        "overage": True,
        "loss_leader": False,
        "prices": {
            "USD": (99 * _U, {"answer": 15_000, "page_ocr": 8_000, "answer_custom_llm": 4_000}),
            "INR": (
                6999 * _U,
                {"answer": 1_200_000, "page_ocr": 600_000, "answer_custom_llm": 300_000},
            ),
        },
    },
    {
        "code": "business",
        "name": "Business",
        "sort": 3,
        "free": False,
        "public": True,
        "included": {
            "answer": 20000,
            "page_ocr": 15000,
            "pages_stored": 50000,
            "storage_mb": 25600,
            "seats": 25,
            "assistants": 20,
        },
        "ent": {
            "channels": ["hosted", "widget", "api", "wordpress", "android"],
            "remove_branding": True,
            "visitor_email": "required",
            "analytics_retention_days": 365,
            "custom_llm": False,
            "max_output_tokens": 1000,
            "max_input_tokens": 8000,
            "context_token_budget": 4000,
            "max_concurrent_answers": 12,
        },
        "overage": True,
        "loss_leader": False,
        "prices": {
            "USD": (299 * _U, {"answer": 12_000, "page_ocr": 6_000, "answer_custom_llm": 3_000}),
            "INR": (
                19999 * _U,
                {"answer": 1_000_000, "page_ocr": 500_000, "answer_custom_llm": 250_000},
            ),
        },
    },
    {
        "code": "enterprise",
        "name": "Enterprise",
        "sort": 4,
        "free": False,
        "public": False,  # contact sales: the Platform Admin assigns it per deal
        "included": {
            "answer": 100000,
            "page_ocr": 50000,
            "pages_stored": 250000,
            "storage_mb": 102400,
            "seats": 100,
            "assistants": 100,
        },
        "ent": {
            "channels": ["hosted", "widget", "api", "wordpress", "android"],
            "remove_branding": True,
            "visitor_email": "required",
            "analytics_retention_days": 730,
            "custom_llm": False,
            "max_output_tokens": 1200,
            "max_input_tokens": 12000,
            "context_token_budget": 6000,
            "max_concurrent_answers": 24,
        },
        "overage": True,
        "loss_leader": False,
        "prices": {
            "USD": (999 * _U, {"answer": 10_000, "page_ocr": 5_000, "answer_custom_llm": 2_500}),
            "INR": (
                74999 * _U,
                {"answer": 800_000, "page_ocr": 400_000, "answer_custom_llm": 200_000},
            ),
        },
    },
]

# Pricing inputs maintained by the Platform Admin (billing.md §3–4); all editable.
SEED_RATES = [
    ("fx_usd_inr", 84_000_000, "inr_micros_per_usd"),
    ("payment_fee_bps", 290, "basis_points"),
    ("payment_fee_fixed", 300_000, "usd_micros_per_charge"),
    ("fixed_cost_per_tenant_month", 2_000_000, "usd_micros_per_tenant_month"),
    ("custom_llm_infra_per_answer", 200, "usd_micros_per_answer"),
    ("expected_utilisation_pct", 60, "percent"),
    ("typical_answer_tokens_in", 4000, "tokens"),
    ("typical_answer_tokens_out", 300, "tokens"),
]
BILLING_SETTINGS = {
    "free_plan_code": "free",
    "trial_plan_code": "growth",
    "trial_days": 14,
    "grace_days": 7,
}


def upgrade() -> None:
    # --- Catalogue (global; written only by codura_platform) -----------------------------------
    op.execute("""
        CREATE TABLE plans (
            id          text PRIMARY KEY,
            code        citext NOT NULL UNIQUE,
            name        text NOT NULL,
            description text NOT NULL DEFAULT '',
            sort_order  int NOT NULL DEFAULT 0,
            is_public   boolean NOT NULL DEFAULT true,
            is_free     boolean NOT NULL DEFAULT false,
            status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
            current_version_id text,
            created_at  timestamptz NOT NULL DEFAULT now(),
            updated_at  timestamptz NOT NULL DEFAULT now()
        )""")
    op.execute("""
        CREATE TABLE plan_versions (
            id               text PRIMARY KEY,
            plan_id          text NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
            version          int NOT NULL,
            included         jsonb NOT NULL DEFAULT '{}'::jsonb,
            entitlements     jsonb NOT NULL DEFAULT '{}'::jsonb,
            overage_enabled  boolean NOT NULL DEFAULT false,
            default_model_profile_id text REFERENCES model_profiles(id),
            target_margin_pct numeric(5,2) NOT NULL DEFAULT 70 CHECK (target_margin_pct >= 0 AND target_margin_pct < 100),
            loss_leader      boolean NOT NULL DEFAULT false,
            used_at          timestamptz,
            created_by       text,
            created_at       timestamptz NOT NULL DEFAULT now(),
            UNIQUE (plan_id, version)
        )""")
    op.execute(
        "ALTER TABLE plans ADD CONSTRAINT plans_current_version_fk "
        "FOREIGN KEY (current_version_id) REFERENCES plan_versions(id) DEFERRABLE INITIALLY DEFERRED"
    )
    op.execute("""
        CREATE TABLE plan_prices (
            plan_version_id text NOT NULL REFERENCES plan_versions(id) ON DELETE CASCADE,
            currency        text NOT NULL CHECK (currency IN ('USD','INR')),
            base_micros     bigint NOT NULL DEFAULT 0 CHECK (base_micros >= 0),
            overage_micros  jsonb NOT NULL DEFAULT '{}'::jsonb,
            PRIMARY KEY (plan_version_id, currency)
        )""")
    # A Plan version that a Subscription has used is immutable (billing terms; FR-61).
    op.execute("""
        CREATE FUNCTION plan_versions_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.used_at IS NOT NULL AND (
                NEW.included IS DISTINCT FROM OLD.included OR
                NEW.entitlements IS DISTINCT FROM OLD.entitlements OR
                NEW.overage_enabled IS DISTINCT FROM OLD.overage_enabled OR
                NEW.target_margin_pct IS DISTINCT FROM OLD.target_margin_pct OR
                NEW.loss_leader IS DISTINCT FROM OLD.loss_leader OR
                NEW.plan_id IS DISTINCT FROM OLD.plan_id OR NEW.version IS DISTINCT FROM OLD.version)
            THEN RAISE EXCEPTION 'plan version % is in use and immutable; create a new version', OLD.id;
            END IF;
            RETURN NEW;
        END $$""")
    op.execute("""CREATE TRIGGER plan_versions_no_change BEFORE UPDATE ON plan_versions
                  FOR EACH ROW EXECUTE FUNCTION plan_versions_immutable()""")
    op.execute("""
        CREATE FUNCTION plan_prices_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE v text := COALESCE(OLD.plan_version_id, NEW.plan_version_id);
        BEGIN
            IF TG_OP <> 'INSERT' AND EXISTS (SELECT 1 FROM plan_versions WHERE id = v AND used_at IS NOT NULL)
            THEN RAISE EXCEPTION 'prices of plan version % are in use and immutable', v;
            END IF;
            RETURN COALESCE(NEW, OLD);
        END $$""")
    op.execute("""CREATE TRIGGER plan_prices_no_change BEFORE UPDATE OR DELETE ON plan_prices
                  FOR EACH ROW EXECUTE FUNCTION plan_prices_immutable()""")

    # --- Tenant billing tables -------------------------------------------------------------------
    op.execute("""
        CREATE TABLE billing_accounts (
            tenant_id      text PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
            country        char(2) CHECK (country ~ '^[A-Z]{2}$'),
            currency       text CHECK (currency IN ('USD','INR')),
            billing_email  citext,
            company_name   text,
            tax_id         text,
            address        jsonb NOT NULL DEFAULT '{}'::jsonb,
            provider       text NOT NULL DEFAULT 'manual',
            provider_customer_id text,
            currency_locked_at timestamptz,
            created_at     timestamptz NOT NULL DEFAULT now(),
            updated_at     timestamptz NOT NULL DEFAULT now(),
            UNIQUE (provider, provider_customer_id),
            CHECK (currency IS NULL OR country IS NOT NULL),
            CHECK (currency IS NULL OR (country = 'IN') = (currency = 'INR'))
        )""")
    op.execute("""
        CREATE TABLE subscriptions (
            id               text PRIMARY KEY,
            tenant_id        text NOT NULL UNIQUE REFERENCES tenants(id) ON DELETE CASCADE,
            plan_version_id  text NOT NULL REFERENCES plan_versions(id),
            status           text NOT NULL CHECK (status IN ('trialing','active','past_due')),
            period_start     date NOT NULL,
            trial_ends_at    timestamptz,
            pending_plan_version_id text REFERENCES plan_versions(id),
            cancel_at_period_end boolean NOT NULL DEFAULT false,
            spend_cap_micros bigint CHECK (spend_cap_micros IS NULL OR spend_cap_micros >= 0),
            payment_failed_at timestamptz,
            created_at       timestamptz NOT NULL DEFAULT now(),
            updated_at       timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id)
        )""")
    op.execute("""
        CREATE TABLE subscription_events (
            id              text PRIMARY KEY,
            tenant_id       text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            subscription_id text NOT NULL,
            action          text NOT NULL,
            from_plan_version_id text,
            to_plan_version_id   text,
            from_status     text,
            to_status       text,
            actor           text NOT NULL,
            details         jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at      timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (tenant_id, subscription_id) REFERENCES subscriptions(tenant_id, id) ON DELETE CASCADE
        )""")
    op.execute("""
        CREATE FUNCTION subscription_events_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'subscription_events is append-only'; END $$""")
    op.execute("""CREATE TRIGGER subscription_events_no_change BEFORE UPDATE ON subscription_events
                  FOR EACH ROW EXECUTE FUNCTION subscription_events_append_only()""")
    # Using a Plan version marks it immutable. SECURITY DEFINER: billing roles can't write the catalogue.
    op.execute("""
        CREATE FUNCTION subscriptions_mark_used() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        BEGIN
            UPDATE plan_versions SET used_at = now()
            WHERE used_at IS NULL AND id IN (NEW.plan_version_id, NEW.pending_plan_version_id);
            RETURN NEW;
        END $$""")
    op.execute("""CREATE TRIGGER subscriptions_used AFTER INSERT OR UPDATE OF plan_version_id, pending_plan_version_id
                  ON subscriptions FOR EACH ROW EXECUTE FUNCTION subscriptions_mark_used()""")
    op.execute("""
        CREATE TABLE tenant_billing_overrides (
            tenant_id       text PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
            included        jsonb NOT NULL DEFAULT '{}'::jsonb,
            entitlements    jsonb NOT NULL DEFAULT '{}'::jsonb,
            overage_enabled boolean,
            note            text NOT NULL DEFAULT '',
            updated_by      text,
            updated_at      timestamptz NOT NULL DEFAULT now()
        )""")
    op.execute("""
        CREATE TABLE quota_reservations (
            id           text PRIMARY KEY,
            tenant_id    text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            meter        text NOT NULL,
            period_start date NOT NULL,
            quantity     numeric NOT NULL CHECK (quantity > 0),
            expires_at   timestamptz NOT NULL,
            created_at   timestamptz NOT NULL DEFAULT now()
        )""")
    op.execute(
        "CREATE INDEX quota_reservations_live ON quota_reservations (tenant_id, meter, period_start, expires_at)"
    )
    op.execute("""
        CREATE TABLE quota_notices (
            tenant_id    text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            meter        text NOT NULL,
            period_start date NOT NULL,
            threshold    smallint NOT NULL CHECK (threshold IN (80, 100)),
            created_at   timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, meter, period_start, threshold)
        )""")
    op.execute(
        "ALTER TABLE usage_counters ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now()"
    )
    op.execute("""
        CREATE TABLE usage_rollups (
            tenant_id        text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            day              date NOT NULL,
            meter            text NOT NULL,
            model_profile_id text NOT NULL DEFAULT '',
            quantity         numeric NOT NULL DEFAULT 0,
            cogs_micros      bigint NOT NULL DEFAULT 0,
            events           int NOT NULL DEFAULT 0,
            computed_at      timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, day, meter, model_profile_id)
        )""")
    op.execute("CREATE SEQUENCE invoice_number_seq START 1001")
    op.execute("""
        CREATE TABLE invoices (
            id              text PRIMARY KEY,
            tenant_id       text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            number          text NOT NULL UNIQUE,
            subscription_id text,
            kind            text NOT NULL CHECK (kind IN ('period','proration','adjustment')),
            period_start    date NOT NULL,
            currency        text NOT NULL CHECK (currency IN ('USD','INR')),
            status          text NOT NULL CHECK (status IN ('draft','open','paid','void','uncollectible')),
            subtotal_minor  bigint NOT NULL DEFAULT 0,
            total_minor     bigint NOT NULL DEFAULT 0,
            issued_at       timestamptz,
            due_at          timestamptz,
            paid_at         timestamptz,
            provider        text NOT NULL,
            provider_invoice_id text,
            hosted_url      text,
            idempotency_key text NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, idempotency_key)
        )""")
    op.execute("CREATE INDEX invoices_tenant_period ON invoices (tenant_id, period_start DESC)")
    op.execute("""
        CREATE TABLE invoice_lines (
            id           text PRIMARY KEY,
            tenant_id    text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            invoice_id   text NOT NULL,
            kind         text NOT NULL CHECK (kind IN ('base','overage','proration_credit','proration_charge','adjustment')),
            meter        text,
            description  text NOT NULL,
            quantity     numeric NOT NULL DEFAULT 1,
            unit_micros  bigint NOT NULL DEFAULT 0,
            amount_minor bigint NOT NULL,
            sort         int NOT NULL DEFAULT 0,
            FOREIGN KEY (tenant_id, invoice_id) REFERENCES invoices(tenant_id, id) ON DELETE CASCADE
        )""")
    op.execute("""
        CREATE TABLE payments (
            id            text PRIMARY KEY,
            tenant_id     text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            invoice_id    text NOT NULL,
            provider      text NOT NULL,
            provider_payment_id text,
            amount_minor  bigint NOT NULL,
            currency      text NOT NULL CHECK (currency IN ('USD','INR')),
            status        text NOT NULL CHECK (status IN ('succeeded','failed','refunded')),
            method        text NOT NULL DEFAULT '',
            recorded_by   text,
            note          text NOT NULL DEFAULT '',
            created_at    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (provider, provider_payment_id),
            FOREIGN KEY (tenant_id, invoice_id) REFERENCES invoices(tenant_id, id) ON DELETE CASCADE
        )""")
    # Webhook dedupe: global, no Tenant data, no payload (ADR-0026 §6, SEC-20).
    op.execute("""
        CREATE TABLE payment_events (
            provider       text NOT NULL,
            event_id       text NOT NULL,
            event_type     text NOT NULL,
            payload_sha256 text NOT NULL,
            status         text NOT NULL DEFAULT 'received' CHECK (status IN ('received','processed','failed','ignored')),
            error          text,
            received_at    timestamptz NOT NULL DEFAULT now(),
            processed_at   timestamptz,
            PRIMARY KEY (provider, event_id)
        )""")

    # Worker scheduler slots: one enqueue per (name, window) across all workers (specs/billing.md §7).
    op.execute("""
        CREATE TABLE scheduled_runs (
            name        text NOT NULL,
            run_window  text NOT NULL,
            enqueued_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (name, run_window)
        )""")

    for table in (*BILLING_TABLES, *METERING_TABLES):
        for stmt in tenant_rls_sql(table):
            op.execute(stmt)

    # --- Lookups that run before a Tenant is known (SECURITY DEFINER, narrow) -------------------
    op.execute("""
        CREATE FUNCTION billing_tenant_for_customer(p_provider text, p_customer_id text) RETURNS text
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            SELECT tenant_id FROM billing_accounts WHERE provider = p_provider AND provider_customer_id = p_customer_id
        $$""")
    op.execute("""
        CREATE FUNCTION billing_due_tenants(p_now timestamptz) RETURNS TABLE (tenant_id text)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            SELECT s.tenant_id FROM subscriptions s
            WHERE s.period_start < date_trunc('month', p_now AT TIME ZONE 'UTC')::date
               OR (s.status = 'trialing' AND s.trial_ends_at <= p_now)
               OR s.payment_failed_at IS NOT NULL
            ORDER BY s.tenant_id
        $$""")
    op.execute("""
        CREATE FUNCTION billing_tenants_with_usage(p_day date) RETURNS TABLE (tenant_id text)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            SELECT DISTINCT tenant_id FROM usage_events
            WHERE occurred_at >= p_day AND occurred_at < p_day + 1
            UNION SELECT id FROM tenants WHERE status IN ('active','past_due')
        $$""")
    for fn in (
        "billing_tenant_for_customer(text, text)",
        "billing_due_tenants(timestamptz)",
        "billing_tenants_with_usage(date)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {fn} TO {WORKER}, {PLATFORM_ROLE}")

    # Verified signup now starts the Trial (billing.md §7): create the Subscription atomically.
    op.execute("DROP FUNCTION activate_tenants_of_admin(text)")
    op.execute("""
        CREATE FUNCTION activate_tenants_of_admin(p_user_id text) RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        DECLARE cfg jsonb := (SELECT value FROM system_settings WHERE key = 'billing');
        BEGIN
            WITH activated AS (
                UPDATE tenants SET status = 'active', updated_at = now()
                WHERE status = 'pending_verification'
                  AND id IN (SELECT tenant_id FROM tenant_members WHERE user_id = p_user_id AND role = 'admin')
                RETURNING id
            )
            INSERT INTO subscriptions (id, tenant_id, plan_version_id, status, period_start, trial_ends_at)
            SELECT 'sub_' || a.id, a.id, p.current_version_id, 'trialing',
                   date_trunc('month', now() AT TIME ZONE 'UTC')::date,
                   now() + make_interval(days => COALESCE((cfg->>'trial_days')::int, 14))
            FROM activated a JOIN plans p ON p.code = COALESCE(cfg->>'free_plan_code', 'free')
            ON CONFLICT (tenant_id) DO NOTHING;
        END $$""")
    op.execute("REVOKE ALL ON FUNCTION activate_tenants_of_admin(text) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION activate_tenants_of_admin(text) TO {APP}")

    # --- Seed catalogue, rates and settings ------------------------------------------------------
    for plan in SEED_PLANS:
        code = str(plan["code"])
        pid, vid = f"plan_{code}", f"plv_{code}_1"
        op.execute(
            "INSERT INTO plans (id, code, name, sort_order, is_public, is_free, current_version_id) "
            f"VALUES ('{pid}', '{code}', '{plan['name']}', {plan['sort']}, {plan['public']}, "
            f"{plan['free']}, '{vid}')"
        )
        included = json.dumps(plan["included"]).replace("'", "''")
        ent = json.dumps(plan["ent"]).replace("'", "''")
        op.execute(
            "INSERT INTO plan_versions (id, plan_id, version, included, entitlements, overage_enabled, "
            f"loss_leader, created_by) VALUES ('{vid}', '{pid}', 1, '{included}'::jsonb, '{ent}'::jsonb, "
            f"{plan['overage']}, {plan['loss_leader']}, 'migration')"
        )
        prices = plan["prices"]
        assert isinstance(prices, dict)
        for currency, (base, overage) in prices.items():
            op.execute(
                "INSERT INTO plan_prices (plan_version_id, currency, base_micros, overage_micros) "
                f"VALUES ('{vid}', '{currency}', {base}, '{json.dumps(overage)}'::jsonb)"
            )
    for key, micros, unit in SEED_RATES:
        op.execute(
            f"INSERT INTO cost_rates (key, micros, unit) VALUES ('{key}', {micros}, '{unit}') "
            "ON CONFLICT (key) DO NOTHING"
        )
    op.execute(
        "INSERT INTO system_settings (key, value) VALUES "
        f"('billing', '{json.dumps(BILLING_SETTINGS)}'::jsonb) ON CONFLICT (key) DO NOTHING"
    )
    # Existing active Tenants start a Trial from their signup date (or Free if it already ended).
    op.execute("""
        INSERT INTO subscriptions (id, tenant_id, plan_version_id, status, period_start, trial_ends_at)
        SELECT 'sub_' || t.id, t.id, 'plv_free_1',
               CASE WHEN t.created_at + interval '14 days' > now() THEN 'trialing' ELSE 'active' END,
               date_trunc('month', now() AT TIME ZONE 'UTC')::date,
               CASE WHEN t.created_at + interval '14 days' > now() THEN t.created_at + interval '14 days' END
        FROM tenants t WHERE t.status IN ('active','past_due')""")

    # --- Grants (ADR-0026 §4) ---------------------------------------------------------------------
    for table in ("plans", "plan_versions", "plan_prices"):
        op.execute(grant_sql(table, "SELECT", ALL))
        op.execute(grant_sql(table, "INSERT, UPDATE, DELETE", (PLATFORM_ROLE,)))
    for table in BILLING_TABLES:
        op.execute(grant_sql(table, "SELECT", ALL))
        op.execute(grant_sql(table, "INSERT, UPDATE, DELETE", (WORKER, PLATFORM_ROLE)))
    for table in METERING_TABLES:
        op.execute(grant_sql(table, "SELECT, INSERT, UPDATE, DELETE", ALL))
    op.execute(grant_sql("payment_events", "SELECT, INSERT, UPDATE", (WORKER, PLATFORM_ROLE)))
    op.execute(grant_sql("scheduled_runs", "SELECT, INSERT, DELETE", (WORKER, PLATFORM_ROLE)))
    op.execute(f"GRANT USAGE ON SEQUENCE invoice_number_seq TO {WORKER}, {PLATFORM_ROLE}")
    # past-due (billing.md §7): billing may flip its own Tenant's status, nothing else.
    op.execute(f"GRANT UPDATE (status, updated_at) ON tenants TO {WORKER}")


def downgrade() -> None:
    op.execute(f"REVOKE UPDATE (status, updated_at) ON tenants FROM {WORKER}")
    op.execute("DROP FUNCTION IF EXISTS activate_tenants_of_admin(text)")
    op.execute("""
        CREATE FUNCTION activate_tenants_of_admin(p_user_id text) RETURNS void
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
            UPDATE tenants SET status = 'active', updated_at = now()
            WHERE status = 'pending_verification'
              AND id IN (SELECT tenant_id FROM tenant_members WHERE user_id = p_user_id AND role = 'admin')
        $$""")
    op.execute("REVOKE ALL ON FUNCTION activate_tenants_of_admin(text) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION activate_tenants_of_admin(text) TO {APP}")
    for fn in (
        "billing_tenant_for_customer(text, text)",
        "billing_due_tenants(timestamptz)",
        "billing_tenants_with_usage(date)",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {fn}")
    for table in (
        "scheduled_runs",
        "payment_events",
        "payments",
        "invoice_lines",
        "invoices",
        "usage_rollups",
        "quota_notices",
        "quota_reservations",
        "tenant_billing_overrides",
        "subscription_events",
        "subscriptions",
        "billing_accounts",
        "plan_prices",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("ALTER TABLE plans DROP CONSTRAINT IF EXISTS plans_current_version_fk")
    op.execute("DROP TABLE IF EXISTS plan_versions CASCADE")
    op.execute("DROP TABLE IF EXISTS plans CASCADE")
    op.execute("DROP SEQUENCE IF EXISTS invoice_number_seq")
    for fn in (
        "plan_versions_immutable()",
        "plan_prices_immutable()",
        "subscriptions_mark_used()",
        "subscription_events_append_only()",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {fn}")
    op.execute("ALTER TABLE usage_counters DROP COLUMN IF EXISTS updated_at")
    op.execute("DELETE FROM system_settings WHERE key = 'billing'")
    for key, _, _ in SEED_RATES:
        op.execute(f"DELETE FROM cost_rates WHERE key = '{key}'")
