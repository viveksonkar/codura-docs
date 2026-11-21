"""LocalBlobStore: content-addressed files on a volume, atomic writes (ADR-0009)."""

from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

from codura_docs.ports.blob import BlobData, check_blob_key
from codura_docs.tenancy import TenantContext

_CHUNK = 1024 * 1024


class LocalBlobStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, ctx: TenantContext, key: str) -> Path:
        check_blob_key(ctx, key)  # validated format ⇒ no traversal possible
        return self._root / key

    async def put(self, ctx: TenantContext, key: str, data: BlobData, *, content_type: str) -> int:
        path = self._path(ctx, key)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        fd, tmp_name = await asyncio.to_thread(tempfile.mkstemp, dir=path.parent, prefix=".upload-")
        written = 0
        try:
            with os.fdopen(fd, "wb") as tmp:
                if hasattr(data, "read"):
                    while chunk := await asyncio.to_thread(data.read, _CHUNK):
                        await asyncio.to_thread(tmp.write, chunk)
                        written += len(chunk)
                else:
                    async for chunk in data:
                        await asyncio.to_thread(tmp.write, chunk)
                        written += len(chunk)
                await asyncio.to_thread(tmp.flush)
                await asyncio.to_thread(os.fsync, tmp.fileno())
            await asyncio.to_thread(os.replace, tmp_name, path)
        except BaseException:
            await asyncio.to_thread(_unlink_quiet, Path(tmp_name))
            raise
        return written

    async def open(self, ctx: TenantContext, key: str) -> AsyncIterator[bytes]:
        path = self._path(ctx, key)
        handle = await asyncio.to_thread(path.open, "rb")
        try:
            while chunk := await asyncio.to_thread(handle.read, _CHUNK):
                yield chunk
        finally:
            await asyncio.to_thread(handle.close)

    async def exists(self, ctx: TenantContext, key: str) -> bool:
        return await asyncio.to_thread(self._path(ctx, key).is_file)

    async def delete(self, ctx: TenantContext, key: str) -> None:
        await asyncio.to_thread(_unlink_quiet, self._path(ctx, key))


def _unlink_quiet(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
