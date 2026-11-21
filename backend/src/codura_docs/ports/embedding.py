"""EmbeddingModel Port (specs/ports.md §2). Exactly one fixed model per installation (ADR-0015)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Literal, Protocol, runtime_checkable

InputType = Literal["query", "document"]


@runtime_checkable
class EmbeddingModel(Protocol):
    @property
    def provider(self) -> str: ...

    @property
    def model_id(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    @property
    def max_batch(self) -> int: ...

    @property
    def is_local(self) -> bool: ...

    async def embed(self, texts: Sequence[str], input_type: InputType) -> list[list[float]]:
        """Return len(texts) L2-normalised vectors of ``dimension`` floats."""
        ...

    def count_tokens(self, text: str) -> int | None: ...

    async def health(self) -> None: ...


def l2_normalise(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        return [0.0 for _ in vector]
    return [x / norm for x in vector]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two already-normalised vectors."""
    return sum(x * y for x, y in zip(a, b, strict=True))
