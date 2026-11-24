"""Basic Tenant insights (FR-72 basic, telemetry.md §3). Runs under the Tenant's RLS context.

Quantities and that Tenant's own question text only: never COGS, model names or other Tenants.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from codura_docs import metering
from codura_docs.config import Settings


def _projection(used: float, now: datetime) -> float:
    """Linear projection of this period's usage to the period end (calendar month)."""
    start = datetime(now.year, now.month, 1, tzinfo=UTC)
    nxt = datetime(now.year + (now.month == 12), now.month % 12 + 1, 1, tzinfo=UTC)
    elapsed = max((now - start).total_seconds(), 3600.0)
    return round(used * (nxt - start).total_seconds() / elapsed, 1)


async def summary(
    session: AsyncSession, settings: Settings, tenant_id: str, *, days: int = 30
) -> dict[str, Any]:
    now = datetime.now(UTC)
    since = now - timedelta(days=days)
    meters = await metering.usage_summary(session, settings, tenant_id)
    for m in meters:
        m["projected"] = _projection(float(m["used"]), now)
    by_channel = (
        await session.execute(
            text(
                "SELECT channel, count(*) AS n FROM conversations WHERE started_at >= :s "
                "GROUP BY channel ORDER BY n DESC"
            ),
            {"s": since},
        )
    ).all()
    by_language = (
        await session.execute(
            text(
                "SELECT coalesce(language, 'unknown') AS language, count(*) AS n FROM messages "
                "WHERE role = 'user' AND created_at >= :s GROUP BY 1 ORDER BY n DESC"
            ),
            {"s": since},
        )
    ).all()
    per_day = (
        await session.execute(
            text(
                "SELECT date_trunc('day', started_at)::date AS day, count(*) AS n FROM conversations "
                "WHERE started_at >= :s GROUP BY 1 ORDER BY 1"
            ),
            {"s": since},
        )
    ).all()
    answers = (
        await session.execute(
            text(
                "SELECT count(*) FILTER (WHERE outcome = 'answered') AS answered, "
                "count(*) FILTER (WHERE outcome = 'fallback') AS fallback, "
                "count(*) FILTER (WHERE feedback = 1) AS up, count(*) FILTER (WHERE feedback = -1) AS down, "
                "avg(latency_ms) FILTER (WHERE outcome = 'answered') AS avg_latency_ms "
                "FROM messages WHERE role = 'assistant' AND created_at >= :s"
            ),
            {"s": since},
        )
    ).one()
    rated = (answers.up or 0) + (answers.down or 0)
    return {
        "days": days,
        "usage": {"plan": "trial", "period_start": str(metering.period_start()), "meters": meters},
        "conversations_by_channel": [{"channel": r.channel, "count": r.n} for r in by_channel],
        "questions_by_language": [{"language": r.language, "count": r.n} for r in by_language],
        "conversations_per_day": [{"day": str(r.day), "count": r.n} for r in per_day],
        "answers": {
            "answered": answers.answered or 0,
            "fallback": answers.fallback or 0,
            "thumbs_up": answers.up or 0,
            "thumbs_down": answers.down or 0,
            "feedback_ratio": round((answers.up or 0) / rated, 3) if rated else None,
            "avg_latency_ms": int(answers.avg_latency_ms) if answers.avg_latency_ms else None,
        },
    }


async def unanswered(
    session: AsyncSession, *, days: int = 30, limit: int = 100
) -> list[dict[str, Any]]:
    """The content-gap list: questions that got the fallback text or a thumbs-down."""
    rows = (
        await session.execute(
            text(
                """
                SELECT q.content AS question, q.language, a.outcome, a.feedback, a.feedback_note,
                       a.created_at, c.id AS conversation_id, c.channel, asst.name AS assistant_name
                FROM messages a
                JOIN conversations c ON c.id = a.conversation_id
                JOIN assistants asst ON asst.id = c.assistant_id
                JOIN LATERAL (
                    SELECT content, language FROM messages u
                    WHERE u.conversation_id = a.conversation_id AND u.role = 'user'
                      AND u.created_at <= a.created_at
                    ORDER BY u.created_at DESC LIMIT 1
                ) q ON TRUE
                WHERE a.role = 'assistant' AND a.created_at >= :s
                  AND (a.outcome = 'fallback' OR a.feedback = -1)
                ORDER BY a.created_at DESC
                LIMIT :l
                """
            ),
            {"s": datetime.now(UTC) - timedelta(days=days), "l": limit},
        )
    ).mappings()
    return [
        dict(r) | {"reason": "fallback" if r["outcome"] == "fallback" else "thumbs_down"}
        for r in rows.all()
    ]
