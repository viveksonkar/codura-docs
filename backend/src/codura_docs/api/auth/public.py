"""Channel Key authentication for the public Chat API (specs/api.md §3.1, ADR-0012, ADR-0025).

The Channel Key alone decides the Tenant and the Assistant (SEC-15). Rules, in order:
 1. `Authorization: Bearer pk_…` only; `sk_`, unknown, revoked or suspended-Tenant keys → 401.
 2. A browser `Origin` must be allowed for the key's channel (SEC-05):
    hosted → the platform's own origin; widget/wordpress → Allowed Origins (empty = none);
    api/android → no browser origin at all. No `Origin` header = not a browser; SEC-06 applies.
 3. Per-IP token bucket on every public route (SEC-06). Chat adds per-key/Tenant/Visitor buckets
    and concurrency caps in the chat route.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncEngine

from codura_docs.api.errors import ApiError
from codura_docs.config import Settings
from codura_docs.context import tenant_id_var
from codura_docs.db.session import global_session
from codura_docs.ratelimit import RateLimiter
from codura_docs.services import channel_keys
from codura_docs.services.channel_keys import ResolvedKey
from codura_docs.tenancy import TenantContext

log = logging.getLogger(__name__)
_VISITOR = re.compile(r"^[A-Za-z0-9-]{8,64}$")
CORS_STATE = "cors_allow_origin"
_TOUCH_EVERY_S = 60.0
_last_touch: dict[str, float] = {}


@dataclass(frozen=True)
class ChannelAccess:
    ctx: TenantContext
    assistant_id: str
    key_id: str
    channel: str
    visitor_id: str
    client_ip: str


def platform_origin(settings: Settings) -> str:
    try:
        return channel_keys.normalize_origin(settings.public_base_url)
    except channel_keys.KeyServiceError:
        return settings.public_base_url.rstrip("/")


def client_ip(request: Request) -> str:
    """Caddy (the only entry point, ADR-0013) overwrites X-Forwarded-For with the real client."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded and request.app.state.settings.trust_forwarded_for:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host if request.client else "unknown"


def origin_allowed(key: ResolvedKey, origin: str | None, settings: Settings) -> bool:
    if origin is None:
        return True
    try:
        normalized = channel_keys.normalize_origin(origin)
    except channel_keys.KeyServiceError:
        return False
    own = platform_origin(settings)
    if key.channel == "hosted":
        return normalized == own
    if key.channel in channel_keys.ORIGIN_CHANNELS:
        if normalized in key.allowed_origins:
            return True
        # The dev-only demo page (/demo/?key=) runs on the platform origin.
        return settings.app_env != "production" and normalized == own
    return False  # api / android keys are not for browsers


def limit(limiter: RateLimiter, bucket: str, per_minute: int) -> None:
    decision = limiter.hit(bucket, per_minute=per_minute)
    if not decision.allowed:
        raise ApiError(
            429,
            "rate_limited",
            "Too many requests. Please slow down.",
            headers={"Retry-After": str(decision.retry_after_s)},
        )


def _touch_later(engine: AsyncEngine, key_id: str) -> None:
    now = time.monotonic()
    if now - _last_touch.get(key_id, 0.0) < _TOUCH_EVERY_S:
        return
    _last_touch[key_id] = now

    async def run() -> None:
        try:
            async with global_session(engine) as s:
                await channel_keys.touch(s, key_id)
        except Exception:  # best effort: last_used_at is informational
            log.warning("could not update key last_used_at", exc_info=True)

    task = asyncio.get_running_loop().create_task(run())
    _pending.add(task)
    task.add_done_callback(_pending.discard)


_pending: set[asyncio.Task[None]] = set()


async def require_channel_key(request: Request) -> ChannelAccess:
    settings: Settings = request.app.state.settings
    ip = client_ip(request)
    limit(request.app.state.rate_limiter, f"ip:{ip}", settings.public_rate_per_ip_per_min)
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not token.startswith("pk_"):
        raise ApiError(401, "unauthenticated", "A Channel Key (pk_…) is required")
    async with global_session(request.app.state.engine) as s:
        key = await channel_keys.resolve_key(s, token)
    if (
        key is None
        or key.kind != "channel"
        or key.revoked
        or key.tenant_status != "active"
        or not key.assistant_id
    ):
        raise ApiError(401, "unauthenticated", "Invalid or revoked Channel Key")
    origin = request.headers.get("origin")
    if not origin_allowed(key, origin, settings):
        raise ApiError(403, "origin_not_allowed", "This site is not allowed to use this key")
    if origin is not None:
        request.state.cors_allow_origin = origin  # read by PublicCorsMiddleware (CORS_STATE)
    visitor = request.headers.get("x-codura-visitor", "")
    if not _VISITOR.match(visitor):
        raise ApiError(
            422, "validation_error", "X-Codura-Visitor header (client-generated id) is required"
        )
    tenant_id_var.set(key.tenant_id)
    _touch_later(request.app.state.engine, key.key_id)
    return ChannelAccess(
        TenantContext(key.tenant_id),
        key.assistant_id,
        key.key_id,
        key.channel or "api",
        visitor,
        ip,
    )


CHANNEL = Depends(require_channel_key)
