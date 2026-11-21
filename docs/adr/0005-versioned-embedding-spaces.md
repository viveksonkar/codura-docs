# ADR-0005 — Versioned Embedding Spaces with rebuild-and-cutover

- **Status:** Superseded by ADR-0015 (2026-11-18): the embedding model is fixed
- **Date:** 2026-11-18
- **Relates to:** G-2, FR-24, ADR-0003, ADR-0004

## Context

Switching the chat model is trivial; switching the embedding model is not. Vectors from different
models (or dimensions) are meaningless to compare, and pgvector HNSW indexes need a fixed
dimension. A naive switch either breaks Retrieval or forces downtime while everything re-embeds.

## Decision

- An **Embedding Space** row records `(provider, model, dimension, status)`. Each Workspace has
  exactly one `active` Space.
- Embeddings live in `chunk_embeddings(chunk_id, embedding_space_id, workspace_id, embedding vector)`
  — an **untyped** `vector` column — and each Space gets its own **partial expression HNSW index**
  created when the Space is created:
  `CREATE INDEX … USING hnsw ((embedding::vector(768)) vector_cosine_ops) WHERE embedding_space_id = '<id>'`.
  Queries cast the same way and filter by the Space, so the planner uses that index.
- Ingestion always writes to the Active Space and, if one is `building`, to it as well.
- Changing the embedding config creates a `building` Space and enqueues `reembed_space`, which
  embeds all Chunks in batches. On completion it atomically flips `active`, marks the old Space
  `retired`, and a later Job drops its rows and index.
- Query embedding always uses the Active Space's recorded model, not the current config.

## Alternatives considered

### One `vector(N)` column on `chunks`, migrate in place — rejected
Requires downtime or a broken window during re-embed; can't hold two models at once.

### One table per Space (dynamic DDL) — rejected
Works, but complicates ORM mapping, RLS policies and migrations; partial indexes give the same
index isolation on one table.

## Consequences

- Retrieval never mixes models; embedding switches have zero answer downtime (FR-24).
- During a rebuild storage roughly doubles for vectors, and ingestion embeds twice.
- Creating a Space runs DDL (index) at runtime from the worker — done with `CREATE INDEX
  CONCURRENTLY` and tested.
