"""Per-key dynamic CORS for the public Chat API (SEC-05, ADR-0025). Never `*`, never credentials.

- Preflight `OPTIONS /api/v1/*` carries no Authorization header, so it is answered for any origin.
  It grants nothing: the real request is still checked against the key.
- On the real request, `Access-Control-Allow-Origin` is added only when `require_channel_key`
  accepted the Origin for that key (it records it in the request state).
- Admin and platform APIs are same-origin only: they never get CORS headers.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from codura_docs.api.auth.public import CORS_STATE

PUBLIC_PREFIX = "/api/v1/"
ALLOW_HEADERS = (
    "authorization, content-type, accept, accept-language, x-codura-visitor, x-codura-client"
)
EXPOSE_HEADERS = "retry-after, x-request-id"


class PublicCorsMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(PUBLIC_PREFIX):
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        origin = headers.get(b"origin")
        if scope["method"] == "OPTIONS" and origin and b"access-control-request-method" in headers:
            await send(
                {
                    "type": "http.response.start",
                    "status": 204,
                    "headers": [
                        (b"access-control-allow-origin", origin),
                        (b"access-control-allow-methods", b"GET, POST"),
                        (b"access-control-allow-headers", ALLOW_HEADERS.encode()),
                        (b"access-control-max-age", b"600"),
                        (b"vary", b"Origin"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": b""})
            return

        async def send_with_cors(message: Message) -> None:
            if message["type"] == "http.response.start":
                out = message.setdefault("headers", [])
                out.append((b"vary", b"Origin"))
                allowed = (scope.get("state") or {}).get(CORS_STATE)
                if allowed is not None and origin is not None and allowed.encode() == origin:
                    out.append((b"access-control-allow-origin", origin))
                    out.append((b"access-control-expose-headers", EXPOSE_HEADERS.encode()))
            await send(message)

        await self.app(scope, receive, send_with_cors)
