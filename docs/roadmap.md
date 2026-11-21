# Roadmap

**Status:** Draft v0.3 · **Last updated:** 2026-11-19

Durations assume one developer working with AI agents in parallel. They are sequencing estimates.

## Sequencing rationale

- **Ports, tenancy and metering from the start.** Provider switching, tenant isolation and usage
  events with cost are cheap to build in on day one and very expensive to retrofit. Usage Events
  are written from the first answer (M1), even though invoicing comes in M3. Pricing then rests on
  real cost data.
- **Answer engine before Channels; web before native** (ADR-0017).
- **Monetisation before more Channels.** Billing (M3) has to exist before onboarding paying
  organisations, and before the costlier premium models (Anthropic/OpenAI) are switched on.
  WordPress and Android (M4) then grow a product that already earns.

| Milestone | Theme | Duration | Ledger |
|---|---|---|---|
| M0 | Foundations | 1–2 wks | [M0](./ledger/M0.md) |
| M1 | Signup, roles, ingestion + OCR, multilingual answers, consoles, usage events | 5–6 wks | [M1](./ledger/M1.md) |
| M2 | **Web Channel = MVP** (+ Visitor email, basic insights) | 2–3 wks | [M2](./ledger/M2.md) |
| M3 | **Monetisation**: Plans, Entitlements, quotas, payments, finance dashboards, model management, Custom LLM, Anthropic | 4–5 wks | [M3](./ledger/M3.md) |
| M4 | WordPress plugin & Android | 3–4 wks | [M4](./ledger/M4.md) |
| M5 | Quality & cost optimisation | 2–3 wks | [M5](./ledger/M5.md) |
| M6 | Production & observability | 2 wks | [M6](./ledger/M6.md) |

## M0 — Foundations
Repo, compose (GPU default on the dev box, Mailpit), backend skeleton, DB + tenant RLS, Ports
(incl. `EmailSender`) + fakes + contract suites, Ollama Adapters (`qwen3:4b` with thinking off,
`bge-m3`), embedding guard, licence allowlist, Job queue, Blob Store, secrets encryption helper, CI.

**Exit:** stack healthy on GPU; `llm ping` streams from `qwen3:4b` and embeds with `bge-m3`; RLS
test green; CI green.

## M1 — Signup, roles, ingestion, answering, consoles
Self-service signup + email verification, Tenant Admin/User roles + user management, Platform
Console basics (Tenants, model override, audit), platform-managed Model Profiles (seeded,
read-only UI), parsers + OCR + language, ingestion, cross-lingual Retrieval, answering, Playground,
`openai_compat` Adapter, **Usage Events with COGS** + fixed trial limits, isolation suite, eval
seed, `/metrics`.

**Exit:** MVP acceptance steps 1–6 and 8.

## M2 — Web Channel (MVP)
Channel Keys, Origins, rate limits, Hosted Chat Page, Widget, i18n/RTL, Visitor email capture,
feedback, Conversation log, basic Tenant insights (usage, unanswered questions), branding badge.

**Exit:** MVP acceptance complete.

## M3 — Monetisation
Plans + versions + Entitlements, Plan editor with margin floor, subscriptions and lifecycle,
real-time quotas + overage + spend cap, invoices from our ledger, PaymentProvider (`manual`,
`stripe`), webhooks, Tenant billing page, Platform finance dashboard + unprofitable-Tenant alerts +
price simulator, full Model Profile editor with encrypted credentials and prices, **Custom LLM**
enablement, Anthropic Adapter, growth funnel.

**Exit:** a Tenant upgrades through Checkout (Stripe test mode) or a `manual` invoice; overage is
billed correctly; the finance dashboard shows per-Tenant margin; a Custom LLM Tenant answers
through its own endpoint.

## M4 — WordPress plugin & Android
As before (ADR-0010/0011/0017), now gated by Plan Entitlements (`channels`).

## M5 — Quality & cost optimisation
Reranker, eval gating, answer cache for repeated questions (cuts LLM cost), provider prompt
caching in Adapters, cheap-model routing for rewrites, better OCR Adapters, URL ingestion,
retention, per-Tenant chunk settings.

## M6 — Production & observability
`compose.prod.yml`, TLS, image publishing, backups, OTel + Prometheus + Grafana + alerts, LLM cost
reconciliation, load test and self-hosted-vs-API sizing, security review incl. isolation and
billing pen-test.
