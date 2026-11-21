"""FakeEmbeddingModel: deterministic hashed character-trigram vectors (similar text → near vectors)."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from codura_docs.ports.embedding import InputType, l2_normalise


class FakeEmbeddingModel:
    def __init__(self, dimension: int = 1024, model_id: str = "bge-m3") -> None:
        self._dimension = dimension
        self._model_id = model_id

    @property
    def provider(self) -> str:
        return "fake"

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def max_batch(self) -> int:
        return 64

    @property
    def is_local(self) -> bool:
        return True

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self._dimension
        padded = f"  {text.lower()}  "
        for i in range(len(padded) - 2):
            digest = hashlib.blake2b(padded[i : i + 3].encode(), digest_size=8).digest()
            idx = int.from_bytes(digest[:4], "little") % self._dimension
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[idx] += sign
        if not any(vec):
            vec[0] = 1.0
        return l2_normalise(vec)

    async def embed(self, texts: Sequence[str], input_type: InputType) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def count_tokens(self, text: str) -> int | None:
        return max(1, len(text) // 4)

    async def health(self) -> None:
        return None
