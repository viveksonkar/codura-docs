"""Knowledge Bases, Documents, Assistants and the Playground (FR-01…07, FR-31…33; M1-04/07/08)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from codura_docs.api.deps import TenantAccess, require_tenant
from codura_docs.api.errors import ApiError
from codura_docs.api.sse import sse_response
from codura_docs.db.session import tenant_session
from codura_docs.domain.language import ocr_languages_valid
from codura_docs.services import answering
from codura_docs.services import assistants as assistants_svc
from codura_docs.services import documents as docs

router = APIRouter(prefix="/api/admin", tags=["content"])
READ = Depends(require_tenant("user"))
ADMIN = Depends(require_tenant("admin"))
ADMIN_ACTIVE = Depends(require_tenant("admin", active=True))
MAX_FILES = 20


class KbIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)


class KbPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)


def _not_found(what: str) -> ApiError:
    return ApiError(404, "not_found", f"{what} not found")


@router.get("/knowledge-bases")
async def list_kbs(request: Request, a: TenantAccess = READ) -> dict[str, Any]:
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        return {"items": await docs.list_kbs(s)}


@router.post("/knowledge-bases", status_code=201)
async def create_kb(body: KbIn, request: Request, a: TenantAccess = ADMIN_ACTIVE) -> dict[str, Any]:
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        return await docs.create_kb(s, a.tenant_id, body.name, body.description)


@router.get("/knowledge-bases/{kb_id}")
async def get_kb(kb_id: str, request: Request, a: TenantAccess = READ) -> dict[str, Any]:
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        kb = await docs.get_kb(s, kb_id)
    if kb is None:
        raise _not_found("Knowledge Base")
    return kb


@router.patch("/knowledge-bases/{kb_id}")
async def patch_kb(
    kb_id: str, body: KbPatch, request: Request, a: TenantAccess = ADMIN
) -> dict[str, Any]:
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        if not await docs.update_kb(s, kb_id, body.name, body.description):
            raise _not_found("Knowledge Base")
        kb = await docs.get_kb(s, kb_id)
    assert kb is not None
    return kb


@router.delete("/knowledge-bases/{kb_id}", status_code=204)
async def delete_kb(kb_id: str, request: Request, a: TenantAccess = ADMIN) -> None:
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        if not await docs.delete_kb(s, a.ctx, kb_id):
            raise _not_found("Knowledge Base")


async def _chunks(upload: UploadFile) -> AsyncIterator[bytes]:
    while chunk := await upload.read(1024 * 1024):
        yield chunk


@router.post("/knowledge-bases/{kb_id}/documents", status_code=201)
async def upload(
    kb_id: str,
    request: Request,
    file: list[UploadFile] = File(...),
    ocr_languages: str | None = Form(default=None),
    a: TenantAccess = ADMIN_ACTIVE,
) -> dict[str, Any]:
    settings = request.app.state.settings
    registry = request.app.state.registry
    if len(file) > MAX_FILES:
        raise ApiError(422, "validation_error", f"Upload at most {MAX_FILES} files at once")
    if ocr_languages:
        try:
            installed = registry.ocr().available_languages
        except Exception:
            installed = frozenset()
        if not ocr_languages_valid(ocr_languages, installed):
            raise ApiError(422, "validation_error", "Unknown or uninstalled OCR language")
    results: list[dict[str, Any]] = []
    for item in file:
        name = item.filename or "upload"
        try:
            spooled = await docs.spool_upload(
                _chunks(item), name, settings.max_upload_mb * 1024 * 1024
            )
        except docs.DocumentError as exc:
            if len(file) == 1:
                raise ApiError(exc.status, exc.code, str(exc)) from exc
            results.append(
                {
                    "filename": name,
                    "status": "rejected",
                    "error": {"code": exc.code, "message": str(exc)},
                }
            )
            continue
        try:
            async with tenant_session(request.app.state.engine, a.ctx) as s:
                results.append(
                    await docs.create_document(
                        s,
                        a.ctx,
                        registry.blob_store(),
                        kb_id=kb_id,
                        filename=name,
                        upload=spooled,
                        user_id=a.principal.user_id,
                        ocr_languages=ocr_languages,
                    )
                )
        except docs.DocumentError as exc:
            raise ApiError(exc.status, exc.code, str(exc)) from exc
        finally:
            spooled.cleanup()
    return {"items": results}


@router.get("/knowledge-bases/{kb_id}/documents")
async def list_documents(
    kb_id: str, request: Request, status: str | None = None, a: TenantAccess = READ
) -> dict[str, Any]:
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        return {"items": await docs.list_documents(s, kb_id, status)}


@router.get("/documents/{doc_id}")
async def get_document(doc_id: str, request: Request, a: TenantAccess = READ) -> dict[str, Any]:
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        doc = await docs.get_document(s, doc_id)
    if doc is None:
        raise _not_found("Document")
    return doc


@router.delete("/documents/{doc_id}", status_code=204)
async def delete_document(doc_id: str, request: Request, a: TenantAccess = ADMIN) -> None:
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        if not await docs.delete_document(s, doc_id):
            raise _not_found("Document")


class ReingestIn(BaseModel):
    ocr_languages: str | None = None


@router.post("/documents/{doc_id}/reingest", status_code=202)
async def reingest(
    doc_id: str, body: ReingestIn, request: Request, a: TenantAccess = ADMIN_ACTIVE
) -> dict[str, str]:
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        if not await docs.reingest(s, doc_id, body.ocr_languages):
            raise _not_found("Document")
    return {"status": "pending"}


@router.get("/documents/{doc_id}/chunks")
async def chunks(doc_id: str, request: Request, a: TenantAccess = READ) -> dict[str, Any]:
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        if await docs.get_document(s, doc_id) is None:
            raise _not_found("Document")
        return {"items": await docs.list_chunks(s, doc_id)}


# --- Assistants (no model field: ADR-0019) ------------------------------------------------------------


class AssistantIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    slug: str | None = Field(default=None, max_length=40)
    knowledge_base_ids: list[str] | None = Field(default=None, max_length=50)
    system_prompt: str | None = Field(default=None, max_length=4000)
    default_language: str | None = None
    welcome_text: dict[str, str] | None = None
    fallback_text: dict[str, str] | None = None
    limit_reached_text: dict[str, str] | None = None
    visitor_email: Literal["off", "optional", "required"] | None = None
    visitor_email_consent: dict[str, str] | None = None
    privacy_url: str | None = Field(default=None, max_length=500)
    retrieval: dict[str, Any] | None = None
    theme: dict[str, Any] | None = None
    hosted_enabled: bool | None = None


@router.get("/assistants")
async def list_assistants(request: Request, a: TenantAccess = READ) -> dict[str, Any]:
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        return {"items": await assistants_svc.list_all(s)}


@router.post("/assistants", status_code=201)
async def create_assistant(
    body: AssistantIn, request: Request, a: TenantAccess = ADMIN_ACTIVE
) -> dict[str, Any]:
    if not body.name:
        raise ApiError(422, "validation_error", "name is required")
    try:
        async with tenant_session(request.app.state.engine, a.ctx) as s:
            return await assistants_svc.create(s, a.tenant_id, body.model_dump(exclude_none=True))
    except assistants_svc.AssistantError as exc:
        raise ApiError(exc.status, exc.code, str(exc)) from exc


@router.get("/assistants/{assistant_id}")
async def get_assistant(
    assistant_id: str, request: Request, a: TenantAccess = READ
) -> dict[str, Any]:
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        item = await assistants_svc.get(s, assistant_id)
    if item is None:
        raise _not_found("Assistant")
    return item


@router.patch("/assistants/{assistant_id}")
async def patch_assistant(
    assistant_id: str, body: AssistantIn, request: Request, a: TenantAccess = ADMIN
) -> dict[str, Any]:
    try:
        async with tenant_session(request.app.state.engine, a.ctx) as s:
            item = await assistants_svc.update(s, assistant_id, body.model_dump(exclude_none=True))
    except assistants_svc.AssistantError as exc:
        raise ApiError(exc.status, exc.code, str(exc)) from exc
    if item is None:
        raise _not_found("Assistant")
    return item


@router.delete("/assistants/{assistant_id}", status_code=204)
async def delete_assistant(assistant_id: str, request: Request, a: TenantAccess = ADMIN) -> None:
    async with tenant_session(request.app.state.engine, a.ctx) as s:
        if not await assistants_svc.delete(s, assistant_id):
            raise _not_found("Assistant")


# --- Playground (Tenant Admins and Users; metered like any answer) -------------------------------------


class PlaygroundIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None


@router.post("/assistants/{assistant_id}/playground/stream")
async def playground(
    assistant_id: str,
    body: PlaygroundIn,
    request: Request,
    a: TenantAccess = Depends(require_tenant("user", active=True)),
) -> StreamingResponse:
    req = answering.AnswerRequest(
        ctx=a.ctx,
        assistant_id=assistant_id,
        question=body.question.strip(),
        channel="playground",
        conversation_id=body.conversation_id,
        user_id=a.principal.user_id,
        debug=True,
    )
    events = answering.answer(
        req,
        engine=request.app.state.engine,
        registry=request.app.state.registry,
        keyring=request.app.state.keyring,
    )
    try:
        first = await anext(events)  # surface not-found as JSON errors before the stream opens
    except answering.AssistantNotFound as exc:
        raise _not_found("Assistant") from exc
    except answering.ConversationNotFound as exc:
        raise _not_found("Conversation") from exc

    return sse_response(replay_from(first, events), include_debug=True)


async def replay_from(
    first: answering.AnswerEvent, events: AsyncIterator[answering.AnswerEvent]
) -> AsyncIterator[answering.AnswerEvent]:
    """Re-emit the already-consumed first event, then the rest; always close the source so a client
    disconnect stops generation immediately."""
    try:
        yield first
        async for event in events:
            yield event
    finally:
        await events.aclose()  # type: ignore[attr-defined]
