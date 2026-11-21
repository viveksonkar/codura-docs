"""Postgres-backed Job queue (ADR-0006, specs/db.md §3).

- ``enqueue`` runs inside the caller's transaction, so the domain write and its Job commit together.
- Workers claim with ``FOR UPDATE SKIP LOCKED`` and hold a lease (``locked_until``) they renew.
- A reaper re-queues Jobs whose lease expired (crashed worker).
- Failures retry with backoff; ``NonRetryable`` fails immediately.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from codura_docs.context import tenant_id_var
from codura_docs.ids import new_id

NOTIFY_CHANNEL = "codura_jobs"
BACKOFF_S = (10, 60, 300)
LEASE_S = 60


class NonRetryable(Exception):
    """Raise from a handler to fail the Job immediately without retries."""


@dataclass(frozen=True)
class Job:
    id: str
    tenant_id: str | None
    kind: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int


def backoff_for(attempt: int) -> int:
    return BACKOFF_S[min(attempt, len(BACKOFF_S)) - 1] if attempt > 0 else BACKOFF_S[0]


async def enqueue(
    session: AsyncSession,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    run_after: datetime | None = None,
    max_attempts: int = 3,
) -> str:
    """Enqueue in the caller's transaction. ``tenant_id`` comes from the active TenantContext."""
    job_id = new_id("job")
    tenant_id = tenant_id_var.get()  # set by tenant_session(); None for platform-level Jobs
    await session.execute(
        text(
            "INSERT INTO jobs (id, tenant_id, kind, payload, max_attempts, run_after) "
            "VALUES (:id, :tid, :kind, CAST(:payload AS jsonb), :max, COALESCE(:run_after, now()))"
        ),
        {
            "id": job_id,
            "tid": tenant_id,
            "kind": kind,
            "payload": json.dumps(payload or {}),
            "max": max_attempts,
            "run_after": run_after,
        },
    )
    await session.execute(text(f"NOTIFY {NOTIFY_CHANNEL}"))
    return job_id


async def claim(conn: AsyncConnection, worker_id: str, kinds: list[str]) -> Job | None:
    row = (
        await conn.execute(
            text(
                f"""
                UPDATE jobs SET status = 'running', locked_by = :w,
                       locked_until = now() + interval '{LEASE_S} seconds',
                       attempts = attempts + 1, updated_at = now()
                WHERE id = (
                    SELECT id FROM jobs
                    WHERE status = 'queued' AND run_after <= now() AND kind = ANY(:kinds)
                    ORDER BY run_after
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1)
                RETURNING id, tenant_id, kind, payload, attempts, max_attempts"""  # noqa: S608 - constant
            ),
            {"w": worker_id, "kinds": kinds},
        )
    ).first()
    if row is None:
        return None
    payload = row.payload if isinstance(row.payload, dict) else json.loads(row.payload)
    return Job(row.id, row.tenant_id, row.kind, payload, row.attempts, row.max_attempts)


async def renew_lease(conn: AsyncConnection, job_id: str, worker_id: str) -> None:
    await conn.execute(
        text(
            f"UPDATE jobs SET locked_until = now() + interval '{LEASE_S} seconds' "  # noqa: S608
            "WHERE id = :id AND locked_by = :w AND status = 'running'"
        ),
        {"id": job_id, "w": worker_id},
    )


async def complete(conn: AsyncConnection, job_id: str, worker_id: str) -> None:
    await conn.execute(
        text(
            "UPDATE jobs SET status = 'succeeded', finished_at = now(), updated_at = now(), "
            "locked_by = NULL, locked_until = NULL WHERE id = :id AND locked_by = :w"
        ),
        {"id": job_id, "w": worker_id},
    )


async def fail(
    conn: AsyncConnection, job: Job, worker_id: str, error: str, *, retryable: bool
) -> str:
    """Retry with backoff or fail permanently. Returns the new status."""
    if retryable and job.attempts < job.max_attempts:
        await conn.execute(
            text(
                "UPDATE jobs SET status = 'queued', last_error = :e, locked_by = NULL, locked_until = NULL, "
                "run_after = now() + make_interval(secs => :delay), updated_at = now() "
                "WHERE id = :id AND locked_by = :w"
            ),
            {"id": job.id, "w": worker_id, "e": error[:2000], "delay": backoff_for(job.attempts)},
        )
        return "queued"
    await conn.execute(
        text(
            "UPDATE jobs SET status = 'failed', last_error = :e, finished_at = now(), updated_at = now(), "
            "locked_by = NULL, locked_until = NULL WHERE id = :id AND locked_by = :w"
        ),
        {"id": job.id, "w": worker_id, "e": error[:2000]},
    )
    return "failed"


async def reap_expired(conn: AsyncConnection) -> int:
    """Re-queue Jobs whose worker died (lease expired); fail them if out of attempts."""
    result = await conn.execute(
        text(
            "UPDATE jobs SET status = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'queued' END, "
            "last_error = COALESCE(last_error, '') || ' [lease expired]', locked_by = NULL, "
            "locked_until = NULL, updated_at = now(), "
            "finished_at = CASE WHEN attempts >= max_attempts THEN now() ELSE NULL END "
            "WHERE status = 'running' AND locked_until < now()"
        )
    )
    return int(result.rowcount or 0)


def delay(seconds: float) -> timedelta:
    return timedelta(seconds=seconds)
