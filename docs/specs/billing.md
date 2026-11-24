# Spec — Billing (Plans, Entitlements, quotas, invoices, payments)

**Status:** Frozen v1.0 (M3-00) · **Last updated:** 2026-11-24 · **ADR:** 0021, 0026 · **Implements:** FR-60…70, NFR-14…16, SEC-20

This spec turns [billing.md](../billing.md) into schema and code. Migration
`backend/migrations/versions/0004_billing.py` is normative for the DDL.

## 1. Money and currencies

| Rule | Value |
|---|---|
| Billing currency | `INR` when the billing country is `IN`; `USD` for every other country (ADR-0026 §1). Fixed at the first issued invoice (`billing_accounts.currency_locked_at`) |
| Price unit | `*_micros`: 10⁻⁶ of the currency unit (`29_000_000` = $29; `15_000` = $0.015) |
| Invoice unit | `*_minor`: cents or paise. `amount_minor = round_half_up(quantity × unit_micros / 10⁴)` per line |
| COGS | micro-USD on every Usage Event (unchanged). Revenue in INR converts with `cost_rates.fx_usd_inr` (INR micros per USD) |
| Period | Calendar month, UTC. `period_start` = first day; the period ends at the next month's first day |
| Plan fee | In advance: prorated at upgrade, full at each period roll |
| Overage | In arrears, on the period-end invoice, from `usage_rollups` |

Proration for a change on day `d` of a month with `D` days: `remaining = (D − d + 1) / D`. Upgrade
charge = `(new_base − old_base) × remaining`, never negative. A downgrade takes effect at the period
roll and earns no refund.

## 2. Tables

Tenant tables have forced RLS. Grants follow ADR-0026 §4:
- `codura_app` has **SELECT** only on billing tables.
- `codura_worker` writes them inside a `tenant_session` (`billing/` code only).
- `codura_platform` writes them for Platform Admin actions.

| Table | Scope | Purpose |
|---|---|---|
| `plans(id, code citext UNIQUE, name, description, sort_order, is_public, is_free, status, current_version_id)` | global | A sellable Plan. `is_public = false` hides it from Tenants (Enterprise) |
| `plan_versions(id, plan_id, version, included jsonb, entitlements jsonb, overage_enabled, default_model_profile_id, target_margin_pct, loss_leader, used_at, created_by)` | global | Billing terms. **Immutable once `used_at` is set** (trigger). Editing a used version creates version n+1 (FR-61) |
| `plan_prices(plan_version_id, currency, base_micros, overage_micros jsonb)` | global | One row per currency. `overage_micros = {meter: micros per unit}` |
| `billing_accounts(tenant_id PK, country, currency, billing_email, company_name, tax_id, address, provider, provider_customer_id, currency_locked_at)` | tenant | Who pays and how. `CHECK ((country = 'IN') = (currency = 'INR'))` |
| `subscriptions(id, tenant_id UNIQUE, plan_version_id, status, period_start, trial_ends_at, pending_plan_version_id, cancel_at_period_end, spend_cap_micros, payment_failed_at)` | tenant | Exactly one per Tenant. `status ∈ trialing, active, past_due` |
| `subscription_events(… action, from/to plan version, from/to status, actor, details)` | tenant | Audit of every transition (append-only) |
| `tenant_billing_overrides(tenant_id PK, included, entitlements, overage_enabled, note, updated_by)` | tenant | Platform Admin deal terms; merged over the Plan version |
| `quota_reservations(id, tenant_id, meter, period_start, quantity, expires_at)` | tenant | In-flight admissions (§4). Expire after 10 min |
| `quota_notices(tenant_id, meter, period_start, threshold)` | tenant | 80 % / 100 % notices sent once per period |
| `usage_counters(…, updated_at)` | tenant | Unchanged keys; still updated in the Usage Event's transaction |
| `usage_rollups(tenant_id, day, meter, model_profile_id, quantity, cogs_micros, events)` | tenant | Daily rollups from `usage_events` (M3-08). `model_profile_id = ''` when none |
| `invoices(id, tenant_id, number UNIQUE, subscription_id, kind, period_start, currency, status, subtotal_minor, total_minor, issued_at, due_at, paid_at, provider, provider_invoice_id, hosted_url, idempotency_key)` | tenant | `kind ∈ period, proration, adjustment`; `status ∈ draft, open, paid, void, uncollectible`. `UNIQUE (tenant_id, idempotency_key)` makes every issue idempotent |
| `invoice_lines(id, tenant_id, invoice_id, kind, meter, description, quantity, unit_micros, amount_minor, sort)` | tenant | `kind ∈ base, overage, proration_credit, proration_charge, adjustment` |
| `payments(id, tenant_id, invoice_id, provider, provider_payment_id, amount_minor, currency, status, method, recorded_by, note)` | tenant | Recorded by webhooks or by a Platform Admin (`manual`) |
| `payment_events(provider, event_id, event_type, payload_sha256, status, error, received_at, processed_at)` | global | Webhook dedupe. Holds no Tenant data and no payload |

