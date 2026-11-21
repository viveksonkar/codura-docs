# Requirements

**Status:** Draft v0.3 · **Last updated:** 2026-11-19 · **Source:** `business-idea.txt` + owner revisions 2026-11-18 and 2026-11-19 (PROGRESS.md)

## 1. Vision

A **profitable, multi-tenant SaaS** with two sides:

- **Admin side:** organisations sign up themselves. Their Tenant Admins upload documents
  (including scans and photos, in several languages) and manage users. The platform turns the
  documents into a searchable Knowledge Base.
- **Chat side:** anonymous Visitors (optionally leaving an email) ask questions in their own
  language through the Tenant's hosted chat page, website widget and, later, WordPress plugin or
  Android app. They get cited answers drawn only from that Tenant's documents.

The **platform owns the LLM**: it chooses and pays for the answering model per plan, and charges
Tenants through plans and usage. Every answer's cost is measured, so prices stay above cost.
Everything runs locally for free during development.

### Goals

- **G-1 Local-first and free to run** in development: Ollama (`qwen3:4b`, `bge-m3`), Tesseract,
  Mailpit, `manual` payments.
- **G-2 Platform-managed, switchable answering LLM.** The Platform Admin decides the model per
  Plan or Tenant. A Custom LLM per Tenant is possible only when the Platform Admin enables it. The
  embedding model is fixed (ADR-0015, ADR-0019).
- **G-3 Strict tenant isolation** (ADR-0014).
- **G-4 Multilingual**, including cross-lingual answers.
- **G-5 Scans work** in the MVP (ADR-0016).
- **G-6 Web first** (ADR-0017).
- **G-7 Local to production with the same artefacts.**
- **G-8 Profitable by design:** every billable action is metered with its cost. Plans are priced
  from cost with a target margin, and unprofitable Tenants are visible (ADR-0021).
- **G-9 Commercially safe models only:** Apache-2.0/MIT open weights or commercial APIs (ADR-0018).

### Non-goals (for now)

- Training or fine-tuning; changing the embedding model.
- Agentic tool use and web browsing.
- Logged-in Visitors (email is optional contact data only, ADR-0023).
- iOS SDK; publishing the WordPress plugin on wordpress.org.

## 2. Personas

| Persona | Side | Wants |
|---|---|---|
| **Platform Admin** (SaaS operator) | Platform | Plans and prices, model choice per Plan/Tenant, Custom LLM approvals, Tenant support, finance and health dashboards |
| **Tenant Admin** | Admin | Sign up, upload Documents/scans, configure Assistants and Channels, manage Tenant Users, manage billing |
| **Tenant User** | Admin (read-only) | Log in and view Documents, Assistants, Conversations, insights, usage |
| **Visitor** | Chat | Ask in their own language, get a fast, correct, cited answer; optionally leave an email |
| **Site integrator** | Chat | Paste a snippet (MVP) or install the plugin / SDK (later) |
| **Developer (us)** | — | `docker compose up` on a 6 GB GPU laptop/desktop and be productive |

## 3. Functional requirements

### Tenancy, signup and roles

| ID | Requirement | Pri | Milestone |
|---|---|---|---|
| FR-36 | ~~Platform Admin creates Tenants~~ | — | **Deprecated** by FR-49 (ADR-0020) |
| FR-37 | ~~Roles owner/admin/editor~~ | — | **Deprecated** by FR-50 |
| FR-38 | Every admin action is scoped to the User's current Tenant; a User in several Tenants switches explicitly | M | M1 |
| FR-39 | Every chat request is scoped to the Tenant of its Channel Key | M | M1/M2 |
| FR-48 | Platform Console: Tenants list, search, detail (plan, usage, margin), suspend/reactivate, delete, model override, quota adjustments, Custom LLM enablement | M | M1 (basic), M3 (billing parts) |
| FR-49 | **Self-service signup**: organisation name, name, email, password → Tenant on Free/Trial plan with the registrant as Tenant Admin; email verification required before uploads and chat | M | M1 |
| FR-50 | **Tenant roles**: Tenant Admin (manage everything in the Tenant incl. users and billing) and Tenant User (read-only); at least one Tenant Admin always | M | M1 |
| FR-51 | **User management screen**: Tenant Admins invite by email with a role, change roles, remove users; seat limits from the Plan | M | M1 |
| FR-52 | Password reset, email verification, invite acceptance flows via email | M | M1 |

### Documents and ingestion (admin side)

| ID | Requirement | Pri | Milestone |
|---|---|---|---|
| FR-01 | Tenant Admins upload one or many files to a Knowledge Base via console and API | M | M1 |
| FR-02 | Types: PDF (text and scanned), DOCX, TXT, Markdown, HTML, images (PNG, JPEG, TIFF, WebP) | M | M1 |
| FR-03 | OCR per page with the Tenant's OCR Languages; low-confidence pages flagged | M | M1 |
| FR-04 | Background ingestion with status, page progress and failure reason | M | M1 |
| FR-05 | Duplicate upload detection by content hash within a Knowledge Base | S | M1 |
| FR-06 | Delete a Document; it never appears in answers again | M | M1 |
| FR-07 | Re-ingest a Document | S | M1 |
| FR-08 | Chunks retain page and heading for Citations | M | M1 |
| FR-09 | Ingest from URL / sitemap | C | M5 |

