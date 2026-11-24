"""Readiness checks aggregated by ``/readyz``. Later Tasks register more checks."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

Check = Callable[[], Awaitable[None]]


@dataclass
class Readiness:
    checks: dict[str, Check] = field(default_factory=dict)
    timeout_s: float = 5.0

    def register(self, name: str, check: Check) -> None:
        self.checks[name] = check

    async def run(self) -> tuple[bool, dict[str, str]]:
        async def one(name: str, check: Check) -> tuple[str, str]:
            try:
                await asyncio.wait_for(check(), self.timeout_s)
            except Exception as exc:
                return name, f"fail: {type(exc).__name__}: {str(exc)[:200]}"
            return name, "ok"

        results = dict(await asyncio.gather(*(one(n, c) for n, c in self.checks.items())))
        return all(v == "ok" for v in results.values()), results