Invoice numbers: `CD-YYYYMM-<invoice_number_seq>`. Subscription ids are `sub_<tenant_id>`.

`SECURITY DEFINER` lookups, granted to `codura_worker` and `codura_platform` only:
- `billing_tenant_for_customer(provider, customer_id)` → `tenant_id`, for webhooks.
- `billing_due_tenants(now)`: Tenants whose period rolled, whose Trial ended, or whose payment failed.
- `billing_tenants_with_usage(day)`: the Tenants a rollup day must visit.

`activate_tenants_of_admin` (email verification) now creates the Trial Subscription in the same
transaction.

`system_settings.billing = {free_plan_code, trial_plan_code, trial_days, grace_days}`. Seeded with
`free`, `growth`, 14 and 7.

New `cost_rates` keys:

| Key | Seed | Unit |
|---|---|---|
| `fx_usd_inr` | 84,000,000 | INR micros per USD |
| `payment_fee_bps` | 290 | basis points |
| `payment_fee_fixed` | 300,000 | USD micros per charge |
| `fixed_cost_per_tenant_month` | 2,000,000 | USD micros |
| `custom_llm_infra_per_answer` | 200 | USD micros |
| `expected_utilisation_pct` | 60 | percent |
| `typical_answer_tokens_in` | 4,000 | tokens |
| `typical_answer_tokens_out` | 300 | tokens |

## 3. Included quantities and the Entitlement registry

`plan_versions.included`. **Flow** meters reset every period. **Stock** limits measure what exists
now; they are hard limits on every Plan, and the Tenant upgrades to raise them.

| Key | Kind | Measured from |
|---|---|---|
| `answer` | flow | `usage_counters` meter `answer` (and `answer_custom_llm`, §4) |
| `page_ocr` | flow | `usage_counters` meter `page_ocr` |
| `pages_stored` | stock | `sum(documents.page_count)` of non-deleted Documents |
| `storage_mb` | stock | `sum(documents.size_bytes)` / 1 MiB |
| `seats` | stock | Tenant members + open invites |
| `assistants` | stock | Assistants |

`plan_versions.entitlements`. The typed registry lives in `billing/entitlements.py` (`REGISTRY`).
Unknown keys are rejected by the Plan editor.

| Key | Type | Default (missing) | Enforced in |
|---|---|---|---|
| `channels` | list of `hosted, widget, wordpress, android, api` | `["hosted","widget"]` | Channel Key creation, the hosted toggle and `resolve` of existing keys |
| `remove_branding` | bool | false | `/api/v1/channel/config` → `branding` |
| `visitor_email` | `off, optional, required` | `off` | Effective Assistant mode = min(Assistant setting, Entitlement) |
| `analytics_retention_days` | int | 7 | Conversations list, Insights windows |
| `custom_llm` | bool | false | Model resolution (§5) |
| `max_output_tokens` | int | 500 | Answering (NFR-16) |
| `max_input_tokens` | int | 4,000 | Answering: caps the Context Pack + history (NFR-16) |
| `context_token_budget` | int | 2,000 | Retrieval budget = min(Assistant setting, Entitlement) |
| `max_concurrent_answers` | int | 2 | Per-Tenant concurrency cap = min(setting, Entitlement) |

Resolution (`entitlements.for_tenant(session, tenant_id)`):
1. Start from the Subscription's Plan version.
2. If `status = trialing` and the Trial hasn't ended, use the **trial Plan's** current version for
   `included` and `entitlements`, with overage off.
