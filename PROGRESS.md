# Progress

The running handoff between sessions, for humans and agents. The ledger records **what** is done
(per-Task checkboxes). This file records **where we are, what's blocked and what the next session
should know**. Keep "Current state" short and current. Add session-log entries at the top, newest
first.

## Current state

| | |
|---|---|
| Phase | M0, M1 and M2 done: **the MVP is complete** (requirements.md §6 steps 1–9) |
| Current milestone | M3 Monetisation, [ledger/M3.md](./docs/ledger/M3.md) (decomposed) |
| Next Task(s) | M3-00 billing spec + schema (freeze gate), then Wave 1: M3-01, M3-08, M3-10, M3-12 |
| Blocked | M3-06 needs Stripe test keys; Q10 (currency/provider) should be answered before M3-06 |
| Last updated | 2026-11-24 |

**Runs today on the dev box (GTX 1060 6 GB):** `docker compose up -d` → Admin Console at
http://localhost:8080. Signup → email verification (Mailpit :8025) → Knowledge Bases → upload
PDF/DOCX/TXT/MD/HTML/images (OCR) → Assistants → streaming multilingual Playground with
Citations → metering and trial limits → Tenant Users (read-only) → Platform Console (Tenants,
model override, suspend, audit). **Web Channel:** Channels page (Channel Keys with Allowed Origins,
Widget snippet, Hosted Chat Page toggle with link + QR) → Hosted Chat Page at `/c/{tenant}/{assistant}`
(seeded `/c/demo/help`) and the embeddable `<codura-chat>` Widget (`/demo/` locally) → optional
Visitor email → feedback → Conversation log viewer → Insights with unanswered questions and
Visitor emails.

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

## Open questions

| # | Question | Default taken |
|---|---|---|
| Q5 | Android: SDK for other apps, a standalone app, or both? | SDK + sample app (M4) |
| Q10 | Billing currency and payment provider (Stripe vs Razorpay; INR/USD)? | `manual` provider locally; Stripe adapter first (M3) |
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
- The Platform Console shows Model Profiles read-only. Editing, prices and Custom LLM come in M3.
- **M4 toolchains:** the dev box has PHP 5.6 (the plugin targets 8.1+) and no Android SDK. The
  WordPress plugin can be tested in the planned `wp` compose profile; Android needs an SDK/emulator.

## Session log

<!-- Template — copy above the previous entry:
### YYYY-MM-DD — <Task IDs or topic>
- **Did:** …
- **State:** branch / PR, what's merged
- **Learned / changed in spec:** … (link ADRs or doc sections edited)
- **Next:** …
-->

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
- **State:** branch `task/m2-web-channel`, not committed.
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
