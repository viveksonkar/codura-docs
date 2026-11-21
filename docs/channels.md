# Channels

**Status:** Draft v0.2 · **Last updated:** 2026-11-18 · **Implements:** FR-39…47 · **ADR:** 0010, 0011, 0012, 0017

Every Channel is a thin client of one contract: the public Chat API in
[specs/api.md](./specs/api.md) §3, authenticated by a Channel Key. **The Channel Key decides the
Tenant.** No Channel can name or switch a Tenant, talk to Ollama or Postgres, or use the admin
API, and none holds a Secret Key.

| Order | Channel | Milestone |
|---|---|---|
| 1 | **Hosted Chat Page** (`hosted`) | M2 (MVP) |
| 2 | **Widget** (`widget`) for any website | M2 (MVP) |
| 3 | WordPress plugin (`wordpress`) | M4 (Plan Entitlement `channels`) |
| 4 | Android SDK (`android`) | M4 (Plan Entitlement `channels`) |

```
                    ┌──────────── web/packages/chat-client (TS) ────────────┐
Hosted Chat Page ───┤ fetch + SSE parser, Visitor/Conversation persistence   │
Widget (Preact WC) ─┤                                                        │
WordPress plugin ───┘ (M4: embeds the same widget.js)                        │
Android SDK ──────── (M4: Kotlin port of the same client)                    │
                    └──────────────── POST /api/v1/chat/stream ──────────────┘
```

## 1. Shared behaviour (all Channels)

