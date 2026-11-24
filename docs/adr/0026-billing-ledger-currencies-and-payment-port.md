# ADR-0026 — Billing ledger schema, regional currencies (INR/USD), write boundary and the PaymentProvider Port

- **Status:** Accepted
- **Date:** 2026-11-24
- **Deciders:** owner (Q10: INR for India, USD elsewhere; Stripe first), agent
- **Amends:** ADR-0021 (§5 Payments), specs/db.md §7
- **Relates to:** FR-61…70, NFR-14…16, SEC-20, docs/billing.md, specs/billing.md

## Context

ADR-0021 makes our usage ledger the source of truth for quotas, invoices and margins, with
payments behind a Port. M3 needs the concrete shape: which tables hold Plans, Subscriptions and
invoices, which database role may write them, how two currencies coexist with COGS measured in
USD, when Plan fees and overage are charged, and what the Port looks like. The owner answered Q10:
**Tenants billed in India pay in INR; everyone else pays in USD; Stripe is the first real
provider** (test keys configured later; `manual` stays the local default).

## Decision

1. **Currency per Tenant from its billing country.** `billing_accounts.country = 'IN'` → `INR`,
   any other country → `USD`. The currency is fixed once the first invoice is issued (Stripe
   customers are single-currency). Every Plan version carries a price row per currency
   (`plan_prices`). COGS stays in micro-USD. Margins convert revenue with the `fx_usd_inr`
   cost rate, which the Platform Admin maintains like every other cost input.
2. **Money is integers.** Prices are `*_micros` (10⁻⁶ of the currency unit, so fractional
   per-unit overage like $0.012 is exact). Invoice amounts are `*_minor` (cents/paise), rounded
   half-up once per invoice line.
3. **Calendar-month UTC periods for everything.** Subscriptions, `usage_counters` and invoices
   share `period_start = first day of the month`. The Plan fee is charged **in advance** (at the
   upgrade, prorated by days, and on each period roll); overage is charged **in arrears** from the
   period's rollups. Period roll and invoicing are idempotent Jobs.
4. **Write boundary.** Billing tables are tenant tables with forced RLS. `codura_app` (which serves
   every Tenant request) gets **SELECT only**. Writes happen in `codura_docs.billing` on a
   dedicated engine that uses the existing `codura_worker` role, still inside a `tenant_session`
   (RLS on), plus `codura_platform` for Platform Admin actions. Global catalogue tables (`plans`,
   `plan_versions`, `plan_prices`) are readable by all and written only by `codura_platform`. A
   used Plan version is immutable (trigger); editing creates a new version.
5. **Quotas reserve before they spend.** A quota check that admits an LLM call or OCR inserts a
   short-lived `quota_reservations` row under a row lock on the period counter, and the Usage
   Event write deletes it in the same transaction. Parallel requests can't overshoot a hard limit
   or a spend cap.
6. **PaymentProvider Port** (`ports/payments.py`, specs/ports.md §9): `ensure_customer`,
   `create_checkout`, `report_invoice`, `customer_portal_url`, `verify_webhook`. Providers collect
   money; they never compute amounts. Stripe is used through plain HTTPS (no SDK): Checkout in
   `payment` mode for the prorated upgrade charge (saving the card for later), invoice items +
   invoices from our ledger, the Customer Portal for payment methods, and signed webhooks.
   `manual` applies upgrades immediately and the Platform Admin marks invoices paid. Webhooks are
   deduplicated by `(provider, event_id)` in the global `payment_events` table, which stores no
   payload, only its hash.

## Alternatives considered

### Stripe Subscriptions + Prices as the source of truth — rejected
It would duplicate Plans in Stripe per currency, make Stripe compute overage and proration, and
break local-first development. ADR-0021 already rejected Stripe metering.

### A new database role for billing writes — rejected for now
`codura_worker` is already a separate login with RLS, used only by back-office code. A fifth role
adds configuration and rotation work for no isolation gain today. It can be split out later.

### One currency (USD) with display conversion — rejected
Indian customers expect INR invoices, and card networks charge more for cross-currency payments.

### Anniversary billing periods — rejected
Calendar months match the existing `usage_counters`, simplify proration and finance reporting,
and avoid per-Tenant schedules.

## Consequences

- Billing code must open its writes on `app.state.billing_engine` / the worker engine, never the
  app engine. A test asserts `codura_app` can't write billing tables.
- Proration and period rolls need careful tests: upgrade mid-month, downgrade at period end,
  cancel, trial expiry, past due.
- The finance dashboard reports in USD; INR revenue depends on the `fx_usd_inr` rate being kept
  current.
- GST and other taxes are out of scope for M3. Stripe Tax can be switched on in the Stripe
  adapter later. Razorpay remains an optional India adapter behind the same Port.

## Escape hatch

Adding a currency means adding a `CHECK` value, a `plan_prices` row per Plan version and a country
rule. Moving billing writes to a dedicated role means changing one engine URL and the grants.
