"""Public Chat API abuse limits (SEC-06): token buckets and concurrency caps behind one interface.

The in-memory implementation is per process, which is right for the single-`api` topology
(ADR-0013). M6 adds a Postgres/Redis implementation of the same Protocols for several replicas.
"""

from __future__ import annotations

import math
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Decision:
    allowed: bool
    retry_after_s: int = 0


class RateLimiter(Protocol):
    def hit(self, bucket: str, *, per_minute: int, cost: float = 1.0) -> Decision:
        """Take `cost` tokens from `bucket` (capacity = refill per minute). 0 disables the bucket."""
        ...


class ConcurrencyLimiter(Protocol):
    def acquire(self, slots: list[tuple[str, int]]) -> bool:
        """Take one slot in every named pool, all or nothing. A limit of 0 means unlimited."""
        ...

    def release(self, slots: list[tuple[str, int]]) -> None: ...


class InMemoryRateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic, max_buckets: int = 50_000):
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}  # name -> (tokens, updated_at)
        self._max = max_buckets

    def hit(self, bucket: str, *, per_minute: int, cost: float = 1.0) -> Decision:
        if per_minute <= 0:
            return Decision(True)
        now = self._clock()
        capacity = float(per_minute)
        rate = capacity / 60.0
        tokens, updated = self._buckets.get(bucket, (capacity, now))
        tokens = min(capacity, tokens + (now - updated) * rate)
        if tokens >= cost:
            self._store(bucket, tokens - cost, now)
            return Decision(True)
        self._store(bucket, tokens, now)
        return Decision(False, max(1, math.ceil((cost - tokens) / rate)))

    def _store(self, bucket: str, tokens: float, now: float) -> None:
        if bucket not in self._buckets and len(self._buckets) >= self._max:
            # Drop the oldest-updated half: full buckets are equivalent to absent ones.
            for name, _ in sorted(self._buckets.items(), key=lambda kv: kv[1][1])[: self._max // 2]:
                del self._buckets[name]
        self._buckets[bucket] = (tokens, now)


class InMemoryConcurrencyLimiter:
    def __init__(self) -> None:
        self._active: dict[str, int] = {}

    def acquire(self, slots: list[tuple[str, int]]) -> bool:
        if any(limit > 0 and self._active.get(name, 0) >= limit for name, limit in slots):
            return False
        for name, _ in slots:
            self._active[name] = self._active.get(name, 0) + 1
        return True

    def release(self, slots: list[tuple[str, int]]) -> None:
        for name, _ in slots:
            left = self._active.get(name, 0) - 1
            if left > 0:
                self._active[name] = left
            else:
                self._active.pop(name, None)

    def active(self, name: str) -> int:
        return self._active.get(name, 0)


async def release_after[T](
    events: AsyncIterator[T], limiter: ConcurrencyLimiter, slots: list[tuple[str, int]]
) -> AsyncIterator[T]:
    """Pass a stream through and free the slots however it ends (incl. client disconnect)."""
    try:
        async for item in events:
            yield item
    finally:
        limiter.release(slots)
        aclose = getattr(events, "aclose", None)
        if aclose is not None:
            await aclose()
