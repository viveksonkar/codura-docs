"""API skeleton without a database: envelope, request ids, health, readiness failure reporting."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from codura_docs.api.app import create_app
from codura_docs.api.errors import ApiError
from codura_docs.config import Settings
from codura_docs.registry import Registry


@pytest.fixture
def app(settings: Settings, registry: Registry) -> FastAPI:
    # Points at a database that does not exist; nothing connects unless a check runs.
    settings = settings.model_copy(
        update={"database_app_url": "postgresql+psycopg://nobody:x@127.0.0.1:1/none"}
    )
    application = create_app(settings, registry=registry, startup_guard=False)

    @application.get("/boom")
    async def boom() -> None:
        raise ApiError(
            409, "duplicate_document", "Already uploaded", details={"document_id": "doc_1"}
        )

    @application.get("/crash")
    async def crash() -> None:
        raise RuntimeError("secret internals")

    return application


async def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t"
    )


async def test_healthz_and_request_id(app: FastAPI) -> None:
    async with await _client(app) as client:
        r = await client.get("/healthz", headers={"X-Request-ID": "abc12345-xyz"})
        assert r.status_code == 200
        assert r.headers["x-request-id"] == "abc12345-xyz"
        generated = await client.get("/healthz", headers={"X-Request-ID": "bad id!"})
        assert generated.headers["x-request-id"].startswith("req_")


async def test_error_envelope(app: FastAPI) -> None:
    async with await _client(app) as client:
        r = await client.get("/boom")
        assert r.status_code == 409
        body = r.json()["error"]
        assert body["code"] == "duplicate_document"
        assert body["details"] == {"document_id": "doc_1"}
        assert body["request_id"] == r.headers["x-request-id"]
        nf = await client.get("/nope")
        assert nf.status_code == 404
        assert nf.json()["error"]["code"] == "not_found"
        crash = await client.get("/crash")
        assert crash.status_code == 500
        assert "secret" not in crash.text


async def test_readyz_reports_failures(app: FastAPI) -> None:
    async with await _client(app) as client:
        r = await client.get("/readyz")
    assert r.status_code == 503
    checks = r.json()["checks"]
    assert checks["database"].startswith("fail")
    assert checks["embedding_model"] == "ok"  # fake adapter
    assert checks["chat_model"] == "ok"


async def test_metrics_endpoint(app: FastAPI) -> None:
    async with await _client(app) as client:
        await client.get("/healthz")
        r = await client.get("/metrics")
    assert r.status_code == 200
    assert 'http_requests_total{method="GET",route="/healthz",status_class="2xx"}' in r.text
