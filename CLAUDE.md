# Codura Docs

Local-first, monetised, multi-tenant RAG SaaS with two sides:

- **Admin side:** organisations **sign up** as Tenants. Tenant Admins upload documents (PDF,
  DOCX, text, HTML, **scanned PDFs and images**, which are OCR'd), manage Tenant Users (read-only)
  and billing. The system builds that Tenant's Knowledge Base: chunks + `bge-m3` embeddings in
  Postgres + pgvector. **Platform Admins** run the SaaS: Plans, prices, models, Custom LLM,
  finance.
- **Chat side:** Visitors ask questions **in any supported language** through a Channel bound to
  one Tenant: Hosted Chat Page and Widget first (MVP), then the WordPress plugin and Android SDK.
  They get answers with Citations, in the language they asked in, drawn only from that Tenant's
  Documents.

Everything runs on `docker compose` with Ollama (`qwen3:4b` chat + `bge-m3` embeddings, on the
dev box's 6 GB GPU), Tesseract (OCR), Mailpit (email) and the `manual` payment provider, so local
development costs nothing. **The answering LLM is platform-managed and switchable** (per Plan,
per-Tenant override, or a Tenant's Custom LLM, by Platform Admins only). **The embedding model is
fixed.** **Every billable action is metered with its cost**, so the platform stays profitable
(docs/billing.md).

Spec-first: `docs/` is the design. The original brief is `business-idea.txt`; owner revisions are
in `PROGRESS.md`.

Stack: Python 3.12 (FastAPI, SQLAlchemy 2, Alembic, Pydantic v2, uv, ruff, mypy strict, pytest)
for `backend/`; TypeScript strict (pnpm, Vite, Preact/React, Vitest) for `web/`; PHP 8.1+ for
`wordpress/` (M3); Kotlin + Compose for `android/` (M3). Postgres 16 + pgvector. Ollama.
Tesseract 5.

## Start here, every session

1. Read `PROGRESS.md` (where things stand, open questions, last handoff).
2. Read `docs/ledger/README.md`, then the current milestone file, and pick a Task (`/next-task`).
3. Read the Task's **Spec** and every doc section it cites before writing code.
4. End by verifying and recording the handoff (`/close-task`).

## Spec

`CONTEXT.md` is the glossary. Use its terms, never the ones under `_Avoid_`, in code, docs and chat.

`docs/README.md` maps each doc to the work it governs. Read `docs/security.md` before touching
tenancy, uploads/OCR, the public Chat API, any Channel, or anything that renders model output.

- Cite requirements by ID (`FR-xx`, `NFR-xx`, `SEC-xx`) and decisions by ADR number (`ADR-0014`).
  IDs are stable: deprecate, never renumber.
- Docs are the source of truth. When code and spec disagree, surface the conflict and update the
  doc in the same change.
- A change to a Port, the schema, tenancy rules, the public Chat API or a Channel needs an ADR
  (`/adr`). Never edit an Accepted ADR's decision; supersede or amend it with a new one.
- Code and DDL in the docs are illustrative. Once they exist, `backend/migrations/` and the
  generated OpenAPI schema are normative.

## Invariants

Each is enforced by import-linter, a test or review. Breaking one needs an ADR first.

- **Tenant isolation, always.** Every tenant table has `tenant_id`, composite FKs and forced RLS.
  Repositories require a `TenantContext`. The Tenant comes only from credentials (session, `sk_`,
  `pk_`), never from request data. Another Tenant's IDs return 404. Only `platform/` may cross
  Tenants. The cross-Tenant probe suite must stay green (ADR-0014, SEC-02/09/15).
- **Ports, not vendors.** Only `backend/src/codura_docs/adapters/**` may import a vendor SDK, OCR
  or parser library, or call a vendor API. `domain/` and `services/` import `ports/` only.
  Switching the chat LLM is a Model Profile change, never a code change (ADR-0004).
- **One fixed embedding model.** `bge-m3`, 1024-d, recorded in `system_settings`. Startup fails on
  mismatch. Don't add code paths that embed with anything else (ADR-0015).
- **Answer in the question's language, only from the Context Pack.** Fallback text is configured,
  never generated (rag-pipeline.md §3).
- **Channels hold no secrets.** They use a publishable Channel Key against the public Chat API
  only (ADR-0012).
- **Model and Document text is untrusted.** Every UI renders it through the sanitiser (SEC-07).
  Images pass the decompression-bomb limits before OCR (SEC-16).
- **Local-first default.** The default `.env.example` makes zero paid or external calls once
  models are pulled (NFR-01).
- **Tenants never choose or see the LLM.** Model resolution is Custom LLM → Platform override →
  Plan default (ADR-0019). Tenant APIs never return model names, provider names or COGS.
  Self-hosted models must be Apache-2.0/MIT (ADR-0018).
- **Meter every billable action** with `metering.record()` in the same transaction as the action,
  idempotently, with COGS. Check the quota before any LLM call or OCR. Nothing bills from logs
  (ADR-0021).
- **Roles:** every Tenant write route requires Tenant Admin; Tenant Users are read-only; platform
  routes are Platform Admin only (ADR-0020).
- **Secrets and PII at rest** (provider credentials, Visitor emails) go through `crypto.py`
  (SEC-17, SEC-19).

## Commands

```
cp .env.example .env && docker compose up -d     # whole stack; GPU via COMPOSE_FILE in .env
scripts/smoke.ps1   (or scripts/smoke.sh)         # M0 exit check against the running stack
cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run lint-imports
cd backend && uv run pytest                       # unit + contract[fake]; integration needs Postgres:
  CODURA_TEST_PG_ADMIN_URL=postgresql+psycopg://codura_owner:codura@127.0.0.1:5432/postgres
  (publish Postgres with compose.dev.yml on COMPOSE_FILE)
  CODURA_TEST_OLLAMA_URL=http://localhost:11434 uv run pytest -m ollama   # live model contracts
cd backend && uv run python scripts/live_e2e.py   # real models + OCR + email, end to end
cd backend && uv run python scripts/eval.py       # golden multilingual eval report
pnpm -C web install && pnpm -C web -r test && pnpm -C web -r build
```

Windows host: async psycopg needs the selector event loop (handled in `cli.run_async` and tests).
Admin Console: http://localhost:8080 · Mailpit: http://localhost:8025 · demo Tenant Admin
`admin@demo.test` / `demo-password` · Platform Admin `platform@local.test` / `admin` (local only).

Update this section when a task changes how the project is built, run or tested.

## Working rules

- Work inside the Task's **Owns** paths. Branch `task/<id>-<slug>`. Flip only your own checkbox.
- Tests come with the change. A new Adapter must pass the shared Port contract tests. A new route
  or table needs its isolation case.
- Don't commit or push unless asked. Never commit `.env`, model files or uploaded Documents.
