"""NoopReranker: keeps input order (until a real reranker lands in M5)."""

from __future__ import annotations

from collections.abc import Sequence


class NoopReranker:
    async def rerank(
        self, query: str, candidates: Sequence[str], top_n: int
    ) -> list[tuple[int, float]]:
        n = min(top_n, len(candidates))
        return [(i, 1.0 - i / max(1, len(candidates))) for i in range(n)]
