# Spec — Database

**Status:** Draft v0.3 · **Last updated:** 2026-11-19 · **Frozen by:** M0-03 (§1–3), M1-00 (§4–7) · **ADR:** 0003, 0006, 0014, 0015, 0019, 0020, 0021, 0023

Postgres 16 + `vector` + `pgcrypto` + `citext`. Alembic migrations in `backend/migrations/` become
normative once they exist. The DDL below is illustrative; the names and types are the contract.

## 1. Conventions and tenant isolation

- IDs: `text` primary keys holding prefixed ULIDs (`tnt_`, `usr_`, `kb_`, `doc_`, `chk_`,
  `asst_`, `key_`, `conv_`, `msg_`, `job_`), generated in Python.
- Every tenant table has `tenant_id text NOT NULL REFERENCES tenants(id) ON DELETE CASCADE`,
  `created_at timestamptz NOT NULL DEFAULT now()`, and `updated_at` where mutable.
- **Composite keys:** tenant tables have `UNIQUE (tenant_id, id)`, and child FKs reference
  `(tenant_id, parent_id)`. A Chunk can't point at another Tenant's Document even through an
  application bug.
- **RLS on every tenant table**, fail-closed:
  ```sql
  ALTER TABLE <t> ENABLE ROW LEVEL SECURITY;
  ALTER TABLE <t> FORCE ROW LEVEL SECURITY;
  CREATE POLICY tenant_isolation ON <t>
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
  ```
  An unset `app.tenant_id` yields NULL and matches nothing. The app connects as `codura_app` (no
  `BYPASSRLS`, not owner). Migrations run as `codura_owner`. Platform Admin routes use a separate
  role, `codura_platform`, with explicit policies, only from `codura_docs.platform`.
- Migration test: fails if any table with a `tenant_id` column lacks an enabled, forced policy.
- Enums are stored as `text` + `CHECK`.

## 2. Platform, tenancy and auth (M0-03; `sessions`, `api_keys` in M1-00)

```sql
system_settings(key text PK, value jsonb)          -- 'embedding' → {provider, model, dimension}; written once (FR-25)

tenants(id PK, slug citext UNIQUE, name,
        status CHECK (status IN ('pending_verification','active','past_due','suspended')),
        plan_code text DEFAULT 'trial',            -- DEPRECATED (M3): the Plan comes from subscriptions (specs/billing.md)
        model_profile_override text NULL REFERENCES model_profiles(id),   -- Platform Admin only (ADR-0019)
        ocr_languages text DEFAULT 'eng', default_language text DEFAULT 'en',
        created_at, updated_at)                    -- not RLS'd; read via platform role or by id from a verified credential

users(id PK, email citext UNIQUE, email_verified_at NULL, name, password_hash,
      is_platform_admin bool DEFAULT false, created_at, last_login_at)
tenant_members(tenant_id, user_id, role CHECK (role IN ('admin','user')),     -- ADR-0020
               PRIMARY KEY (tenant_id, user_id))   -- RLS'd; trigger keeps ≥ 1 admin per tenant
invites(id PK, tenant_id, email, role, token_hash, expires_at, accepted_at NULL, created_by)   -- RLS'd
auth_tokens(id PK, user_id, purpose CHECK (purpose IN ('verify_email','reset_password')),
            token_hash, expires_at, used_at NULL, created_at)                  -- single-use
platform_audit(id PK, actor_user_id, action, target_tenant_id NULL, details jsonb, created_at)  -- append-only

sessions(id PK /* sha256(token) */, user_id, current_tenant_id NULL, csrf_token, expires_at, created_at, last_seen_at)
         -- current_tenant_id is the working Tenant, not ownership; membership is re-verified per request.
         -- Not RLS-scoped: looked up before a Tenant is known.
api_keys(id PK, tenant_id, kind CHECK (kind IN ('secret','channel')), prefix, key_hash UNIQUE,
         name, channel CHECK (channel IN ('hosted','widget','wordpress','android','api')) NULL,
         assistant_id NULL, allowed_origins text[] DEFAULT '{}', rate_limit_per_min int NULL,
         revoked_at NULL, last_used_at NULL, created_at,
         public_key_enc bytea NULL,  -- hosted keys only, crypto.py-encrypted (ADR-0025, migration 0003)
         FOREIGN KEY (tenant_id, assistant_id) REFERENCES assistants(tenant_id, id))
```

