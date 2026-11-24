"""Real-time quota admission (FR-64, NFR-14, NFR-16; specs/billing.md §4).

``admit`` runs BEFORE the expensive work. It locks the period counter row, adds the live
reservations, and either denies (hard stop / spend cap) or inserts a reservation that
``metering.record`` deletes in the Usage Event's transaction. Parallel requests therefore never
overshoot a hard limit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from codura_docs.billing.entitlements import SHARED_ALLOWANCE, Terms, for_tenant
from codura_docs.ids import new_id

RESERVATION_TTL = timedelta(minutes=10)
THRESHOLDS = (80, 100)
Reason = Literal["ok", "overage", "quota_exceeded", "spend_cap_reached", "past_due"]
# Meters that share one allowance (Custom LLM answers count as answers; billing.md §4).
ALLOWANCE = {"answer": "answer", "answer_custom_llm": "answer", "page_ocr": "page_ocr"}
SHARED = SHARED_ALLOWANCE


@dataclass(frozen=True)
class Admission:
    allowed: bool
    reason: Reason
    used: float
    limit: float | None
    reservation_id: str | None = None

    @property
    def warn(self) -> bool:
        return self.limit is not None and self.limit > 0 and self.used >= 0.8 * self.limit


def period_of(now: datetime | None = None) -> date:
    now = now or datetime.now(UTC)
    return date(now.year, now.month, 1)


async def _used(
    session: AsyncSession, tenant_id: str, allowance: str, period: date, now: datetime
) -> float:
    meters = list(SHARED[allowance])
    counted = (
        await session.execute(
            text(
                "SELECT coalesce(sum(quantity), 0) FROM usage_counters WHERE tenant_id = :t "
                "AND period_start = :p AND meter = ANY(:m)"
            ),
            {"t": tenant_id, "p": period, "m": meters},
        )
    ).scalar()
    reserved = (
        await session.execute(
            text(
                "SELECT coalesce(sum(quantity), 0) FROM quota_reservations WHERE tenant_id = :t "
                "AND period_start = :p AND meter = ANY(:m) AND expires_at > :now"
            ),
            {"t": tenant_id, "p": period, "m": meters, "now": now},
        )
    ).scalar()
    return float(counted or 0) + float(reserved or 0)


async def overage_spend_micros(
    session: AsyncSession, terms: Terms, period: date, now: datetime
) -> int:
    """Overage cost so far this period, in micros of the Tenant's currency."""
    total = Decimal(0)
    for allowance, meters in SHARED.items():
        used = await _used(session, terms.tenant_id, allowance, period, now)
        over = max(0.0, used - terms.limit(allowance))
        if over:
            price = max(terms.overage_price(m) for m in meters)
            total += Decimal(str(over)) * price
    return int(total)


async def admit(
    session: AsyncSession,
    tenant_id: str,
    meter: str,
    quantity: float = 1,
    *,
    terms: Terms | None = None,
    now: datetime | None = None,
) -> Admission:
    now = now or datetime.now(UTC)
    terms = terms or await for_tenant(session, tenant_id, now=now)
    if terms.past_due:
        return Admission(False, "past_due", 0, None)
    allowance = ALLOWANCE.get(meter)
    if allowance is None:
        return Admission(True, "ok", 0, None)
    period = period_of(now)
    # Serialise admissions per Tenant + allowance on the counter row (row lock until commit).
    await session.execute(
        text(
            "INSERT INTO usage_counters (tenant_id, meter, period_start) VALUES (:t, :m, :p) "
            "ON CONFLICT DO NOTHING"
        ),
        {"t": tenant_id, "m": allowance, "p": period},
    )
    await session.execute(
        text(
            "SELECT 1 FROM usage_counters WHERE tenant_id = :t AND meter = :m AND period_start = :p FOR UPDATE"
        ),
        {"t": tenant_id, "m": allowance, "p": period},
    )
    used = await _used(session, tenant_id, allowance, period, now)
    limit = float(terms.limit(allowance))
    reason: Reason = "ok"
    if used + quantity > limit:
        if not terms.overage_enabled:
            return Admission(False, "quota_exceeded", used, limit)
        if terms.spend_cap_micros is not None:
            spent = await overage_spend_micros(session, terms, period, now)
            marginal = int(Decimal(str(quantity)) * terms.overage_price(meter))
            if spent + marginal > terms.spend_cap_micros:
                return Admission(False, "spend_cap_reached", used, limit)
        reason = "overage"
    reservation_id = new_id("qres")
    await session.execute(
        text(
            "INSERT INTO quota_reservations (id, tenant_id, meter, period_start, quantity, expires_at) "
            "VALUES (:id, :t, :m, :p, :q, :e)"
        ),
        {
            "id": reservation_id,
            "t": tenant_id,
            "m": meter,
            "p": period,
            "q": quantity,
            "e": now + RESERVATION_TTL,
        },
    )
    return Admission(True, reason, used, limit, reservation_id)


