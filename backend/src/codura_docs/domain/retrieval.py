"""Pure retrieval math: RRF fusion, relevance gate, token budget (rag-pipeline.md §2, ADR-0007)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class Candidate:
    chunk_id: str
    document_id: str
    document_title: str
    text: str
    token_count: int
    ordinal: int
    page_start: int | None
    page_end: int | None
    heading_path: tuple[str, ...]
    language: str | None
    vector_score: float | None = None
    fts_rank: float | None = None
    rrf: float = 0.0


@dataclass(frozen=True)
class RetrievalSettings:
    top_k_vector: int = 20
    top_k_fts: int = 20
    top_n_context: int = 6
    min_score: float = 0.45
    context_token_budget: int = 3000
    rrf_k: int = 60

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RetrievalSettings:
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        return cls(**known)


@dataclass
class ContextPack:
    items: list[Candidate] = field(default_factory=list)
    debug: list[dict[str, Any]] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.items


def rrf_fuse(vector: Sequence[Candidate], fts: Sequence[Candidate], k: int = 60) -> list[Candidate]:
    """Reciprocal Rank Fusion: score = Σ 1/(k + rank). Stable on ties (by chunk id)."""
    merged: dict[str, Candidate] = {}
    for rank, cand in enumerate(vector, start=1):
        merged[cand.chunk_id] = replace(cand, rrf=1.0 / (k + rank))
    for rank, cand in enumerate(fts, start=1):
        existing = merged.get(cand.chunk_id)
        if existing:
            merged[cand.chunk_id] = replace(
                existing, fts_rank=cand.fts_rank, rrf=existing.rrf + 1.0 / (k + rank)
            )
        else:
            merged[cand.chunk_id] = replace(cand, rrf=1.0 / (k + rank))
    return sorted(merged.values(), key=lambda c: (-c.rrf, c.chunk_id))


def passes_gate(vector: Sequence[Candidate], fts: Sequence[Candidate], min_score: float) -> bool:
    best = max((c.vector_score or 0.0 for c in vector), default=0.0)
    return best >= min_score or bool(fts)


def build_context_pack(ranked: Sequence[Candidate], settings: RetrievalSettings) -> ContextPack:
    pack = ContextPack()
    budget = settings.context_token_budget
    for cand in ranked:
        used = len(pack.items) < settings.top_n_context and cand.token_count <= budget
        if used:
            pack.items.append(cand)
            budget -= cand.token_count
        pack.debug.append(
            {
                "chunk_id": cand.chunk_id,
                "vector_score": cand.vector_score,
                "fts_rank": cand.fts_rank,
                "rrf": round(cand.rrf, 6),
                "used": used,
            }
        )
    # Prompt order: by document then position, so adjacent chunks read naturally.
    pack.items.sort(key=lambda c: (c.document_id, c.ordinal))
    return pack
