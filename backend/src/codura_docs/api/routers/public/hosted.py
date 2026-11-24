"""Hosted Chat Page bootstrap (specs/api.md §3.2a, FR-46, ADR-0025). No auth, rate-limited per IP.

Disabled, unknown and suspended give the same 404 body, so slugs can't be enumerated (SEC-15).
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import text

from codura_docs.api.auth.public import client_ip, limit
from codura_docs.api.errors import ApiError
from codura_docs.crypto import DecryptionError
from codura_docs.db.session import global_session
from codura_docs.services.channel_keys import hosted_aad

router = APIRouter(prefix="/api/v1", tags=["public"])
_SLUG = re.compile(r"^[A-Za-z0-9-]{1,63}$")


def _not_found() -> ApiError:
    return ApiError(404, "not_found", "This chat page does not exist")


@router.get("/hosted/{tenant_slug}/{assistant_slug}")
async def hosted(tenant_slug: str, assistant_slug: str, request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    limit(
        request.app.state.rate_limiter,
        f"hosted:{client_ip(request)}",
        settings.hosted_lookup_per_ip_per_min,
    )
    if not (_SLUG.match(tenant_slug) and _SLUG.match(assistant_slug)):
        raise _not_found()
    async with global_session(request.app.state.engine) as s:
        row = (
            await s.execute(
                text("SELECT * FROM resolve_hosted(:t, :a)"),
                {"t": tenant_slug, "a": assistant_slug},
            )
        ).first()
    if row is None:
        raise _not_found()
    try:
        key = request.app.state.keyring.decrypt_str(
            row.public_key_enc, aad=hosted_aad(row.tenant_id)
        )
    except DecryptionError as exc:
        raise _not_found() from exc
    return {"channel_key": key, "api": settings.public_base_url.rstrip("/")}
