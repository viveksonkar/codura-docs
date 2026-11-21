# Codura Docs — Glossary

A local-first, multi-tenant, monetised SaaS with two sides:

- **Tenant Admins** sign their organisation up, upload documents and build a Knowledge Base.
- **Visitors** chat with that Tenant's knowledge in any supported language, through a website, a
  hosted chat page or an app.

The **platform** owns the LLM and bills Tenants for usage. These terms mean exactly this in code,
API names, docs and conversation.

## People and tenancy

**Tenant**:
An organisation that signed up. It owns Knowledge Bases, Documents, Assistants, Channel Keys,
Conversations, its Subscription and its usage. No Tenant can read or affect another's data
(ADR-0014).
_Avoid_: Workspace, Organization (in code; fine in UI copy as "organisation"), Account, Client

**Signup**:
Self-service creation of a Tenant from an organisation name. The registrant becomes its first
Tenant Admin (ADR-0020).

**Platform Admin**:
An administrator of the SaaS platform itself, belonging to no Tenant. Manages Plans, prices, Model
Profiles, Tenants (suspend, model override, quotas, Custom LLM) and sees finance and telemetry. The
only role that may cross Tenants.
_Avoid_: Superuser, Root, Super admin

**Tenant Admin**:
A User with role `admin` in a Tenant. Uploads Documents, builds the Knowledge Base, configures
Assistants and Channels, manages Tenant Users and billing.

**Tenant User**:
A User with role `user` in a Tenant: read-only. Can log in and view Documents, Assistants,
Conversations, insights and usage, and use the Playground.
_Avoid_: Viewer, Member (as a role name), Normal user (in code)

**User**:
Any account that logs into the consoles (Platform Admins, Tenant Admins, Tenant Users). Visitors
are not Users.

**Member**:
The link between a User and a Tenant, carrying the role (`admin` or `user`).

**Visitor**:
An anonymous person chatting with a Tenant through a Channel, identified only by a
client-generated Visitor ID. May leave a Visitor Email.
_Avoid_: Customer, End user (in code)

**Visitor Email**:
An optional email a Visitor gives when opening the chat (per Assistant: off, optional or required).
It is contact data for the Tenant, stored encrypted, and **never an identity** (ADR-0023).

## Content (admin side)

**Knowledge Base**:
A named set of Documents inside a Tenant.
_Avoid_: Collection, Corpus, Index

**Document**:
One uploaded source (PDF, DOCX, TXT, MD, HTML, or an image) and everything derived from it. Its
original bytes are its Blob.
_Avoid_: File (except for the raw upload)

**Blob**:
The original uploaded bytes, kept under the Tenant's prefix and addressed by content hash.

**Ingestion**:
parse (OCR where needed) → language → chunk → embed → index. Status `pending`, `processing`,
`ready` or `failed`.
_Avoid_: Indexing, Training

**OCR** / **OCR Languages**:
Text recognition for scanned pages and images (ADR-0016). The Tesseract languages used, set per
Tenant (e.g. `eng+hin`).

**Parsed Document**:
The Parser's output: Sections with pages, heading paths and OCR confidence.

**Chunk**:
A span of Document text with its embedding and location, used for Retrieval and Citations.
_Avoid_: Passage, Segment, Node

**Embedding**:
The 1024-d `bge-m3` vector of a Chunk. One fixed model for the installation (ADR-0015).

## Answering (chat side)

**Assistant**:
A Tenant's chat configuration: Knowledge Bases, prompt, retrieval settings, per-language texts,
Visitor Email setting. **It does not choose the model.**
_Avoid_: Bot, Agent, Chatbot (in code)

**Retrieval** / **Context Pack** / **Citation** / **Grounded Answer**:
Hybrid search scoped to the Tenant. The Chunks chosen for one answer. The references shown with an
answer. An answer drawn only from the Context Pack.

**Answer Language**:
The detected language of the question. The answer is written in it.

**Conversation** / **Message**:
A Visitor's (or Playground User's) exchange with an Assistant. One turn of it. Assistant Messages
record Citations, Model Profile, tokens and COGS.
_Avoid_: Session, Thread, Chat (as a noun in code)

**Playground**:
The console's chat pane for testing an Assistant. Metered like any answer.

## Models and providers

**Port** / **Adapter**:
A Python Protocol the domain depends on (`ChatModel`, `EmbeddingModel`, `Parser`, `OcrEngine`,
`BlobStore`, `Reranker`, `EmailSender`, `PaymentProvider`). An implementation of a Port for one
vendor or library, the only code allowed to import that vendor.

**Provider**:
An LLM backend: `ollama`, `openai_compat`, `anthropic`.

**Model Profile**:
A **platform-managed** record: Provider, model, parameters, licence, prices and encrypted
credentials. Resolved per question: Custom LLM → Platform Admin override → Plan default
(ADR-0019). Tenants never see or choose one.
_Avoid_: Model config, LLM setting

**Custom LLM**:
A Tenant-scoped Model Profile pointing at the Tenant's own LLM endpoint. Usable only when a Platform
Admin enables the `custom_llm` Entitlement for that Tenant.
_Avoid_: BYOK (in code)

## Channels

**Channel**:
A surface Visitors chat through: `hosted`, `widget` (MVP), `wordpress`, `android`, `api`.
_Avoid_: Platform, Integration

**Hosted Chat Page** / **Widget**:
The full-page chat at `/c/{tenant-slug}/{assistant-slug}`. The embeddable `<codura-chat>` Web
Component.

**Channel Key** / **Secret Key** / **Allowed Origin**:
A publishable `pk_` bound to one Tenant, Assistant and Channel. A server-side `sk_` for the admin
API. A web origin allowed for a browser Channel Key.

## Billing and metering (ADR-0021)

**Plan**:
A versioned offer defined by the Platform Admin: price, included quantities, overage prices,
Entitlements and default Model Profile.
_Avoid_: Tier, Package (in code)

**Subscription**:
A Tenant's current Plan version with its billing period, trial and status.

**Entitlement**:
A typed feature flag or limit granted by a Plan (or a Platform Admin override), e.g. `channels`,
`max_assistants`, `custom_llm`.

**Meter**:
A billable quantity: `answer`, `answer_custom_llm`, `page_ingested`, `page_ocr`,
`storage_mb_day`, `seat_day`.

**Usage Event**:
One append-only, idempotent record of metered usage, with quantity and COGS, written in the same
transaction as the action.

**Quota**:
A per-period limit on a Meter from the Subscription's Plan. At the limit: warn, block or allow
overage.

**COGS**:
The platform's cost for a Usage Event: LLM tokens × the Model Profile's prices, or OCR, embedding
or storage cost rates. Recorded in micro-USD.

**Gross Margin**:
(Revenue − COGS) / Revenue, per Tenant, Plan or Model Profile. Plans must meet the margin floor.

**Invoice**:
The period's charge (base + overage) computed from our usage ledger and collected through the
PaymentProvider.

## Platform

**Job**:
A unit of background work in the Postgres queue (ADR-0006).

**Platform Console** / **Tenant Console**:
The web UI for Platform Admins (Tenants, Plans, models, finance, telemetry). The web UI for Tenant
Admins and Users (content, Assistants, Channels, users, insights, billing). Both are served by the
same Admin Console app, by role.
_Avoid_: Dashboard (in code), Backoffice

**Audit Log**:
An append-only record of Platform Admin actions and support access.
