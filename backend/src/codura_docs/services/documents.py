"""Knowledge Bases and Documents (FR-01…07, FR-31, SEC-03). All calls run in a tenant_session."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from codura_docs.db.session import rowcount
from codura_docs.domain import mime
from codura_docs.domain.blobs import blob_key
from codura_docs.ids import new_id
from codura_docs.jobs.queue import enqueue
from codura_docs.ports.blob import BlobStore
from codura_docs.tenancy import TenantContext

_CHUNK = 1024 * 1024


class DocumentError(ValueError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code = status, code


# --- Knowledge Bases ----------------------------------------------------------------------------


async def create_kb(
    session: AsyncSession, tenant_id: str, name: str, description: str = ""
) -> dict[str, Any]:
    kb_id = new_id("kb")
    row = (
        (
            await session.execute(
                text(
                    "INSERT INTO knowledge_bases (id, tenant_id, name, description) VALUES (:id, :t, :n, :d) "
                    "RETURNING id, name, description, created_at, updated_at"
                ),
                {"id": kb_id, "t": tenant_id, "n": name.strip(), "d": description.strip()},
            )
        )
        .mappings()
        .one()
    )
    return dict(row) | {"document_count": 0}


async def list_kbs(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                text(
                    "SELECT k.id, k.name, k.description, k.created_at, k.updated_at, "
                    "count(d.id) AS document_count, count(d.id) FILTER (WHERE d.status = 'ready') AS ready_count "
                    "FROM knowledge_bases k LEFT JOIN documents d ON d.knowledge_base_id = k.id "
                    "GROUP BY k.id ORDER BY k.created_at"
                )
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def get_kb(session: AsyncSession, kb_id: str) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, name, description, created_at, updated_at FROM knowledge_bases WHERE id = :id"
                ),
                {"id": kb_id},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def update_kb(
    session: AsyncSession, kb_id: str, name: str | None, description: str | None
) -> bool:
    result = await session.execute(
        text(
            "UPDATE knowledge_bases SET name = COALESCE(:n, name), description = COALESCE(:d, description), "
            "updated_at = now() WHERE id = :id"
        ),
        {"id": kb_id, "n": name.strip() if name else None, "d": description},
    )
    return bool(rowcount(result))


async def delete_kb(session: AsyncSession, ctx: TenantContext, kb_id: str) -> bool:
    shas = (
        (
            await session.execute(
                text("SELECT DISTINCT sha256 FROM documents WHERE knowledge_base_id = :k"),
                {"k": kb_id},
            )
        )
        .scalars()
        .all()
    )
    result = await session.execute(
        text("DELETE FROM knowledge_bases WHERE id = :id"), {"id": kb_id}
    )
    for sha in shas:
        await enqueue(session, "purge_blob", {"sha256": sha})
    return bool(rowcount(result))


# --- Upload (streamed, hashed, size-capped, sniffed) ------------------------------------------------


@dataclass
class SpooledUpload:
    path: str
    size: int
    sha256: str
    mime_type: str

    def open(self) -> BinaryIO:
        return open(self.path, "rb")

    def cleanup(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self.path)


async def spool_upload(
    chunks: AsyncIterator[bytes], filename: str, max_bytes: int
) -> SpooledUpload:
    """Stream to a temp file while hashing; enforce the size cap before reading everything."""
    digest = hashlib.sha256()
    size = 0
    head = b""
    fd, path = tempfile.mkstemp(prefix="codura-upload-")
    try:
        with os.fdopen(fd, "wb") as out:
            async for chunk in chunks:
                size += len(chunk)
                if size > max_bytes:
                    raise DocumentError(
                        413, "document_too_large", f"File exceeds {max_bytes // (1024 * 1024)} MB"
                    )
                if len(head) < 8192:
                    head += chunk[: 8192 - len(head)]
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise DocumentError(422, "validation_error", "The file is empty")
        kind = mime.sniff(head, filename)
        if kind == mime.DOCX or (kind is None and head.startswith(b"PK")):
            raw = await asyncio.to_thread(Path(path).read_bytes)
            kind = mime.DOCX if mime.is_docx(raw) else None
        if kind is None or kind not in mime.SUPPORTED:
            raise DocumentError(
                415,
                "unsupported_media_type",
                "Unsupported file type. Upload PDF, DOCX, TXT, MD, HTML, PNG, JPEG, TIFF or WebP.",
            )
        return SpooledUpload(path, size, digest.hexdigest(), kind)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)
        raise


async def create_document(
    session: AsyncSession,
    ctx: TenantContext,
    blobs: BlobStore,
    *,
    kb_id: str,
    filename: str,
    upload: SpooledUpload,
    user_id: str | None,
    ocr_languages: str | None,
) -> dict[str, Any]:
    if await get_kb(session, kb_id) is None:
        raise DocumentError(404, "not_found", "Knowledge Base not found")
    existing = (
        await session.execute(
            text("SELECT id FROM documents WHERE knowledge_base_id = :k AND sha256 = :s"),
            {"k": kb_id, "s": upload.sha256},
        )
    ).scalar()
    if existing:
        return {"id": existing, "filename": filename, "status": "duplicate"}
    key = blob_key(ctx.tenant_id, upload.sha256)
    if not await blobs.exists(ctx, key):
        with upload.open() as fh:
            await blobs.put(ctx, key, fh, content_type=upload.mime_type)
    doc_id = new_id("doc")
    title = os.path.splitext(os.path.basename(filename))[0][:200] or "Untitled"
    try:
        row = (
            (
                await session.execute(
                    text(
                        "INSERT INTO documents (id, tenant_id, knowledge_base_id, title, filename, mime_type, "
                        "size_bytes, sha256, blob_key, ocr_languages, created_by) "
                        "VALUES (:id, :t, :k, :title, :f, :m, :size, :sha, :key, :ocr, :by) "
                        "RETURNING id, title, filename, mime_type, size_bytes, status, created_at"
                    ),
                    {
                        "id": doc_id,
                        "t": ctx.tenant_id,
                        "k": kb_id,
                        "title": title,
                        "f": filename[:255],
                        "m": upload.mime_type,
                        "size": upload.size,
                        "sha": upload.sha256,
                        "key": key,
                        "ocr": ocr_languages,
                        "by": user_id,
                    },
                )
            )
            .mappings()
            .one()
        )
    except IntegrityError:  # concurrent duplicate upload
        return {"id": None, "filename": filename, "status": "duplicate"}
    await enqueue(session, "ingest_document", {"document_id": doc_id})
    return dict(row)


DOC_COLUMNS = (
    "id, knowledge_base_id, title, filename, mime_type, size_bytes, status, error, warnings, "
    "language, ocr_languages, page_count, pages_done, ocr_pages, chunk_count, ingested_at, "
    "created_at, updated_at"
)


async def list_documents(
    session: AsyncSession, kb_id: str, status: str | None = None
) -> list[dict[str, Any]]:
    sql = f"SELECT {DOC_COLUMNS} FROM documents WHERE knowledge_base_id = :k"
    params: dict[str, Any] = {"k": kb_id}
    if status:
        sql += " AND status = :s"
        params["s"] = status
    rows = (await session.execute(text(sql + " ORDER BY created_at DESC"), params)).mappings().all()
    return [dict(r) for r in rows]


async def get_document(session: AsyncSession, doc_id: str) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text(f"SELECT {DOC_COLUMNS} FROM documents WHERE id = :id"),
                {"id": doc_id},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def delete_document(session: AsyncSession, doc_id: str) -> bool:
    row = (
        await session.execute(
            text("DELETE FROM documents WHERE id = :id RETURNING sha256"), {"id": doc_id}
        )
    ).first()
    if row is None:
        return False
    await enqueue(session, "purge_blob", {"sha256": row.sha256, "document_id": doc_id})
    return True


async def reingest(session: AsyncSession, doc_id: str, ocr_languages: str | None) -> bool:
    result = await session.execute(
        text(
            "UPDATE documents SET status = 'pending', error = NULL, pages_done = 0, "
            "ocr_languages = COALESCE(:o, ocr_languages), updated_at = now() WHERE id = :id"
        ),
        {"id": doc_id, "o": ocr_languages},
    )
    if not rowcount(result):
        return False
    await enqueue(session, "ingest_document", {"document_id": doc_id})
    return True


async def list_chunks(session: AsyncSession, doc_id: str, limit: int = 200) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                text(
                    "SELECT id, ordinal, text, token_count, language, page_start, page_end, heading_path, "
                    "ocr_confidence FROM chunks WHERE document_id = :d ORDER BY ordinal LIMIT :l"
                ),
                {"d": doc_id, "l": limit},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]
