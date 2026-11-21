"""Development-only endpoints (never mounted when APP_ENV=production)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import text

from codura_docs.db.session import global_session
from codura_docs.jobs.queue import enqueue

router = APIRouter(prefix="/api/admin/debug", tags=["debug"])


@router.post("/jobs/noop", status_code=202)
async def enqueue_noop(request: Request) -> dict[str, Any]:
    async with global_session(request.app.state.engine) as session:
        job_id = await enqueue(session, "noop", {"source": "debug"})
    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
async def job_status(job_id: str, request: Request) -> dict[str, Any]:
    # codura_app has no SELECT on jobs; read through the worker engine for this debug view.
    engine = request.app.state.debug_worker_engine
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, attempts, last_error FROM jobs WHERE id = :id"), {"id": job_id}
            )
        ).first()
    if row is None:
        return {"job_id": job_id, "status": "unknown"}
    return {
        "job_id": job_id,
        "status": row.status,
        "attempts": row.attempts,
        "last_error": row.last_error,
    }
