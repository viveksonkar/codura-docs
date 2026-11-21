"""API against a real database: readiness and the debug Job round trip (M0 exit)."""

from __future__ import annotations

import httpx
import pytest

from codura_docs.api.app import create_app
from codura_docs.config import Settings
from codura_docs.registry import Registry

pytestmark = pytest.mark.integration


async def test_readyz_green_with_fakes_and_debug_enqueue(pg_database: Settings) -> None:
    app = create_app(pg_database, registry=Registry.from_settings(pg_database))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            ready = await client.get("/readyz")
            assert ready.status_code == 200, ready.json()
            assert set(ready.json()["checks"].values()) == {"ok"}
            enq = await client.post("/api/admin/debug/jobs/noop")
            assert enq.status_code == 202
            status = await client.get(f"/api/admin/debug/jobs/{enq.json()['job_id']}")
            assert status.json()["status"] in {"queued", "running", "succeeded"}
    await app.state.debug_worker_engine.dispose()
