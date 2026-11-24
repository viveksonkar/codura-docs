"""BlobStore Port (specs/ports.md §4, ADR-0009).

Keys are ``<tenant_id>/<aa>/<sha256>`` and every call carries the caller's TenantContext; stores
refuse keys outside that Tenant's prefix (ADR-0014).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import BinaryIO, Protocol, runtime_checkable

from codura_docs.tenancy import TenantContext

BLOB_KEY_RE = re.compile(r"^(tnt_[0-9A-HJKMNP-TV-Z]{26})/([0-9a-f]{2})/([0-9a-f]{64})$")

BlobData = AsyncIterator[bytes] | BinaryIO


class BlobKeyError(ValueError):
    """Malformed key or key outside the caller's Tenant."""


def check_blob_key(ctx: TenantContext, key: str) -> None:
    match = BLOB_KEY_RE.match(key)
    if match is None:
        raise BlobKeyError("malformed blob key")
    tenant_id, shard, digest = match.groups()
    if tenant_id != ctx.tenant_id:
        raise BlobKeyError("blob key belongs to another tenant")
    if digest[:2] != shard:
        raise BlobKeyError("blob key shard does not match digest")


@runtime_checkable
class BlobStore(Protocol):
    async def put(self, ctx: TenantContext, key: str, data: BlobData, *, content_type: str) -> int:
        """Store bytes; return the number written."""
        ...

    def open(self, ctx: TenantContext, key: str) -> AsyncIterator[bytes]: ...

    async def exists(self, ctx: TenantContext, key: str) -> bool: ...

    async def delete(self, ctx: TenantContext, key: str) -> None:
        """Idempotent."""
        ...
