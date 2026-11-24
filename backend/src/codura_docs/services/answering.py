"""Answer a question as a stream of domain events, independent of HTTP (rag-pipeline.md §3, M1-07).

Order of operations per question:
 1. persist the user Message, detect the Answer Language, check quota, resolve the Model Profile
 2. over quota      → limit-reached text (no LLM call)
 3. rewrite follow-ups into a standalone query; hybrid Retrieval within the Tenant
 4. nothing relevant → fallback text (no LLM call, not billable)
 5. otherwise stream from the platform-chosen ChatModel, grounded and in the Answer Language
 6. persist the assistant Message + `answer` Usage Event with real tokens and COGS (same transaction)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from codura_docs import metering
from codura_docs.billing.entitlements import for_tenant
from codura_docs.crypto import Keyring
from codura_docs.db.session import tenant_session
from codura_docs.domain.language import answer_language, language_name, localized
from codura_docs.domain.prompting import (
    HistoryTurn,
    build_messages,
    citations_for,
    rewrite_messages,
)
from codura_docs.domain.retrieval import RetrievalSettings
from codura_docs.ids import new_id
from codura_docs.metering import quotas
from codura_docs.ports.chat import ChatDelta, ChatDone, ChatParams, ModelProfile
from codura_docs.ports.errors import ProviderError
from codura_docs.registry import Registry
from codura_docs.services import assistants as assistants_svc
from codura_docs.services.model_resolution import NoModelProfile, resolve
from codura_docs.services.retrieval import retrieve
from codura_docs.tenancy import TenantContext

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Meta:
    conversation_id: str
    message_id: str
    user_message_id: str
    language: str


@dataclass(frozen=True)
class Delta:
    text: str


@dataclass(frozen=True)
class Citations:
    items: list[dict[str, Any]]


@dataclass(frozen=True)
class Done:
    finish_reason: str
    latency_ms: int


@dataclass(frozen=True)
class Failed:
    code: str
    message: str


@dataclass(frozen=True)
class Debug:
    standalone_query: str
    retrieval: list[dict[str, Any]]
    model_profile: str


AnswerEvent = Meta | Delta | Citations | Done | Failed | Debug


class ConversationNotFound(LookupError):
    pass


class AssistantNotFound(LookupError):
    pass


@dataclass
class AnswerRequest:
    ctx: TenantContext
    assistant_id: str
    question: str
    channel: str
    conversation_id: str | None = None
    visitor_id: str | None = None
    user_id: str | None = None
    channel_key_id: str | None = None
    visitor_email_enc: bytes | None = None
    visitor_email_hash: str | None = None
    debug: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


async def answer(
    req: AnswerRequest, *, engine: AsyncEngine, registry: Registry, keyring: Keyring
) -> AsyncIterator[AnswerEvent]:
    started = time.perf_counter()
    tid = req.ctx.tenant_id
    detector = registry.language()

    # --- 1. bookkeeping, language, quota, model -------------------------------------------------
    async with tenant_session(engine, req.ctx) as s:
        assistant = await assistants_svc.get(s, req.assistant_id)
        if assistant is None:
            raise AssistantNotFound(req.assistant_id)
        if req.conversation_id:
            conv = (
                await s.execute(
                    text(
                        "SELECT id, last_language, visitor_id, user_id FROM conversations "
                        "WHERE id = :id AND assistant_id = :a"
                    ),
                    {"id": req.conversation_id, "a": req.assistant_id},
                )
            ).first()
            same_owner = conv is not None and (
                (req.visitor_id and conv.visitor_id == req.visitor_id)
                or (req.user_id and conv.user_id == req.user_id)
            )
            if not same_owner:
                raise ConversationNotFound(req.conversation_id)
            assert conv is not None
            conversation_id, last_language = conv.id, conv.last_language
        else:
            conversation_id, last_language = new_id("conv"), None
            await s.execute(
                text(
                    "INSERT INTO conversations (id, tenant_id, assistant_id, channel, channel_key_id, visitor_id, "
                    "user_id, visitor_email_enc, visitor_email_hash, visitor_email_consent_at) VALUES "
                    "(:id, :t, :a, :c, :k, :v, :u, :ee, :eh, :ca)"
                ),
                {
                    "id": conversation_id,
                    "t": tid,
                    "a": req.assistant_id,
                    "c": req.channel,
                    "k": req.channel_key_id,
                    "v": req.visitor_id,
                    "u": req.user_id,
                    "ee": req.visitor_email_enc,
                    "eh": req.visitor_email_hash,
                    "ca": datetime.now(UTC) if req.visitor_email_hash else None,
                },
            )
        history_rows = (
            await s.execute(
                text(
                    "SELECT role, content FROM messages WHERE conversation_id = :c AND outcome IS DISTINCT FROM 'error' "
                    "ORDER BY created_at DESC LIMIT 6"
                ),
                {"c": conversation_id},
            )
        ).all()
        history = [HistoryTurn(r.role, r.content) for r in reversed(history_rows)]
        language = answer_language(
            req.question,
            detector.detect(req.question),
            conversation_last=last_language,
            assistant_default=assistant["default_language"],
        )
        user_msg_id, asst_msg_id = new_id("msg"), new_id("msg")
        await s.execute(
            text(
                "INSERT INTO messages (id, tenant_id, conversation_id, role, content, language) "
                "VALUES (:id, :t, :c, 'user', :q, :l)"
            ),
            {"id": user_msg_id, "t": tid, "c": conversation_id, "q": req.question, "l": language},
        )
        # Plan, model and quota BEFORE any LLM call (ADR-0021, specs/billing.md sections 4-5).
        terms = await for_tenant(s, tid)
        profile: ModelProfile | None = None
        meter: metering.Meter = "answer"
        admission: quotas.Admission | None = None
        blocked: str | None = "unavailable" if terms.past_due else None
        if blocked is None:
            try:
                resolved = await resolve(s, tid, keyring, terms)
                profile = resolved.profile
                meter = "answer_custom_llm" if resolved.custom_llm else "answer"
            except NoModelProfile:
                blocked = "error"
        if blocked is None:
            admission = await quotas.admit(s, tid, meter, terms=terms)
            if not admission.allowed:
                blocked = "limit"
        custom_infra = (
            await metering.cost_rate(s, "custom_llm_infra_per_answer")
            if meter == "answer_custom_llm"
            else 0
        )
    reservation_id = admission.reservation_id if admission else None
    yield Meta(conversation_id, asst_msg_id, user_msg_id, language)

    async def persist(
        content: str,
        outcome: str,
        finish: str,
        *,
        citations: list[dict[str, Any]] | None = None,
        standalone: str | None = None,
        retrieval_dbg: list[dict[str, Any]] | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        first_token_ms: int | None = None,
        cogs: int = 0,
    ) -> None:
        latency = int((time.perf_counter() - started) * 1000)
        async with tenant_session(engine, req.ctx) as s2:
            await s2.execute(
                text(
                    "INSERT INTO messages (id, tenant_id, conversation_id, role, content, language, standalone_query, "
                    "citations, retrieval, model_profile_id, model, prompt_tokens, completion_tokens, cogs_micros, "
                    "outcome, latency_ms, first_token_ms, finish_reason) VALUES (:id, :t, :c, 'assistant', :content, "
                    ":l, :sq, CAST(:cit AS jsonb), CAST(:ret AS jsonb), :mp, :m, :pt, :ct, :cogs, :o, :lat, :ft, :fr)"
                ),
                {
                    "id": asst_msg_id,
                    "t": tid,
                    "c": conversation_id,
                    "content": content,
                    "l": language,
                    "sq": standalone,
                    "cit": json.dumps(citations or []),
                    "ret": json.dumps(retrieval_dbg or []),
                    "mp": profile.id if profile and outcome == "answered" else None,
                    "m": profile.model if profile and outcome == "answered" else None,
                    "pt": prompt_tokens,
                    "ct": completion_tokens,
                    "cogs": cogs,
                    "o": outcome,
                    "lat": latency,
                    "ft": first_token_ms,
                    "fr": finish,
                },
            )
            await s2.execute(
                text(
                    "UPDATE conversations SET last_language = :l, last_message_at = now(), "
                    "message_count = message_count + 2 WHERE id = :c"
                ),
                {"l": language, "c": conversation_id},
            )
            if outcome == "answered":  # fallback / limit / error answers make no billable LLM call
                await metering.record(
                    s2,
                    tid,
                    meter,
                    1,
                    # Custom LLM: the Tenant pays its provider; we carry the infra share only.
                    cogs_micros=custom_infra if meter == "answer_custom_llm" else cogs,
                    idempotency_key=asst_msg_id,
                    model_profile_id=profile.id if profile else None,
                    attrs={
                        "channel": req.channel,
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "overage": bool(admission and admission.reason == "overage"),
                    },
                    reservation_id=reservation_id,
                )
            else:
                await quotas.release(s2, reservation_id)

    # --- 2. over quota / past due / no model --------------------------------------------------------
    if blocked is not None or profile is None:
        reason = blocked or "error"
        defaults = (
            assistants_svc.DEFAULT_UNAVAILABLE
            if reason == "unavailable"
            else assistants_svc.DEFAULT_LIMIT
        )
        text_out = localized(
            {} if reason == "unavailable" else assistant["limit_reached_text"],
            language,
            defaults.get(language, defaults["default"]),
        )
        await persist(text_out, "limit" if reason == "limit" else "error", reason)
        yield Delta(text_out)
        yield Citations([])
        yield Done(reason, int((time.perf_counter() - started) * 1000))
        return

    chat = registry.chat(profile)
    # Worst-case cost per answer is bounded by the Plan (NFR-16).
    max_out = min(profile.max_output_tokens, int(terms.get("max_output_tokens")))
    cogs_total = 0

    # --- 3. standalone query + retrieval ----------------------------------------------------------
    standalone = req.question
    try:
        if history:
            rewritten, rdone = await chat.complete(
                rewrite_messages(history, req.question),
                ChatParams(temperature=0.0, max_output_tokens=120),
            )
            standalone = rewritten.strip().strip('"') or req.question
            cogs_total += metering.answer_cogs(
                profile, rdone.usage.prompt_tokens, rdone.usage.completion_tokens
            )
        rsettings = RetrievalSettings.from_dict(assistant["retrieval"] or {})
        budget = min(
            rsettings.context_token_budget,
            int(terms.get("context_token_budget")),
            max(200, int(terms.get("max_input_tokens")) - 1000),  # room for prompt + history
        )
        rsettings = replace(rsettings, context_token_budget=budget)
        async with tenant_session(engine, req.ctx) as s3:
            pack = await retrieve(
                s3,
                tenant_id=tid,
                kb_ids=list(assistant["knowledge_base_ids"] or []),
                query=standalone,
                embedding=registry.embedding(),
                settings=rsettings,
            )
    except ProviderError as exc:
        log.warning("retrieval/rewrite failed", extra={"error": str(exc)})
        await persist("", "error", "error")
        yield Failed(
            "provider_unavailable", "The assistant is temporarily unavailable. Please try again."
        )
        return
    if req.debug:
        yield Debug(standalone, pack.debug, profile.id)

    # --- 4. fallback --------------------------------------------------------------------------------
    if pack.empty:
        text_out = localized(
            assistant["fallback_text"],
            language,
            assistants_svc.DEFAULT_FALLBACK.get(
                language, assistants_svc.DEFAULT_FALLBACK["default"]
            ),
        )
        await persist(
            text_out, "fallback", "fallback", standalone=standalone, retrieval_dbg=pack.debug
        )
        yield Delta(text_out)
        yield Citations([])
        yield Done("fallback", int((time.perf_counter() - started) * 1000))
        return

    # --- 5. grounded generation ------------------------------------------------------------------------
    messages = build_messages(
        system_prompt=assistant["system_prompt"],
        language_name=language_name(language),
        items=pack.items,
        history=history,
        question=req.question,
    )
    parts: list[str] = []
    first_token_ms: int | None = None
    done: ChatDone | None = None
    finished = False
    stream = chat.stream(
        messages, ChatParams(temperature=profile.temperature, max_output_tokens=max_out)
    )
    try:
        async for event in stream:
            if isinstance(event, ChatDelta):
                if first_token_ms is None:
                    first_token_ms = int((time.perf_counter() - started) * 1000)
                parts.append(event.text)
                yield Delta(event.text)
            else:
                done = event
        finished = True
    except ProviderError as exc:
        finished = True  # handled here; the finally block must not persist a second time
        log.warning("generation failed", extra={"error": str(exc)})
        await persist("".join(parts), "error", "error")
        yield Failed(
            "provider_unavailable", "The assistant is temporarily unavailable. Please try again."
        )
        return
    finally:
        if (
            not finished
        ):  # client went away: stop the LLM and still record what it cost (billing.md §2)
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                await aclose()
            content = "".join(parts)
            est = max(1, len(content) // 3)
            await asyncio.shield(
                persist(
                    content,
                    "answered",
                    "cancelled",
                    standalone=standalone,
                    retrieval_dbg=pack.debug,
                    completion_tokens=est,
                    first_token_ms=first_token_ms,
                    cogs=cogs_total + metering.answer_cogs(profile, None, est),
                )
            )

    content = "".join(parts)
    citations = citations_for(content, pack.items)
    prompt_tokens = done.usage.prompt_tokens if done else None
    completion_tokens = done.usage.completion_tokens if done else None
    cogs_total += metering.answer_cogs(profile, prompt_tokens, completion_tokens)
    await persist(
        content,
        "answered",
        done.finish_reason if done else "stop",
        citations=citations,
        standalone=standalone,
        retrieval_dbg=pack.debug,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        first_token_ms=first_token_ms,
        cogs=cogs_total,
    )
    yield Citations([{k: v for k, v in c.items() if k != "chunk_id"} for c in citations])
    yield Done(done.finish_reason if done else "stop", int((time.perf_counter() - started) * 1000))
