# ADR-0020 — Self-service Tenant signup; Platform Admin vs Tenant Admin vs Tenant User

- **Status:** Accepted
- **Date:** 2026-11-19
- **Amends:** ADR-0014 (Tenant creation, roles)
- **Relates to:** FR-36…38, FR-49…52, SEC-18

## Context

Owner decision (2026-11-19): organisations register themselves by entering an organisation name.
A Tenant then has **admin users** (upload Documents, build embeddings, manage users) and **normal
users** (log in and view). Separately, the SaaS operator has **Platform Admins** who belong to no
Tenant.

## Decision

- **Signup** (`/signup`): organisation name, the registrant's name, email and password. This
  creates a Tenant on the **Free/Trial plan** (ADR-0021), with the registrant as its first Tenant
  Admin. The Tenant is `pending_verification` until the email is verified, and the console is
  read-only until then. The slug is derived from the organisation name and can be edited once.
- **Roles**
  - **Platform Admin** (`users.is_platform_admin`, no Tenant membership): manages Plans, Model
    Profiles, provider prices, Tenants (suspend, override model, enable Custom LLM, adjust quotas),
    and sees platform finance and telemetry. The only cross-Tenant actor.
  - **Tenant Admin** (`tenant_members.role = 'admin'`): manages Knowledge Bases and Documents
    (upload, re-ingest, delete), Assistants, Channel Keys, Tenant Users, Tenant settings and billing
    (plan, payment method, invoices). A Tenant always has at least one Tenant Admin.
  - **Tenant User** (`role = 'user'`): read-only. Can view Knowledge Bases, Document status,
    Assistants, Conversations, analytics and usage, and use the Playground (metered like any
    answer). Cannot upload, change settings, see Channel Keys, or manage users or billing.
- **User management screen**: Tenant Admins invite by email (role chosen), change roles, remove
  users. They can't remove or demote the last Tenant Admin.
- Platform Admins can't be created by signup. The first one is bootstrapped from env; further ones
  are invited by a Platform Admin.
- One email can belong to several Tenants (consultants). The Tenant switcher from ADR-0014 stays.

## Alternatives considered

### Platform Admin creates every Tenant (previous default) — superseded
Doesn't scale for a self-serve SaaS.

### Finer roles (owner/admin/editor/viewer) — rejected for now
The owner asked for two. A `billing_owner` flag can be added later without changing the schema's
shape.

## Consequences

Signup is a public, abusable endpoint, so it gets SEC-18 (email verification, rate limits,
disposable-domain blocklist, optional self-hosted captcha) and a small trial quota. Email sending
becomes a dependency: an `EmailSender` Port with an SMTP adapter, and Mailpit locally (NFR-01 safe).
