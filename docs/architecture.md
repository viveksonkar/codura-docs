# Architecture

**Status:** Draft v0.2 · **Last updated:** 2026-11-18

Two sides, one engine: the **admin side** (organisations sign up as Tenants; Tenant Admins upload
Documents → OCR/parse → chunk → embed; Tenant Users view) and the **chat side** (Visitors of a Tenant ask questions
through a Channel). Every request, Job and query runs inside exactly one Tenant (ADR-0014).

## 1. Shape in one picture

```
        ┌──────────────── Chat side: Channels (Visitors of one Tenant) ────────────────┐
        │  Hosted Chat Page   Widget (any site)  │ M4: WordPress plugin   Android SDK  │
        └───────────────┬─────────────────────────────────────────────────────────────┘
                        │  public Chat API (Channel Key ⇒ Tenant + Assistant, SSE)
 Admin side: Admin Console (Platform Admin, Tenant Admins) ──┐      │
   Secret/session auth   ▼      ▼
                    ┌──────────────────┐        ┌──────────────────────┐
                    │  caddy (reverse  │───────▶│ api  (FastAPI)       │
                    │  proxy, TLS)     │        │  admin + public      │
                    └──────────────────┘        │  routers             │
                       │ /widget.js, /admin     └───┬──────────┬───────┘
                       ▼ (static)                   │          │ enqueue Job
                    web assets                      │          ▼
                                                    │   ┌──────────────┐
                                                    │   │ worker       │
                                                    │   │ (ingestion,  │
                                                    │   │  OCR)        │
                                                    │   └──┬───────┬───┘
                     ┌──────────────────────────────┴──────┘       │
                     ▼                                              ▼
            ┌───────────────────┐   ChatModel / EmbeddingModel  ┌────────────┐
            │ postgres 16       │◀── Ports ── Adapters ────────▶│ ollama     │
            │ + pgvector        │                               │ (default)  │
            │ app data, chunks, │   embeddings: bge-m3 (fixed)  └────────────┘
            │ vectors, FTS, jobs│   chat: Model Profile → ollama | openai_compat | anthropic
            │ RLS per Tenant    │   OCR: Tesseract (in worker image)
            └───────────────────┘
                     ▲
            Blob Store (local volume │ S3-compatible)
```

Two runtime processes from **one Python image** (`api`, `worker`), one database, one model server.
No Redis, no separate vector DB (ADR-0003, ADR-0006).

## 2. Services (docker compose)

| Service | Image | Role | Notes |
|---|---|---|---|
| `postgres` | `pgvector/pgvector:pg16` | App data, Chunks, Embeddings, FTS, Job queue | Volume `pgdata` |
| `ollama` | `ollama/ollama` | Local chat + embedding models (`qwen3:4b`, `bge-m3`) | Volume `ollama`; NVIDIA GPU enabled by default via `compose.gpu.yml` (ADR-0018) |
| `ollama-init` | `ollama/ollama` | One-shot: `ollama pull` the models in `.env` | Exits 0; `api` waits on it |
| `mailpit` | `axllent/mailpit` | Local SMTP catcher + web UI for signup/verify/reset/invite emails | Dev only; prod uses an SMTP relay |
| `prometheus` + `grafana` (+ `loki`) | official images | **Profile `observability`**: metrics, dashboards, alerts (ADR-0022) | Off locally by default, on in prod |
| `migrate` | backend image | One-shot: `alembic upgrade head`, record embedding model in `system_settings`, bootstrap Platform Admin (+ optional demo Tenant) | `api`/`worker` depend on its success |
| `api` | backend image | FastAPI: platform API, admin API, public Chat API | `uvicorn`, port 8000 internal |
| `worker` | backend image (includes Tesseract + language packs) | Runs Jobs: `ingest_document`, `send_email`, `purge_*`, `usage_snapshot` (storage/seats daily), `analytics_rollup`, `billing_period_close` (M3) | Scale with `--scale worker=N` |
| `web` | web image (nginx) | Serves Admin Console SPA, Hosted Chat Page (`/c/…`) and `widget.js` | Built by multi-stage Dockerfile |
| `caddy` | `caddy:2` | Single entry point: `/api/*` → api, `/` → web; automatic TLS in prod | Port 8080 locally |
| `wordpress` + `wp-db` | `wordpress`, `mariadb` | **Profile `wp`**, M4: local WordPress to test the plugin | Dev only |

Details, env vars and production overrides: [deployment.md](./deployment.md).

## 3. Code layout

