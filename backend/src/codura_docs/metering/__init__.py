"""Metering: Usage Events with COGS, period counters and quota checks (ADR-0021, M1-20).

Every billable action calls ``record`` inside the SAME transaction as the action. Duplicate
idempotency keys are no-ops, so Job retries never double-bill (NFR-14).
"""

from __future__ import annotations

import calendar
import json
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
METERS: tuple[str, ...] = (
    "answer",
    "answer_custom_llm",
    "page_ingested",
    "page_ocr",
    "storage_mb_day",
    "seat_day",
)


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
    reservation_id: str | None = None,
) -> bool:
    """Write a Usage Event and bump the period counter. Returns False if it was a duplicate.

    ``reservation_id`` (from ``quotas.admit``) is consumed in the same transaction.
    """
    from codura_docs.metering.quotas import note_usage, release

    await release(session, reservation_id)
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
            "cogs_micros = usage_counters.cogs_micros + EXCLUDED.cogs_micros, updated_at = now()"
        ),
        {"t": tenant_id, "m": meter, "p": period_start(), "q": quantity, "c": cogs_micros},
    )
    await note_usage(session, tenant_id, meter)
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


# --- Usage vs Plan (Tenant-facing: quantities only, never COGS) ----------------------------------


async def usage_summary(
    session: AsyncSession, settings: Settings, tenant_id: str
) -> list[dict[str, Any]]:
    """Flow meters (this period) and stock limits vs the effective Plan, with a month-end projection."""
    from codura_docs.billing.entitlements import FLOW_METERS, INCLUDED, for_tenant
    from codura_docs.metering.quotas import stock_usage

    terms = await for_tenant(session, tenant_id)
    rows = (
        await session.execute(
            text(
                "SELECT meter, quantity FROM usage_counters WHERE tenant_id = :t AND period_start = :p"
            ),
            {"t": tenant_id, "p": period_start()},
        )
    ).all()
    used = {r.meter: float(r.quantity) for r in rows}
    used["answer"] = used.get("answer", 0.0) + used.pop("answer_custom_llm", 0.0)
    now = datetime.now(UTC)
    days = calendar.monthrange(now.year, now.month)[1]
    elapsed = max(1.0, (now - datetime(now.year, now.month, 1, tzinfo=UTC)).total_seconds() / 86400)
    out: list[dict[str, Any]] = []
    for meter in FLOW_METERS:
        value = used.get(meter, 0.0)
        out.append(
            {
                "meter": meter,
                "kind": "flow",
                "used": value,
                "limit": terms.limit(meter),
                "projected": round(value / elapsed * days, 1),
            }
        )
    stock = await stock_usage(session, tenant_id)
    for key, (kind, _) in INCLUDED.items():
        if kind == "stock":
            out.append({"meter": key, "kind": kind, "used": stock[key], "limit": terms.limit(key)})
    out.append(
        {
            "meter": "page_ingested",
            "kind": "flow",
            "used": used.get("page_ingested", 0.0),
            "limit": None,
        }
    )
    return out
