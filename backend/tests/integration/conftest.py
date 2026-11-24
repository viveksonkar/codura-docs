"""Integration fixtures shared by every flow test."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from codura_docs.config import Settings
from codura_docs.registry import Registry
from codura_docs.services import auth as auth_svc
from codura_docs.services import signup as signup_svc


@pytest.fixture(autouse=True)
def _reset_process_throttles() -> None:
    """Login/signup throttles are per process; each test starts from a clean window."""
    for throttle in (
        auth_svc.LOGIN_THROTTLE,
        signup_svc.SIGNUP_THROTTLE,
        signup_svc.RESEND_THROTTLE,
    ):
        throttle._hits.clear()


@pytest.fixture
async def stack(pg_database: Settings) -> AsyncIterator[dict[str, Any]]:
    """The API app (fake models) plus a worker that runs queued Jobs on demand (`drain`)."""
    from codura_docs.api.app import create_app
    from codura_docs.worker.runner import Worker

    registry = Registry.from_settings(pg_database)
    app = create_app(pg_database, registry=registry)
    worker = Worker(pg_database, registry, app.state.debug_worker_engine, app.state.engine)
    async with app.router.lifespan_context(app):
        yield {"app": app, "registry": registry, "worker": worker}
    await app.state.platform_engine.dispose()
    await app.state.debug_worker_engine.dispose()
