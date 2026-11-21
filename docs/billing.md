# Billing, Pricing & Unit Economics

**Status:** Draft v0.1 · **Last updated:** 2026-11-19 · **ADR:** 0019, 0021 · **Implements:** FR-60…71, NFR-14…16

Goal: **every Tenant is profitable or visibly flagged.** Costs are measured per answer, page and
Tenant. Prices are derived from cost with a target margin. No Tenant can create unbounded cost.

## 1. Money flow

```
Tenant pays ──▶ Plan subscription + overage ──▶ Revenue
                                                    │
Visitor question ──▶ answer ──▶ LLM tokens ─┐       ▼
Admin upload ──▶ pages ──▶ OCR + embeddings ├──▶ COGS ──▶ Gross margin per Tenant / Plan / Model
Stored content, seats, bandwidth ───────────┘
```

## 2. Meters (Usage Events)

Every billable action writes one `usage_events` row **in the same transaction** as the action
(ADR-0021). Each row records the quantity and the **COGS** in micro-USD at the moment of use.

| Meter | Unit | Emitted when | COGS source |
|---|---|---|---|
| `answer` | 1 answer | An assistant Message that called the platform LLM (Playground or Channel) | Real prompt/completion tokens × Model Profile price snapshot (incl. query rewrite call) |
| `answer_custom_llm` | 1 answer | Same, via the Tenant's Custom LLM | ≈ 0 LLM COGS (Tenant pays its provider); infra share only |
| `answer_fallback` | 1 answer | Fallback without an LLM call | Retrieval only (tiny). **Not billable**, but counted for rate limits and insights |
| `page_ingested` | 1 page | Each page/section-equivalent chunked + embedded (TXT/MD/HTML: per 3,000 chars) | Embedding compute (amortised) |
| `page_ocr` | 1 page | Each page that went through OCR (in addition to `page_ingested`) | OCR CPU seconds × CPU rate |
| `storage_mb_day` | MB·day | Daily snapshot of Blob + Chunk storage | Storage rate |
| `seat_day` | user·day | Daily snapshot of Tenant Users + Admins | ≈ 0 (pricing lever) |

`usage_events` is append-only. Corrections are compensating events. An idempotency key
(`message_id`, `document_id:page`, `date`) prevents double counting on Job retries.

## 3. Cost model (COGS)

All provider prices live in `model_profiles` (per 1M input/output tokens) and `cost_rates`
(CPU-second, GPU-hour, GB-month, payment fee %). The Platform Admin maintains them from the
providers' current price pages; **nothing is hard-coded**. Snapshots are stored on each event, so
past margins don't change when prices do.

### 3.1 Per answer

```
T_in   ≈ system prompt (~400) + Context Pack (≤ context_token_budget, default 3,000) + history (~500) + question (~50)
T_out  ≈ 200–400 (capped by plan max_output_tokens)
COGS_answer = (T_in × P_in + T_out × P_out) / 1e6        + rewrite call on follow-ups (cheap profile)
```

**Worked example with hypothetical prices.** Replace these with real ones before setting prices.
`T_in = 4,000`, `T_out = 300`:

| Model class | Hypothetical P_in / P_out ($ per 1M) | COGS / answer |
|---|---|---|
| Hosted small open model | 0.10 / 0.30 | ≈ $0.0005 |
| Mid commercial API | 0.40 / 1.60 | ≈ $0.0021 |
| Premium commercial API | 3.00 / 15.00 | ≈ $0.0165 |

Premium models cost about 30× more per answer than small open ones, so **model choice per plan is
the main profitability lever** (ADR-0019).

### 3.2 Self-hosted models (GPU)

```
P_selfhosted_per_1M_tokens = GPU_cost_per_hour / (sustained tokens per hour / 1e6) × (1 / target_utilisation)
```

Measure tokens/hour with the load test (M6) per model and GPU, and set it on the profile. Below
roughly 40 % utilisation, a hosted API is usually cheaper. The finance dashboard shows this
comparison.

### 3.3 Ingestion and storage

- `page_ingested`: bge-m3 runs self-hosted. About 600 tokens per page means about 1,700 pages per
  1M tokens, so the cost is small but not zero.
- `page_ocr`: about 2–5 CPU-seconds per page on Tesseract.
- Storage per page ≈ Blob size + about 1.5 Chunks × (4 KB vector + about 2 KB text + index
  overhead) ≈ Blob + ~20 KB.

### 3.4 Fixed and other costs

Base servers, backups, email, payment fees (% + fixed per charge), support time. These are allocated
per paying Tenant monthly in the finance dashboard, so "contribution margin" and "net margin" are
both visible.

## 4. Pricing method

For each meter: `unit_price ≥ unit_COGS / (1 − target_margin)`, with **target_margin = 70 %** by
default, configurable per Plan. A Plan's fixed price covers:

```
plan_price ≥ ( Σ included_qty_m × expected_utilisation_m × unit_COGS_m  +  fixed_cost_share ) / (1 − target_margin)
```

The Plan editor computes this live and **refuses to save** a Plan below the floor unless the
Platform Admin ticks "loss leader" (e.g. the Free plan), which is recorded in the audit log.

