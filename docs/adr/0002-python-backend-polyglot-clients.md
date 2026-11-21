# ADR-0002 — Python/FastAPI backend; TypeScript, PHP, Kotlin clients

- **Status:** Accepted
- **Date:** 2026-11-18
- **Relates to:** FR-02, FR-40…43, ADR-0008

## Context

The backend's hardest parts are document parsing, chunking, embeddings and evaluation. The
Channels must be native to their platforms: a browser bundle, a WordPress (PHP) plugin, an
Android library. The sibling `codura-agents` is a TypeScript monorepo.

## Decision

- **Backend** (`api` + `worker`): Python 3.12, FastAPI, SQLAlchemy 2 (async, psycopg 3), Alembic,
  Pydantic v2, managed with `uv`; `ruff`, `mypy --strict`, `pytest`, `import-linter`.
- **Web** (`web/`): TypeScript strict, pnpm workspace. Admin Console in React + Vite; Widget in
  Preact (small bundle) as a Web Component; shared `chat-client` package.
- **WordPress**: PHP 8.1+, WordPress Coding Standards.
- **Android**: Kotlin, coroutines/Flow, OkHttp, Jetpack Compose.

## Alternatives considered

### TypeScript backend (NestJS/Fastify), matching codura-agents — rejected
Parsing and RAG tooling in Node is weaker (PDF/DOCX extraction, tokenisers, eval tooling,
optional docling/OCR). We'd end up shelling out to Python anyway. Consistency with the sibling
repo matters less than the core competency of this one.

### Go backend — rejected
Excellent ops profile, poor document/ML ecosystem; same shelling-out problem.

### Cross-platform mobile (Flutter / React Native) — see ADR-0011.

## Consequences

Four toolchains in one repo; CI is path-filtered per toolchain. The public Chat API contract
(`specs/api.md`) is what keeps clients consistent, so it must be precise and versioned.
