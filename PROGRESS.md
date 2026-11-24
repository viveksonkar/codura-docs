# Progress

The running handoff between sessions, for humans and agents. The ledger records **what** is done
(per-Task checkboxes). This file records **where we are, what's blocked and what the next session
should know**. Keep "Current state" short and current. Add session-log entries at the top, newest
first.

## Current state

| | |
|---|---|
| Phase | M0–M3 done: MVP + **monetisation** (Plans, quotas, invoices, Stripe adapter, finance, Custom LLM) |
| Current milestone | M4 WordPress plugin & Android, [ledger/M4.md](./docs/ledger/M4.md) (decomposed) |
| Next Task(s) | M4-00 Channel contract freeze, then Wave 1: M4-01, M4-02, M4-03 |
| Blocked | Live Stripe test-mode run (M3-06) waits for the owner's test keys; adapter and webhooks are tested against mocked HTTPS |
| Last updated | 2026-11-24 |

**Runs today on the dev box (GTX 1060 6 GB):** `docker compose up -d` → Admin Console at
http://localhost:8080. Signup → email verification (Mailpit :8025) → Knowledge Bases → upload
PDF/DOCX/TXT/MD/HTML/images (OCR) → Assistants → streaming multilingual Playground with
Citations → metering and trial limits → Tenant Users (read-only) → Platform Console (Tenants,
model override, suspend, audit). **Web Channel:** Channels page (Channel Keys with Allowed Origins,
Widget snippet, Hosted Chat Page toggle with link + QR) → Hosted Chat Page at `/c/{tenant}/{assistant}`
(seeded `/c/demo/help`) and the embeddable `<codura-chat>` Widget (`/demo/` locally) → optional
Visitor email → feedback → Conversation log viewer → Insights with unanswered questions and
Visitor emails. **Billing:**
- Tenants see Billing: Plan, usage vs quota with projection, plans in INR (India) or USD, upgrade
  (prorated, `manual` applies at once), downgrade/cancel at month end, spend cap, invoices.
- Platform Admins see Plans (versioned, live margin floor), Model Profiles (create/test/default,
  Custom LLM), Tenant billing (assign Plan, overrides), Invoices (mark paid), Finance (MRR,
  margins, unprofitable Tenants, simulator, funnel) and Cost rates.

## Owner decisions

| # | Decision | Recorded in |
|---|---|---|
| D1–D6 (2026-11-18) | Two sides; fixed embeddings; strict tenancy; multilingual; OCR in MVP; web first | ADR-0014…0017 |
| D7 (2026-11-19) | Only open-weight models licensed for commercial use; local model sized for the dev box | ADR-0018, ADR-0024 |
| D8 | Visitors are anonymous; optional email at chat start | ADR-0023 |
| D9 | Self-service signup by organisation name; Tenant Admin + read-only Tenant User | ADR-0020 |
| D10 | The LLM is platform-managed; a Custom LLM per Tenant only when a Platform Admin enables it | ADR-0019 |
| D11 | Monetised SaaS: metering, Plans, margin floor, telemetry | ADR-0021, ADR-0022, billing.md, telemetry.md |
| Q3 resolved | Launch languages: en, hi, es, fr, de, ar, zh | NFR-13 |
| Q10 resolved (2026-11-24) | INR for Tenants billed in India, USD for the rest of the world; Stripe first (test keys later); `manual` locally | ADR-0026, specs/billing.md |

## Open questions

| # | Question | Default taken |
|---|---|---|
| Q5 | Android: SDK for other apps, a standalone app, or both? | SDK + sample app (M4) |
| Q11 | Production hosting: one GPU VPS with vLLM, a hosted open-weight API, or Anthropic/OpenAI for premium Plans? | Decide with the M6 load test; prices are already metered per Model Profile |

## Known gaps / follow-ups

- **CI `e2e.yml` hasn't run on GitHub yet.** Both jobs were exercised locally: the egress job's
  steps (internal-only network, no-egress probe, `live_e2e.py`) and the Playwright suite (9 specs)
  pass on the dev box. On GitHub runners they use CPU models, so expect them to be slow.
- **Rate limits are per `api` process** (`ratelimit/` in-memory). That is fine for one replica
  (ADR-0013); M6 scale-out adds a shared implementation behind the same Protocols.
- **Signup throttle (SEC-18) is per process too:** repeated local e2e runs (two signups each) hit
  5/hour/IP. `docker compose restart api` clears it.
- **Widget cache:** `/widget/v1/` is cached 1 h, not immutable (channels.md §3). Per-release
  immutable paths are an M6 distribution item.