- **Bootstrap:** `GET /api/v1/channel/config` returns the Assistant name, welcome text (for the
  Visitor's locale), theme, supported UI languages and limits.
- **Identity:** Visitor ID is a random UUID generated on first use and stored locally per Channel
  Key, so two Tenants' chats on one device never share identity or history. The Conversation ID
  comes from the first `meta` event.
- **Language:** the UI locale is taken from `navigator.language` / device locale and can be
  overridden with the `lang` attribute. The Visitor can type in any language, and the answer comes
  back in that language (rag-pipeline.md §3). Message bubbles use `dir="auto"`, and the whole UI
  mirrors for RTL locales.
- **Rendering:** answer Markdown → sanitised HTML (`marked` + `DOMPurify` allowlist; Android:
  Markwon without HTML). Links get `target=_blank rel="noopener noreferrer nofollow"`. Fonts fall
  back to system fonts that cover Devanagari, Arabic and CJK.
- **Citations:** numbered chips under the answer. Tapping one shows the Document title, page and
  the snippet in its source language.
- **States:** idle, streaming (stop button aborts), error (retry), offline.
- **Feedback:** 👍/👎 → `POST /api/v1/messages/{id}/feedback`.
- **Pre-chat email (ADR-0023):** if the Assistant's `visitor_email` is `optional` or `required`,
  show a short form (email + consent text + privacy link) before the first message. `optional` can
  be skipped. The email goes with the first message and is never used to look up history.
- **Limits:** when the Tenant's quota is exhausted, the answer is the limit-reached text
  (`done.finish_reason: "limit"`). The UI shows it like a normal message.
- **Branding:** "Powered by Codura" footer unless the Plan's `remove_branding` Entitlement is on.
- **Accessibility:** keyboard reachable, `aria-live="polite"` on the streaming answer, WCAG AA
  contrast.

## 2. Hosted Chat Page (`web/apps/widget`, page build) — MVP

- URL: `https://<host>/c/{tenant-slug}/{assistant-slug}`. It works with no website, which makes it
  the fastest way to test as a Visitor, and a link Tenants can share.
- The page calls `GET /api/v1/hosted/{tenant-slug}/{assistant-slug}`, which returns that
  Assistant's `hosted` Channel Key **only if the Admin enabled the hosted page**. Otherwise it
  returns 404, the same as for a non-existent slug, so slugs can't be enumerated.
- It renders the Widget in `inline` mode at full viewport, with the Tenant's theme, title and logo.
  Mobile-first.
- `?lang=hi` overrides the UI locale. `?embed=1` hides the header for use in an iframe.

## 3. Widget (`web/apps/widget`) — MVP

- A Custom Element `<codura-chat>` rendered with Preact inside **Shadow DOM** (NFR-08).
- Modes: `launcher` (floating bubble, default) and `inline`.
- Embed:

  ```html
  <script src="https://chat.example.com/widget/v1/widget.js" defer
          data-key="pk_live_…" data-api="https://chat.example.com"
          data-mode="launcher" data-position="right" data-lang="auto"></script>
  <!-- or inline anywhere: -->
  <codura-chat key="pk_live_…" api="https://chat.example.com" mode="inline"></codura-chat>
  ```

- Theming: attributes for the basics. CSS custom properties pierce the shadow root
  (`--codura-accent`, `--codura-radius`, `--codura-font`).
- JS API: `window.CoduraChat.open() / close() / send(text) / setLanguage(code) / on('message', fn)`.
- UI strings: `web/apps/widget/src/i18n/<lang>.json` for the launch language set (Q3). Missing
  keys fall back to `en`.
- Build: Vite library mode produces `widget.js` (IIFE) and `widget.esm.js`, served with versioned
  paths and long cache.
- Local test page: `http://localhost:8080/demo/?key=pk_…` (dev only).

## 4. WordPress plugin (`wordpress/codura-docs-chat`) — M4, local distribution

**Why a plugin rather than "paste the script tag":** both work. The plugin gives WordPress users a
settings screen instead of theme edits, survives theme switches, adds a shortcode and a block for
inline placement, and can check the connection from wp-admin. It reuses the exact Widget, so there
is one chat UI to maintain (ADR-0010).

| Piece | Behaviour |
|---|---|
| Settings → Codura Chat | API URL, Channel Key (a `wordpress` key of one Tenant), mode, position, title, accent colour, language (auto / fixed), "show on all pages", page include/exclude. "Test connection" calls `/api/v1/channel/config` server-side |
| Front end | Enqueues the bundled `widget.js` and prints the element with escaped attributes |
| `[codura_chat]` | Inline chat; attributes override settings |
| Gutenberg block | `codura/chat` wrapping the shortcode |
| Security | `manage_options` + nonces, `sanitize_*` on save, `esc_attr`/`esc_url` on output; uninstall removes options |
| Distribution | **Local zip** built by CI (`codura-docs-chat-<version>.zip`) and installed via Plugins → Upload. Not listed on wordpress.org (ADR-0017), but built to its guidelines |

Local testing: `docker compose --profile wp up -d` gives WordPress at `http://localhost:8081` with
the plugin mounted.

## 5. Android SDK (`android/`) — M4

| Module | Contents |
|---|---|
| `:codura-chat` | Kotlin client: `CoduraChat.configure(apiUrl, channelKey)`, `config()`, `stream(question, conversationId): Flow<ChatEvent>` (OkHttp SSE), DataStore persistence keyed by Channel Key |
| `:codura-chat-ui` | Jetpack Compose `CoduraChatScreen(...)` + `CoduraChatActivity`; Material 3; RTL and locale aware |
| `:sample` | Demo app with settings for API URL + Channel Key (Q5: may become the standalone app) |

- `minSdk 24`. The emulator reaches the host stack at `http://10.0.2.2:8080`. Cleartext is allowed
  only in the debug build.
- `android` keys aren't Origin-checked. They rely on rate limits and optional package-name and
  signing-cert headers.

## 6. Channel parity checklist (FR-45)

A Channel is done when it passes the same scripted scenario against the seeded demo Tenant:
welcome text in the device locale → cited answer → question in a second language answered in that
language → follow-up using history → fallback on an unrelated question → stop mid-stream → feedback
→ reload/restart keeps the Conversation → **a second Tenant's key on the same device shows none of
the first Tenant's history.**