Key lookup (`WHERE key_hash = $1`) happens **before** a Tenant is known. It runs through one
narrow `SECURITY DEFINER` function, `resolve_api_key(hash) → (key_id, tenant_id, kind, channel,
assistant_id, allowed_origins, revoked)`, so `codura_app` never needs cross-Tenant read access.
The hosted-page lookup works the same way through `resolve_hosted(tenant_slug, assistant_slug) →
(tenant_id, assistant_id, public_key_enc)`. It returns a row only for an enabled Assistant of an
active Tenant with an active `hosted` key; the partial unique index `api_keys_one_hosted` allows at
most one per Assistant. `touch_api_key(key_id)` updates `last_used_at` at most once a minute.

## 3. Jobs (M0-03 table, M0-06 behaviour)

```sql
jobs(id PK, tenant_id NULL, kind text, payload jsonb,
     status CHECK (status IN ('queued','running','succeeded','failed','cancelled')) DEFAULT 'queued',
     attempts int DEFAULT 0, max_attempts int DEFAULT 3, run_after timestamptz DEFAULT now(),
     locked_by text NULL, locked_until timestamptz NULL, last_error text NULL,
     created_at, updated_at, finished_at NULL)
CREATE INDEX jobs_claim ON jobs (run_after) WHERE status = 'queued';
```

Claim with `FOR UPDATE SKIP LOCKED` (ADR-0006) as the `codura_worker` role, which can read only
`jobs`. The handler then opens a tenant transaction with `SET LOCAL app.tenant_id = job.tenant_id`
for all domain work. A Job's `tenant_id` is set at enqueue from the caller's TenantContext, never
from user input.

## 4. Content (M1-00)

```sql
knowledge_bases(id PK, tenant_id, name, description, created_at, updated_at, UNIQUE (tenant_id, id))

documents(id PK, tenant_id, knowledge_base_id, title, filename, mime_type,
          size_bytes bigint, sha256 char(64), blob_key,
          status CHECK (status IN ('pending','processing','ready','failed')),
          error text NULL, warnings text[] DEFAULT '{}',
          language text NULL, ocr_languages text NULL,             -- override used at ingest
          page_count int NULL, pages_done int DEFAULT 0, ocr_pages int DEFAULT 0, chunk_count int DEFAULT 0,
          ingested_at NULL, created_by, created_at, updated_at,
          UNIQUE (tenant_id, id), UNIQUE (knowledge_base_id, sha256),            -- FR-05
          FOREIGN KEY (tenant_id, knowledge_base_id) REFERENCES knowledge_bases(tenant_id, id) ON DELETE CASCADE)

chunks(id PK, tenant_id, document_id, knowledge_base_id, ordinal int,
       text text, token_count int, language text NULL,
       page_start int NULL, page_end int NULL, heading_path text[] DEFAULT '{}',
       ocr_confidence real NULL,
       embedding vector(1024) NOT NULL,                              -- bge-m3, fixed (ADR-0015)
       tsv tsvector NOT NULL,                                        -- to_tsvector('simple', …)
       created_at,
       UNIQUE (document_id, ordinal),
       FOREIGN KEY (tenant_id, document_id) REFERENCES documents(tenant_id, id) ON DELETE CASCADE)

CREATE INDEX chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX chunks_tsv ON chunks USING gin (tsv);
CREATE INDEX chunks_tenant_kb ON chunks (tenant_id, knowledge_base_id);
```

`tsv` = `setweight(to_tsvector('simple', title || ' ' || heading_path), 'A') ||
setweight(to_tsvector('simple', text), 'B')`, set by the ingestion service.

Vector query (RLS adds the tenant predicate; we also filter explicitly):
```sql
SET LOCAL hnsw.iterative_scan = relaxed_order;   -- pgvector ≥ 0.8: filtered queries still return k rows
SET LOCAL hnsw.ef_search = 80;
SELECT id, 1 - (embedding <=> :q) AS score
FROM chunks
WHERE tenant_id = :tenant AND knowledge_base_id = ANY(:kb_ids)
ORDER BY embedding <=> :q LIMIT :k;
```

## 5. Assistants and Conversations (M1-00)