- **Image size:** `lingua-language-detector` adds about 160 MB to the backend image. Consider a
  lighter detector if image size matters.
- **Tenant deletion:** Blobs under the Tenant prefix are not yet purged (needs a BlobStore
  list/delete-prefix operation).
- **Stripe live run:** set `STRIPE_SECRET_KEY` + `STRIPE_WEBHOOK_SECRET` (test mode) and
  `PAYMENT_PROVIDER=stripe`. Then point a Stripe webhook (or `stripe listen`) at
  `/api/webhooks/payments/stripe` with `checkout.session.completed`, `invoice.paid` and
  `invoice.payment_failed`, and run one upgrade by hand. INR needs a Stripe account that can
  charge INR.
- **Taxes:** GST/VAT aren't computed. Tax ID and country are stored; Stripe Tax can be switched on
  later (ADR-0026).
- **Live Anthropic contract** runs only with `CODURA_TEST_ANTHROPIC_KEY` (it costs money). The
  recorded-stream contract runs in every CI run.
- **Margin floor inputs:** the seed `cost_rates` (`fx_usd_inr` 84, fees, fixed cost, utilisation)
  are placeholders. Set real values in Platform Console → Cost rates before setting prices.
- **M4 toolchains:**
  - PHP 8.3 is installed at
    `%LOCALAPPDATA%\Microsoft\WinGet\Packages\PHP.PHP.NTS.8.3_*`, but the machine PATH lists WAMP's
    PHP 5.6 first. Fixing that needs an elevated shell.
  - Composer isn't installed yet.
  - The Android SDK is at `C:\Android\sdk` (platform 35, build-tools 35, platform-tools; JDK 17),
    with no emulator image yet.

## Session log

<!-- Template — copy above the previous entry:
### YYYY-MM-DD — <Task IDs or topic>
- **Did:** …
- **State:** branch / PR, what's merged
- **Learned / changed in spec:** … (link ADRs or doc sections edited)
- **Next:** …
-->

### 2026-11-24 — M3 Monetisation complete; M4 decomposed; M4 toolchains installed
- **Did:**
  - All 14 M3 Tasks:
    - billing spec + ADR-0026 + migration 0004;
    - Entitlements service; quota reservations; rollups + reconciliation + worker scheduler;
    - subscriptions lifecycle; invoices/ledger; `manual` + `stripe` PaymentProviders with signed,
      deduplicated webhooks;
    - Anthropic adapter; Plan editor with margin floor; Model Profile management + Custom LLM;
    - finance views; Tenant Billing page + quota banner; Platform Console Plans / Models / Tenant
      billing / Invoices / Finance / Cost rates.
  - The demo Tenant is seeded on Growth.
  - Smoke has 9 checks (billing).
  - M2 branch committed and pushed (`task/m2-web-channel`).
  - Installed PHP 8.3 and the Android SDK.
- **Verified:**
  - Static checks: ruff, format, mypy --strict and import-linter clean.
  - Backend: 122 pass, 13 skipped. New suites:
    - `test_billing.py` (9 flows): write boundary, INR upgrade, hard stop + 20-way concurrency,
      period invoice = ledger + reconciliation, Stripe webhooks/forgery/replay/past-due, margin
      floor + versions, finance flag, Custom LLM, scheduler.
    - Payment and Anthropic contracts.
    - The cross-Tenant gate now covers every billing route.
  - Web: 54 Vitest (widget 43, admin 6, chat-client 5); widget 38.3 KB gz.
  - Playwright: 11 specs incl. billing UI.
  - `smoke.sh` and `smoke.ps1`: 9/9 on the rebuilt stack.
