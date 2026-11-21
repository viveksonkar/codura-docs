"""Worker loop: claim → run handler (in the Job's Tenant) → complete / retry (ADR-0006, M0-06)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket
from collections.abc import Awaitable, Callable

from psycopg import AsyncConnection as PgConnection
from sqlalchemy.ext.asyncio import AsyncEngine

import codura_docs.services.ingestion  # noqa: F401 - registers ingest/purge/email Job handlers
from codura_docs.config import Settings
from codura_docs.context import job_id_var, tenant_id_var
from codura_docs.db.engine import make_engine
from codura_docs.db.guards import check_embedding_guard
from codura_docs.jobs import handlers as handler_registry
from codura_docs.jobs.handlers import JobContext
from codura_docs.jobs.queue import (
    NOTIFY_CHANNEL,
    Job,
    NonRetryable,
    claim,
    complete,
    fail,
    reap_expired,
    renew_lease,
)
from codura_docs.registry import Registry

log = logging.getLogger(__name__)
LEASE_RENEW_S = 20


class Worker:
    def __init__(
        self,
        settings: Settings,
        registry: Registry,
        worker_engine: AsyncEngine,
        app_engine: AsyncEngine,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.worker_engine = worker_engine
        self.app_engine = app_engine
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self.stopping = asyncio.Event()
        self._wake = asyncio.Event()

    async def run_one(self) -> bool:
        """Claim and run at most one Job. Returns True if a Job was processed."""
        async with self.worker_engine.begin() as conn:
            job = await claim(conn, self.worker_id, handler_registry.kinds())
        if job is None:
            return False
        await self._execute(job)
        return True

    async def _execute(self, job: Job) -> None:
        handler = handler_registry.get_handler(job.kind)
        jtoken, ttoken = job_id_var.set(job.id), tenant_id_var.set(job.tenant_id)
        renewer = asyncio.create_task(self._renew(job))
        try:
            if handler is None:
                raise NonRetryable(f"no handler for kind {job.kind!r}")
            await handler(JobContext(job, self.app_engine, self.registry))
        except NonRetryable as exc:
            await self._finish_failed(job, str(exc), retryable=False)
        except Exception as exc:
            log.exception("job failed")
            await self._finish_failed(job, f"{type(exc).__name__}: {exc}", retryable=True)
        else:
            async with self.worker_engine.begin() as conn:
                await complete(conn, job.id, self.worker_id)
        finally:
            renewer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await renewer
            job_id_var.reset(jtoken)
            tenant_id_var.reset(ttoken)

    async def _finish_failed(self, job: Job, error: str, *, retryable: bool) -> None:
        async with self.worker_engine.begin() as conn:
            status = await fail(conn, job, self.worker_id, error, retryable=retryable)
        log.warning("job not completed", extra={"job_status": status, "job_kind": job.kind})

    async def _renew(self, job: Job) -> None:
        while True:
            await asyncio.sleep(LEASE_RENEW_S)
            async with self.worker_engine.begin() as conn:
                await renew_lease(conn, job.id, self.worker_id)

    async def _listen(self) -> None:
        """LISTEN/NOTIFY wake-ups; polling remains the fallback if this connection drops."""
        dsn = self.settings.database_worker_url.replace("postgresql+psycopg://", "postgresql://")
        while not self.stopping.is_set():
            try:
                async with await PgConnection.connect(dsn, autocommit=True) as conn:
                    await conn.execute(f"LISTEN {NOTIFY_CHANNEL}")
                    async for _ in conn.notifies():
                        self._wake.set()
            except Exception:
                log.warning("LISTEN connection lost; relying on polling")
                await asyncio.sleep(5)

    async def _slot(self) -> None:
        while not self.stopping.is_set():
            processed = await self.run_one()
            if processed:
                continue
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self.settings.worker_poll_interval_s)

    async def _reaper(self) -> None:
        while not self.stopping.is_set():
            async with self.worker_engine.begin() as conn:
                reaped = await reap_expired(conn)
            if reaped:
                log.warning("re-queued expired jobs", extra={"count": reaped})
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.stopping.wait(), 30)

    async def serve(self) -> None:
        listener = asyncio.create_task(self._listen())
        reaper = asyncio.create_task(self._reaper())
        slots = [asyncio.create_task(self._slot()) for _ in range(self.settings.worker_concurrency)]
        log.info(
            "worker started", extra={"worker_id": self.worker_id, "kinds": handler_registry.kinds()}
        )
        await self.stopping.wait()
        # Graceful stop: let in-flight Jobs finish (their slot loop exits after the current Job).
        await asyncio.gather(*slots, return_exceptions=True)
        for task in (listener, reaper):
            task.cancel()
        await asyncio.gather(listener, reaper, return_exceptions=True)
        log.info("worker stopped")


def _install_signal_handlers(stop: Callable[[], None]) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop)
        except (NotImplementedError, RuntimeError):  # Windows dev shells
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop))


async def run_worker(
    settings: Settings,
    registry: Registry | None = None,
    ready: Callable[[Worker], Awaitable[None]] | None = None,
) -> None:
    settings.validate_for_production()
    registry = registry or Registry.from_settings(settings)
    app_engine = make_engine(settings.database_app_url)
    worker_engine = make_engine(
        settings.database_worker_url, pool_size=settings.worker_concurrency + 2
    )
    try:
        await check_embedding_guard(app_engine, registry.models.embedding)
        worker = Worker(settings, registry, worker_engine, app_engine)
        _install_signal_handlers(worker.stopping.set)
        if ready:
            await ready(worker)
        await worker.serve()
    finally:
        await app_engine.dispose()
        await worker_engine.dispose()
