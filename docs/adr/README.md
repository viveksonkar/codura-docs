# Architecture Decision Records

One decision per file, `NNNN-kebab-title.md`, numbered globally and never reused. Keep the
rejected alternatives: they're usually what someone re-proposes six months later.

**Lifecycle:** `Proposed` → `Accepted` → (`Superseded by ADR-NNNN` | `Deprecated`). Never rewrite
an Accepted decision. Write a new ADR that supersedes it or amends it, and change only the old one's
Status line. Create new ones with `/adr <title>` using [template.md](./template.md).

**Needs an ADR:** changing a Port, the core shape of the schema, the public Chat API or SSE events,
or tenancy rules; adding a service or container; adding a runtime dependency with licence or ops
impact; or breaking a CLAUDE.md invariant.

| ADR | Title | Status |
|---|---|---|
| [0001](./0001-spec-first-agentic-sdlc.md) | Spec-first agentic SDLC with ledger, ADRs and progress log | Accepted |
| [0002](./0002-python-backend-polyglot-clients.md) | Python/FastAPI backend; TypeScript, PHP, Kotlin clients | Accepted |
| [0003](./0003-postgres-pgvector-only-datastore.md) | Postgres + pgvector as the only datastore | Accepted |
| [0004](./0004-ports-and-adapters-for-llm-providers.md) | Ports & Adapters for LLM Providers; Ollama default | Accepted, amended by 0019 |
| [0005](./0005-versioned-embedding-spaces.md) | Versioned Embedding Spaces with rebuild-and-cutover | **Superseded by 0015** |
| [0006](./0006-postgres-job-queue.md) | Postgres-backed Job queue (`SKIP LOCKED`), no Redis | Accepted |
| [0007](./0007-hybrid-retrieval-rrf.md) | Hybrid Retrieval: pgvector + full-text fused by RRF | Accepted, amended by 0015 |
| [0008](./0008-permissive-document-parsers.md) | Permissively licensed parsers behind a Parser Port | Accepted, amended by 0016 |
| [0009](./0009-blob-store-port.md) | Blob Store Port: local volume default, S3-compatible for prod | Accepted |
| [0010](./0010-web-component-widget-and-wordpress-plugin.md) | One Web Component Widget; WordPress plugin wraps it | Accepted, amended by 0017 |
| [0011](./0011-native-kotlin-android-sdk.md) | Native Kotlin Android SDK over the public Chat API | Accepted (M3) |
| [0012](./0012-public-chat-api-sse-channel-keys.md) | Public Chat API: SSE streaming, publishable Channel Keys | Accepted |
| [0013](./0013-single-compose-topology.md) | One docker compose with profiles and a prod override | Accepted |
| [0014](./0014-strict-multi-tenancy.md) | Strict multi-tenancy: Tenant-scoped data + RLS | Accepted, amended by 0020 |
| [0015](./0015-fixed-multilingual-embedding-model.md) | One fixed multilingual embedding model (bge-m3) | Accepted |
| [0016](./0016-ocr-in-mvp-tesseract.md) | OCR in the MVP: Tesseract behind an OcrEngine Port | Accepted |
| [0017](./0017-web-channel-first.md) | Web Channel first; WordPress and Android after the MVP | Accepted |
| [0018](./0018-commercial-open-weight-models.md) | Only commercially licensed open-weight models; local default `qwen3:4b` | Accepted |
| [0019](./0019-platform-managed-llm-and-custom-llm.md) | Platform-managed LLMs; per-Tenant Custom LLM when enabled | Accepted |
| [0020](./0020-self-service-signup-and-roles.md) | Self-service signup; Platform Admin / Tenant Admin / Tenant User | Accepted |
| [0021](./0021-internal-metering-plans-and-billing.md) | Internal metering, Plans, Entitlements, quotas; PaymentProvider Port | Accepted |
| [0022](./0022-self-hosted-telemetry.md) | Self-hosted telemetry: OTel + Prometheus/Grafana, Postgres analytics | Accepted |
| [0023](./0023-optional-visitor-email.md) | Anonymous Visitors; optional email is contact data, never identity | Accepted |
| [0024](./0024-qwen3-instruct-and-6gb-vram-settings.md) | Local default `qwen3:4b-instruct-2507`; Ollama tuned for 6 GB GPUs | Accepted |
| [0025](./0025-retrievable-hosted-channel-keys.md) | Hosted keys stored encrypted; per-key Origin rules and CORS | Accepted |
| [0026](./0026-billing-ledger-currencies-and-payment-port.md) | Billing ledger schema, INR/USD by country, billing write boundary, PaymentProvider Port | Accepted |
