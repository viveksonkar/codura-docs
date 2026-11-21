"""Blob key construction (specs/ports.md §4). Keys are the only paths a Blob Store ever sees."""

from __future__ import annotations

import re

from codura_docs.ids import is_id

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def blob_key(tenant_id: str, sha256: str) -> str:
    if not is_id("tnt", tenant_id):
        raise ValueError("invalid tenant id")
    if not _SHA256.match(sha256):
        raise ValueError("sha256 must be 64 lowercase hex characters")
    return f"{tenant_id}/{sha256[:2]}/{sha256}"
