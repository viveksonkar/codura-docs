"""Job handler registry keyed by ``kind``. Handlers must be idempotent per payload (ADR-0006)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from codura_docs.jobs.queue import Job
from codura_docs.registry import Registry

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class JobContext:
    job: Job
    app_engine: AsyncEngine
    registry: Registry
    billing_engine: AsyncEngine | None = None  # codura_worker role: billing writes (ADR-0026)


Handler = Callable[[JobContext], Awaitable[None]]
_HANDLERS: dict[str, Handler] = {}


def handler(kind: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        if kind in _HANDLERS:
            raise ValueError(f"duplicate handler for {kind!r}")
        _HANDLERS[kind] = fn
        return fn

    return register


def get_handler(kind: str) -> Handler | None:
    return _HANDLERS.get(kind)


def kinds() -> list[str]:
    return sorted(_HANDLERS)


@handler("noop")
async def noop(ctx: JobContext) -> None:
    payload: dict[str, Any] = ctx.job.payload
    if payload.get("fail") == "retryable":
        raise RuntimeError("noop asked to fail")
    log.info("noop job ran", extra={"job_kind": ctx.job.kind})
