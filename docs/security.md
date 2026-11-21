# Security

**Status:** Draft v0.3 · **Last updated:** 2026-11-19

Read this before changing tenancy, uploads/OCR, the public Chat API, any Channel, or anything
that renders model or Document text. **Tenant isolation is the top security property.**

## 1. Trust boundaries

| Boundary | Untrusted input | Main risk |
|---|---|---|
| Tenant ↔ Tenant | Everything one Tenant's Admins or Visitors send | **Reading, retrieving, citing or enumerating another Tenant's data** |
| Public Chat API ← Channels | Visitor messages, headers, Channel Keys (public by design), IDs | Cross-Tenant access via IDs, abuse/DoS of the local LLM, prompt injection |
| Admin API ← Admins | Uploaded files and images, settings | Malicious files (parser/OCR exploits, zip and decompression bombs), stored XSS |
| Platform API ← Platform Admin | Tenant management, Plans, prices, Custom LLM credentials | Over-broad cross-Tenant code paths, leaked provider keys |
| Signup ← Internet | Self-service registrations | Fake Tenants burning free LLM quota, enumeration |
| Payment provider → webhooks | Payment events | Forged "paid" events |
| Visitors → Tenant | Optional email | PII handling |
| Documents → LLM | Document text placed in prompts | Indirect prompt injection |
| LLM → Channels | Model output | XSS / phishing links in host pages or apps |
| Backend → Providers | — | Sending a Tenant's text to a remote Provider when local-only is expected |

## 2. Requirements

