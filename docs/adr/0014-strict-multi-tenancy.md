# ADR-0014 — Strict multi-tenancy: Tenant-scoped data + RLS from the first migration

- **Status:** Accepted (confirmed 2026-11-18), amended by ADR-0020 (self-service signup; roles `admin`/`user`)
- **Date:** 2026-11-18
- **Relates to:** FR-36…39, SEC-02, SEC-09, SEC-15

## Context

Codura Docs serves many Tenants from one installation. Each Tenant's Admins upload their own
Documents. Each Tenant's chat users reach only that Tenant, through the website or app the Tenant
embeds the chat in. **One Tenant must never be able to access another Tenant's data.** That covers
Documents, Chunks, Embeddings, Conversations and Assistant settings, through every path: admin
API, public Chat API, Retrieval, Jobs, Blobs and logs.

## Decision

Defence in depth, with each layer enough to stop a leak on its own:

1. **Schema:** every tenant table has `tenant_id NOT NULL`, which is part of the foreign keys
   that matter (composite FKs `(tenant_id, id)`, so a row can't reference another Tenant's parent).
2. **Application:** repositories require a `TenantContext`. There is no unscoped query API outside
   the `platform` module, which is used only by Platform Admin routes.
3. **Database:** every request and Job transaction runs `SET LOCAL app.tenant_id = …`. RLS
   policies enforce `tenant_id = current_setting('app.tenant_id')`. The app connects as a
   non-owner role, so RLS can't be bypassed. A missing setting matches no rows (fail closed).
4. **Tenant resolution comes from credentials only.** On the admin API it comes from the session or
   Secret Key. On the public Chat API it comes from the Channel Key. A client-supplied tenant ID is
   never trusted.
5. **Storage:** Blob keys are prefixed `<tenant_id>/…`. Dedupe by hash happens only within a Tenant.
6. **Caches, rate limits and logs** are keyed by or tagged with `tenant_id`.
7. **Tests:** a cross-Tenant probe suite (two Tenants, every read and write path, including
   Retrieval and Citations) is a merge gate from M1.

Tenants are created by a **Platform Admin** (the operator), who also invites each Tenant's first
Admin. Self-service signup is not in scope (PROGRESS Q8).

## Alternatives considered

### Single-tenant schema — rejected
Ruled out by the requirement.

### Schema-per-tenant or database-per-tenant — rejected
Stronger physical isolation, but migrations multiply with every Tenant, connection pools fragment,
and the shared worker and Ollama pool gets complicated. RLS together with composite FKs and the
probe suite gives strong isolation at our scale. Database-per-tenant remains an option for a single
large Tenant later, because `DATABASE_URL` resolution sits behind the session factory.

## Consequences

Slight overhead on every query and migration. Every new table needs RLS in the same migration
(enforced by a migration test that lists tables without policies). Platform Admin routes are the
only code allowed to cross Tenants, and they are audited.