### Retrieval and answering (chat side)

| ID | Requirement | Pri | Milestone |
|---|---|---|---|
| FR-10 | Hybrid Retrieval scoped to the Assistant's Tenant and Knowledge Bases | M | M1 |
| FR-11 | Streaming answers | M | M1 |
| FR-12 | Citations on every answer | M | M1 |
| FR-13 | Fallback text when nothing relevant is found (no LLM call) | M | M1 |
| FR-14 | Multi-turn with standalone-query rewrite | S | M1 |
| FR-15 | Optional Reranker | C | M5 |
| FR-16 | Visitor thumbs up/down | S | M2 |
| FR-17 | Cross-lingual Retrieval | M | M1 |
| FR-18 | Answer in the question's language | M | M1 |
| FR-19 | Welcome/fallback/limit-reached text per language | S | M1 |
| FR-53 | **Optional Visitor email** at chat start: per Assistant `off`/`optional`/`required`, with consent text; stored on the Conversation; never used as identity (ADR-0023) | M | M2 |
| FR-54 | Tenant Admins view/export captured Visitor emails and delete them on request | S | M2 |

### Providers and models

| ID | Requirement | Pri | Milestone |
|---|---|---|---|
| FR-20 | Chat, embedding and OCR go through Ports; Ollama and Tesseract are the defaults | M | M0/M1 |
| FR-21 | OpenAI-compatible chat Adapter | M | M1 |
| FR-22 | Anthropic chat Adapter | M | M3 (needed for premium plans) |
| FR-23 | ~~Assistants pick a Model Profile from config~~ | — | **Deprecated** by FR-26 (ADR-0019) |
| FR-24 | ~~Embedding model switch~~ | — | **Deprecated** (ADR-0015) |
| FR-25 | Fixed embedding model with startup guard | M | M0 |
| FR-26 | **Model Profiles are platform-managed** in the database (provider, model, params, licence, prices, encrypted credentials); Tenants can't see or choose them | M | M1 (seeded, read-only UI), M3 (full editor) |
| FR-27 | Model resolution per question: Tenant Custom LLM (if enabled) → Platform Admin override → Plan default; recorded on the Message | M | M1 (override + default), M3 (plans, custom) |
| FR-28 | **Custom LLM**: the Platform Admin enables the `custom_llm` Entitlement for a Tenant and configures/tests its own `openai_compat`/`anthropic` profile; metered as `answer_custom_llm` | S | M3 |
| FR-29 | Licence allowlist: self-hosted profiles must be Apache-2.0 or MIT (ADR-0018) | M | M1 |

### Consoles

| ID | Requirement | Pri | Milestone |
|---|---|---|---|
| FR-30 | Login; the first Platform Admin is bootstrapped from env; further Platform Admins by invite | M | M1 |
| FR-31 | Knowledge Bases and Documents management (Tenant Admin; read-only for Tenant User) | M | M1 |
| FR-32 | Assistant configuration: Knowledge Bases, system prompt, per-language texts, retrieval settings, hosted toggle, Visitor email setting (**no model choice**) | M | M1 |
| FR-33 | Playground (Tenant Admin and Tenant User; metered) | M | M1 |
| FR-34 | Channel Keys, Allowed Origins, hosted link and widget snippet (Tenant Admin) | M | M2 |
| FR-35 | Conversation log viewer | S | M2 |

### Channels

| ID | Requirement | Pri | Milestone |
|---|---|---|---|
| FR-46 | Hosted Chat Page | M | M2 |
| FR-40 | Widget (script tag / `<codura-chat>`) | M | M2 |
| FR-41 | Widget theming, Shadow DOM | M | M2 |
| FR-47 | UI localisation + RTL | M | M2 |
| FR-44 | Conversations persist on the device | S | M2 |
| FR-45 | Channel parity | M | M2 (web), M4 (others) |
| FR-55 | "Powered by Codura" branding on Channels unless the Plan's `remove_branding` Entitlement is on | S | M2 |
| FR-42 | WordPress plugin (local zip) | M | M4 |
| FR-43 | Android SDK + sample app | M | M4 |

### Billing and monetisation ([billing.md](./billing.md))

