# ADR-0022 — Self-hosted telemetry: OpenTelemetry + Prometheus/Grafana for ops, Postgres for product and finance analytics

- **Status:** Accepted
- **Date:** 2026-11-19
- **Relates to:** NFR-09, NFR-15, docs/telemetry.md, ADR-0021

## Context

The owner needs to see whether the platform is healthy (latency, errors, GPU saturation), whether
it is used (signups, activation, answers), and whether it is profitable (revenue vs COGS by Tenant,
plan and model). Tenants need usage and insight dashboards. Sending Tenant or Visitor data to
third-party analytics would break local-first development and complicate privacy commitments.

## Decision

- **Ops telemetry:** OpenTelemetry SDK in `api`/`worker` (traces + metrics). Prometheus scrapes
  `/metrics`. Grafana dashboards are provisioned as code. Logs stay as JSON to stdout (optionally
  Loki). These run under the compose profile `observability`, which is off by default locally and on
  in production.
- **Product and finance analytics:** computed in Postgres from `usage_events`, `messages`,
  `documents`, `subscriptions` and `invoices` through rollup tables refreshed by a Job (hourly/daily).
  They are shown in the Platform Console (finance, growth, health) and Tenant Console (usage,
  insights).
- **Metric labels never include** raw text, emails or IDs with unbounded cardinality. Tenant IDs
  appear in metrics only as an aggregated top-N, through analytics, not as Prometheus labels.
- No third-party product analytics by default. Adding any needs an ADR and a Tenant-facing privacy
  update.

## Alternatives considered

### SaaS APM / analytics (Datadog, Mixpanel, PostHog Cloud) — rejected as default
Costs money, sends data off-box, and breaks NFR-01. Self-hosted PostHog could be added later if
funnel tooling outgrows SQL.

### Prometheus labels per Tenant — rejected
Cardinality explodes as Tenants grow. Postgres rollups are cheaper and exact.

## Consequences

Two small containers in the `observability` profile. The SQL analytics Jobs need indexes and
retention. Dashboards are versioned in `deploy/grafana/`.