```
backend/                         Python, uv project, package `codura_docs`
  src/codura_docs/
    config.py                    pydantic-settings; Model Profile seeds parsed here
    crypto.py                    AES-GCM envelope encryption for credentials and Visitor emails (SEC-17)
    ports/                       Protocols only: chat, embedding, parser, ocr, blob, rerank, email, payment
    adapters/                    the ONLY place vendor SDKs are imported
      ollama/  openai_compat/  anthropic/  fake/
      parsers/ (pdf, image, docx, text, html)   ocr/ (tesseract)   blob/ (local, s3)
      langdetect/ (lingua)   email/ (smtp)   payments/ (manual, stripe, razorpay)
    domain/                      entities + pure logic (chunking, RRF, prompt assembly, citations, language, pricing math)
    services/                    use cases: signup, members, ingestion, retrieval, answering, model_resolution, insights
    metering/                    Usage Events, counters, cost functions, quota checks (ADR-0021)
    billing/                     M3: plans, entitlements, subscriptions, invoices (Tenant-facing parts RLS-scoped)
    platform/                    the ONLY cross-Tenant code (Platform Admin: tenants, models, plans, finance), audited
    db/                          SQLAlchemy models, repositories (TenantContext required), session
    jobs/                        queue (enqueue/claim/complete), handlers registry
    api/                         FastAPI: app factory, routers/admin/*, routers/public/*, auth, sse
    worker/                      entrypoint: claim loop → handlers
    registry.py                  builds Adapters from config → Ports (composition root)
  migrations/                    Alembic (normative schema)
  tests/                         unit/, contract/ (Port suites), integration/, eval/
web/                             pnpm workspace
  packages/chat-client/          TS client for the public Chat API (fetch + SSE parser), no deps
  apps/admin/                    React + Vite + TanStack Query: signup, Tenant Console, Platform Console, Playground
  apps/widget/                   Preact Web Component `<codura-chat>` → dist/widget.js + Hosted Chat Page build
wordpress/codura-docs-chat/      M4 — PHP plugin: settings, shortcode, block; bundles widget.js
android/                         M4 — Gradle: :codura-chat (client), :codura-chat-ui (Compose), :sample
deploy/grafana/                  dashboards-as-code (ADR-0022)
docker-compose.yml               local stack;  compose.prod.yml  production override
.env.example                     every variable, local-first defaults
```

### Dependency rules (enforced by import-linter in CI)

```
api, worker ──▶ services ──▶ domain, ports, db
api (platform routes only) ──▶ platform ──▶ db (platform role)
registry    ──▶ adapters ──▶ ports           (adapters never import services/domain/db)
domain      ──▶ ports (types only)           (domain imports no I/O)
nothing except api.routers.platform imports platform
```

`registry.py` is the composition root: the only module that knows which Adapter backs which Port.

## 4. Ports & Adapters (provider switching)

The domain depends on these Ports, frozen in [specs/ports.md](./specs/ports.md) (PaymentProvider
in specs/billing.md, M3):

| Port | Default Adapter | Alternatives | Switchable? |
|---|---|---|---|
| `ChatModel` | `ollama` (`qwen3:4b`, thinking off) | `openai_compat` (M1), `anthropic` (M3) | **Yes, by the Platform Admin only**: per Plan, per-Tenant override, or Custom LLM (ADR-0019) |
| `EmbeddingModel` | `ollama` (`bge-m3`, 1024-d) | same model via another runtime | **No**, fixed and guarded (ADR-0015) |
| `OcrEngine` | `tesseract` | docling / vision LLM (M4) | Yes (config) |
| `Parser` | `pdf` (pypdf + pypdfium2), `image`, `docx`, `text`, `html` | — | — |
| `BlobStore` | `local` (volume) | `s3` (any S3-compatible) | Yes (config) |
| `Reranker` | `none` | cross-encoder (M5) | Yes (per Plan) |
| `EmailSender` | `smtp` → Mailpit locally | any SMTP relay | Yes (config) |
| `PaymentProvider` | `manual` (no network) | `stripe`, `razorpay` (M3) | Yes (config) |

Seed config (`config/models.yaml`, loaded into `model_profiles` on first boot; afterwards the
Platform Console is authoritative):

```yaml
default_profile: local-default
chat_profiles:
  local-default: { provider: ollama, model: "qwen3:4b",  license: apache-2.0, disable_thinking: true,
                   temperature: 0.1, extra: { num_ctx: 8192 }, price_in_per_mtok_usd: 0, price_out_per_mtok_usd: 0 }
  local-quality: { provider: ollama, model: "qwen2.5:7b", license: apache-2.0,
                   temperature: 0.1, extra: { num_ctx: 8192 } }
  # prod examples (prices from provider pages, set in the Platform Console):
  # open-std:  { provider: openai_compat, base_url: http://vllm:8000/v1, model: Qwen/Qwen3-14B, license: apache-2.0 }
  # premium:   { provider: anthropic, model: <current model id>, license: commercial-api, api_key_env: ANTHROPIC_API_KEY }
embedding:                              # fixed for the installation, do not change
  provider: ollama
  model: bge-m3
  dimension: 1024
ocr:
  engine: tesseract
```