| ID | Requirement | Milestone |
|---|---|---|
| SEC-01 | Admin auth: role checks on every route (Platform Admin / Tenant Admin / Tenant User, ADR-0020); argon2id, HttpOnly + Secure + SameSite=Lax session cookie, CSRF token on unsafe methods, login rate limit + lockout, invite tokens single-use and expiring | M1 |
| SEC-02 | **Tenant isolation in storage**: `tenant_id` on every tenant table, composite `(tenant_id, id)` FKs, forced fail-closed RLS, app role without BYPASSRLS, migration test that fails on any tenant table without a policy (specs/db.md §1) | M0 (base), M1 (all tables) |
| SEC-03 | Uploads: size and page caps (NFR-07), type by magic bytes, DOCX zip-bomb guard, encrypted PDFs rejected, parsing/OCR only in the worker with per-page timeouts and memory limits, Blob keys are tenant-prefixed hashes | M1 |
| SEC-04 | Keys: `pk_` / `sk_` random 32-byte, stored as SHA-256, shown once, revocable, bound to one Tenant; public routes accept only `pk_`, admin only session or `sk_` | M2 (M1 for `sk_`) |
| SEC-05 | `widget`/`wordpress` Channel Keys accepted only from Allowed Origins; CORS reflects only those. Origin is a browser control, not authentication; SEC-06 is the real limit | M2 |
| SEC-06 | Public Chat API abuse limits: per-key, per-Tenant and per-IP+Visitor token buckets; max question length 2,000 chars; max concurrent streams per key and per Tenant (so one Tenant can't starve the shared LLM); max output tokens; timeouts. Behind a `RateLimiter` interface | M2 |
| SEC-07 | Output rendering: Markdown → HTML via strict sanitiser allowlist; no raw HTML, `javascript:` or `data:` URLs; admin UI renders Document/Conversation text as text | M1 (admin), M2 (web Channels), M3 (others) |
| SEC-08 | Prompt-injection hardening: Document text inside `<context>` marked as data; system-prompt canary in evals; Assistants have no tools | M1 |
| SEC-09 | **Tenant isolation in Retrieval and answering**: every vector/FTS query runs under the Tenant's RLS context *and* filters `tenant_id` + the Assistant's Knowledge Base IDs; Assistant KB IDs validated as same-Tenant; prompts only ever contain one Tenant's Chunks; no cross-Tenant caches | M1 |
| SEC-10 | Logs: no Document text, prompts or Visitor messages at INFO; keys and passwords redacted; every log line tagged `tenant_id` | M0 |
| SEC-11 | Provider egress: remote Providers only via platform-managed Model Profiles (ADR-0019); the Platform Console flags non-local profiles and which Tenants/Plans use them; the Tenant privacy notice lists sub-processors per Plan; NFR-01 egress test in CI | M1 |
| SEC-12 | Secrets only via env / Docker secrets; startup refuses default secrets when `APP_ENV=production` | M0 |
| SEC-13 | Dependencies: lockfiles committed; Dependabot, `pip-audit`, `pnpm audit`, secret scanning; no AGPL/GPL in the backend image (ADR-0008) | M0 |
| SEC-14 | WordPress: capability checks, nonces, sanitise/escape; Android: no Secret Keys, cleartext only in debug, no WebView JS bridge | M3 |
| SEC-15 | **Tenant resolution only from credentials**: session's current Tenant (must be a Member), `sk_`'s Tenant, or `pk_`'s Tenant. Tenant IDs in paths/bodies are ignored; another Tenant's object IDs return 404 (not 403); hosted-page slugs don't reveal existence; Jobs carry the enqueuing context's `tenant_id`; cross-Tenant code only in `platform/`, audited | M1 |
| SEC-16 | Image/OCR safety: Pillow `MAX_IMAGE_PIXELS` enforced (decompression bombs), multi-page TIFF frame cap, render DPI capped, Tesseract run with timeout and no shell, OCR languages validated against installed packs | M1 |
| SEC-17 | Secrets at rest: provider credentials (platform and Custom LLM) encrypted with AES-256-GCM via `crypto.py`, key from `SECRETS_MASTER_KEY` (versioned for rotation); write-only in APIs (never returned); decrypted only inside the registry | M0 (helper), M1 |
| SEC-18 | Signup abuse: per-IP/email rate limits, disposable-domain blocklist, email verification before uploads/chat, optional self-hosted captcha hook, uniform responses (no account enumeration), trial quotas bound the cost of a fake signup | M1 |
| SEC-19 | Visitor email PII: stored encrypted + hashed for lookup, visible/exportable only to Tenant Admins, deletable on request, purged with Conversation retention; never used as identity (ADR-0023) | M2 |
| SEC-20 | Billing integrity: payment webhooks signature-verified and deduplicated by event ID; only webhooks and the Platform Admin change payment status; Usage Events append-only and idempotent; Tenants can't write meters, prices, Plans or Entitlements; billing actions require Tenant Admin | M1 (events), M3 |
## 3. Data handling

- Deleting a Document deletes its Chunks in the same transaction and its Blob when no other
  Document **in the same Tenant** shares the hash. Citation snippets that point to it are nulled.
- Deleting a Tenant removes all of its rows (cascade) and its Blob prefix (Job).
- Conversation retention: per-Tenant setting (default 90 days), enforced by a scheduled purge Job (M4).
- Visitor IDs are random and kept per Channel Key on the client, so the same device talking to two
  Tenants has two unrelated identities.

## 4. Security testing

| Test | Where | From |
|---|---|---|
| **Cross-Tenant probe suite**: two Tenants with overlapping content; every admin, platform, public and Job path; Retrieval and Citations; ID guessing; must return 404 / empty | `tests/integration/test_isolation.py` | M1 (merge gate) |
| RLS coverage test: every table with `tenant_id` has a forced policy | `tests/integration/test_rls_coverage.py` | M0 |
| Key-type, Origin and Tenant matrix for every public route | `tests/integration/test_public_auth.py` | M2 |
| Malicious file corpus: zip bomb, decompression-bomb PNG, huge TIFF, encrypted PDF, polyglots | `tests/integration/test_upload_hostile.py` | M1 |
| XSS payloads in answers/Citations rendered by Widget and Hosted Page | Vitest + Playwright | M2 |
| Prompt-injection and multilingual injection eval cases | `tests/eval/golden/` | M1 |
| Egress-blocked full flow (NFR-01) | CI job, internal network | M1 |
| Role matrix (Tenant User read-only, platform routes platform-only) | `tests/integration/test_roles.py` | M1 |
| Signup abuse (rate limits, enumeration, unverified restrictions) | `tests/integration/test_signup.py` | M1 |
| Metering idempotency and reconciliation under retries/concurrency | `tests/integration/test_metering.py` | M1 |
| Webhook forgery/replay, Tenant attempts to alter Plans/usage | `tests/integration/test_billing_security.py` | M3 |
| SAST / dependency / secret scanning | `.github/workflows/security.yml` | M0 |
