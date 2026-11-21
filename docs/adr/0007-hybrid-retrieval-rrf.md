# ADR-0007 — Hybrid Retrieval: pgvector + full-text fused by RRF

- **Status:** Accepted, amended by ADR-0015 (FTS uses the `simple` config)
- **Date:** 2026-11-18
- **Relates to:** FR-10, FR-13, FR-15, NFR-05, NFR-10

## Context

Business documents are full of exact tokens (model numbers, SKUs, policy codes) that small local
embedding models handle poorly, while paraphrased questions need semantic search.

## Decision

Run vector search (cosine) and Postgres FTS (`websearch_to_tsquery`, `ts_rank_cd`)
in parallel, fuse with Reciprocal Rank Fusion (k = 60), then optionally rerank via the `Reranker`
Port, then apply a relevance gate and token budget (rag-pipeline.md §2). Defaults are tuned
against the golden eval set.

## Alternatives considered

### Vector only — rejected
Misses exact-identifier questions; measurably worse on our eval cases.

### Weighted score blending — rejected
Cosine and `ts_rank` scales aren't comparable; RRF needs no calibration.

### Always-on cross-encoder reranker — deferred to M3 (optional)
Adds latency on CPU; enable per Assistant once measured.

## Consequences

Two queries per question (both cheap); FTS language config per Knowledge Base (default `english`,
`simple` for mixed-language).
