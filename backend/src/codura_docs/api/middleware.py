"""Pure-ASGI middleware: request IDs (NFR-09) and HTTP metrics (FR-74). Streaming-safe (SSE)."""

from __future__ import annotations

import re
import time
import uuid

from prometheus_client import Counter, Histogram
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from codura_docs.context import request_id_var

_VALID_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

HTTP_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration (time to response start)",
    ["route", "method", "status_class"],
)
HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["route", "method", "status_class"])


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope.get("headers") or []).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _VALID_ID.match(incoming) else f"req_{uuid.uuid4().hex}"
        token = request_id_var.set(request_id)

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            request_id_var.reset(token)


class MetricsMiddleware:
    """Labels use the route template (low cardinality), never raw paths or tenant ids."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()
        status = 500

        async def capture(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
                route = scope.get("route")
                template = getattr(route, "path", "unmatched")
                labels = (template, scope.get("method", ""), f"{status // 100}xx")
                HTTP_DURATION.labels(*labels).observe(time.perf_counter() - start)
                HTTP_REQUESTS.labels(*labels).inc()
            await send(message)

        await self.app(scope, receive, capture)
