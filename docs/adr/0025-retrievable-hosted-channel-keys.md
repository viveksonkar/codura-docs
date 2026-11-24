# ADR-0025 — Hosted Channel Keys are stored retrievable (encrypted); public limits and CORS per key

- **Status:** Accepted
- **Date:** 2026-11-24
- **Amends:** ADR-0012 (public Chat API), specs/db.md §2 (`api_keys`, `resolve_hosted`)
- **Relates to:** FR-34, FR-46, SEC-04, SEC-05, SEC-06, SEC-15

## Context

The Hosted Chat Page (`/c/{tenant}/{assistant}`) has no embed code, so the page itself must learn
its Channel Key: `GET /api/v1/hosted/{tenant}/{assistant}` returns it (specs/api.md §3.2a). SEC-04
stores every key only as a SHA-256 hash, so the server cannot give a hashed key back.

## Decision

- `api_keys` gains `public_key_enc bytea`. Only `hosted` keys set it: the plaintext is encrypted
  with `crypto.py` (AAD `hosted_key:<tenant_id>`). Every other key stays hash-only and shown once.
- A `hosted` key is managed by the system: enabling `hosted_enabled` on an Assistant creates it,
  disabling revokes it. Tenant Admins cannot create `hosted` keys by hand.
- `resolve_hosted(tenant_slug, assistant_slug)` (SECURITY DEFINER) also returns the active hosted
  key's `public_key_enc`. Disabled, unknown and suspended give the same 404 body.
- `hosted` keys accept requests only from the platform's own origin (`PUBLIC_BASE_URL`).
  `widget`/`wordpress` keys accept a browser `Origin` only if it is in Allowed Origins, which fails
  closed when the list is empty. Outside production they also accept the platform origin, so the
  dev-only `/demo/` page can load any widget key. A request without an `Origin` header is not a browser and is
  limited by SEC-06 alone. `api`/`android` keys get no CORS headers.
- CORS on `/api/v1/*` reflects the request origin for preflight and, on the real request, only
  when the key allows that origin. It never sends `*` or credentials.
- `last_used_at` is updated at most once a minute per key, outside the request's critical path.

## Alternatives considered

### Derive the hosted key from a server secret (HMAC of the Assistant id): rejected
It can't be revoked or rotated without changing the secret for every Tenant.

### Let the hosted page call the API with the slugs instead of a key: rejected
That adds a second way to resolve a Tenant to the public API. ADR-0012 keeps exactly one: the
Channel Key.

## Consequences

- A hosted key is public anyway (any Visitor sees it in the page), so encrypting it only protects
  database dumps. The other keys keep the stronger hash-only property.
- Migration `0003` adds the column and replaces `resolve_hosted`.
