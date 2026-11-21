# ADR-0015 — One fixed multilingual embedding model (bge-m3); no Embedding Spaces

- **Status:** Accepted
- **Date:** 2026-11-18
- **Supersedes:** ADR-0005
- **Amends:** ADR-0007 (full-text config)
- **Relates to:** FR-17, FR-18, FR-20, FR-24 (deprecated), NFR-10

## Context

Product owner decision (2026-11-18): the embedding model will not change; only the chat LLM that
generates answers may be switched. Answers must be multilingual: Documents and questions may be in
different languages. ADR-0005's Embedding Spaces (versioned vectors, rebuild-and-cutover) solved a
problem we no longer have, and cost schema and Job complexity.

## Decision

1. **One embedding model for the whole installation: `bge-m3`** (1024-d, multilingual,
   cross-lingual, 8k-token input), served by Ollama through the `EmbeddingModel` Port.
2. Vectors live directly on `chunks.embedding vector(1024)` with one HNSW (cosine) index.
3. **Startup guard:** the first boot records `(embedding provider, model, dimension)` in
   `system_settings`. If the configured embedding model ever differs, `api` and `worker` refuse to
   start with an explicit error. The model can't be changed by accident.
4. The `EmbeddingModel` Port stays: the same model may be served by another runtime (e.g. TEI)
   without touching domain code. The chat LLM stays fully switchable via Model Profiles (ADR-0004).
5. **Full-text search uses the `simple` configuration** (no stemming, any script). Its job in
   hybrid Retrieval is exact tokens (codes, SKUs, names); bge-m3 provides semantic and
   cross-lingual matching.

## Alternatives considered

### Keep Embedding Spaces (ADR-0005) — rejected
Pays for flexibility the owner has ruled out: runtime index DDL, dual writes, cutover Jobs.

### `nomic-embed-text` (previous default) — rejected
English-centric; poor cross-lingual retrieval.

### `multilingual-e5-large` / `paraphrase-multilingual` — rejected
e5-large has 512-token input and needs query/passage prefixes; bge-m3 has longer input and stronger
multilingual benchmark results, and Ollama serves it directly.

### Per-language FTS configs (`english`, `french`…) — rejected
Needs reliable per-Chunk language detection and per-query config choice, and gives nothing for
scripts without a Postgres stemmer. Stemming is the vector side's job here.

## Consequences

- Simpler schema and ingestion; no rebuild Jobs; FR-24 deprecated.
- Index dimension is hard-coded (1024). If the owner ever does change the model, it's an offline
  maintenance task (new migration + `codura-docs reembed` CLI), written as a new ADR then.
- bge-m3 is larger than nomic (~1.2 GB); CPU ingestion is slower. That cost is part of NFR-06.
