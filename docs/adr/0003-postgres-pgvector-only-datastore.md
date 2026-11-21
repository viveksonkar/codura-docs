# ADR-0003 — Postgres + pgvector as the only datastore

- **Status:** Accepted
- **Date:** 2026-11-18
- **Relates to:** FR-06, FR-10, NFR-05, ADR-0006, ADR-0007

## Context

The brief names "vector database postgres". We need relational app data, vectors, full-text
search and a Job queue, all locally and in production, with deletes that are immediately consistent
(a deleted Document must never be cited again, FR-06).

## Decision

Use one Postgres 16 instance with the `pgvector` extension (image `pgvector/pgvector:pg16`) for
everything: app tables, Chunks + `tsvector` FTS, Embeddings with HNSW indexes (cosine), and Jobs.

## Alternatives considered

### Dedicated vector DB (Qdrant, Weaviate, Chroma, Milvus) — rejected
Another service to run, back up and keep consistent; deletes and Tenant filters become a
two-system transaction problem. At our scale (≤ a few million Chunks per install) pgvector HNSW
meets NFR-05.

### Elasticsearch/OpenSearch for hybrid — rejected
Heavy for a laptop; Postgres FTS is good enough for the keyword half of hybrid retrieval.

## Consequences

- One backup, one transaction boundary, one connection pool.
- We own HNSW tuning (`m`, `ef_construction`, `hnsw.ef_search`) and must watch index build memory.
- Embedding dimension is fixed per index → handled by ADR-0005.

## Escape hatch

Retrieval goes through `services/retrieval.py` with a repository interface; a `VectorIndex` Port
can be extracted if a dedicated vector DB is ever needed.
