# ADR-0010 — One Web Component Widget; WordPress plugin wraps it

- **Status:** Accepted, amended by ADR-0017 (plugin ships in M3, distributed locally as a zip)
- **Date:** 2026-11-18
- **Relates to:** FR-40…42, FR-45, NFR-08, SEC-07, SEC-14

## Context

The brief asks for a web chat box for websites and for WordPress, asking which is "more suitable
and better" for WordPress: a plugin or a generic embed. WordPress users expect a plugin with a
settings screen; generic sites need a script tag. Maintaining two chat UIs would double the work
and the parity bugs.

## Decision

- Build **one** Widget: a Preact Custom Element `<codura-chat>` in Shadow DOM, shipped as
  `widget.js`, usable on any site via one `<script>` tag.
- For WordPress, ship a **plugin** that bundles that same `widget.js` and adds: a settings page,
  site-wide launcher toggle with page rules, `[codura_chat]` shortcode, a Gutenberg block, and a
  "Test connection" button. The plugin contains no chat UI of its own.
- The plugin bundles the Widget file rather than loading it from our server (wordpress.org
  guideline on remote code; also works offline/behind CSP).

## Alternatives considered

### Script tag only, documented for WordPress users — rejected as the WordPress answer
Works technically (header/footer plugins or theme edits), but fragile across theme changes and
unfriendly for non-developers. Still supported for any site.

### Native PHP-rendered chat UI in the plugin — rejected
A second UI to keep at parity; PHP can't stream SSE to the browser more simply than the Widget does.

### iframe-based widget — rejected
Simplest isolation, but awkward sizing/launcher behaviour, weaker host theming, and third-party
storage restrictions break Conversation persistence. Shadow DOM gives enough isolation.

## Consequences

One UI codebase; WordPress plugin is mostly settings + escaping. Plugin releases must re-bundle
the Widget when it changes (CI step). Widget must stay framework-agnostic and small (NFR-08).
