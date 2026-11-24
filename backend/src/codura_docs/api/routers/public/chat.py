"""Public Chat API v1 (specs/api.md §3, ADR-0012). The Channel Key alone decides Tenant + Assistant.

Auth, Origin and per-IP limits: `api/auth/public.py`. Chat adds per-key, per-Tenant and
per-Visitor buckets and concurrency caps so one Tenant can't starve the shared LLM (SEC-06).
Other Tenants' ids return 404, never 403 (SEC-15).
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from starlette.responses import StreamingResponse

from codura_docs.api.auth.public import CHANNEL, ChannelAccess, limit
from codura_docs.api.errors import ApiError
from codura_docs.api.routers.admin.content import replay_from
from codura_docs.api.sse import sse_response
from codura_docs.config import Settings
from codura_docs.db.session import rowcount, tenant_session
from codura_docs.domain.language import localized
from codura_docs.ratelimit import release_after
from codura_docs.services import answering
from codura_docs.services import assistants as assistants_svc
from codura_docs.services.auth import sha256

router = APIRouter(prefix="/api/v1", tags=["public"])
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
UI_LANGUAGES = ["en", "hi", "es", "fr", "de", "ar", "zh"]


def _ui_lang(request: Request) -> str:
    return (request.headers.get("accept-language") or "en")[:2].lower()


async def _assistant(request: Request, c: ChannelAccess) -> dict[str, Any]:
    async with tenant_session(request.app.state.engine, c.ctx) as s:
        a = await assistants_svc.get(s, c.assistant_id)
    if a is None:
        raise ApiError(404, "not_found", "Assistant not found")
    return a


@router.get("/channel/config")
async def channel_config(request: Request, c: ChannelAccess = CHANNEL) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    lang = _ui_lang(request)
    a = await _assistant(request, c)
    return {
        "assistant": {
            "id": a["id"],
            "name": a["name"],
            "default_language": a["default_language"],
            "welcome_text": localized(a["welcome_text"], lang, "Hi! How can I help?"),
        },
        "theme": a["theme"] or {},
        "ui_languages": UI_LANGUAGES,
        "features": {"feedback": True, "citations": True},
        "visitor_email": {
            "mode": a["visitor_email"],
            "consent_text": localized(a["visitor_email_consent"], lang, ""),
            "privacy_url": a["privacy_url"],
        },
        "branding": True,  # "Powered by Codura" until M3 Plans add `remove_branding` (FR-55)
        "limits": {"max_question_chars": settings.chat_max_question_chars},
    }


class ChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = Field(default=None, max_length=64)
    visitor_email: str | None = Field(default=None, max_length=254)
    consent: bool | None = None


@router.post("/chat/stream")
async def chat_stream(
    body: ChatIn, request: Request, c: ChannelAccess = CHANNEL
) -> StreamingResponse:
    settings: Settings = request.app.state.settings
    question = body.question.strip()
    if not question or len(question) > settings.chat_max_question_chars:
        raise ApiError(
            422,
            "validation_error",
            f"Questions must be 1–{settings.chat_max_question_chars} characters",
        )
    limiter = request.app.state.rate_limiter
    limit(limiter, f"key:{c.key_id}", settings.chat_rate_per_key_per_min)
    limit(limiter, f"tenant:{c.ctx.tenant_id}", settings.chat_rate_per_tenant_per_min)
    limit(
        limiter,
        f"visitor:{c.key_id}:{c.client_ip}:{c.visitor_id}",
        settings.chat_rate_per_visitor_per_min,
    )

    email_enc = email_hash = None
    if not body.conversation_id:
        mode = (await _assistant(request, c))["visitor_email"]
        email = (body.visitor_email or "").strip().lower()
        if mode == "required" and not email:
            raise ApiError(422, "visitor_email_required", "Please enter your email to start")
        if email and mode != "off":  # ADR-0023: contact data only, never identity
            if not _EMAIL.match(email):
                raise ApiError(422, "validation_error", "Please enter a valid email address")
            if not body.consent:
                raise ApiError(422, "consent_required", "Please accept the consent text")
            email_enc = request.app.state.keyring.encrypt(
                email, aad=f"visitor_email:{c.ctx.tenant_id}".encode()
            )
            email_hash = sha256(f"{c.ctx.tenant_id}:{email}")

    slots = [
        (f"key:{c.key_id}", settings.chat_max_concurrent_per_key),
        (f"tenant:{c.ctx.tenant_id}", settings.chat_max_concurrent_per_tenant),
    ]
    concurrency = request.app.state.concurrency
    if not concurrency.acquire(slots):
        raise ApiError(
            429,
            "rate_limited",
            "This assistant is busy. Please try again in a moment.",
            headers={"Retry-After": "2"},
        )
    try:
        req = answering.AnswerRequest(
            ctx=c.ctx,
            assistant_id=c.assistant_id,
            question=question,
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
        first = await anext(events)
    except (answering.AssistantNotFound, answering.ConversationNotFound) as exc:
        concurrency.release(slots)
        raise ApiError(404, "not_found", "Conversation not found") from exc
    except BaseException:
        concurrency.release(slots)
        raise
    return sse_response(release_after(replay_from(first, events), concurrency, slots))


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
                        "SELECT id, role, content, language, citations, feedback, finish_reason, "
                        "created_at FROM messages WHERE conversation_id = :id ORDER BY created_at"
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