**Custom LLM** Tenants pay a platform fee per `answer_custom_llm` (retrieval, hosting, support)
instead of the token-inclusive answer price.

## 5. Plans and Entitlements (starting point, all values editable)

Currency and exact prices are open (PROGRESS Q10). Quantities are a starting point to tune from the
cost model and real data.

| | **Free / Trial** | **Starter** | **Growth** | **Business** | **Enterprise** |
|---|---|---|---|---|---|
| Answers / month included | 100 | 1,000 | 5,000 | 20,000 | custom |
| Pages ingested (cumulative cap) | 200 | 2,000 | 10,000 | 50,000 | custom |
| OCR pages / month | 50 | 500 | 3,000 | 15,000 | custom |
| Storage | 100 MB | 1 GB | 5 GB | 25 GB | custom |
| Seats (Tenant Admins + Users) | 2 | 3 | 10 | 25 | custom |
| Assistants | 1 | 2 | 5 | 20 | custom |
| Channels | Hosted, Widget | + | + WordPress | + Android | all |
| Answer quality (Model Profile) | local open model | standard open | standard open | premium API | choice |
| Overage allowed | no (hard stop) | yes | yes | yes | yes |
| Remove "Powered by" branding | no | no | yes | yes | yes |
| Visitor email capture | no | optional | yes | yes | yes |
| Analytics retention | 7 days | 30 days | 90 days | 1 year | custom |
| Custom LLM | no | no | no | on request | on request (Platform Admin enables) |
| Trial | 14 days of Growth limits, then Free | — | — | — | — |

Entitlements are typed keys on the Plan (`channels`, `max_assistants`, `custom_llm`,
`remove_branding`, `visitor_email`, `analytics_retention_days`, `max_output_tokens`,
`context_token_budget`, `max_concurrent_answers`…). They are checked through one
`entitlements.require(tenant, key)` service. The UI hides what a plan lacks and shows an upgrade
prompt.

## 6. Quotas and enforcement

- `usage_counters(tenant_id, meter, period_start)` is incremented atomically with the Usage Event.
  The check happens before the expensive work (LLM call or OCR):
  - **< 80 %:** normal. **80 % / 100 %:** in-console banner + email to Tenant Admins.
  - **Hard limit, no overage:** chat streams the Plan's `limit_reached_text` (per language, no LLM
    call). Ingestion Jobs stay `pending` with the reason `quota_exceeded`.
  - **Overage enabled:** continue, and bill overage at period end. An optional Tenant-set spend cap
    stops at the cap.
- **Per-answer cost cap:** `max_input_tokens` and `max_output_tokens` from Entitlements bound the
  worst-case COGS of a single answer.
- **Abuse:** SEC-06 rate limits apply on top. They also protect against a Tenant's widget being
  scraped to burn its quota.

## 7. Subscription lifecycle

| Event | Behaviour |
|---|---|
| Signup | Free plan with Trial (Growth limits, 14 days) once the email is verified |
| Trial ends | Drops to Free limits; data kept |
| Upgrade | Immediate; prorated charge; quotas raised immediately |
| Downgrade | At period end; if over the new limits, the Tenant must reduce (e.g. Assistants) before it applies |
| Period end (monthly, UTC) | Invoice = base + overage per meter, computed from our rollups; sent to PaymentProvider |
| Payment failed | Retries by the provider; **7-day grace**, then `past_due` → chat answers the "service unavailable" text, console read-only; data kept 30 days, then the Platform Admin decides |
| Cancel | Active until period end, then Free |

## 8. Payments (PaymentProvider Port)

```python
class PaymentProvider(Protocol):
    async def ensure_customer(self, tenant: TenantBilling) -> str: ...
    async def create_checkout(self, tenant_id: str, plan_version_id: str, return_url: str) -> str: ...   # hosted page URL
    async def report_invoice(self, invoice: Invoice) -> ExternalInvoiceRef: ...                           # our amounts, their collection
    async def customer_portal_url(self, tenant_id: str, return_url: str) -> str: ...
    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> ProviderEvent: ...
```

Adapters: `manual` (default; the Platform Admin records payments; no network), `stripe`
(Checkout + Customer Portal + invoice items from our ledger; Stripe Tax optional), `razorpay`
(optional, India). Webhooks: signature verified, deduplicated by event ID, and the only writes they
make are invoice and subscription status.

## 9. Platform Admin finance views

- MRR / ARR, new / expansion / churned MRR, trial → paid conversion.
- Revenue vs COGS by **Tenant, Plan, Model Profile, meter**, with gross and contribution margin.
- **Unprofitable Tenants** list (margin < floor in the last 30 days), with the cause (model, OCR,
  overage disabled…).
- LLM spend forecast vs provider invoices (reconcile our token COGS with the actual
  Anthropic/OpenAI bill monthly; > 5 % drift raises an alert).
- Price simulator: change a Plan's price or quotas and see the projected margin from last month's
  usage.

## 10. Tenant-facing billing views

Current plan and Entitlements, usage vs quota per meter (with a projection to period end),
invoices, payment method (provider portal), upgrade/downgrade, optional spend cap. **Tenants see
quantities, never our COGS or model names.**
