"""Reranker Port (specs/ports.md §5)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class Reranker(Protocol):
    async def rerank(
        self, query: str, candidates: Sequence[str], top_n: int
    ) -> list[tuple[int, float]]:
        """Return (candidate index, score) pairs, best first, at most ``top_n``."""
        ...