async def release(session: AsyncSession, reservation_id: str | None) -> None:
    if reservation_id:
        await session.execute(
            text("DELETE FROM quota_reservations WHERE id = :id"), {"id": reservation_id}
        )


async def stock_usage(session: AsyncSession, tenant_id: str) -> dict[str, float]:
    """Current values of the stock limits (pages, storage, seats, assistants)."""
    row = (
        await session.execute(
            text(
                "SELECT "
                "(SELECT coalesce(sum(page_count), 0) FROM documents WHERE tenant_id = :t AND status IN ('ready','processing')) AS pages, "
                "(SELECT coalesce(sum(size_bytes), 0) FROM documents WHERE tenant_id = :t) AS bytes, "
                "(SELECT count(*) FROM tenant_members WHERE tenant_id = :t) + "
                "(SELECT count(*) FROM invites WHERE tenant_id = :t AND accepted_at IS NULL AND expires_at > now()) AS seats, "
                "(SELECT count(*) FROM assistants WHERE tenant_id = :t) AS assistants"
            ),
            {"t": tenant_id},
        )
    ).one()
    return {
        "pages_stored": float(row.pages or 0),
        "storage_mb": round(float(row.bytes or 0) / (1024 * 1024), 2),
        "seats": float(row.seats or 0),
        "assistants": float(row.assistants or 0),
    }


async def check_stock(
    session: AsyncSession, terms: Terms, key: str, needed: float = 1
) -> Admission:
    current = (await stock_usage(session, terms.tenant_id))[key]
    limit = float(terms.limit(key))
    return Admission(
        current + needed <= limit,
        "ok" if current + needed <= limit else "quota_exceeded",
        current,
        limit,
    )


async def note_usage(
    session: AsyncSession, tenant_id: str, meter: str, *, now: datetime | None = None
) -> None:
    """After a Usage Event: record 80 % / 100 % crossings once per period and queue the email."""
    allowance = ALLOWANCE.get(meter)
    if allowance is None:
        return
    now = now or datetime.now(UTC)
    terms = await for_tenant(session, tenant_id, now=now)
    limit = terms.limit(allowance)
    if limit <= 0:
        return
    period = period_of(now)
    used = await _used(session, tenant_id, allowance, period, now)
    for threshold in THRESHOLDS:
        if used * 100 < threshold * limit:
            continue
        inserted = (
            await session.execute(
                text(
                    "INSERT INTO quota_notices (tenant_id, meter, period_start, threshold) "
                    "VALUES (:t, :m, :p, :th) ON CONFLICT DO NOTHING RETURNING threshold"
                ),
                {"t": tenant_id, "m": allowance, "p": period, "th": threshold},
            )
        ).first()
        if inserted is not None:
            await _email_admins(session, tenant_id, terms, allowance, threshold)


METER_LABEL = {"answer": "answers", "page_ocr": "OCR pages"}


async def _email_admins(
    session: AsyncSession, tenant_id: str, terms: Terms, meter: str, threshold: int
) -> None:
    from codura_docs.services.email_flows import queue_email

    admins = (
        (
            await session.execute(
                text(
                    "SELECT u.email FROM tenant_members m JOIN users u ON u.id = m.user_id "
                    "WHERE m.tenant_id = :t AND m.role = 'admin' ORDER BY u.email"
                ),
                {"t": tenant_id},
            )
        )
        .scalars()
        .all()
    )
    label = METER_LABEL.get(meter, meter)
    if threshold >= 100:
        what = (
            "Further usage is billed as overage."
            if terms.overage_enabled
            else "New requests are paused until the next period or an upgrade."
        )
    else:
        what = "You can upgrade your plan in the Admin Console under Billing."
    for email in admins:
        await queue_email(
            session,
            str(email),
            f"You have used {threshold}% of your {label} this month",
            f"Your organisation has used {threshold}% of the {terms.limit(meter)} {label} included "
            f"in the {terms.plan_name} plan this month.\n\n{what}",
        )


async def notices(
    session: AsyncSession, tenant_id: str, *, now: datetime | None = None
) -> list[dict[str, object]]:
    """Banner data for the Admin Console: the highest threshold reached per meter this period."""
    rows = (
        await session.execute(
            text(
                "SELECT meter, max(threshold) AS threshold FROM quota_notices WHERE tenant_id = :t "
                "AND period_start = :p GROUP BY meter ORDER BY meter"
            ),
            {"t": tenant_id, "p": period_of(now)},
        )
    ).all()
    return [{"meter": r.meter, "threshold": int(r.threshold)} for r in rows]
