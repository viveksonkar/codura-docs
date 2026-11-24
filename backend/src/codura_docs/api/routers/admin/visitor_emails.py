"""Captured Visitor emails: list, CSV export, delete on request (FR-54, SEC-19, ADR-0023).

Tenant Admin only. Emails are decrypted here and nowhere else; they are never an identity.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text
from starlette.responses import Response

from codura_docs.api.deps import TenantAccess, require_tenant
from codura_docs.api.errors import ApiError
from codura_docs.crypto import DecryptionError, Keyring
from codura_docs.db.session import rowcount, tenant_session
from codura_docs.services.auth import sha256

router = APIRouter(prefix="/api/admin", tags=["visitor-emails"])
ADMIN = Depends(require_tenant("admin"))


async def _rows(request: Request, a: TenantAccess, limit: int) -> list[dict[str, Any]]:
    keyring: Keyring = request.app.state.keyring
    aad = f"visitor_email:{a.tenant_id}".encode()
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        rows = (
            await s.execute(
                text(
                    "SELECT c.id, c.visitor_email_enc, c.visitor_email_consent_at, c.channel, "
                    "c.started_at, a.name AS assistant_name FROM conversations c "
                    "JOIN assistants a ON a.id = c.assistant_id "
                    "WHERE c.visitor_email_enc IS NOT NULL ORDER BY c.started_at DESC LIMIT :l"
                ),
                {"l": limit},
            )
        ).all()
    out = []
    for r in rows:
        try:
            email = keyring.decrypt_str(bytes(r.visitor_email_enc), aad=aad)
        except DecryptionError:
            continue
        out.append(
            {
                "conversation_id": r.id,
                "email": email,
                "consent_at": r.visitor_email_consent_at,
                "channel": r.channel,
                "assistant_name": r.assistant_name,
                "started_at": r.started_at,
            }
        )
    return out


@router.get("/visitor-emails")
async def list_emails(
    request: Request, a: TenantAccess = ADMIN, limit: int = Query(default=500, ge=1, le=5000)
) -> dict[str, Any]:
    return {"items": await _rows(request, a, limit)}


@router.get("/visitor-emails/export")
async def export_emails(request: Request, a: TenantAccess = ADMIN) -> Response:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["email", "consent_at", "channel", "assistant", "conversation_id", "started_at"]
    )
    for r in await _rows(request, a, 100_000):
        email = r["email"]
        if email[:1] in ("=", "+", "-", "@"):  # no spreadsheet formula injection
            email = "'" + email
        writer.writerow(
            [
                email,
                r["consent_at"],
                r["channel"],
                r["assistant_name"],
                r["conversation_id"],
                r["started_at"],
            ]
        )
    return Response(
        buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="visitor-emails.csv"'},
    )


@router.delete("/visitor-emails")
async def delete_email(
    request: Request, a: TenantAccess = ADMIN, email: str = Query(min_length=3, max_length=254)
) -> dict[str, int]:
    """Erase one person's email from every Conversation of this Tenant (DSAR, ADR-0023)."""
    digest = sha256(f"{a.tenant_id}:{email.strip().lower()}")
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        result = await s.execute(
            text(
                "UPDATE conversations SET visitor_email_enc = NULL, visitor_email_hash = NULL, "
                "visitor_email_consent_at = NULL WHERE visitor_email_hash = :h"
            ),
            {"h": digest},
        )
    deleted = rowcount(result)
    if not deleted:
        raise ApiError(404, "not_found", "No conversations with that email")
    return {"deleted": deleted}
