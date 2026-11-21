"""Shared HTTP helpers for Ollama: client construction and error mapping (specs/ports.md §6)."""

from __future__ import annotations

import httpx

from codura_docs.ports.errors import (
    ProviderAuthError,
    ProviderBadRequest,
    ProviderError,
    ProviderRateLimited,
    ProviderUnavailable,
)

PROVIDER = "ollama"


def make_client(
    base_url: str, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        timeout=httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=10.0),
        transport=transport,
    )


def map_status(status: int, body: str) -> ProviderError:
    detail = body[:300]
    if status == 404 or "not found" in detail.lower():
        return ProviderUnavailable(PROVIDER, f"model not available (pull it first): {detail}")
    if status in (401, 403):
        return ProviderAuthError(PROVIDER, detail)
    if status == 429:
        return ProviderRateLimited(PROVIDER, detail)
    if 400 <= status < 500:
        return ProviderBadRequest(PROVIDER, detail)
    return ProviderUnavailable(PROVIDER, f"HTTP {status}: {detail}")


def map_transport_error(exc: httpx.HTTPError) -> ProviderError:
    return ProviderUnavailable(PROVIDER, f"{type(exc).__name__}: {exc}")


async def installed_models(client: httpx.AsyncClient) -> set[str]:
    try:
        resp = await client.get("/api/tags", timeout=5.0)
    except httpx.HTTPError as exc:
        raise map_transport_error(exc) from exc
    if resp.status_code != 200:
        raise map_status(resp.status_code, resp.text)
    names: set[str] = set()
    for item in resp.json().get("models", []):
        name = str(item.get("name", ""))
        names.add(name)
        if name.endswith(":latest"):
            names.add(name.removesuffix(":latest"))
    return names


async def require_model(client: httpx.AsyncClient, model: str) -> None:
    if model not in await installed_models(client):
        raise ProviderUnavailable(PROVIDER, f"model {model!r} is not pulled")
