"""Ingestion Job: parse (+OCR) → language → chunk → embed → index, metered (rag-pipeline §1, M1-05)."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import math
from typing import Any

from sqlalchemy import text

from codura_docs import metering
from codura_docs.billing.entitlements import for_tenant
from codura_docs.db.session import tenant_session
from codura_docs.domain import mime
from codura_docs.domain.blobs import blob_key
from codura_docs.domain.chunking import chunk
from codura_docs.jobs.handlers import JobContext, handler
from codura_docs.jobs.queue import NonRetryable
from codura_docs.metering import quotas
from codura_docs.ports.errors import ParseError, UnsupportedDocument
from codura_docs.ports.parser import ParsedDocument, ParseOptions
from codura_docs.tenancy import TenantContext

log = logging.getLogger(__name__)
PARSE_TIMEOUT_S = 900
OCR_CAPABLE = frozenset({mime.PDF, mime.PNG, mime.JPEG, mime.TIFF, mime.WEBP})


def vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


def billable_pages(parsed: ParsedDocument) -> int:
    if parsed.page_count:
        return parsed.page_count
    chars = sum(len(s.text) for s in parsed.sections)
    return max(1, math.ceil(chars / 3000))  # billing.md §2: text formats count per 3,000 chars


async def _set_status(
    engine: Any, ctx: TenantContext, doc_id: str, status: str, error: str | None = None
) -> None:
    async with tenant_session(engine, ctx) as s:
        await s.execute(
            text(
                "UPDATE documents SET status = :st, error = :e, updated_at = now() WHERE id = :id"
            ),
            {"st": status, "e": error, "id": doc_id},
        )


@handler("ingest_document")
async def ingest_document(jc: JobContext) -> None:
    job, engine, registry = jc.job, jc.app_engine, jc.registry
    if not job.tenant_id:
        raise NonRetryable("ingest_document requires a tenant")
    ctx = TenantContext(job.tenant_id)
    doc_id = str(job.payload["document_id"])
    settings = registry.settings

    async with tenant_session(engine, ctx) as s:
        doc = (
            (
                await s.execute(
                    text(
                        "SELECT d.*, t.ocr_languages AS tenant_ocr FROM documents d JOIN tenants t ON t.id = d.tenant_id "
                        "WHERE d.id = :id"
                    ),
                    {"id": doc_id},
                )
            )
            .mappings()
            .first()
        )
        if doc is None:
            return  # deleted meanwhile: nothing to do (idempotent)
        # Quota BEFORE parsing/OCR (specs/billing.md section 4): pages stored is a stock limit on
        # every Plan; OCR is admitted per Document while allowance (or overage) remains.
        terms = await for_tenant(s, ctx.tenant_id)
        stock = await quotas.check_stock(s, terms, "pages_stored", 1 - (doc["page_count"] or 0))
        ocr_open = True
        if doc["mime_type"] in OCR_CAPABLE:
            ocr = await quotas.admit(s, ctx.tenant_id, "page_ocr", 1, terms=terms)
            await quotas.release(s, ocr.reservation_id)
            ocr_open = ocr.allowed
        if terms.past_due or not stock.allowed or not ocr_open:
            await s.execute(
                text(
                    "UPDATE documents SET status = 'pending', error = 'quota_exceeded' WHERE id = :id"
                ),
                {"id": doc_id},
            )
            return
        await s.execute(
            text(
                "UPDATE documents SET status = 'processing', error = NULL, pages_done = 0, "
                "updated_at = now() WHERE id = :id"
            ),
            {"id": doc_id},
        )

    try:
        data = b"".join(
            [
                c
                async for c in registry.blob_store().open(
                    ctx, blob_key(ctx.tenant_id, doc["sha256"])
                )
            ]
        )
        parser = registry.parser_for(doc["mime_type"])
        progress: dict[str, int] = {"done": 0, "total": 0}

        def on_page(done: int, total: int) -> None:
            progress["done"], progress["total"] = done, total

        options = ParseOptions(
            ocr_languages=doc["ocr_languages"] or doc["tenant_ocr"] or "eng",
            ocr=registry.ocr(),
            max_pages=settings.max_pages,
            text_layer_min_chars=registry.models.ocr.text_layer_min_chars,
            low_confidence_threshold=registry.models.ocr.low_confidence_threshold,
            page_timeout_s=registry.models.ocr.page_timeout_s,
            render_dpi=registry.models.ocr.render_dpi,
            on_page_done=on_page,
        )

        async def report_progress() -> None:
            while True:
                await asyncio.sleep(2)
                async with tenant_session(engine, ctx) as s:
                    await s.execute(
                        text(
                            "UPDATE documents SET pages_done = :d, page_count = NULLIF(:t, 0) "
                            "WHERE id = :id"
                        ),
                        {"d": progress["done"], "t": progress["total"], "id": doc_id},
                    )

        reporter = asyncio.create_task(report_progress())
        try:
            parsed = await asyncio.wait_for(
                asyncio.to_thread(
                    parser.parse,
                    io.BytesIO(data),
                    filename=doc["filename"],
                    mime_type=doc["mime_type"],
                    options=options,
                ),
                PARSE_TIMEOUT_S,
            )
        finally:
            reporter.cancel()

        detector = registry.language()
        sample = " ".join(s.text for s in parsed.sections)[:5000]
        doc_language = detector.detect(sample).language
        title = parsed.title or doc["title"]
        embedding = registry.embedding()
        drafts = chunk(parsed, title=title)
        if not drafts:
            raise UnsupportedDocument("no text to index")
        vectors: list[list[float]] = []
        for i in range(0, len(drafts), 16):
            vectors += await embedding.embed([d.embed_text for d in drafts[i : i + 16]], "document")
    except UnsupportedDocument as exc:
        await _set_status(engine, ctx, doc_id, "failed", exc.reason)
        raise NonRetryable(exc.reason) from exc
    except (TimeoutError, ParseError) as exc:
        final = job.attempts >= job.max_attempts
        await _set_status(
            engine,
            ctx,
            doc_id,
            "failed" if final else "pending",
            "Processing timed out" if isinstance(exc, TimeoutError) else str(exc),
        )
        raise
    except Exception as exc:
        final = job.attempts >= job.max_attempts
        await _set_status(
            engine,
            ctx,
            doc_id,
            "failed" if final else "pending",
            f"Temporary error, {'giving up' if final else 'retrying'}: {type(exc).__name__}",
        )
        raise

    pages = billable_pages(parsed)
    ocr_pages = len({s.page for s in parsed.sections if s.ocr_confidence is not None})
    tokens = sum(d.token_count for d in drafts)

    async with tenant_session(engine, ctx) as s:
        current = (await quotas.stock_usage(s, ctx.tenant_id))["pages_stored"]
        previous = (
            await s.execute(
                text(
                    "SELECT coalesce(page_count, 0) FROM documents WHERE id = :id AND status = 'processing'"
                ),
                {"id": doc_id},
            )
        ).scalar() or 0
        if current - float(previous) + pages > float(
            (await for_tenant(s, ctx.tenant_id)).limit("pages_stored")
        ):
            await s.execute(
                text(
                    "UPDATE documents SET status = 'pending', error = 'quota_exceeded', updated_at = now() "
                    "WHERE id = :id"
                ),
                {"id": doc_id},
            )
            return
        ingest_n = (
            int(
                (
                    await s.execute(
                        text("SELECT ingest_count FROM documents WHERE id = :id FOR UPDATE"),
                        {"id": doc_id},
                    )
                ).scalar()
                or 0
            )
            + 1
        )
        await s.execute(text("DELETE FROM chunks WHERE document_id = :id"), {"id": doc_id})
        for draft, vec in zip(drafts, vectors, strict=True):
            lang = detector.detect(draft.text).language if len(draft.text) > 40 else doc_language
            await s.execute(
                text(
                    "INSERT INTO chunks (id, tenant_id, document_id, knowledge_base_id, ordinal, text, token_count, "
                    "language, page_start, page_end, heading_path, ocr_confidence, embedding, tsv) VALUES "
                    "(:id, :t, :d, :k, :o, :txt, :tok, :lang, :ps, :pe, :hp, :ocr, CAST(:emb AS vector), "
                    "setweight(to_tsvector('simple', :head), 'A') || setweight(to_tsvector('simple', :txt), 'B'))"
                ),
                {
                    "id": f"chk_{doc_id[4:]}_{draft.ordinal:05d}"[:64],
                    "t": ctx.tenant_id,
                    "d": doc_id,
                    "k": doc["knowledge_base_id"],
                    "o": draft.ordinal,
                    "txt": draft.text,
                    "tok": draft.token_count,
                    "lang": lang,
                    "ps": draft.page_start,
                    "pe": draft.page_end,
                    "hp": list(draft.heading_path),
                    "ocr": draft.ocr_confidence,
                    "emb": vector_literal(vec),
                    "head": f"{title} {' '.join(draft.heading_path)}",
                },
            )
        await s.execute(
            text(
                "UPDATE documents SET status = 'ready', error = NULL, title = :title, language = :lang, "
                "warnings = :w, page_count = :pc, pages_done = :pc2, ocr_pages = :ocr, chunk_count = :cc, "
                "ingest_count = :n, ingested_at = now(), updated_at = now() WHERE id = :id"
            ),
            {
                "title": title[:200],
                "lang": doc_language,
                "w": list(parsed.warnings),
                "pc": parsed.page_count,
                "pc2": parsed.page_count or pages,
                "ocr": ocr_pages,
                "cc": len(drafts),
                "n": ingest_n,
                "id": doc_id,
            },
        )
        embed_rate = await metering.cost_rate(s, "embed_per_mtok")
        await metering.record(
            s,
            ctx.tenant_id,
            "page_ingested",
            pages,
            cogs_micros=round(tokens * embed_rate / 1_000_000),
            idempotency_key=f"{doc_id}:{ingest_n}",
            attrs={"document_id": doc_id, "chunks": len(drafts), "tokens": tokens},
        )
        if ocr_pages:
            ocr_rate = await metering.cost_rate(s, "ocr_per_page")
            await metering.record(
                s,
                ctx.tenant_id,
                "page_ocr",
                ocr_pages,
                cogs_micros=ocr_pages * ocr_rate,
                idempotency_key=f"{doc_id}:{ingest_n}",
                attrs={"document_id": doc_id},
            )
    log.info(
        "document ingested",
        extra={
            "document_id": doc_id,
            "chunks": len(drafts),
            "pages": pages,
            "ocr_pages": ocr_pages,
            "warnings": json.dumps(list(parsed.warnings)),
        },
    )


@handler("purge_blob")
async def purge_blob(jc: JobContext) -> None:
    """Delete a Blob once no Document in the same Tenant references its hash (FR-06)."""
    if not jc.job.tenant_id:
        return
    ctx = TenantContext(jc.job.tenant_id)
    sha = str(jc.job.payload["sha256"])
    async with tenant_session(jc.app_engine, ctx) as s:
        still_used = (
            await s.execute(text("SELECT 1 FROM documents WHERE sha256 = :s LIMIT 1"), {"s": sha})
        ).first()
        if not still_used and jc.job.payload.get("document_id"):
            await s.execute(
                text(
                    "UPDATE messages SET citations = (SELECT coalesce(jsonb_agg(CASE WHEN c->>'document_id' = :d "
                    "THEN c || jsonb_build_object('snippet', NULL, 'deleted', true) ELSE c END), '[]'::jsonb) "
                    "FROM jsonb_array_elements(citations) c) WHERE citations @> CAST(:probe AS jsonb)"
                ),
                {
                    "d": jc.job.payload["document_id"],
                    "probe": json.dumps([{"document_id": jc.job.payload["document_id"]}]),
                },
            )
    if not still_used:
        await jc.registry.blob_store().delete(ctx, blob_key(ctx.tenant_id, sha))


@handler("send_email")
async def send_email(jc: JobContext) -> None:
    from codura_docs.ports.email import EmailMessage

    p = jc.job.payload
    await jc.registry.email().send(EmailMessage(str(p["to"]), str(p["subject"]), str(p["text"])))