- **Learned / changed in spec:**
  - Billing writes run as `codura_worker` inside RLS; `codura_app` is SELECT-only on billing
    (ADR-0026).
  - Calendar-month periods: fee in advance, overage in arrears.
  - Stock limits (pages, storage, users, Assistants) never overage.
  - OCR is admitted per Document (specs/billing.md §4).
  - `trial_*` settings were removed (the Trial is the Growth Plan's limits for 14 days).
  - `tenants.plan_code` is deprecated.
  - The demo Tenant on Growth hides the "Powered by" footer (parity e2e updated).
- **State:** branch `task/m3-monetisation` (from `task/m2-web-channel`), not committed.
- **Next:** M4-00, then the WordPress plugin (Docker `wp` profile, PHP 8.3) and the Android client.

### 2026-11-24 — M1 closed, M2 (Web Channel MVP) complete, M3 decomposed
- **Did:** M1-14 and M1-18 closed. All 12 M2 Tasks: Channel Keys service + admin API with
  system-managed hosted keys (ADR-0025, migration 0003); per-key Origin rules + dynamic CORS; token
  buckets + concurrency caps (`ratelimit/`); Hosted Chat Page API + nginx `/c/*`; Conversation log
  viewer; Visitor email (required/optional/off, encrypted, CSV export, DSAR delete); Insights with
  unanswered questions. Web: Preact `<codura-chat>` Widget in Shadow DOM (7 languages, RTL,
  sanitised Markdown, Citations, stop/retry/offline, pre-chat form, JS API), hosted page, demo
  page, Admin Console Channels / Conversations / Insights pages and Assistant theme settings.
  Shared SSE fixtures in `docs/specs/fixtures/sse/`. Playwright suite `tests/e2e/`, CI `e2e.yml`
  (egress-blocked flow + browser parity), smoke scripts extended. M3 decomposed into 14 Tasks.
- **Verified:** ruff, mypy --strict, import-linter clean; backend 90 pass, 9 skipped (live-model
  and SMTP contracts need their env vars) (unit + contract +
  integration, including the key × Origin × Tenant matrix and the route-inventory gate); web 51
  Vitest tests; widget.js 38.3 KB gz (budget 40); Playwright 9/9 against the compose stack with
  real models (Hosted + Widget parity checklist, Origin, cross-Tenant, XSS, Admin UI signup flow);
  `smoke.sh` and `smoke.ps1` 8/8; full live flow passes on an internal-only Docker network (NFR-01).
- **Learned / changed in spec:** the parity e2e found that short follow-ups in a new language
  ("And how do I reset it?": en 0.50, de 0.36) fell below the detector's 0.5 floor and were answered
  in the previous turn's language. The detector now also accepts a clear lead (rag-pipeline.md §3).
  Hosted keys must be retrievable, so they are stored encrypted (ADR-0025). `/widget/v1/` is cached
  for 1 h rather than immutably (channels.md §3). `api.md` §2/§3 and `db.md` §2 updated.
- **State:** branch `task/m2-web-channel`, committed and pushed (`df1bdb9`).
- **Next:** M3-00 (billing spec + schema freeze), ideally after the owner answers Q10.

### 2026-11-19 — M0 complete, M1 implemented
- **Did:** All 10 M0 Tasks and 20 of 21 M1 Tasks (M1-14 partial). Backend: FastAPI + Postgres/RLS
  (migrations 0001–0002, roles app/worker/platform, SECURITY DEFINER lookups); Ports + Adapters
  (Ollama, OpenAI-compatible, Tesseract, lingua, local/S3 blobs, SMTP, fakes); Job queue +
  worker; signup/verify/reset/invites; roles; ingestion with OCR; hybrid cross-lingual retrieval;
  grounded multilingual answering over SSE; metering with COGS and trial limits; Platform Console
  API; public Chat API v1. Web: chat-client (SSE parser) and the Admin Console (React). Compose:
  GPU by default, Mailpit, Caddy. CI and security workflows, licence check, smoke scripts.
- **Verified:** ruff, mypy --strict and import-linter clean; 61 backend tests (unit, contract,
  integration incl. the cross-Tenant probe flow) + 7 live-model contract tests + 5 chat-client
  tests pass. `scripts/smoke.ps1` OK. `scripts/live_e2e.py` OK with real models.
  `scripts/eval.py`: hit@1 1.0, language accuracy 1.0, fallback precision 1.0 on 14 questions
  (en/hi/es/de), p50 1.7 s.
- **Learned / changed in spec:** `qwen3:4b` is a thinking model → default is now
  `qwen3:4b-instruct-2507-q4_K_M` with 6 GB-GPU Ollama settings (ADR-0024). Added a
  LanguageDetector Port (ports.md §5b). `sessions.current_tenant_id` renamed and RLS added to
  `model_profiles` (db.md). DB URLs per role (deployment.md). The RLS coverage test caught two
  unscoped tables during development.
- **State:** Not committed. Local `.env` adds `compose.dev.yml` (Postgres on 127.0.0.1:5432).
- **Next:** Close M1-14, then M2.

### 2026-11-19 — Requirement revision v0.3 (monetisation)
- **Did:** ADR-0018…0023, billing.md, telemetry.md; requirements v0.3; roadmap M0–M6; ledger re-cut.

### 2026-11-18 — Requirement revision v0.2 and SDLC scaffold
- **Did:** Tenancy, fixed embeddings, multilingual, OCR, web-first decisions; initial docs, ADRs,
  specs and ledger.
