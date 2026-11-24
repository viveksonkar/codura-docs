"""Finance & growth views for Platform Admins (FR-69, FR-73; billing.md §9; M3-09).

All money is reported in micro-USD: INR revenue converts with ``cost_rates.fx_usd_inr``. Revenue =
issued, non-void invoices in the window; COGS = Usage Event rollups in the window (the same
ledger quotas and invoices use).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from codura_docs.billing.money import to_usd_micros


async def _fx(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(text("SELECT micros FROM cost_rates WHERE key = 'fx_usd_inr'"))
        ).scalar()
        or 84_000_000
    )


async def _rate(session: AsyncSession, key: str) -> int:
    return int(
        (
            await session.execute(text("SELECT micros FROM cost_rates WHERE key = :k"), {"k": key})
        ).scalar()
        or 0
    )


def _margin(revenue: int, cogs: int) -> float | None:
    return round((revenue - cogs) / revenue * 100, 1) if revenue > 0 else None


async def mrr(session: AsyncSession) -> dict[str, Any]:
    """Monthly recurring revenue: active paid Subscriptions × their Plan version's base price."""
    fx = await _fx(session)
    rows = (
        await session.execute(
            text(
                "SELECT p.code, b.currency, pp.base_micros FROM subscriptions s "
                "JOIN plan_versions v ON v.id = s.plan_version_id JOIN plans p ON p.id = v.plan_id "
                "JOIN billing_accounts b ON b.tenant_id = s.tenant_id "
                "JOIN plan_prices pp ON pp.plan_version_id = v.id AND pp.currency = b.currency "
                "WHERE s.status IN ('active','past_due') AND NOT p.is_free AND NOT s.cancel_at_period_end"
            )
        )
    ).all()
    by_plan: dict[str, int] = defaultdict(int)
    for r in rows:
        by_plan[r.code] += to_usd_micros(int(r.base_micros) // 10_000, r.currency, fx)
    total = sum(by_plan.values())
    return {
        "mrr_usd_micros": total,
        "arr_usd_micros": total * 12,
        "paying_tenants": len(rows),
        "by_plan": dict(by_plan),
    }


async def summary(session: AsyncSession, days: int = 30) -> dict[str, Any]:
    since = datetime.now(UTC).date() - timedelta(days=days)
    fx = await _fx(session)
    revenue_rows = (
        await session.execute(
            text(
                "SELECT i.tenant_id, i.currency, sum(i.total_minor) AS total, "
                "(SELECT p.code FROM subscriptions s JOIN plan_versions v ON v.id = s.plan_version_id "
                " JOIN plans p ON p.id = v.plan_id WHERE s.tenant_id = i.tenant_id) AS plan "
                "FROM invoices i WHERE i.status IN ('open','paid') AND i.issued_at >= :s "
                "GROUP BY i.tenant_id, i.currency"
            ),
            {"s": since},
        )
    ).all()
    revenue_by_tenant: dict[str, int] = defaultdict(int)
    revenue_by_plan: dict[str, int] = defaultdict(int)
    for r in revenue_rows:
        usd = to_usd_micros(int(r.total), r.currency, fx)
        revenue_by_tenant[r.tenant_id] += usd
        revenue_by_plan[r.plan or "unknown"] += usd
    cogs_rows = (
        await session.execute(
            text(
                "SELECT u.tenant_id, u.meter, u.model_profile_id, sum(u.cogs_micros) AS cogs, sum(u.quantity) AS qty, "
                "(SELECT p.code FROM subscriptions s JOIN plan_versions v ON v.id = s.plan_version_id "
                " JOIN plans p ON p.id = v.plan_id WHERE s.tenant_id = u.tenant_id) AS plan "
                "FROM usage_rollups u WHERE u.day >= :s GROUP BY u.tenant_id, u.meter, u.model_profile_id"
            ),
            {"s": since},
        )
    ).all()
    cogs_by_plan: dict[str, int] = defaultdict(int)
    cogs_by_meter: dict[str, int] = defaultdict(int)
    cogs_by_model: dict[str, int] = defaultdict(int)
    answers_by_model: dict[str, Decimal] = defaultdict(Decimal)
    answers_by_tenant_model: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    answers_by_tenant: dict[str, Decimal] = defaultdict(Decimal)
    for r in cogs_rows:
        cogs = int(r.cogs or 0)
        cogs_by_plan[r.plan or "unknown"] += cogs
        cogs_by_meter[r.meter] += cogs
        if r.model_profile_id:
            cogs_by_model[r.model_profile_id] += cogs
        if r.meter in ("answer", "answer_custom_llm") and r.model_profile_id:
            answers_by_model[r.model_profile_id] += Decimal(r.qty)
            answers_by_tenant_model[(r.tenant_id, r.model_profile_id)] += Decimal(r.qty)
            answers_by_tenant[r.tenant_id] += Decimal(r.qty)
    # Revenue per Model Profile: each Tenant's revenue split by its answers per profile.
    revenue_by_model: dict[str, int] = defaultdict(int)
    for (tenant_id, model), qty in answers_by_tenant_model.items():
        share = qty / answers_by_tenant[tenant_id] if answers_by_tenant[tenant_id] else Decimal(0)
        revenue_by_model[model] += int(Decimal(revenue_by_tenant.get(tenant_id, 0)) * share)
    revenue = sum(revenue_by_tenant.values())
    cogs_total = sum(cogs_by_meter.values())
    fixed = await _rate(session, "fixed_cost_per_tenant_month")
    paying = len([t for t, v in revenue_by_tenant.items() if v > 0])
    fixed_share = int(fixed * paying * days / 30)
    plans = sorted(set(revenue_by_plan) | set(cogs_by_plan))
    models = sorted(set(cogs_by_model) | set(revenue_by_model))
    return {
        "days": days,
        "since": since.isoformat(),
        **await mrr(session),
        "revenue_usd_micros": revenue,
        "cogs_usd_micros": cogs_total,
        "gross_margin_pct": _margin(revenue, cogs_total),
        "fixed_cost_usd_micros": fixed_share,
        "contribution_margin_pct": _margin(revenue, cogs_total + fixed_share),
        "by_plan": [
            {
                "plan": p,
                "revenue": revenue_by_plan.get(p, 0),
                "cogs": cogs_by_plan.get(p, 0),
                "margin_pct": _margin(revenue_by_plan.get(p, 0), cogs_by_plan.get(p, 0)),
            }
            for p in plans
        ],
        "by_model": [
            {
                "model_profile_id": m,
                "answers": float(answers_by_model.get(m, 0)),
                "revenue": revenue_by_model.get(m, 0),
                "cogs": cogs_by_model.get(m, 0),
                "margin_pct": _margin(revenue_by_model.get(m, 0), cogs_by_model.get(m, 0)),
            }
            for m in models
        ],
        "by_meter": [{"meter": k, "cogs": v} for k, v in sorted(cogs_by_meter.items())],
    }


async def tenants(session: AsyncSession, days: int = 30) -> list[dict[str, Any]]:
    """Per-Tenant revenue vs COGS; flags Tenants below their Plan's margin floor, with the cause."""
    since = datetime.now(UTC).date() - timedelta(days=days)
    fx = await _fx(session)
    rows = (
        await session.execute(
            text(
                "SELECT t.id, t.name, t.status, p.code AS plan, p.is_free, v.target_margin_pct, v.overage_enabled, "
                "coalesce((SELECT sum(total_minor) FROM invoices i WHERE i.tenant_id = t.id AND i.status IN "
                " ('open','paid') AND i.issued_at >= :s AND i.currency = 'USD'), 0) AS usd_minor, "
                "coalesce((SELECT sum(total_minor) FROM invoices i WHERE i.tenant_id = t.id AND i.status IN "
                " ('open','paid') AND i.issued_at >= :s AND i.currency = 'INR'), 0) AS inr_minor, "
                "coalesce((SELECT sum(cogs_micros) FROM usage_rollups u WHERE u.tenant_id = t.id AND u.day >= :s), 0) AS cogs "
                "FROM tenants t LEFT JOIN subscriptions s ON s.tenant_id = t.id "
                "LEFT JOIN plan_versions v ON v.id = s.plan_version_id LEFT JOIN plans p ON p.id = v.plan_id "
                "ORDER BY cogs DESC LIMIT 500"
            ),
            {"s": since},
        )
    ).all()
    drivers = (
        await session.execute(
            text(
                "SELECT DISTINCT ON (u.tenant_id) u.tenant_id, u.meter, u.model_profile_id, m.is_local, "
                "sum(u.cogs_micros) AS c FROM usage_rollups u LEFT JOIN model_profiles m ON m.id = u.model_profile_id "
                "WHERE u.day >= :s GROUP BY u.tenant_id, u.meter, u.model_profile_id, m.is_local "
                "ORDER BY u.tenant_id, c DESC"
            ),
            {"s": since},
        )
    ).all()
    top = {d.tenant_id: d for d in drivers}
    out = []
    for r in rows:
        revenue = to_usd_micros(int(r.usd_minor), "USD", fx) + to_usd_micros(
            int(r.inr_minor), "INR", fx
        )
        cogs = int(r.cogs)
        margin = _margin(revenue, cogs)
        floor = float(r.target_margin_pct or 70)
        unprofitable = cogs > 0 and (margin is None or margin < floor) and not r.is_free
        causes: list[str] = []
        d = top.get(r.id)
        if unprofitable and d is not None:
            causes.append(
                f"top cost: {d.meter}" + (f" on {d.model_profile_id}" if d.model_profile_id else "")
            )
            if d.is_local is False:
                causes.append("remote (paid) model")
            if not r.overage_enabled:
                causes.append("overage disabled")
        out.append(
            {
                "tenant_id": r.id,
                "name": r.name,
                "status": r.status,
                "plan": r.plan,
                "revenue_usd_micros": revenue,
                "cogs_usd_micros": cogs,
                "margin_pct": margin,
                "floor_pct": floor,
                "unprofitable": unprofitable,
                "free_cost": bool(r.is_free and cogs > 0),
                "causes": causes,
            }
        )
    return out


async def simulate(
    session: AsyncSession,
    plan_code: str,
    *,
    base_micros: dict[str, int],
    included_answers: int | None,
) -> dict[str, Any]:
    """Price simulator: last 30 days of usage of this Plan's Tenants under a new price/quota."""
    since = datetime.now(UTC).date() - timedelta(days=30)
    fx = await _fx(session)
    subs = (
        await session.execute(
            text(
                "SELECT s.tenant_id, b.currency, v.included, pp.overage_micros FROM subscriptions s "
                "JOIN plan_versions v ON v.id = s.plan_version_id JOIN plans p ON p.id = v.plan_id "
                "LEFT JOIN billing_accounts b ON b.tenant_id = s.tenant_id "
                "LEFT JOIN plan_prices pp ON pp.plan_version_id = v.id AND pp.currency = coalesce(b.currency, 'USD') "
                "WHERE p.code = :c"
            ),
            {"c": plan_code},
        )
    ).all()
    revenue = cogs = 0
    for s in subs:
        currency = s.currency or "USD"
        base = int(base_micros.get(currency, 0))
        used = Decimal(
            (
                await session.execute(
                    text(
                        "SELECT coalesce(sum(quantity), 0) FROM usage_rollups WHERE tenant_id = :t AND day >= :s "
                        "AND meter IN ('answer','answer_custom_llm')"
                    ),
                    {"t": s.tenant_id, "s": since},
                )
            ).scalar()
            or 0
        )
        included = (
            included_answers
            if included_answers is not None
            else int((s.included or {}).get("answer", 0))
        )
        over = max(Decimal(0), used - included)
        over_price = int((s.overage_micros or {}).get("answer", 0))
        revenue += to_usd_micros(
            int((Decimal(base) + over * over_price) / 10_000),
            "INR" if currency == "INR" else "USD",
            fx,
        )
        cogs += int(
            (
                await session.execute(
                    text(
                        "SELECT coalesce(sum(cogs_micros), 0) FROM usage_rollups WHERE tenant_id = :t AND day >= :s"
                    ),
                    {"t": s.tenant_id, "s": since},
                )
            ).scalar()
            or 0
        )
    return {
        "plan": plan_code,
        "tenants": len(subs),
        "revenue_usd_micros": revenue,
        "cogs_usd_micros": cogs,
        "margin_pct": _margin(revenue, cogs),
    }


async def funnel(session: AsyncSession, days: int = 90) -> dict[str, Any]:
    """Signup → verified → activated (a Document or an answer) → paid (FR-73)."""
    since = datetime.now(UTC) - timedelta(days=days)
    row = (
        await session.execute(
            text(
                "SELECT count(*) AS signups, "
                "count(*) FILTER (WHERE t.status <> 'pending_verification') AS verified, "
                "count(*) FILTER (WHERE EXISTS (SELECT 1 FROM documents d WHERE d.tenant_id = t.id) "
                " OR EXISTS (SELECT 1 FROM usage_events e WHERE e.tenant_id = t.id AND e.meter = 'answer')) AS activated, "
                "count(*) FILTER (WHERE EXISTS (SELECT 1 FROM invoices i WHERE i.tenant_id = t.id AND i.status = 'paid' "
                " AND i.total_minor > 0)) AS paid "
                "FROM tenants t WHERE t.created_at >= :s"
            ),
            {"s": since},
        )
    ).one()
    weekly = (
        await session.execute(
            text(
                "SELECT date_trunc('week', created_at)::date AS week, count(*) AS n FROM tenants "
                "WHERE created_at >= :s GROUP BY 1 ORDER BY 1"
            ),
            {"s": since},
        )
    ).all()
    steps = {
        "signups": int(row.signups),
        "verified": int(row.verified),
        "activated": int(row.activated),
        "paid": int(row.paid),
    }
    return {
        "days": days,
        **steps,
        "conversion_pct": {
            k: round(v / steps["signups"] * 100, 1) if steps["signups"] else None
            for k, v in steps.items()
        },
        "weekly_signups": [
            {"week": w.week.isoformat() if isinstance(w.week, date) else str(w.week), "n": int(w.n)}
            for w in weekly
        ],
    }
