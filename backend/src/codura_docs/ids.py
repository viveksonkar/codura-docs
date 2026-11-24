"""Prefixed, sortable IDs (ULID), e.g. ``tnt_01JD8X...``. See specs/db.md §1."""

from __future__ import annotations

import re

from ulid import ULID

PREFIXES = frozenset(
    {
        "tnt",
        "usr",
        "kb",
        "doc",
        "chk",
        "asst",
        "key",
        "conv",
        "msg",
        "job",
        "inv",
        "tok",
        "aud",
        "ue",
        "plv",
        "sev",
        "bill",
        "line",
        "pay",
        "qres",
    }
)
_ULID = r"[0-9A-HJKMNP-TV-Z]{26}"
_PATTERNS = {p: re.compile(rf"^{p}_{_ULID}$") for p in PREFIXES}


def new_id(prefix: str) -> str:
    if prefix not in PREFIXES:
        raise ValueError(f"unknown id prefix: {prefix!r}")
    return f"{prefix}_{ULID()}"


def is_id(prefix: str, value: object) -> bool:
    pattern = _PATTERNS.get(prefix)
    return isinstance(value, str) and pattern is not None and pattern.match(value) is not None
