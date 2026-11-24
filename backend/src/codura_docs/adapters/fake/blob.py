"""InMemoryBlobStore: dict-backed BlobStore for tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

from codura_docs.ports.blob import BlobData, check_blob_key
from codura_docs.tenancy import TenantContext


async def read_all(data: BlobData) -> bytes:
    if hasattr(data, "read"):
        return data.read()
    chunks = [chunk async for chunk in data]
    return b"".join(chunks)


class InMemoryBlobStore:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def put(self, ctx: TenantContext, key: str, data: BlobData, *, content_type: str) -> int:
        check_blob_key(ctx, key)
        payload = await read_all(data)
        self.objects[key] = (payload, content_type)
        return len(payload)

    async def open(self, ctx: TenantContext, key: str) -> AsyncIterator[bytes]:
        check_blob_key(ctx, key)
        if key not in self.objects:
            raise FileNotFoundError(key)
        payload = self.objects[key][0]
        for i in range(0, len(payload), 64 * 1024):
            yield payload[i : i + 64 * 1024]

    async def exists(self, ctx: TenantContext, key: str) -> bool:
        check_blob_key(ctx, key)
        return key in self.objects

    async def delete(self, ctx: TenantContext, key: str) -> None:
        check_blob_key(ctx, key)
        self.objects.pop(key, None)
