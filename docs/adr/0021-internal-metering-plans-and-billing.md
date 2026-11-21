# ADR-0021 — Internal metering as the billing source of truth; Plans, Entitlements, Quotas; payment provider behind a Port

- **Status:** Accepted
- **Date:** 2026-11-19
- **Relates to:** FR-60…71, NFR-14…16, docs/billing.md, ADR-0019, ADR-0022

## Context

The platform must be profitable. Revenue comes from Tenants. Costs (COGS) come mainly from LLM
tokens, especially when production uses Anthropic/OpenAI, plus GPU/CPU for embeddings and OCR,
storage and bandwidth. Pricing must be set from measured cost, and no Tenant may run up unbounded
cost.

## Decision

1. **Every billable action writes a Usage Event** in the same transaction as the action. Meters:
   `answer`, `answer_custom_llm`, `page_ingested`, `page_ocr`, `storage_mb_day`, `seat_day`. Each
   event stores quantity and **COGS** (micro-USD), computed from the Model Profile's price snapshot
   and real token counts. This is the source of truth for quotas, invoices and margin.
2. **Plans** (managed by the Platform Admin, versioned) define the price, the included quantity per
   meter, overage price per meter, **Entitlements** (feature flags/limits) and the default Model
   Profile. A Tenant has one **Subscription** to a Plan version. Per-Tenant overrides are possible
   (enterprise deals).
3. **Quotas are enforced in real time** from period counters (`usage_counters`, updated
   atomically). Warnings go out at 80 % and 100 %. At the hard limit, chat answers the plan's
   "limit reached" text (no LLM call) and ingestion pauses, unless overage is enabled for that
   Tenant (paid plans only).
4. **Margin guardrails:** Plan prices must satisfy `price ≥ expected_COGS / (1 − target_margin)`
   (default target 70 %), checked when a Plan is saved. Every Tenant's gross margin is computed
   daily, with alerts for Tenants below the floor. Per-plan caps on output tokens, context size and
   concurrency bound the worst-case cost of a single answer.
5. **Payments** go through a `PaymentProvider` Port. Adapters: `manual` (default, local-first: the
   Platform Admin marks invoices paid, NFR-01), `stripe` (subscriptions + usage reported from our
   rollups) and optionally `razorpay`. The provider collects money; **our ledger computes the
   amounts**. Webhooks are verified and idempotent.

## Alternatives considered

### Let Stripe Billing meter usage directly — rejected
It would make Stripe the source of truth for quotas, need network calls on the hot path, and break
local-first development. Stripe still receives the rolled-up usage.

### Flat subscription without usage metering — rejected
LLM cost scales with usage. A heavy Tenant on a flat price can make the platform unprofitable.

### Credits-only (prepaid wallet) — deferred
Simple and safe, but less familiar to SMB buyers. The model can add a `credit` meter later. Plans
with included quantities plus overage cover both cases.

## Consequences

Billing correctness becomes core engineering: idempotent events, period boundaries and timezone
(UTC), proration on plan change, and reconciliation tests. In return, every rupee or dollar of cost
is attributable to a Tenant, a plan and a model, and prices can be tuned from data.
