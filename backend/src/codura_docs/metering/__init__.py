"""Metering: Usage Events with COGS, period counters and quota checks (ADR-0021, M1-20).

Every billable action calls ``record`` inside the SAME transaction as the action. Duplicate
idempotency keys are no-ops, so Job retries never double-bill (NFR-14).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from codura_docs.config import Settings
from codura_docs.ids import new_id
from codura_docs.ports.chat import ModelProfile

Meter = Literal[
    "answer", "answer_custom_llm", "page_ingested", "page_ocr", "storage_mb_day", "seat_day"
]


def period_start(now: datetime | None = None) -> date:
    now = now or datetime.now(UTC)
    return date(now.year, now.month, 1)


async def record(
    session: AsyncSession,
    tenant_id: str,
    meter: Meter,
    quantity: float,
    *,
    cogs_micros: int,
    idempotency_key: str,
    model_profile_id: str | None = None,
    attrs: dict[str, Any] | None = None,
) -> bool:
    """Write a Usage Event and bump the period counter. Returns False if it was a duplicate."""
    inserted = (
        await session.execute(
            text(
                "INSERT INTO usage_events (id, tenant_id, meter, quantity, cogs_micros, model_profile_id, "
                "idempotency_key, attrs) VALUES (:id, :t, :m, :q, :c, :p, :k, CAST(:a AS jsonb)) "
                "ON CONFLICT (tenant_id, meter, idempotency_key) DO NOTHING RETURNING id"
            ),
            {
                "id": new_id("ue"),
                "t": tenant_id,
                "m": meter,
                "q": quantity,
                "c": cogs_micros,
                "p": model_profile_id,
                "k": idempotency_key,
                "a": json.dumps(attrs or {}),
            },
        )
    ).first()
    if inserted is None:
        return False
    await session.execute(
        text(
            "INSERT INTO usage_counters (tenant_id, meter, period_start, quantity, cogs_micros) "
            "VALUES (:t, :m, :p, :q, :c) ON CONFLICT (tenant_id, meter, period_start) DO UPDATE "
            "SET quantity = usage_counters.quantity + EXCLUDED.quantity, "
            "cogs_micros = usage_counters.cogs_micros + EXCLUDED.cogs_micros"
        ),
        {"t": tenant_id, "m": meter, "p": period_start(), "q": quantity, "c": cogs_micros},
    )
    return True


# --- COGS (billing.md §3) -----------------------------------------------------------------------


def answer_cogs(
    profile: ModelProfile, prompt_tokens: int | None, completion_tokens: int | None
) -> int:
    """Micro-USD for one LLM call from the profile's price snapshot."""
    p, c = prompt_tokens or 0, completion_tokens or 0
    return round(
        (p * profile.price_in_per_mtok_micros + c * profile.price_out_per_mtok_micros) / 1_000_000
    )


async def cost_rate(session: AsyncSession, key: str) -> int:
    value = (
        await session.execute(text("SELECT micros FROM cost_rates WHERE key = :k"), {"k": key})
    ).scalar()
    return int(value or 0)


# --- Quotas (fixed trial limits until Plans arrive in M3) -----------------------------------------


@dataclass(frozen=True)
class QuotaDecision:
    allowed: bool
    used: float
    limit: float | None
    warn: bool


def trial_limit(settings: Settings, meter: Meter) -> float | None:
    return {
        "answer": settings.trial_answers,
        "page_ingested": settings.trial_pages,
        "page_ocr": settings.trial_ocr_pages,
    }.get(meter)


async def check_quota(
    session: AsyncSession, settings: Settings, tenant_id: str, meter: Meter, needed: float = 1
) -> QuotaDecision:
    limit = trial_limit(settings, meter)
    used = float(
        (
            await session.execute(
                text(
                    "SELECT quantity FROM usage_counters WHERE tenant_id = :t AND meter = :m AND period_start = :p"
                ),
                {"t": tenant_id, "m": meter, "p": period_start()},
            )
        ).scalar()
        or 0
    )
    if limit is None:
        return QuotaDecision(True, used, None, False)
    return QuotaDecision(used + needed <= limit, used, limit, used + needed > 0.8 * limit)


async def usage_summary(
    session: AsyncSession, settings: Settings, tenant_id: str
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                "SELECT meter, quantity FROM usage_counters WHERE tenant_id = :t AND period_start = :p"
            ),
            {"t": tenant_id, "p": period_start()},
        )
    ).all()
    used = {r.meter: float(r.quantity) for r in rows}
    meters: list[Meter] = ["answer", "page_ingested", "page_ocr"]
    return [
        {"meter": m, "used": used.get(m, 0.0), "limit": trial_limit(settings, m)} for m in meters
    ]
