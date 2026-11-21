# ADR-0012 — Public Chat API: SSE streaming, publishable Channel Keys

- **Status:** Accepted
- **Date:** 2026-11-18
- **Relates to:** FR-11, FR-12, FR-40…45, SEC-04…06

## Context

Three Channels need streamed answers with Citations. Web pages and APKs can't keep secrets.

## Decision

- One versioned public surface, `/api/v1/…`, separate routers from the admin API.
- Streaming via **Server-Sent Events** over `POST /api/v1/chat/stream` (`text/event-stream`), with
  typed events `meta`, `delta`, `citations`, `done`, `error` (specs/api.md §3). Clients parse SSE
  from `fetch` streams (web) and OkHttp (Android); `EventSource` isn't used because it can't POST.
- Auth by **Channel Key** (`pk_…`) bound to one Assistant and one Channel kind, publishable by
  design. Protection comes from Origin allowlists (browser Channels), rate limits, length/token
  caps and revocation — not secrecy. Secret Keys (`sk_…`) are rejected on public routes.
- Breaking changes go to `/api/v2`; additive fields and new event types are allowed in v1 and
  clients must ignore unknown events.

## Alternatives considered

### WebSockets — rejected
Bidirectional isn't needed; SSE passes proxies/CDNs more easily and is simpler to scale and test.

### Signed short-lived tokens minted by the host's server — deferred
Stronger, but every integrator would need a backend; impossible for a plain HTML site. Can be
added as an optional mode for the `api` Channel.

## Consequences

Simple integration everywhere. Abuse protection (SEC-06) is mandatory before public exposure,
and the stream must disable proxy buffering (`X-Accel-Buffering: no`, Caddy `flush_interval -1`).