Switching the **answering LLM** is a Platform Console action: change a Plan's default profile,
set a Tenant override, or enable and configure a Tenant's Custom LLM. It takes effect on the next
question, with no restart and no re-ingestion. The **embedding model** never changes; startup
aborts if its config differs from `system_settings` (FR-25).

## 5. Key flows

### Signup (admin side)
1. `POST /api/auth/signup` (organisation name, name, email, password) creates the Tenant
   (`pending_verification`, Trial) and its first Tenant Admin, and enqueues a verification email
   (Mailpit locally).
2. Verify → Tenant `active`. The Tenant Admin invites Tenant Users (read-only) or more Tenant Admins.

### Upload → ready (admin side)
1. `POST /api/admin/knowledge-bases/{kb}/documents` (multipart). API resolves `TenantContext`
   from the session, validates size and sniffed type (SEC-03), streams bytes to the Blob Store under
   `<tenant_id>/…`, inserts the `documents` row (`pending`) and enqueues `ingest_document` with that
   `tenant_id` in the **same transaction**.
2. Worker claims the Job, opens a transaction with `SET LOCAL app.tenant_id`, sets `processing`,
   parses page by page (OCR for scanned pages/images, progress updated), detects language, chunks,
   embeds with bge-m3, writes `chunks` (text + vector + tsv), and sets `ready`. Failure → `failed` +
   reason.

### Question → streamed answer (chat side)
1. Channel calls `POST /api/v1/chat/stream` with Channel Key, Visitor ID, optional Conversation ID.
2. API resolves the key → **(Tenant, Assistant)**, checks Origin and rate limits (SEC-05, SEC-06),
   opens a tenant transaction. Nothing in the request can change the Tenant.
3. Detect Answer Language. If there is history, rewrite into a standalone query.
4. Retrieval within the Tenant's Knowledge Bases: bge-m3 vector top-k (cross-lingual) ∪ `simple`
   FTS top-k → RRF → optional rerank → token-budgeted Context Pack.
5. Empty/weak Context Pack → fallback text in Answer Language (no LLM call, not billable).
   **Quota check** → over the limit: limit-reached text (no LLM call). Else **resolve the Model
   Profile** (Custom LLM → override → Plan default) and stream from it with "respond in {language}",
   within the Plan's token caps.
6. SSE events: `meta` (incl. language), `delta`…, `citations`, `done` ([specs/api.md](./specs/api.md) §3).
7. In one transaction: persist Messages (Citations, language, profile, tokens, COGS) **and** the
   `answer` Usage Event + counter (ADR-0021).

Full detail: [rag-pipeline.md](./rag-pipeline.md).

## 6. Cross-cutting

- **Config**: 12-factor env, validated at startup by pydantic-settings; bad config fails fast.
- **Errors**: one JSON error envelope (`specs/api.md` §1.3) across admin and public APIs.
- **Logging**: JSON to stdout with `request_id`, `tenant_id`, `job_id`. No Document text or
  Visitor messages at INFO (SEC-10).
- **Health**: `/healthz` (process up), `/readyz` (DB reachable, migrations at head, Ports
  configured and Ollama reachable).
- **Time & IDs**: UTC everywhere (billing periods too); prefixed ULIDs (`doc_…`, `kb_…`, `asst_…`, `conv_…`).
- **Metering**: every billable action calls `metering.record()` inside its own transaction;
  nothing bills from logs or metrics ([billing.md](./billing.md)).
- **Telemetry**: OTel traces + Prometheus metrics for ops; Postgres rollups for product and
  finance analytics ([telemetry.md](./telemetry.md)).

## 7. Decisions

See [adr/README.md](./adr/README.md). The ones that shape this document most: ADR-0002 (Python
backend), ADR-0003 (Postgres only), ADR-0004 (Ports), ADR-0006 (Postgres queue), ADR-0013
(compose topology), ADR-0014 (tenancy), ADR-0015 (fixed bge-m3), ADR-0016 (OCR), ADR-0017 (web first),
ADR-0018 (licensed models), ADR-0019 (platform-managed LLM), ADR-0020 (signup and roles), ADR-0021 (billing),
ADR-0022 (telemetry), ADR-0023 (Visitor email).
