"""M0-06: Postgres Job queue semantics."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from codura_docs.config import Settings
from codura_docs.db.session import global_session, tenant_session
from codura_docs.ids import new_id
from codura_docs.jobs import handlers as registry_mod
from codura_docs.jobs.handlers import JobContext, handler
from codura_docs.jobs.queue import NonRetryable, claim, enqueue, reap_expired
from codura_docs.registry import Registry
from codura_docs.tenancy import TenantContext
from codura_docs.worker.runner import Worker

pytestmark = pytest.mark.integration

RAN: list[str] = []


@handler("test_record")
async def _record(ctx: JobContext) -> None:
    RAN.append(ctx.job.id)


@handler("test_permanent")
async def _permanent(ctx: JobContext) -> None:
    raise NonRetryable("corrupt file")


async def _job(worker_engine: AsyncEngine, job_id: str) -> dict[str, object]:
    async with worker_engine.connect() as conn:
        row = (
            (await conn.execute(text("SELECT * FROM jobs WHERE id = :id"), {"id": job_id}))
            .mappings()
            .one()
        )
    return dict(row)


async def test_enqueue_takes_tenant_from_context_and_commits_atomically(
    owner_engine: AsyncEngine, app_engine: AsyncEngine, worker_engine: AsyncEngine
) -> None:
    tenant_id = new_id("tnt")
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO tenants (id, slug, name) VALUES (:id, :slug, 'x')"),
            {"id": tenant_id, "slug": tenant_id.lower()},
        )
    async with tenant_session(app_engine, TenantContext(tenant_id)) as s:
        job_id = await enqueue(s, "noop", {"tenant_id": "tnt_SPOOFED"})
    assert (await _job(worker_engine, job_id))["tenant_id"] == tenant_id

    enqueued: list[str] = []

    async def failing_domain_write() -> None:
        async with global_session(app_engine) as s:
            enqueued.append(await enqueue(s, "noop"))
            raise RuntimeError("domain write failed")

    with pytest.raises(RuntimeError):
        await failing_domain_write()
    rolled_back = enqueued[0]
    async with worker_engine.connect() as conn:
        assert (
            await conn.execute(
                text("SELECT count(*) FROM jobs WHERE id = :id"), {"id": rolled_back}
            )
        ).scalar_one() == 0


async def test_two_workers_never_claim_the_same_job(
    app_engine: AsyncEngine, worker_engine: AsyncEngine
) -> None:
    async with global_session(app_engine) as s:
        ids = {await enqueue(s, "test_claim") for _ in range(20)}

    async def grab(worker: str) -> list[str]:
        got = []
        while True:
            async with worker_engine.begin() as conn:
                job = await claim(conn, worker, ["test_claim"])
            if job is None:
                return got
            got.append(job.id)

    a, b = await asyncio.gather(grab("w1"), grab("w2"))
    assert set(a).isdisjoint(b)
    assert set(a) | set(b) == ids


async def test_worker_runs_retries_and_fails_permanently(
    pg_database: Settings, app_engine: AsyncEngine, worker_engine: AsyncEngine
) -> None:
    worker = Worker(pg_database, Registry.from_settings(pg_database), worker_engine, app_engine)
    async with global_session(app_engine) as s:
        ok = await enqueue(s, "test_record")
        retry = await enqueue(s, "noop", {"fail": "retryable"}, max_attempts=2)
        perm = await enqueue(s, "test_permanent")
    while await worker.run_one():
        pass
    assert ok in RAN
    assert (await _job(worker_engine, ok))["status"] == "succeeded"
    first = await _job(worker_engine, retry)
    assert (first["status"], first["attempts"]) == ("queued", 1)  # backed off, run_after in future
    assert "noop asked to fail" in str(first["last_error"])
    assert (await _job(worker_engine, perm))["status"] == "failed"
    assert (await _job(worker_engine, perm))["attempts"] == 1

    async with worker_engine.begin() as conn:  # fast-forward the backoff
        await conn.execute(text("UPDATE jobs SET run_after = now() WHERE id = :id"), {"id": retry})
    while await worker.run_one():
        pass
    assert (await _job(worker_engine, retry))["status"] == "failed"


async def test_reaper_requeues_expired_leases(
    app_engine: AsyncEngine, worker_engine: AsyncEngine
) -> None:
    async with global_session(app_engine) as s:
        job_id = await enqueue(s, "test_reap")
    async with worker_engine.begin() as conn:
        job = await claim(conn, "dead-worker", ["test_reap"])
        assert job is not None
        assert job.id == job_id
        await conn.execute(
            text("UPDATE jobs SET locked_until = now() - interval '1 second' WHERE id = :id"),
            {"id": job_id},
        )
        assert await reap_expired(conn) >= 1
    row = await _job(worker_engine, job_id)
    assert row["status"] == "queued"
    assert row["locked_by"] is None


def test_noop_handler_registered() -> None:
    assert "noop" in registry_mod.kinds()
