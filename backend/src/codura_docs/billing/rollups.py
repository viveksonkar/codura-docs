"""Daily usage rollups and the reconciliation check (FR-69, NFR-14, NFR-15; M3-08).

``rollup_day`` rebuilds one Tenant-day from the append-only ``usage_events``: re-running it for any
day yields the same rows (idempotent). Invoices read overage from rollups; ``reconcile`` proves
events = counters = rollups for a period.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from codura_docs.billing.money import next_month


def _bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(), tzinfo=UTC)
    return start, start + timedelta(days=1)


async def rollup_day(session: AsyncSession, tenant_id: str, day: date) -> int:
    start, end = _bounds(day)
    await session.execute(
        text("DELETE FROM usage_rollups WHERE tenant_id = :t AND day = :d"),
        {"t": tenant_id, "d": day},
    )
    result = await session.execute(
        text(
            "INSERT INTO usage_rollups (tenant_id, day, meter, model_profile_id, quantity, cogs_micros, events) "
            "SELECT tenant_id, CAST(:d AS date), meter, coalesce(model_profile_id, ''), sum(quantity), "
            "sum(cogs_micros), count(*) FROM usage_events WHERE tenant_id = :t AND occurred_at >= :s "
            "AND occurred_at < :e GROUP BY tenant_id, meter, coalesce(model_profile_id, '') RETURNING meter"
        ),
        {"t": tenant_id, "d": day, "s": start, "e": end},
    )
    return len(result.all())


async def rollup_range(session: AsyncSession, tenant_id: str, first: date, last: date) -> None:
    day = first
    while day <= last:
        await rollup_day(session, tenant_id, day)
        day += timedelta(days=1)


async def period_totals(session: AsyncSession, tenant_id: str, period: date) -> dict[str, Decimal]:
    """Quantity per meter for a calendar-month period, from rollups."""
    rows = (
        await session.execute(
            text(
                "SELECT meter, sum(quantity) AS q FROM usage_rollups WHERE tenant_id = :t AND day >= :a "
                "AND day < :b GROUP BY meter"
            ),
            {"t": tenant_id, "a": period, "b": next_month(period)},
        )
    ).all()
    return {r.meter: Decimal(r.q) for r in rows}


@dataclass(frozen=True)
class Mismatch:
    meter: str
    events: Decimal
    counters: Decimal
    rollups: Decimal


async def reconcile(session: AsyncSession, tenant_id: str, period: date) -> list[Mismatch]:
    """Every meter's period quantity must agree across events, counters and rollups (0 drift)."""
    start = datetime.combine(period, time(), tzinfo=UTC)
    end = datetime.combine(next_month(period), time(), tzinfo=UTC)
    events = (
        await session.execute(
            text(
                "SELECT meter, sum(quantity) AS q FROM usage_events WHERE tenant_id = :t "
                "AND occurred_at >= :s AND occurred_at < :e GROUP BY meter"
            ),
            {"t": tenant_id, "s": start, "e": end},
        )
    ).all()
    counters = (
        await session.execute(
            text(
                "SELECT meter, quantity AS q FROM usage_counters WHERE tenant_id = :t AND period_start = :p"
            ),
            {"t": tenant_id, "p": period},
        )
    ).all()
    rollups = await period_totals(session, tenant_id, period)
    table: dict[str, list[Decimal]] = defaultdict(lambda: [Decimal(0), Decimal(0), Decimal(0)])
    for r in events:
        table[r.meter][0] = Decimal(r.q)
    for r in counters:
        table[r.meter][1] = Decimal(r.q)
    for meter, q in rollups.items():
        table[meter][2] = q
    return [Mismatch(m, e, c, r) for m, (e, c, r) in sorted(table.items()) if not (e == c == r)]
