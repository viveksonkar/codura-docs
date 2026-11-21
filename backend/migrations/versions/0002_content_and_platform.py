"""Content, answering, models, metering, sessions and keys (specs/db.md §2, §4–7; M1-00).

Revision ID: 0002_content
Revises: 0001_baseline
Create Date: 2026-11-19
"""

from __future__ import annotations

from alembic import op

from codura_docs.db.rls import APP_ROLES, PLATFORM_ROLE, grant_sql, tenant_rls_sql

revision = "0002_content"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

ALL = (*APP_ROLES, PLATFORM_ROLE)
TENANT_TABLES = (
    "knowledge_bases",
    "documents",
    "chunks",
    "assistants",
    "conversations",
    "messages",
    "api_keys",
    "usage_events",
    "usage_counters",
)


def upgrade() -> None:
    # --- Platform-managed models and cost inputs (ADR-0018/0019/0021) -------------------------
    op.execute("""
        CREATE TABLE model_profiles (
            id               text PRIMARY KEY,
            tenant_id        text REFERENCES tenants(id) ON DELETE CASCADE,  -- set = Custom LLM (M3)
            provider         text NOT NULL CHECK (provider IN ('ollama','openai_compat','anthropic','fake')),
            model            text NOT NULL,
            base_url         text,
            params           jsonb NOT NULL DEFAULT '{}'::jsonb,
            license          text NOT NULL CHECK (license IN ('apache-2.0','mit','commercial-api')),
            is_local         boolean NOT NULL,
            credentials_enc  bytea,
            price_in_per_mtok_micros  bigint NOT NULL DEFAULT 0,
            price_out_per_mtok_micros bigint NOT NULL DEFAULT 0,
            status           text NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
            created_at       timestamptz NOT NULL DEFAULT now(),
            updated_at       timestamptz NOT NULL DEFAULT now()
        )""")
    # Platform profiles (tenant_id NULL) are readable by all; a Custom LLM only by its own Tenant.
    op.execute("ALTER TABLE model_profiles ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE model_profiles FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY tenant_isolation ON model_profiles
                  USING (tenant_id IS NULL OR tenant_id = current_setting('app.tenant_id', true))
                  WITH CHECK (tenant_id = current_setting('app.tenant_id', true))""")
    op.execute(
        f"CREATE POLICY platform_access ON model_profiles TO {PLATFORM_ROLE} USING (true) WITH CHECK (true)"
    )
    op.execute(
        "ALTER TABLE tenants ADD COLUMN model_profile_override text REFERENCES model_profiles(id)"
    )
    op.execute("""
        CREATE TABLE cost_rates (
            key        text PRIMARY KEY,
            micros     bigint NOT NULL,
            unit       text NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now()
        )""")
    op.execute("""
        INSERT INTO cost_rates (key, micros, unit) VALUES
            ('embed_per_mtok', 20000, 'usd_micros_per_1m_tokens'),
            ('ocr_per_page', 300, 'usd_micros_per_page'),
            ('storage_per_mb_day', 1, 'usd_micros_per_mb_day')""")

    # Tenants: readable by id from verified credentials; only the own Tenant is updatable.
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenants FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenants_read ON tenants FOR SELECT USING (true)")
    op.execute("""CREATE POLICY tenants_update_own ON tenants FOR UPDATE
                  USING (id = current_setting('app.tenant_id', true))
                  WITH CHECK (id = current_setting('app.tenant_id', true))""")
    op.execute(
        f"CREATE POLICY platform_access ON tenants TO {PLATFORM_ROLE} USING (true) WITH CHECK (true)"
    )
    op.execute(
        "GRANT UPDATE (name, ocr_languages, default_language, updated_at) ON tenants TO codura_app"
    )

    # --- Sessions and keys ---------------------------------------------------------------------
    op.execute("""
        CREATE TABLE sessions (
            id          text PRIMARY KEY,             -- sha256 of the cookie token
            user_id     text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            -- The Tenant the User is currently working in (membership re-verified per request);
            -- not ownership, so not RLS-scoped: sessions are looked up before a Tenant is known.
            current_tenant_id text REFERENCES tenants(id) ON DELETE SET NULL,
            csrf_token  text NOT NULL,
            expires_at  timestamptz NOT NULL,
            created_at  timestamptz NOT NULL DEFAULT now(),
            last_seen_at timestamptz NOT NULL DEFAULT now()
        )""")
    op.execute("CREATE INDEX sessions_user ON sessions (user_id)")

    # --- Content (specs/db.md §4) --------------------------------------------------------------
    op.execute("""
        CREATE TABLE knowledge_bases (
            id          text PRIMARY KEY,
            tenant_id   text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            name        text NOT NULL,
            description text NOT NULL DEFAULT '',
            created_at  timestamptz NOT NULL DEFAULT now(),
            updated_at  timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id)
        )""")
    op.execute("""
        CREATE TABLE documents (
            id                text PRIMARY KEY,
            tenant_id         text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            knowledge_base_id text NOT NULL,
            title             text NOT NULL,
            filename          text NOT NULL,
            mime_type         text NOT NULL,
            size_bytes        bigint NOT NULL,
            sha256            char(64) NOT NULL,
            blob_key          text NOT NULL,
            status            text NOT NULL DEFAULT 'pending'
                              CHECK (status IN ('pending','processing','ready','failed')),
            error             text,
            warnings          text[] NOT NULL DEFAULT '{}',
            language          text,
            ocr_languages     text,
            page_count        int,
            pages_done        int NOT NULL DEFAULT 0,
            ocr_pages         int NOT NULL DEFAULT 0,
            chunk_count       int NOT NULL DEFAULT 0,
            ingest_count      int NOT NULL DEFAULT 0,
            ingested_at       timestamptz,
            created_by        text REFERENCES users(id) ON DELETE SET NULL,
            created_at        timestamptz NOT NULL DEFAULT now(),
            updated_at        timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id),
            UNIQUE (knowledge_base_id, sha256),
            FOREIGN KEY (tenant_id, knowledge_base_id)
                REFERENCES knowledge_bases(tenant_id, id) ON DELETE CASCADE
        )""")
    op.execute("""
        CREATE TABLE chunks (
            id                text PRIMARY KEY,
            tenant_id         text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            document_id       text NOT NULL,
            knowledge_base_id text NOT NULL,
            ordinal           int NOT NULL,
            text              text NOT NULL,
            token_count       int NOT NULL,
            language          text,
            page_start        int,
            page_end          int,
            heading_path      text[] NOT NULL DEFAULT '{}',
            ocr_confidence    real,
            embedding         vector(1024) NOT NULL,
            tsv               tsvector NOT NULL,
            created_at        timestamptz NOT NULL DEFAULT now(),
            UNIQUE (document_id, ordinal),
            FOREIGN KEY (tenant_id, document_id) REFERENCES documents(tenant_id, id) ON DELETE CASCADE
        )""")
    op.execute("""CREATE INDEX chunks_embedding_hnsw ON chunks
                  USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)""")
    op.execute("CREATE INDEX chunks_tsv ON chunks USING gin (tsv)")
    op.execute("CREATE INDEX chunks_tenant_kb ON chunks (tenant_id, knowledge_base_id)")

    # --- Assistants and Conversations (specs/db.md §5) -----------------------------------------
    op.execute("""
        CREATE TABLE assistants (
            id                   text PRIMARY KEY,
            tenant_id            text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            slug                 citext NOT NULL,
            name                 text NOT NULL,
            knowledge_base_ids   text[] NOT NULL DEFAULT '{}',
            system_prompt        text NOT NULL DEFAULT '',
            default_language     text NOT NULL DEFAULT 'en',
            welcome_text         jsonb NOT NULL DEFAULT '{}'::jsonb,
            fallback_text        jsonb NOT NULL DEFAULT '{}'::jsonb,
            limit_reached_text   jsonb NOT NULL DEFAULT '{}'::jsonb,
            visitor_email        text NOT NULL DEFAULT 'off' CHECK (visitor_email IN ('off','optional','required')),
            visitor_email_consent jsonb NOT NULL DEFAULT '{}'::jsonb,
            privacy_url          text,
            retrieval            jsonb NOT NULL DEFAULT '{}'::jsonb,
            theme                jsonb NOT NULL DEFAULT '{}'::jsonb,
            hosted_enabled       boolean NOT NULL DEFAULT false,
            created_at           timestamptz NOT NULL DEFAULT now(),
            updated_at           timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, slug)
        )""")
    op.execute("""
        CREATE FUNCTION assistants_check_kbs() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        BEGIN
            IF EXISTS (SELECT 1 FROM unnest(NEW.knowledge_base_ids) AS kb(id)
                       WHERE NOT EXISTS (SELECT 1 FROM knowledge_bases k
                                         WHERE k.id = kb.id AND k.tenant_id = NEW.tenant_id)) THEN
                RAISE EXCEPTION 'assistant knowledge bases must belong to the same tenant'
                    USING ERRCODE = 'foreign_key_violation';
            END IF;
            RETURN NEW;
        END $$""")
    op.execute("""CREATE TRIGGER assistants_kbs_same_tenant BEFORE INSERT OR UPDATE OF knowledge_base_ids
                  ON assistants FOR EACH ROW EXECUTE FUNCTION assistants_check_kbs()""")

    op.execute("""
        CREATE TABLE api_keys (
            id                 text PRIMARY KEY,
            tenant_id          text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            kind               text NOT NULL CHECK (kind IN ('secret','channel')),
            prefix             text NOT NULL,
            key_hash           char(64) NOT NULL UNIQUE,
            name               text NOT NULL DEFAULT '',
            channel            text CHECK (channel IN ('hosted','widget','wordpress','android','api')),
            assistant_id       text,
            allowed_origins    text[] NOT NULL DEFAULT '{}',
            rate_limit_per_min int,
            revoked_at         timestamptz,
            last_used_at       timestamptz,
            created_at         timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (tenant_id, assistant_id) REFERENCES assistants(tenant_id, id) ON DELETE CASCADE
        )""")

    op.execute("""
        CREATE TABLE conversations (
            id                       text PRIMARY KEY,
            tenant_id                text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            assistant_id             text NOT NULL,
            channel                  text NOT NULL,
            channel_key_id           text,
            visitor_id               text,
            user_id                  text REFERENCES users(id) ON DELETE SET NULL,
            last_language            text,
            visitor_email_enc        bytea,
            visitor_email_hash       char(64),
            visitor_email_consent_at timestamptz,
            started_at               timestamptz NOT NULL DEFAULT now(),
            last_message_at          timestamptz NOT NULL DEFAULT now(),
            message_count            int NOT NULL DEFAULT 0,
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, assistant_id) REFERENCES assistants(tenant_id, id) ON DELETE CASCADE
        )""")
    op.execute("CREATE INDEX conv_visitor ON conversations (tenant_id, assistant_id, visitor_id)")
    op.execute("""
        CREATE TABLE messages (
            id                text PRIMARY KEY,
            tenant_id         text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            conversation_id   text NOT NULL,
            role              text NOT NULL CHECK (role IN ('user','assistant')),
            content           text NOT NULL,
            language          text,
            standalone_query  text,
            citations         jsonb NOT NULL DEFAULT '[]'::jsonb,
            retrieval         jsonb,
            model_profile_id  text,
            model             text,
            prompt_tokens     int,
            completion_tokens int,
            cogs_micros       bigint,
            outcome           text CHECK (outcome IN ('answered','fallback','limit','error')),
            latency_ms        int,
            first_token_ms    int,
            finish_reason     text,
            feedback          smallint CHECK (feedback IN (-1, 1)),
            feedback_note     text,
            created_at        timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversations(tenant_id, id) ON DELETE CASCADE
        )""")
    op.execute("CREATE INDEX messages_conversation ON messages (conversation_id, created_at)")

    # --- Metering (specs/db.md §7, ADR-0021) ---------------------------------------------------
    op.execute("""
        CREATE TABLE usage_events (
            id               text PRIMARY KEY,
            tenant_id        text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            meter            text NOT NULL,
            quantity         numeric NOT NULL,
            cogs_micros      bigint NOT NULL DEFAULT 0,
            model_profile_id text,
            idempotency_key  text NOT NULL,
            attrs            jsonb NOT NULL DEFAULT '{}'::jsonb,
            occurred_at      timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, meter, idempotency_key)
        )""")
    op.execute("CREATE INDEX usage_events_tenant_time ON usage_events (tenant_id, occurred_at)")
    op.execute("""
        CREATE TABLE usage_counters (
            tenant_id    text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            meter        text NOT NULL,
            period_start date NOT NULL,
            quantity     numeric NOT NULL DEFAULT 0,
            cogs_micros  bigint NOT NULL DEFAULT 0,
            PRIMARY KEY (tenant_id, meter, period_start)
        )""")
    op.execute("""
        CREATE FUNCTION usage_events_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'usage_events is append-only'; END $$""")
    op.execute("""CREATE TRIGGER usage_events_no_change BEFORE UPDATE OR DELETE ON usage_events
                  FOR EACH ROW WHEN (current_setting('app.allow_usage_purge', true) IS DISTINCT FROM 'on')
                  EXECUTE FUNCTION usage_events_append_only()""")

    for table in TENANT_TABLES:
        for stmt in tenant_rls_sql(table):
            op.execute(stmt)

    # --- Credential lookups that run before a Tenant is known (SECURITY DEFINER, narrow) -------
    op.execute("""
        CREATE FUNCTION resolve_api_key(p_hash text)
        RETURNS TABLE (key_id text, tenant_id text, kind text, channel text, assistant_id text,
                       allowed_origins text[], revoked boolean, tenant_status text)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            SELECT k.id, k.tenant_id, k.kind, k.channel, k.assistant_id, k.allowed_origins,
                   k.revoked_at IS NOT NULL, t.status
            FROM api_keys k JOIN tenants t ON t.id = k.tenant_id
            WHERE k.key_hash = p_hash
        $$""")
    op.execute("""
        CREATE FUNCTION resolve_hosted(p_tenant_slug text, p_assistant_slug text)
        RETURNS TABLE (tenant_id text, assistant_id text)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            SELECT a.tenant_id, a.id FROM assistants a JOIN tenants t ON t.id = a.tenant_id
            WHERE t.slug = p_tenant_slug::citext AND a.slug = p_assistant_slug::citext
              AND a.hosted_enabled AND t.status = 'active'
        $$""")
    op.execute("""
        CREATE FUNCTION user_memberships(p_user_id text)
        RETURNS TABLE (tenant_id text, role text, tenant_name text, tenant_slug text, tenant_status text)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            SELECT m.tenant_id, m.role, t.name, t.slug::text, t.status
            FROM tenant_members m JOIN tenants t ON t.id = m.tenant_id
            WHERE m.user_id = p_user_id ORDER BY t.name
        $$""")
    # Self-service signup (ADR-0020): create Tenant + first Tenant Admin atomically.
    op.execute("""
        CREATE FUNCTION signup_create_tenant(p_tenant_id text, p_slug text, p_name text, p_user_id text)
        RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        BEGIN
            INSERT INTO tenants (id, slug, name, status) VALUES (p_tenant_id, p_slug, p_name, 'pending_verification');
            INSERT INTO tenant_members (tenant_id, user_id, role) VALUES (p_tenant_id, p_user_id, 'admin');
        END $$""")
    op.execute("""
        CREATE FUNCTION activate_tenants_of_admin(p_user_id text) RETURNS void
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
            UPDATE tenants SET status = 'active', updated_at = now()
            WHERE status = 'pending_verification'
              AND id IN (SELECT tenant_id FROM tenant_members WHERE user_id = p_user_id AND role = 'admin')
        $$""")
    op.execute("""
        CREATE FUNCTION accept_invite(p_token_hash text, p_user_id text)
        RETURNS TABLE (tenant_id text, role text)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        #variable_conflict use_column
        DECLARE inv invites%ROWTYPE;
        BEGIN
            SELECT * INTO inv FROM invites WHERE token_hash = p_token_hash
              AND accepted_at IS NULL AND expires_at > now() FOR UPDATE;
            IF NOT FOUND THEN RETURN; END IF;
            UPDATE invites SET accepted_at = now() WHERE id = inv.id;
            INSERT INTO tenant_members (tenant_id, user_id, role) VALUES (inv.tenant_id, p_user_id, inv.role)
            ON CONFLICT (tenant_id, user_id) DO NOTHING;
            RETURN QUERY SELECT inv.tenant_id, inv.role;
        END $$""")
    op.execute("""
        CREATE FUNCTION invite_email(p_token_hash text) RETURNS text
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            SELECT email::text FROM invites WHERE token_hash = p_token_hash
              AND accepted_at IS NULL AND expires_at > now()
        $$""")
    for fn in (
        "resolve_api_key(text)",
        "resolve_hosted(text, text)",
        "user_memberships(text)",
        "signup_create_tenant(text, text, text, text)",
        "activate_tenants_of_admin(text)",
        "accept_invite(text, text)",
        "invite_email(text)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {fn} TO codura_app")

    # --- Grants --------------------------------------------------------------------------------
    op.execute(grant_sql("model_profiles", "SELECT", ALL))
    op.execute(grant_sql("model_profiles", "INSERT, UPDATE, DELETE", (PLATFORM_ROLE,)))
    op.execute(grant_sql("cost_rates", "SELECT", ALL))
    op.execute(grant_sql("cost_rates", "INSERT, UPDATE", (PLATFORM_ROLE,)))
    op.execute(
        grant_sql("sessions", "SELECT, INSERT, UPDATE, DELETE", ("codura_app", PLATFORM_ROLE))
    )
    for table in (
        "knowledge_bases",
        "documents",
        "chunks",
        "assistants",
        "conversations",
        "messages",
        "api_keys",
    ):
        op.execute(grant_sql(table, "SELECT, INSERT, UPDATE, DELETE", ALL))
    op.execute(grant_sql("usage_events", "SELECT, INSERT", ALL))
    op.execute(grant_sql("usage_counters", "SELECT, INSERT, UPDATE", ALL))
    op.execute(grant_sql("jobs", "SELECT", (PLATFORM_ROLE,)))
    op.execute(grant_sql("auth_tokens", "SELECT, INSERT", (PLATFORM_ROLE,)))


def downgrade() -> None:
    for fn in (
        "resolve_api_key(text)",
        "resolve_hosted(text, text)",
        "user_memberships(text)",
        "signup_create_tenant(text, text, text, text)",
        "activate_tenants_of_admin(text)",
        "accept_invite(text, text)",
        "invite_email(text)",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {fn}")
    for table in (
        "usage_counters",
        "usage_events",
        "messages",
        "conversations",
        "api_keys",
        "assistants",
        "chunks",
        "documents",
        "knowledge_bases",
        "sessions",
        "cost_rates",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS model_profile_override")
    op.execute("DROP TABLE IF EXISTS model_profiles CASCADE")
