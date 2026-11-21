"""Hybrid cross-lingual Retrieval within one Tenant (rag-pipeline.md §2, ADR-0007, SEC-09).

Runs inside a tenant_session: RLS restricts rows AND every query filters tenant_id explicitly.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from codura_docs.domain.retrieval import (
    Candidate,
    ContextPack,
    RetrievalSettings,
    build_context_pack,
    passes_gate,
    rrf_fuse,
)
from codura_docs.ports.embedding import EmbeddingModel
from codura_docs.services.ingestion import vector_literal

_SELECT = (
    "c.id, c.document_id, d.title, c.text, c.token_count, c.ordinal, c.page_start, c.page_end, "
    "c.heading_path, c.language"
)


def _candidate(
    row: Any, *, vector_score: float | None = None, fts_rank: float | None = None
) -> Candidate:
    r = row
    return Candidate(
        chunk_id=r.id,
        document_id=r.document_id,
        document_title=r.title,
        text=r.text,
        token_count=r.token_count,
        ordinal=r.ordinal,
        page_start=r.page_start,
        page_end=r.page_end,
        heading_path=tuple(r.heading_path or ()),
        language=r.language,
        vector_score=vector_score,
        fts_rank=fts_rank,
    )


async def retrieve(
    session: AsyncSession,
    *,
    tenant_id: str,
    kb_ids: list[str],
    query: str,
    embedding: EmbeddingModel,
    settings: RetrievalSettings,
) -> ContextPack:
    if not kb_ids or not query.strip():
        return ContextPack()
    qvec = (await embedding.embed([query], "query"))[0]
    await session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    await session.execute(text("SET LOCAL hnsw.ef_search = 80"))
    vector_rows = (
        await session.execute(
            text(
                f"SELECT {_SELECT}, 1 - (c.embedding <=> CAST(:q AS vector)) AS score "
                "FROM chunks c JOIN documents d ON d.id = c.document_id "
                "WHERE c.tenant_id = :t AND c.knowledge_base_id = ANY(:kbs) AND d.status = 'ready' "
                "ORDER BY c.embedding <=> CAST(:q AS vector) LIMIT :k"
            ),
            {"q": vector_literal(qvec), "t": tenant_id, "kbs": kb_ids, "k": settings.top_k_vector},
        )
    ).all()
    fts_rows = (
        await session.execute(
            text(
                f"SELECT {_SELECT}, ts_rank_cd(c.tsv, q) AS rank "
                "FROM chunks c JOIN documents d ON d.id = c.document_id, "
                "websearch_to_tsquery('simple', :query) q "
                "WHERE c.tenant_id = :t AND c.knowledge_base_id = ANY(:kbs) AND d.status = 'ready' AND c.tsv @@ q "
                "ORDER BY rank DESC LIMIT :k"
            ),
            {"query": query, "t": tenant_id, "kbs": kb_ids, "k": settings.top_k_fts},
        )
    ).all()
    vector = [_candidate(r, vector_score=float(r.score)) for r in vector_rows]
    fts = [_candidate(r, fts_rank=float(r.rank)) for r in fts_rows]
    if not passes_gate(vector, fts, settings.min_score):
        pack = ContextPack()
        pack.debug = [
            {"chunk_id": c.chunk_id, "vector_score": c.vector_score, "used": False}
            for c in vector[:5]
        ]
        return pack
    return build_context_pack(rrf_fuse(vector, fts, settings.rrf_k), settings)
