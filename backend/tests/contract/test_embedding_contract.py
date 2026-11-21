"""EmbeddingModel contract (specs/ports.md §2), incl. the cross-lingual sanity check (ADR-0015)."""

from __future__ import annotations

import math
from typing import Any

import pytest

from codura_docs.ports.embedding import cosine


async def test_shape_normalisation_and_stability(embedding_model: Any) -> None:
    texts = ["The warranty lasts 24 months.", "Reset the device by holding the button.", "x"]
    vectors = await embedding_model.embed(texts, "document")
    assert len(vectors) == len(texts)
    for vec in vectors:
        assert len(vec) == embedding_model.dimension == 1024
        assert math.isclose(math.sqrt(sum(v * v for v in vec)), 1.0, rel_tol=1e-3)
    again = await embedding_model.embed(texts[:1], "document")
    assert cosine(again[0], vectors[0]) == pytest.approx(1.0, abs=1e-3)


async def test_batches_larger_than_max_batch(embedding_model: Any) -> None:
    texts = [f"sentence number {i}" for i in range(embedding_model.max_batch + 3)]
    assert len(await embedding_model.embed(texts, "document")) == len(texts)


@pytest.mark.ollama
async def test_cross_lingual_similarity(embedding_model: Any) -> None:
    if embedding_model.provider == "fake":
        pytest.skip("hash-based fake has no semantics")
    en, hi, other = await embedding_model.embed(
        [
            "How long is the warranty on this product?",
            "इस उत्पाद की वारंटी कितने समय की है?",
            "The train leaves the station at nine in the morning.",
        ],
        "query",
    )
    assert cosine(en, hi) > cosine(en, other) + 0.1