3. Merge `tenant_billing_overrides` key by key.
4. No Subscription (e.g. an unverified Tenant) resolves to the Free Plan.

`entitlements.require(…, key)` raises `PlanRequired` → **403 `plan_required`**, with
`details.entitlement`.

## 4. Quota admission (`metering/quotas.py`)

`admit(session, tenant_id, meter, quantity)` runs before the expensive work, in one transaction:

1. Resolve Entitlements, and lock the period counter row (`INSERT … ON CONFLICT DO NOTHING`, then
   `SELECT … FOR UPDATE`).
2. `used = counter.quantity + Σ live reservations`.
3. Within the included amount: admit. Above it with overage enabled:
   - check the spend cap: `(used + q − included) × overage_price ≤ spend_cap_micros`;
   - admit, marked `overage`.

   Otherwise **deny** with reason `quota_exceeded` or `spend_cap_reached`.
4. When admitted, insert a reservation and return its id. `metering.record(…, reservation_id=…)`
   deletes it in the Usage Event's transaction, and `release()` deletes it for outcomes that aren't
   billable.
5. After `record`, crossing 80 % or 100 % of an included flow meter inserts a `quota_notices` row
   (`ON CONFLICT DO NOTHING`). The first insert queues `send_email` Jobs to the Tenant Admins in the
   same transaction. The Admin Console banner reads `quota_notices`.

Custom LLM answers use meter `answer_custom_llm` and share the `answer` allowance. Their overage
price is the platform fee in `overage_micros.answer_custom_llm`.

Denied chat streams the Assistant's `limit_reached_text`, with no LLM call. Denied ingestion leaves
the Document `pending` with `error = 'quota_exceeded'`.

Ingestion admits per Document, because OCR page counts are only known after parsing:
- a Document starts while `pages_stored` has room and, for PDFs and images, while `page_ocr` has
  allowance (or overage is on);
- after parsing, it is paused if its pages would exceed `pages_stored`;
- its OCR pages are metered in full, bounded by `MAX_PAGES`.

## 5. Model resolution (amends ADR-0019 order, no change)

Model resolution tries, in order:
1. The Tenant-scoped profile (`model_profiles.tenant_id = tenant`), only with `custom_llm`.
2. `tenants.model_profile_override`.
3. The Plan version's `default_model_profile_id`.
4. `system_settings.default_model_profile`.

Only active profiles count.

## 6. Subscription lifecycle (`billing/subscriptions.py`, `jobs/billing_period.py`)

| Transition | Who | Effect |
|---|---|---|
| verify email | system | `trialing` on Free, `trial_ends_at = now + trial_days` |
| upgrade (paid Plan, higher base) | Tenant Admin | Proration charge invoice (`kind=proration`). `manual`: applied immediately, invoice `open`. `stripe`: Checkout first, applied on `checkout.completed`. Trial ends |
| downgrade / switch to a cheaper Plan | Tenant Admin | `pending_plan_version_id`, applied at the period roll. Refused with 409 `over_new_limits` if a stock limit would be exceeded |
| cancel | Tenant Admin | `cancel_at_period_end = true` → Free at the roll |
| spend cap | Tenant Admin | `spend_cap_micros` (subscription currency), or `null` for no cap |
| period roll (Job) | system | Period-end invoice = overage(ended period) + base(next period, after pending changes). Then `period_start` advances |
| trial ends (Job) | system | `trialing` → `active` on Free |
| payment failed (webhook / admin) | provider | `payment_failed_at` set; after `grace_days` → `past_due`, `tenants.status = past_due` |
| payment succeeded | provider / Platform Admin | Invoice `paid`, `payment_failed_at` cleared, `past_due` → `active` |
| plan assignment | Platform Admin | Any Plan, including non-public ones, immediately, without proration |

While `past_due`:
- Channels answer the localized "service unavailable" text, with no LLM call.
- Tenant write routes return **402 `payment_required`**, except billing routes.

## 7. Jobs

