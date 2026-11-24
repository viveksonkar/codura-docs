"""Public Chat API v1 (specs/api.md §3, ADR-0012). The Channel Key alone decides Tenant + Assistant.

M2 adds full Origin/CORS hardening and rate limits; M1 enforces key type, revocation, Tenant status,
Allowed Origins when configured, and returns 404 (never 403) for other Tenants' ids (SEC-15).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from starlette.responses import StreamingResponse

from codura_docs.api.errors import ApiError
from codura_docs.api.routers.admin.content import replay_from
from codura_docs.api.sse import sse_response
from codura_docs.db.session import global_session, rowcount, tenant_session
from codura_docs.domain.language import localized
from codura_docs.services import answering
from codura_docs.services import assistants as assistants_svc
from codura_docs.services.auth import sha256
from codura_docs.services.channel_keys import resolve_key
from codura_docs.tenancy import TenantContext

router = APIRouter(prefix="/api/v1", tags=["public"])
_VISITOR = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class ChannelAccess:
    ctx: TenantContext
    assistant_id: str
    key_id: str
    channel: str
    visitor_id: str


async def channel_access(request: Request) -> ChannelAccess:
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not token.startswith("pk_"):
        raise ApiError(401, "unauthenticated", "A Channel Key (pk_…) is required")
    async with global_session(request.app.state.engine) as s:
        key = await resolve_key(s, token)
    if (
        key is None
        or key.kind != "channel"
        or key.revoked
        or key.tenant_status != "active"
        or not key.assistant_id
    ):
        raise ApiError(401, "unauthenticated", "Invalid or revoked Channel Key")
    origin = request.headers.get("origin")
    if (
        key.channel in ("widget", "wordpress")
        and key.allowed_origins
        and origin not in key.allowed_origins
    ):
        raise ApiError(403, "origin_not_allowed", "This site is not allowed to use this key")
    visitor = request.headers.get("x-codura-visitor", "")
    if not _VISITOR.match(visitor):
        raise ApiError(
            422, "validation_error", "X-Codura-Visitor header (client-generated id) is required"
        )
    return ChannelAccess(
        TenantContext(key.tenant_id), key.assistant_id, key.key_id, key.channel or "api", visitor
    )


CHANNEL = Depends(channel_access)


@router.get("/channel/config")
async def channel_config(request: Request, c: ChannelAccess = CHANNEL) -> dict[str, Any]:
    lang = (request.headers.get("accept-language") or "en")[:2].lower()
    async with tenant_session(request.app.state.engine, c.ctx) as s:
        a = await assistants_svc.get(s, c.assistant_id)
    if a is None:
        raise ApiError(404, "not_found", "Assistant not found")
    return {
        "assistant": {
            "id": a["id"],
            "name": a["name"],
            "default_language": a["default_language"],
            "welcome_text": localized(a["welcome_text"], lang, "Hi! How can I help?"),
        },
        "theme": a["theme"] or {},
        "ui_languages": ["en", "hi", "es", "fr", "de", "ar", "zh"],
        "features": {"feedback": True, "citations": True},
        "visitor_email": {
            "mode": a["visitor_email"],
            "consent_text": localized(a["visitor_email_consent"], lang, ""),
            "privacy_url": a["privacy_url"],
        },
        "branding": True,
        "limits": {"max_question_chars": 2000},
    }


class ChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=64)
    visitor_email: str | None = Field(default=None, max_length=254)
    consent: bool | None = None


@router.post("/chat/stream")
async def chat_stream(
    body: ChatIn, request: Request, c: ChannelAccess = CHANNEL
) -> StreamingResponse:
    email_enc = email_hash = None
    if body.visitor_email and not body.conversation_id:
        email = body.visitor_email.strip().lower()
        if not _EMAIL.match(email) or not body.consent:
            raise ApiError(422, "validation_error", "A valid email and consent are required")
        email_enc = request.app.state.keyring.encrypt(
            email, aad=f"visitor_email:{c.ctx.tenant_id}".encode()
        )
        email_hash = sha256(f"{c.ctx.tenant_id}:{email}")
    req = answering.AnswerRequest(
        ctx=c.ctx,
        assistant_id=c.assistant_id,
        question=body.question.strip(),
        channel=c.channel,
        conversation_id=body.conversation_id,
        visitor_id=c.visitor_id,
        channel_key_id=c.key_id,
        visitor_email_enc=email_enc,
        visitor_email_hash=email_hash,
    )
    events = answering.answer(
        req,
        engine=request.app.state.engine,
        registry=request.app.state.registry,
        keyring=request.app.state.keyring,
    )
    try:
        first = await anext(events)
    except (answering.AssistantNotFound, answering.ConversationNotFound) as exc:
        raise ApiError(404, "not_found", "Conversation not found") from exc
    return sse_response(replay_from(first, events))


@router.get("/conversations/{conversation_id}/messages")
async def history(
    conversation_id: str, request: Request, c: ChannelAccess = CHANNEL
) -> dict[str, Any]:
    async with tenant_session(request.app.state.engine, c.ctx) as s:
        owner = (
            await s.execute(
                text(
                    "SELECT 1 FROM conversations WHERE id = :id AND assistant_id = :a "
                    "AND visitor_id = :v"
                ),
                {"id": conversation_id, "a": c.assistant_id, "v": c.visitor_id},
            )
        ).first()
        if owner is None:
            raise ApiError(404, "not_found", "Conversation not found")
        rows = (
            (
                await s.execute(
                    text(
                        "SELECT id, role, content, language, citations, feedback, created_at "
                        "FROM messages WHERE conversation_id = :id ORDER BY created_at"
                    ),
                    {"id": conversation_id},
                )
            )
            .mappings()
            .all()
        )
    items = []
    for r in rows:
        item = dict(r)
        item["citations"] = [
            {k: v for k, v in cit.items() if k != "chunk_id"} for cit in (r["citations"] or [])
        ]
        items.append(item)
    return {"items": items}


class FeedbackIn(BaseModel):
    value: int = Field(ge=-1, le=1)
    note: str | None = Field(default=None, max_length=500)


@router.post("/messages/{message_id}/feedback", status_code=204)
async def feedback(
    message_id: str, body: FeedbackIn, request: Request, c: ChannelAccess = CHANNEL
) -> None:
    if body.value == 0:
        raise ApiError(422, "validation_error", "value must be 1 or -1")
    async with tenant_session(request.app.state.engine, c.ctx) as s:
        result = await s.execute(
            text(
                "UPDATE messages m SET feedback = :v, feedback_note = :n FROM conversations c "
                "WHERE m.id = :id AND m.role = 'assistant' AND c.id = m.conversation_id "
                "AND c.visitor_id = :vis AND c.assistant_id = :a"
            ),
            {
                "v": body.value,
                "n": body.note,
                "id": message_id,
                "vis": c.visitor_id,
                "a": c.assistant_id,
            },
        )
    if not rowcount(result):
        raise ApiError(404, "not_found", "Message not found")
