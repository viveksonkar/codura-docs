# ADR-0017 — Web Channel first; WordPress and Android after the MVP

- **Status:** Accepted
- **Date:** 2026-11-18
- **Amends:** ADR-0010 (WordPress distribution), roadmap sequencing
- **Relates to:** FR-40…46

## Context

Product owner decision (2026-11-18): get a web chat interface working first so the system can be
tested quickly, then add the WordPress plugin and Android support. The WordPress plugin is
distributed locally (zip install), not through wordpress.org.

## Decision

- **MVP Channel = web**, delivered in M2:
  - a **Hosted Chat Page** at `/c/{tenant-slug}/{assistant-slug}`, a full-page chat that needs
    no website, for fast testing and sharing a link;
  - the embeddable **Widget** (`<codura-chat>`, ADR-0010), built from the same components.
- **WordPress plugin and Android SDK move to M3.** Both still use the public Chat API v1 only,
  so nothing in M1–M2 changes for them.
- The WordPress plugin ships as a **zip built by CI** and installed manually. wordpress.org rules
  (bundled Widget, GPL) stay in place so a listing remains possible later.

## Alternatives considered

### All three Channels in one milestone — rejected
Delays the first end-to-end test. Problems with the API or answer quality would then need fixing in
three clients instead of one.

### Playground only until channels — rejected
The Playground needs an admin login. Testing as a Visitor would still require a real public surface.

## Consequences

The public Chat API is proven by the web Channel before the native and WordPress clients are built.
The Hosted Chat Page also becomes a feature in its own right (a shareable support link per Tenant).
