"""Startup guards. The embedding model is fixed for the installation (ADR-0015, FR-25)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from codura_docs.config import EmbeddingConfig

EMBEDDING_KEY = "embedding"


class EmbeddingModelMismatch(RuntimeError):
    """Configured embedding model differs from the one this installation was built with."""


def embedding_fingerprint(cfg: EmbeddingConfig) -> dict[str, Any]:
    return {"provider": cfg.provider, "model": cfg.model, "dimension": cfg.dimension}


async def check_embedding_guard(engine: AsyncEngine, cfg: EmbeddingConfig) -> None:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT value FROM system_settings WHERE key = :k"), {"k": EMBEDDING_KEY}
            )
        ).first()
    if row is None:
        raise EmbeddingModelMismatch(
            "system_settings.embedding is missing: run `codura-docs migrate` (the migrate service) first"
        )
    recorded = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    expected = embedding_fingerprint(cfg)
    if {k: recorded.get(k) for k in expected} != expected:
        raise EmbeddingModelMismatch(
            "the embedding model is fixed for this installation (ADR-0015): database was built with "
            f"{recorded}, but config says {expected}. Restore the original embedding config; changing "
            "the model requires an offline re-embedding procedure and a new ADR."
        )
