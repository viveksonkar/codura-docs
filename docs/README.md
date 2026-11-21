# Codura Docs — Documentation

## Document map

| Document | What it covers | Read it when |
|---|---|---|
| [requirements.md](./requirements.md) | Vision, personas, numbered FR / NFR / SEC requirements, MVP acceptance | You need to know *what* is being built and why |
| [architecture.md](./architecture.md) | Services, containers, request flows, code layout, Ports & Adapters | You need to know *how* the system is shaped |
| [rag-pipeline.md](./rag-pipeline.md) | Ingestion (parse, OCR, language, chunk, embed), cross-lingual Retrieval, answering in the question's language, Citations | You touch ingestion, OCR, retrieval or answer generation |
| [channels.md](./channels.md) | Hosted Chat Page and Widget (MVP), WordPress plugin and Android SDK (M4) | You build or change a Channel |
| [security.md](./security.md) | Tenant isolation, trust boundaries, threats, controls (`SEC-xx`) | **Always**, before tenancy, uploads/OCR, public API, Channels or rendering output |
| [deployment.md](./deployment.md) | docker compose services, profiles, env vars, local → production | You change infra, compose or config |
| [billing.md](./billing.md) | Meters, cost model (COGS), pricing method and margin floor, Plans and Entitlements, quotas, subscriptions, payments, finance views | You touch metering, quotas, Plans, model costs or payments |
| [telemetry.md](./telemetry.md) | Ops metrics/traces/alerts, platform growth and finance analytics, Tenant insights, privacy rules | You add metrics, dashboards or analytics |
| [testing.md](./testing.md) | Test layers, Port contract tests, RAG eval set, CI gates | You add tests or CI |
| [roadmap.md](./roadmap.md) | Milestones M0 → M6 with exit criteria | You plan work |
| [adr/](./adr/README.md) | Architecture Decision Records, one file each | You make or question a decision |
| [specs/](./specs/README.md) | Frozen contracts: Ports, DB schema, HTTP API + SSE events | You write code against a shared boundary |
| [ledger/](./ledger/README.md) | Milestone → Task breakdown for parallel agents | You pick up implementation work |

## Conventions

- Domain terms are defined in [`CONTEXT.md`](../CONTEXT.md). Use them; avoid the `_Avoid_` words.
- Requirement IDs (`FR-xx`, `NFR-xx`, `SEC-xx`) are stable. Deprecate, never renumber.
- Priority is MoSCoW (**M**ust / **S**hould / **C**ould / **W**on't-yet) for the milestone named.
- ADRs live in [`adr/`](./adr/README.md) as `NNNN-title.md`, numbered globally, rejected options
  kept. An Accepted ADR is changed only by superseding it.
- Code and schema samples are illustrative. `backend/migrations/` and the generated OpenAPI
  schema are normative once they exist.
- Each doc carries a status line. Update its "Last updated" date when you change it.

## Status

| | |
|---|---|
| Version | 0.3 (draft): owner revisions 2026-11-18 (tenancy, fixed embeddings, multilingual, OCR, web first) and 2026-11-19 (licensed models, platform-managed LLM, signup and roles, billing, telemetry, Visitor email) |
| Last updated | 2026-11-19 |
| Deployment model | Monetised multi-tenant SaaS on docker compose; self-service signup |
| Stack | Python/FastAPI · Postgres 16 + pgvector · Ollama (qwen3:4b, bge-m3) · Tesseract · Mailpit/SMTP · Stripe (optional) · Prometheus/Grafana · TypeScript web · PHP WordPress · Kotlin Android |
