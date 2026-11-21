# Specs — frozen contracts

**Status:** Draft v0.1 · **Last updated:** 2026-11-18

A spec freezes a boundary that several independent Tasks build against, so parallel agents don't
each guess a slightly different shape. A spec is **more specific than the doc it derives from,
never a contradiction of it**; if they disagree, fix both in one change.

| Spec | Boundary | Frozen by Task | Used by |
|---|---|---|---|
| [ports.md](./ports.md) | Python Ports: `ChatModel`, `EmbeddingModel` (fixed), `Parser`, `OcrEngine`, `BlobStore`, `Reranker`, errors, Model Profile config | M0-04 | All Adapters, services, registry |
| [db.md](./db.md) | Postgres schema, tenant RLS, vector + FTS indexes, Job table | M0-03 (base), M1-00 (content) | Repositories, services, worker |
| [api.md](./api.md) | HTTP API: conventions, admin routes, public Chat API, SSE events | M1-08 (v1 public frozen for M2) | Admin Console, chat-client, Widget, WordPress, Android |

**Freeze gate:** a Task that depends on a spec doesn't start until the Task that freezes it is
merged (ledger/README.md §2). Changing a frozen spec needs an ADR if it breaks consumers;
additive changes need only a PR that updates the spec and its consumers together.
