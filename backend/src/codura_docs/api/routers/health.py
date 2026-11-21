"""Liveness, readiness and Prometheus metrics endpoints (architecture.md §6)."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter(tags=["health"])


@router.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", include_in_schema=False)
async def readyz(request: Request) -> JSONResponse:
    ok, checks = await request.app.state.readiness.run()
    return JSONResponse({"status": "ok" if ok else "fail", "checks": checks}, 200 if ok else 503)


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    # Not exposed through Caddy; scraped on the internal network only (ADR-0022).
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
