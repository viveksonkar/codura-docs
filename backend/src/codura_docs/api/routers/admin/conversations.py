"""Conversation log viewer (FR-35, FR-16). Current Tenant only (RLS); Tenant Users may read.

Never returns model names, profile ids or COGS (ADR-0019), and never Visitor emails (those are
Tenant Admin only, `visitor_emails.py`, ADR-0023).
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text

from codura_docs.api.deps import TenantAccess, require_tenant
from codura_docs.api.errors import ApiError
from codura_docs.billing.entitlements import for_tenant
from codura_docs.db.session import tenant_session

router = APIRouter(prefix="/api/admin", tags=["conversations"])
READ = Depends(require_tenant("user"))

_MESSAGE_COLUMNS = (
    "id, role, content, language, standalone_query, citations, retrieval, outcome, latency_ms, "
    "first_token_ms, finish_reason, feedback, feedback_note, created_at"
)


def _cursor_encode(ts: datetime, conv_id: str) -> str:
    return base64.urlsafe_b64encode(json.dumps([ts.isoformat(), conv_id]).encode()).decode()


def _cursor_decode(cursor: str) -> tuple[datetime, str]:
    try:
        ts, conv_id = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return datetime.fromisoformat(ts), str(conv_id)
    except (ValueError, TypeError) as exc:
        raise ApiError(422, "validation_error", "Invalid cursor") from exc


@router.get("/conversations")
async def list_conversations(
    request: Request,
    a: TenantAccess = READ,
    assistant_id: str | None = Query(default=None, max_length=64),
    channel: Literal["hosted", "widget", "wordpress", "android", "api", "playground"] | None = None,
    language: str | None = Query(default=None, max_length=8),
    feedback: Literal["up", "down", "any", "none"] | None = None,
    outcome: Literal["answered", "fallback", "limit", "error"] | None = None,
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=300),
) -> dict[str, Any]:
    where = ["TRUE"]
    params: dict[str, Any] = {"limit": limit + 1}
    if assistant_id:
        where.append("c.assistant_id = :assistant")
        params["assistant"] = assistant_id
    if channel:
        where.append("c.channel = :channel")
        params["channel"] = channel
    if language:
        where.append(
            "EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = c.id AND m.language = :lang)"
        )
        params["lang"] = language
    if feedback in ("up", "down"):
        where.append(
            "EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = c.id AND m.feedback = :fb)"
        )
        params["fb"] = 1 if feedback == "up" else -1
    elif feedback == "any":
        where.append(
            "EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = c.id AND m.feedback IS NOT NULL)"
        )
    elif feedback == "none":
        where.append(
            "NOT EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = c.id AND m.feedback IS NOT NULL)"
        )
    if outcome:
        where.append(
            "EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = c.id AND m.outcome = :outcome)"
        )
        params["outcome"] = outcome
    if date_from:
        where.append("c.last_message_at >= :dfrom")
        params["dfrom"] = datetime.combine(date_from, time.min)
    if date_to:
        where.append("c.last_message_at < :dto")
        params["dto"] = datetime.combine(date_to + timedelta(days=1), time.min)
    if cursor:
        ts, conv_id = _cursor_decode(cursor)
        where.append("(c.last_message_at, c.id) < (:cts, :cid)")
        params |= {"cts": ts, "cid": conv_id}
    sql = f"""
        SELECT c.id, c.assistant_id, a.name AS assistant_name, c.channel, c.last_language AS language,
               c.started_at, c.last_message_at, c.message_count,
               (c.visitor_email_hash IS NOT NULL) AS has_visitor_email,
               (SELECT content FROM messages m WHERE m.conversation_id = c.id AND m.role = 'user'
                ORDER BY m.created_at LIMIT 1) AS first_question,
               (SELECT count(*) FROM messages m WHERE m.conversation_id = c.id AND m.feedback = 1) AS thumbs_up,
               (SELECT count(*) FROM messages m WHERE m.conversation_id = c.id AND m.feedback = -1) AS thumbs_down,
               (SELECT count(*) FROM messages m WHERE m.conversation_id = c.id AND m.outcome = 'fallback') AS fallbacks
        FROM conversations c JOIN assistants a ON a.id = c.assistant_id
        WHERE {" AND ".join(where)} AND c.last_message_at >= :retention_from
        ORDER BY c.last_message_at DESC, c.id DESC
        LIMIT :limit"""
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        retention = int((await for_tenant(s, a.tenant_id)).get("analytics_retention_days"))
        params["retention_from"] = datetime.now(UTC) - timedelta(days=retention)
        rows = [dict(r) for r in (await s.execute(text(sql), params)).mappings().all()]
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = _cursor_encode(rows[-1]["last_message_at"], rows[-1]["id"])
    return {"items": rows, "next_cursor": next_cursor}


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str, request: Request, a: TenantAccess = READ
) -> dict[str, Any]:
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        conv = (
            (
                await s.execute(
                    text(
                        "SELECT c.id, c.assistant_id, a.name AS assistant_name, c.channel, "
                        "c.last_language AS language, c.started_at, c.last_message_at, c.message_count, "
                        "(c.visitor_email_hash IS NOT NULL) AS has_visitor_email "
                        "FROM conversations c JOIN assistants a ON a.id = c.assistant_id WHERE c.id = :id"
                    ),
                    {"id": conversation_id},
                )
            )
            .mappings()
            .first()
        )
        if conv is None:
            raise ApiError(404, "not_found", "Conversation not found")
        messages = (
            (
                await s.execute(
                    text(
                        f"SELECT {_MESSAGE_COLUMNS} FROM messages WHERE conversation_id = :id "
                        "ORDER BY created_at"
                    ),
                    {"id": conversation_id},
                )
            )
            .mappings()
            .all()
        )
    return dict(conv) | {"messages": [dict(m) for m in messages]}
