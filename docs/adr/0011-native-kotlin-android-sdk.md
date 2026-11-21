# ADR-0011 — Native Kotlin Android SDK over the public Chat API

- **Status:** Accepted
- **Date:** 2026-11-18
- **Relates to:** FR-43, FR-44, SEC-14

## Context

The chat must be available in Android apps: our clients' existing apps (embedding a chat screen)
and possibly a standalone app (PROGRESS Q5).

## Decision

Ship a native Kotlin SDK in two modules — `:codura-chat` (UI-free client with streaming `Flow`)
and `:codura-chat-ui` (Jetpack Compose screen + Activity) — plus a `:sample` app that doubles as
the standalone app. It talks only to the public Chat API with an `android` Channel Key.

## Alternatives considered

### WebView wrapping the Widget — rejected as primary
Fastest to build, but feels non-native, needs a JS bridge (attack surface), and handles keyboard,
back navigation and accessibility poorly. Acceptable as a stopgap only.

### Flutter / React Native SDK — rejected
Forces a runtime on host apps that don't use it; host-app developers on native Android (the
majority) would pay the size and integration cost.

## Consequences

A Kotlin port of the `chat-client` logic (SSE parsing, persistence) must stay in lockstep with
the TS one; both are tested against the same SSE fixtures from `specs/api.md`. iOS later would
follow the same split.
