"""Ollama EmbeddingModel over ``/api/embed`` (bge-m3, 1024-d, ADR-0015)."""

from __future__ import annotations

from collections.abc import Sequence

import httpx

from codura_docs.adapters.ollama._http import (
    PROVIDER,
    make_client,
    map_status,
    map_transport_error,
    require_model,
)
from codura_docs.ports.embedding import InputType, l2_normalise
from codura_docs.ports.errors import ProviderBadRequest

# Model-specific query/document prefixes. bge-m3 needs none; listed here so the domain never knows.
_PREFIXES: dict[str, dict[str, str]] = {
    "nomic-embed-text": {"query": "search_query: ", "document": "search_document: "},
}


class OllamaEmbeddingModel:
    def __init__(
        self,
        base_url: str,
        model: str,
        dimension: int,
        *,
        max_batch: int = 16,
        num_ctx: int = 1024,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._num_ctx = num_ctx
        self._client = make_client(base_url, transport)
        self._model = model
        self._dimension = dimension
        self._max_batch = max_batch

    @property
    def provider(self) -> str:
        return PROVIDER

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def max_batch(self) -> int:
        return self._max_batch

    @property
    def is_local(self) -> bool:
        return True

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        try:
            resp = await self._client.post(
                "/api/embed",
                json={
                    "model": self._model,
                    "input": texts,
                    "truncate": True,
                    # Chunks are ≤512 tokens + header; a small context keeps bge-m3 at ~1 GB VRAM
                    # so it stays resident next to the chat model on 6 GB GPUs (ADR-0018).
                    "options": {"num_ctx": self._num_ctx},
                },
            )
        except httpx.HTTPError as exc:
            raise map_transport_error(exc) from exc
        if resp.status_code >= 400:
            raise map_status(resp.status_code, resp.text)
        vectors = resp.json().get("embeddings") or []
        if len(vectors) != len(texts):
            raise ProviderBadRequest(
                PROVIDER, f"expected {len(texts)} embeddings, got {len(vectors)}"
            )
        out: list[list[float]] = []
        for vec in vectors:
            if len(vec) != self._dimension:
                raise ProviderBadRequest(
                    PROVIDER,
                    f"model {self._model} returned {len(vec)} dims, expected {self._dimension}",
                )
            out.append(l2_normalise([float(x) for x in vec]))
        return out

    async def embed(self, texts: Sequence[str], input_type: InputType) -> list[list[float]]:
        prefix = _PREFIXES.get(self._model.split(":")[0], {}).get(input_type, "")
        items = [prefix + t for t in texts]
        result: list[list[float]] = []
        for i in range(0, len(items), self._max_batch):
            result.extend(await self._embed_batch(items[i : i + self._max_batch]))
        return result

    def count_tokens(self, text: str) -> int | None:
        return None

    async def health(self) -> None:
        await require_model(self._client, self._model)

    async def aclose(self) -> None:
        await self._client.aclose()
