"""BlobStore, EmailSender and OcrEngine contracts (specs/ports.md §3a, §4, §5a)."""

from __future__ import annotations

import hashlib
import io
from collections.abc import AsyncIterator
from typing import Any

import pytest

from codura_docs.domain.blobs import blob_key
from codura_docs.ids import new_id
from codura_docs.ports.blob import BlobKeyError
from codura_docs.ports.email import EmailMessage
from codura_docs.ports.errors import UnsupportedDocument
from codura_docs.tenancy import TenantContext


def _ctx() -> TenantContext:
    return TenantContext(new_id("tnt"))


async def _aiter(data: bytes, chunk: int = 7) -> AsyncIterator[bytes]:
    for i in range(0, len(data), chunk):
        yield data[i : i + chunk]


async def _read(store: Any, ctx: TenantContext, key: str) -> bytes:
    return b"".join([c async for c in store.open(ctx, key)])


@pytest.mark.parametrize("as_stream", [False, True])
async def test_blob_roundtrip_exists_delete(blob_store: Any, as_stream: bool) -> None:
    ctx = _ctx()
    payload = ("multilingual बाइट्स " * 5000).encode()
    key = blob_key(ctx.tenant_id, hashlib.sha256(payload).hexdigest())
    source = _aiter(payload) if as_stream else io.BytesIO(payload)
    assert await blob_store.put(ctx, key, source, content_type="application/pdf") == len(payload)
    assert await blob_store.exists(ctx, key)
    assert await _read(blob_store, ctx, key) == payload
    await blob_store.delete(ctx, key)
    await blob_store.delete(ctx, key)  # idempotent
    assert not await blob_store.exists(ctx, key)


async def test_blob_refuses_other_tenant_keys(blob_store: Any) -> None:
    owner, intruder = _ctx(), _ctx()
    key = blob_key(owner.tenant_id, hashlib.sha256(b"x").hexdigest())
    await blob_store.put(owner, key, io.BytesIO(b"x"), content_type="text/plain")
    with pytest.raises(BlobKeyError):
        await blob_store.exists(intruder, key)
    with pytest.raises(BlobKeyError):
        await _read(blob_store, intruder, key)
    with pytest.raises(BlobKeyError):
        await blob_store.delete(intruder, key)
    assert await blob_store.exists(owner, key)


async def test_email_send(email_sender: Any) -> None:
    await email_sender.send(
        EmailMessage("someone@example.test", "Verify your email", "Click the link", "<p>Click</p>")
    )


def test_ocr_rejects_unknown_languages(ocr_engine: Any) -> None:
    assert "eng" in ocr_engine.available_languages
    result = ocr_engine.recognize(object(), "eng+hin", timeout_s=5)
    assert result.text
    assert 0 <= result.mean_confidence <= 100
    with pytest.raises(UnsupportedDocument):
        ocr_engine.recognize(object(), "xxx", timeout_s=5)