```sql
assistants(id PK, tenant_id, slug citext, name, knowledge_base_ids text[] NOT NULL,   -- no model field (ADR-0019)
           system_prompt text, default_language text DEFAULT 'en',
           welcome_text jsonb DEFAULT '{}', fallback_text jsonb DEFAULT '{}',   -- {"default": "...", "hi": "..."}
           limit_reached_text jsonb DEFAULT '{}',
           visitor_email text DEFAULT 'off' CHECK (visitor_email IN ('off','optional','required')),  -- M2-10
           visitor_email_consent jsonb DEFAULT '{}', privacy_url text NULL,
           retrieval jsonb DEFAULT '{}', theme jsonb DEFAULT '{}',
           hosted_enabled bool DEFAULT false,
           created_at, updated_at, UNIQUE (tenant_id, id), UNIQUE (tenant_id, slug))
-- knowledge_base_ids validated in the service + a trigger that checks each id belongs to tenant_id

conversations(id PK, tenant_id, assistant_id, channel text, channel_key_id NULL,
              visitor_id text NULL, user_id NULL, last_language text NULL,
              visitor_email_enc bytea NULL, visitor_email_hash char(64) NULL,     -- AES-GCM + SHA-256 for delete-by-email (ADR-0023)
              visitor_email_consent_at timestamptz NULL,
              started_at, last_message_at, message_count int DEFAULT 0,
              UNIQUE (tenant_id, id),
              FOREIGN KEY (tenant_id, assistant_id) REFERENCES assistants(tenant_id, id))
CREATE INDEX conv_visitor ON conversations (tenant_id, assistant_id, visitor_id);

messages(id PK, tenant_id, conversation_id, role CHECK (role IN ('user','assistant')),
         content text, language text NULL, standalone_query text NULL,
         citations jsonb DEFAULT '[]',     -- [{n, chunk_id, document_id, title, page, heading_path, snippet}]
         retrieval jsonb NULL,             -- [{chunk_id, vector_score, fts_rank, rrf, used}]
         model_profile_id text NULL, model text NULL, prompt_tokens int NULL, completion_tokens int NULL,
         cogs_micros bigint NULL, outcome text NULL CHECK (outcome IN ('answered','fallback','limit','error')),
         latency_ms int NULL, first_token_ms int NULL, finish_reason text NULL,
         feedback smallint NULL CHECK (feedback IN (-1, 1)), feedback_note text NULL, created_at,
         FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversations(tenant_id, id) ON DELETE CASCADE)
```

## 7. Models, metering and costs (M1-00; billing tables in specs/billing.md §2, migration 0004)

```sql
model_profiles(id PK,                                    -- e.g. 'local-default'
               tenant_id NULL,                           -- NULL = platform profile; set = Custom LLM of that Tenant (M3)
               provider CHECK (provider IN ('ollama','openai_compat','anthropic','fake')),
               model text, base_url text NULL, params jsonb DEFAULT '{}',   -- temperature, num_ctx, disable_thinking…
               license text,                             -- 'apache-2.0' | 'mit' | 'commercial-api' (ADR-0018)
               is_local bool, credentials_enc bytea NULL,                    -- crypto.py (SEC-17)
               price_in_per_mtok_micros bigint, price_out_per_mtok_micros bigint,   -- COGS inputs
               status CHECK (status IN ('active','disabled')), created_at, updated_at)
-- RLS: platform rows (tenant_id NULL) readable by all roles; a Custom LLM row only by its Tenant;
-- writes only via codura_platform. Tenant APIs never return these rows (ADR-0019).

cost_rates(key text PK, micros bigint, unit text, updated_at)  -- cpu_second, gpu_hour, gb_month, embed_per_mtok, payment_fee_pct…

usage_events(id PK, tenant_id, meter text, quantity numeric, cogs_micros bigint,
             model_profile_id NULL, idempotency_key text, attrs jsonb DEFAULT '{}',
             occurred_at timestamptz DEFAULT now(),
             UNIQUE (tenant_id, meter, idempotency_key))          -- RLS'd (Tenant reads own); append-only
CREATE INDEX usage_events_tenant_time ON usage_events (tenant_id, occurred_at);

usage_counters(tenant_id, meter, period_start date, quantity numeric DEFAULT 0, cogs_micros bigint DEFAULT 0,
               updated_at,                                       -- M3 (0004)
               PRIMARY KEY (tenant_id, meter, period_start))      -- RLS'd; updated in the same tx as the event
```

M3 adds (normative in `0004_billing.py`, described in [specs/billing.md](./billing.md) §2):
- Catalogue: `plans`, `plan_versions` (immutable once used), `plan_prices`.
- Tenant tables (forced RLS; `codura_app` reads only, ADR-0026 §4): `billing_accounts`,
  `subscriptions`, `subscription_events`, `tenant_billing_overrides`, `quota_reservations`,
  `quota_notices`, `usage_rollups`, `invoices`, `invoice_lines`, `payments`.
- Global: `payment_events` (webhook dedupe) and `scheduled_runs`.

`codura_worker` may update its own Tenant's `tenants.status`, for past due.

## 8. Deletion semantics

- Deleting a Document cascades to its `chunks` in one transaction, then enqueues `purge_blob` if
  no other Document in the **same Tenant** has that `sha256`. Citation snippets in
  `messages.citations` that point to it are nulled by the same Job.
- Deleting a Tenant (Platform Admin) cascades all tenant rows and enqueues a purge of the
  `<tenant_id>/` Blob prefix.