| Kind | Schedule | Idempotent by |
|---|---|---|
| `usage_rollup` `{day}` | hourly for today, daily for yesterday (worker scheduler) | upsert per `(tenant, day, meter, profile)` |
| `usage_snapshot` `{day}` | daily | `storage_mb_day` / `seat_day` events keyed `date` |
| `billing_cycle` | hourly | `invoices.idempotency_key = period:<YYYY-MM>`, subscription state checks |
| `send_email` (quota notice) | queued in the Usage Event transaction | `quota_notices` PK |

The worker runs a scheduler loop. Each slot `(name, window)` is claimed once in the global
`scheduled_runs(name, run_window, enqueued_at)` table by inserting `ON CONFLICT DO NOTHING`, so
several workers never enqueue twice.

## 8. PaymentProvider Port (`ports/payments.py`, specs/ports.md §9)

```python
class PaymentProvider(Protocol):
    name: str                      # 'manual' | 'stripe'
    immediate_upgrades: bool       # manual: True (no redirect)
    async def ensure_customer(self, account: BillingAccount) -> str: ...
    async def create_checkout(self, req: CheckoutRequest) -> CheckoutSession: ...
    async def report_invoice(self, invoice: InvoiceDraft) -> ExternalInvoiceRef: ...
    async def customer_portal_url(self, customer_id: str, return_url: str) -> str | None: ...
    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> ProviderEvent: ...
```

`ProviderEvent.kind` is one of:
- `checkout_completed`
- `invoice_paid`
- `invoice_payment_failed`
- `ignored`

`verify_webhook` raises `WebhookVerificationError` on a bad signature or a timestamp older than
5 minutes. The contract suite (`tests/contract/test_payment_contract.py`) runs against `manual`
and `stripe` (with a mocked HTTPS transport and the signing scheme). It covers:
- idempotent `ensure_customer`;
- checkout returning a URL or the immediate flag;
- `report_invoice` amounts equal to our lines;
- a forged signature is rejected;
- a replayed event is rejected by the dedupe table.

## 9. HTTP

| Route | Role | Notes |
|---|---|---|
| `GET /api/admin/billing` | Tenant User | The current Plan with its Entitlements and included amounts, usage vs quota with a projection, the subscription state, the spend cap and recent invoices. Never COGS or model names |
| `GET /api/admin/billing/plans` | Tenant User | Public Plans with the prices in the Tenant's currency (or both currencies, if not yet chosen) |
| `PUT /api/admin/billing/account` | Tenant Admin | Country, billing email, company, tax id, address. Country can't change after the currency is locked |
| `POST /api/admin/billing/change-plan` `{plan_code}` | Tenant Admin | Upgrade, downgrade or switch → `{applied, redirect_url?, invoice_id?}` |
| `POST /api/admin/billing/cancel` · `POST …/resume` | Tenant Admin | Cancel at period end, or undo the cancellation |
| `PUT /api/admin/billing/spend-cap` `{spend_cap_micros \| null}` | Tenant Admin | |
| `POST /api/admin/billing/portal` | Tenant Admin | → `{url}`. 409 `not_available` on `manual` |
| `GET /api/admin/billing/invoices` · `GET …/{id}` · `GET …/{id}/html` | Tenant User | The HTML invoice is printable and goes through the console's sanitised template |
| `POST /api/webhooks/payments/{provider}` | provider signature | 400 on a bad signature; 200 for duplicates |
| `GET/POST /api/platform/plans` · `GET/PATCH …/{id}` · `POST …/{id}/versions` · `POST /api/platform/plans/margin` | Platform Admin | Versioned editor. 422 `below_margin_floor`, unless `loss_leader` is set (audited) |
| `GET/PUT /api/platform/cost-rates` | Platform Admin | |
| `POST/PATCH /api/platform/model-profiles` · `POST …/{id}/test` | Platform Admin | Credentials write-only. Licence allowlist enforced. Tenant-scoped (Custom LLM) when `tenant_id` is set |
| `GET/PUT /api/platform/tenants/{id}/billing` | Platform Admin | Subscription, overrides, assign Plan, `custom_llm` |
| `GET /api/platform/invoices?status=` · `POST …/{id}/mark-paid` · `POST …/{id}/void` | Platform Admin | `manual` collection |
| `GET /api/platform/finance/summary?days=` · `…/tenants` · `…/simulate` · `…/funnel` | Platform Admin | Detailed in billing.md §9 |