| ID | Requirement | Pri | Milestone |
|---|---|---|---|
| FR-60 | Every billable action writes an idempotent Usage Event with quantity and COGS, in the same transaction (meters in billing.md §2) | M | M1 (events), M3 (rest) |
| FR-61 | Plans (versioned) with price, included quantities, overage prices, Entitlements and default Model Profile, managed by the Platform Admin | M | M3 |
| FR-62 | Plan editor enforces the margin floor (billing.md §4) unless marked loss-leader | M | M3 |
| FR-63 | Subscriptions: trial, upgrade (prorated), downgrade at period end, cancel, past-due grace and suspension | M | M3 |
| FR-64 | Real-time quota enforcement with 80 %/100 % notices; hard stop or overage with an optional Tenant spend cap | M | M3 (M1: fixed trial limits) |
| FR-65 | Entitlement checks through one service; UI hides unavailable features with an upgrade prompt | M | M3 |
| FR-66 | Monthly invoices computed from our usage ledger; sent through the PaymentProvider Port (`manual` default, `stripe` adapter, optional `razorpay`) | M | M3 |
| FR-67 | Verified, idempotent payment webhooks updating subscription/invoice status | M | M3 |
| FR-68 | Tenant billing page: plan, usage vs quota with projection, invoices, payment method, plan change | M | M3 |
| FR-69 | Platform finance dashboard: MRR/ARR, revenue vs COGS by Tenant/Plan/Model/meter, margins, unprofitable Tenants, price simulator | M | M3 |
| FR-70 | Provider price and cost-rate tables maintained by the Platform Admin; snapshots stored on events | M | M3 |
| FR-71 | Monthly reconciliation of computed LLM COGS against provider invoices, with a drift alert | S | M6 |

### Telemetry and analytics ([telemetry.md](./telemetry.md))

| ID | Requirement | Pri | Milestone |
|---|---|---|---|
| FR-72 | Tenant insights: usage, conversations by channel and language, top questions, **unanswered questions**, feedback | M | M2 (basic), M3 |
| FR-73 | Platform growth funnel and activation metrics | S | M3 |
| FR-74 | Ops metrics, traces and alerts (incl. LLM cost-per-hour ceiling) | M | M1 (metrics endpoint), M6 (full stack) |
| FR-75 | Platform audit log for Platform Admin actions and support access | M | M1 |

## 4. Non-functional requirements

| ID | Requirement | Target |
|---|---|---|
| NFR-01 | Local-first: default config makes no paid or external calls (Ollama, Tesseract, Mailpit, `manual` payments) | CI egress-blocked test |
| NFR-02 | Fresh clone to working stack | `docker compose up` + model pull, then ≤ 3 commands |
| NFR-03 | Same images local and production | — |
| NFR-04 | First-token latency (no cold start) | Dev box (GTX 1060 6 GB, `qwen3:4b`): p95 ≤ 3 s. Production: p95 ≤ 2 s |
| NFR-05 | Retrieval latency | p95 ≤ 300 ms at 100k Chunks per Tenant |
| NFR-06 | Ingestion (CPU OCR) | 100-page text PDF ≤ 3 min; 20-page scan ≤ 3 min |
| NFR-07 | Upload limits | 50 MB / 500 pages per file default; Plan may lower |
| NFR-08 | Widget size | ≤ 40 KB gzipped |
| NFR-09 | Observability: JSON logs, metrics, traces, no PII | — |
| NFR-10 | Multilingual eval set; gating from M5 | hit@5 ≥ 0.85, cross-lingual ≥ 0.75 |
| NFR-11 | Backups and tested restore | M6 |
| NFR-12 | Browsers evergreen; later WP 6.4+/PHP 8.1+, Android minSdk 24 | — |
| NFR-13 | Launch language set supported end to end: `en hi es fr de ar zh` | Q3 resolved |
| NFR-14 | **Metering accuracy**: Usage Events exactly-once per billable action; daily reconciliation of counters vs events | 0 drift |
| NFR-15 | **Margin visibility**: per-Tenant gross margin computed daily | ≤ 24 h lag |
| NFR-16 | **Bounded cost per answer**: worst case ≤ Plan's `max_input_tokens`/`max_output_tokens` × profile price | Enforced in code |

## 5. Security requirements

[security.md](./security.md): SEC-01 … SEC-20. Tenant isolation is a merge gate from M1.

## 6. MVP acceptance (end of M2)

1. Fresh machine (incl. the dev box: GTX 1060 6 GB): `cp .env.example .env`, `docker compose up -d`;
   healthy, models pulled, GPU used.
2. An organisation **signs up** at `/signup`, verifies its email (Mailpit), and lands as Tenant
   Admin on the Trial. A second organisation does the same.
3. Tenant Admin A invites a **Tenant User**, who can view but not upload or change anything.
4. Admin A uploads a text PDF, a DOCX, a scanned PDF and a photo (one non-English). All reach `ready`.
5. The Playground gives streamed, cited answers in English and another language, including
   cross-lingual, and fallback for unrelated questions. Each answer creates a Usage Event with COGS.
6. The **Platform Admin** changes Tenant A's model override to an OpenAI-compatible profile. The
   next answer uses it with no code change and no re-ingestion. Tenant A sees no model names.
7. A's Hosted Chat Page and Widget answer as Visitors. With `visitor_email: optional`, an email is
   captured and visible to Tenant Admin A only.
8. Trial quota: after the trial's answer limit, chat returns the limit-reached text without an LLM call.
9. Isolation: B can't see, retrieve, cite or enumerate anything of A's. A's key fails from a
   non-allowed Origin.
