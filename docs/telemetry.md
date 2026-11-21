# Telemetry & Analytics

**Status:** Draft v0.1 · **Last updated:** 2026-11-19 · **ADR:** 0022 · **Implements:** NFR-09, NFR-15

Three audiences:

| Audience | Question | Source | Where |
|---|---|---|---|
| Operator (ops) | Is it up and fast? Where are the bottlenecks? | OpenTelemetry → Prometheus / Grafana, JSON logs | Grafana (`observability` profile) |
| Platform Admin (business) | Is it growing? Is it profitable? Which Tenants or models cost us? | Postgres rollups of `usage_events`, `subscriptions`, `invoices`, `messages` | Platform Console |
| Tenant Admin / User | How is my assistant used? What can't it answer? Am I near my limits? | Same rollups, Tenant-scoped (RLS) | Tenant Console |

## 1. Ops telemetry

### Traces (OTel)
Spans: `http.request` → `chat.answer` → {`retrieval.vector`, `retrieval.fts`, `llm.rewrite`,
`llm.stream`} and `job.ingest` → {`parse.page`, `ocr.page`, `embed.batch`, `db.write`}. Attributes
include model profile, token counts and page counts. **No text, no emails.** `tenant_id` is a span
attribute (traces aren't metrics, so cardinality is fine) and is redacted in exports when needed.

### Metrics (Prometheus, low cardinality)
| Metric | Labels |
|---|---|
| `http_request_duration_seconds` | route, method, status_class |
| `chat_first_token_seconds`, `chat_total_seconds` | model_profile, channel |
| `chat_answers_total` | model_profile, outcome (`answered`/`fallback`/`limit`/`error`) |
| `llm_tokens_total` | model_profile, direction (`in`/`out`) |
| `llm_cost_usd_total` | model_profile |
| `retrieval_seconds` | stage |
| `ingest_pages_total`, `ocr_pages_total`, `ocr_page_seconds` | outcome |
| `jobs_queue_depth`, `jobs_age_seconds` | kind |
| `ollama_up`, GPU util/VRAM (via nvidia exporter, optional) | — |
| `quota_blocks_total` | meter |

### Alerts (provisioned)
p95 first-token > NFR-04, error rate > 2 %, job queue age > 10 min, Ollama down, disk > 80 %, LLM
cost per hour > configured ceiling (catches runaway spend), payment webhook failures.

### Logs
JSON with `request_id`, `tenant_id`, `job_id`, redaction (SEC-10). The `observability` profile
adds Loki.

## 2. Platform analytics (Platform Console)

Rolled up hourly/daily by the `analytics_rollup` Job into `analytics_daily_*` tables.

- **Growth funnel:** signup → email verified → first Document `ready` → first Playground answer →
  first Channel answer → paid. Shows conversion and time between steps per weekly cohort.
- **Activation and health:** active Tenants (≥ 1 Channel answer in 7 days), answers/day, fallback
  rate, feedback ratio, ingestion failures, OCR share.
- **Finance:** see [billing.md](./billing.md) §9: MRR, COGS, margins, unprofitable Tenants, model
  cost mix, reconciliation.
- **Capacity:** answers per GPU-hour, peak concurrency, queue waits. These feed the
  self-hosted-vs-API decision.

## 3. Tenant insights (Tenant Console)

All read under the Tenant's RLS context. Retention follows the Plan's `analytics_retention_days`.

- Usage vs quota per meter, with a period-end projection.
- Conversations over time by Channel and language.
- **Top questions** (clustered by embedding similarity).
- **Unanswered questions** (fallback or 👎). This is the content-gap list that tells Admins which
  documents to add, and it drives retention.
- Answer feedback ratio, average latency, captured Visitor emails (Tenant Admin only, ADR-0023).

## 4. Privacy rules

- No Visitor message text in metrics or traces. Analytics use aggregates and, for top/unanswered
  questions, text shown only to that Tenant.
- Platform Admins see per-Tenant numbers, not Tenant content, by default. Content access for
  support requires a logged "support access" action.
- Raw `usage_events` are kept 25 months for billing disputes. Rollups are kept indefinitely.
