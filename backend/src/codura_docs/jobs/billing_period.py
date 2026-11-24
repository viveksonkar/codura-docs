"""Billing Jobs (specs/billing.md §7): rollups, daily snapshots and the subscription cycle.

Platform-level Jobs (no ``tenant_id``) that visit Tenants one by one, each in its own RLS-scoped
transaction on the billing engine (``codura_worker``, ADR-0026 §4). Every step is idempotent, so
the scheduler or a retry can run any of them again safely.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from codura_docs import metering
from codura_docs.billing import rollups, subscriptions
from codura_docs.db.session import global_session, tenant_session
from codura_docs.jobs.handlers import JobContext, handler
from codura_docs.metering.quotas import stock_usage
from codura_docs.tenancy import TenantContext

log = logging.getLogger(__name__)


def _engine(jc: JobContext) -> AsyncEngine:
    return jc.billing_engine or jc.app_engine


async def _tenants(engine: AsyncEngine, sql: str, param: object) -> list[str]:
    async with global_session(engine) as s:
        return [str(r[0]) for r in (await s.execute(text(sql), {"p": param})).all()]


@handler("usage_rollup")
async def usage_rollup(jc: JobContext) -> None:
    day = date.fromisoformat(str(jc.job.payload.get("day") or datetime.now(UTC).date()))
    engine = _engine(jc)
    for tenant_id in await _tenants(
        engine, "SELECT tenant_id FROM billing_tenants_with_usage(:p)", day
    ):
        async with tenant_session(engine, TenantContext(tenant_id)) as s:
            await rollups.rollup_day(s, tenant_id, day)


@handler("usage_snapshot")
async def usage_snapshot(jc: JobContext) -> None:
    """Daily `storage_mb_day` and `seat_day` Usage Events (billing.md §2), keyed by date."""
    day = date.fromisoformat(str(jc.job.payload.get("day") or datetime.now(UTC).date()))
    engine = _engine(jc)
    for tenant_id in await _tenants(
        engine, "SELECT tenant_id FROM billing_tenants_with_usage(:p)", day
    ):
        async with tenant_session(engine, TenantContext(tenant_id)) as s:
            stock = await stock_usage(s, tenant_id)
            rate = await metering.cost_rate(s, "storage_per_mb_day")
            members = (
                await s.execute(
                    text("SELECT count(*) FROM tenant_members WHERE tenant_id = :t"),
                    {"t": tenant_id},
                )
            ).scalar()
            await metering.record(
                s,
                tenant_id,
                "storage_mb_day",
                stock["storage_mb"],
                cogs_micros=round(stock["storage_mb"] * rate),
                idempotency_key=day.isoformat(),
            )
            await metering.record(
                s,
                tenant_id,
                "seat_day",
                int(members or 0),
                cogs_micros=0,
                idempotency_key=day.isoformat(),
            )


@handler("billing_cycle")
async def billing_cycle(jc: JobContext) -> None:
    """Period roll + invoices, trial expiry and past-due grace for every Tenant that is due."""
    now = datetime.now(UTC)
    engine = _engine(jc)
    provider = jc.registry.payments()
    failed: list[str] = []
    for tenant_id in await _tenants(engine, "SELECT tenant_id FROM billing_due_tenants(:p)", now):
        try:
            async with tenant_session(engine, TenantContext(tenant_id)) as s:
                await subscriptions.roll_period(s, provider, tenant_id, now=now)
        except Exception:
            log.exception("billing cycle failed for a tenant", extra={"tenant_id": tenant_id})
            failed.append(tenant_id)
    if failed:
        raise RuntimeError(f"billing cycle failed for {len(failed)} tenant(s)")


def schedule_windows(now: datetime) -> list[tuple[str, str, dict[str, str]]]:
    """(name, window, payload) slots the scheduler enqueues once each (specs/billing.md §7)."""
    today = now.date()
    yesterday = today - timedelta(days=1)
    hour = f"{today.isoformat()}T{now.hour:02d}"
    return [
        ("usage_rollup", hour, {"day": today.isoformat()}),
        ("usage_rollup", f"{yesterday.isoformat()}:final", {"day": yesterday.isoformat()}),
        ("usage_snapshot", today.isoformat(), {"day": today.isoformat()}),
        ("billing_cycle", hour, {}),
    ]
