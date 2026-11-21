# Progress

The running handoff between sessions, for humans and agents. The ledger records **what** is done
(per-Task checkboxes). This file records **where we are, what's blocked and what the next session
should know**. Keep "Current state" short and current. Add session-log entries at the top, newest
first.

## Current state

| | |
|---|---|
| Phase | M0 done; M1 done except two verification items (M1-14) |
| Current milestone | M1 → M2 (Web Channel = MVP), [ledger/M2.md](./docs/ledger/M2.md) |
| Next Task(s) | Finish M1-14 (egress-blocked CI job, Playwright UI e2e), then M2 Wave 0: M2-00 keys service hardening, M2-02 rate limits, M2-04 widget core |
| Blocked | — |
| Last updated | 2026-11-19 |

**Runs today on the dev box (GTX 1060 6 GB):** `docker compose up -d` → Admin Console at
http://localhost:8080. Signup → email verification (Mailpit :8025) → Knowledge Bases → upload
PDF/DOCX/TXT/MD/HTML/images (OCR) → Assistants → streaming multilingual Playground with
Citations → metering and trial limits → Tenant Users (read-only) → Platform Console (Tenants,
model override, suspend, audit). The Public Chat API v1 works with Channel Keys. The widget/hosted
page UI is M2.

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

- **M1-14:** the egress-blocked CI job (NFR-01) and the Playwright UI e2e are not written yet. The
  live e2e and eval scripts cover the same flow manually.
- **M1-18:** probes exist for every current route; the *automatic* route-inventory check (a new
  route without a probe fails CI) is still to do.
- **Image size:** `lingua-language-detector` adds about 160 MB to the backend image. Consider a
  lighter detector if image size matters.
- **Tenant deletion:** Blobs under the Tenant prefix are not yet purged (needs a BlobStore
  list/delete-prefix operation).
- Rate limiting for the public Chat API is per-process only (login/signup throttles). M2-02 adds
  the `RateLimiter`.
- The Platform Console shows Model Profiles read-only. Editing, prices and Custom LLM come in M3.

## Session log

<!-- Template — copy above the previous entry:
### YYYY-MM-DD — <Task IDs or topic>
- **Did:** …
- **State:** branch / PR, what's merged
- **Learned / changed in spec:** … (link ADRs or doc sections edited)
- **Next:** …
-->

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
