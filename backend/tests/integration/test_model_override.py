"""M1-14: a Platform Admin override to an `openai_compat` profile (mock server) switches a Tenant's
answers with no code change (ADR-0004, ADR-0019). Other Tenants keep the Plan default."""

from __future__ import annotations

import json
import socket
import threading
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import uvicorn
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from tests.integration.test_m1_flows import client_for, drain, signup_and_login, sse_events

pytestmark = pytest.mark.integration

MOCK_ANSWER = "MOCK-OAI: the warranty lasts 24 months [1]."
received: list[dict[str, Any]] = []


async def completions(request: Request) -> StreamingResponse:
    body = await request.json()
    received.append(body)

    async def events() -> AsyncIterator[bytes]:
        for piece in (MOCK_ANSWER[:10], MOCK_ANSWER[10:]):
            chunk = {"model": body["model"], "choices": [{"delta": {"content": piece}}]}
            yield f"data: {json.dumps(chunk)}\n\n".encode()
        usage = {
            "model": body["model"],
            "choices": [],
            "usage": {"prompt_tokens": 50, "completion_tokens": 12},
        }
        yield f"data: {json.dumps(usage)}\n\n".encode()
        yield b"data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


async def models(_: Request) -> JSONResponse:
    return JSONResponse({"data": [{"id": "mock-model"}]})


@pytest.fixture(scope="module")
def mock_openai() -> Iterator[str]:
    app = Starlette(
        routes=[
            Route("/v1/chat/completions", completions, methods=["POST"]),
            Route("/v1/models", models),
        ]
    )
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/v1"
    server.should_exit = True
    thread.join(timeout=5)


async def test_platform_override_switches_provider_without_code_change(
    stack: dict[str, Any], owner_engine: AsyncEngine, mock_openai: str
) -> None:
    app, worker = stack["app"], stack["worker"]
    run = uuid.uuid4().hex[:6]
    a = await signup_and_login(stack, f"Override A {run}", f"a-{run}@override.test")
    b = await signup_and_login(stack, f"Override B {run}", f"b-{run}@override.test")
    aids = {}
    for org in (a, b):
        kb = (await org.client.post("/api/admin/knowledge-bases", json={"name": "Docs"})).json()[
            "id"
        ]
        await org.client.post(
            f"/api/admin/knowledge-bases/{kb}/documents",
            files=[
                (
                    "file",
                    ("w.md", b"# Warranty\n\nThe X200 warranty lasts 24 months.", "text/markdown"),
                )
            ],
        )
        aids[org.tenant_id] = (
            await org.client.post(
                "/api/admin/assistants",
                json={"name": "Help", "knowledge_base_ids": [kb], "retrieval": {"min_score": 0.0}},
            )
        ).json()["id"]
    await drain(worker)

    async def ask(org: Any) -> str:
        r = await org.client.post(
            f"/api/admin/assistants/{aids[org.tenant_id]}/playground/stream",
            json={"question": "Warranty?"},
        )
        return "".join(d["text"] for n, d in sse_events(r.text) if n == "delta")

    assert "MOCK-OAI" not in await ask(a)  # Plan default (fake model)

    profile_id = f"mock-oai-{run}"
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO model_profiles (id, provider, model, base_url, license, is_local, "
                "price_in_per_mtok_micros, price_out_per_mtok_micros) VALUES "
                "(:id, 'openai_compat', 'mock-model', :url, 'apache-2.0', true, 1000000, 2000000)"
            ),
            {"id": profile_id, "url": mock_openai},
        )

    plat = client_for(app)
    login = await plat.post(
        "/api/admin/auth/login", json={"email": "platform@local.test", "password": "admin"}
    )
    plat.headers["x-csrf-token"] = login.json()["csrf_token"]
    r = await plat.patch(
        f"/api/platform/tenants/{a.tenant_id}", json={"model_profile_override": profile_id}
    )
    assert r.status_code == 200, r.text

    answer = await ask(a)
    assert MOCK_ANSWER in answer
    assert received and received[-1]["model"] == "mock-model" and received[-1]["stream"] is True
    assert "MOCK-OAI" not in await ask(b)  # B is untouched

    async with owner_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT m.model_profile_id, m.prompt_tokens, m.completion_tokens, m.cogs_micros FROM messages m "
                    "WHERE m.tenant_id = :t AND m.role = 'assistant' ORDER BY m.created_at DESC LIMIT 1"
                ),
                {"t": a.tenant_id},
            )
        ).one()
    assert row.model_profile_id == profile_id
    assert (row.prompt_tokens, row.completion_tokens) == (50, 12)
    assert row.cogs_micros > 0  # metered with the profile's prices (ADR-0021)
    # The Tenant never sees which model answered (ADR-0019).
    usage = (await a.client.get("/api/admin/usage")).text
    assert "mock" not in usage and "openai" not in usage
