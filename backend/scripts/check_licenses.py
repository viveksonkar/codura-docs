"""Fail if any installed runtime dependency is GPL/AGPL licensed (ADR-0008, SEC-13).

LGPL is allowed (dynamic linking, e.g. psycopg). Run: uv run python scripts/check_licenses.py
"""

from __future__ import annotations

import re
import sys
from importlib.metadata import distributions

STRONG_COPYLEFT = re.compile(r"\b(A?GPL|GNU (Affero )?General Public License)\b", re.IGNORECASE)
WEAK = re.compile(r"\b(LGPL|Lesser General Public)\b", re.IGNORECASE)
# Packages verified by hand (dual-licensed, or metadata that mentions GPL only as an option).
ALLOW = {"text-unidecode"}


def license_text(dist: object) -> str:
    meta = dist.metadata  # type: ignore[attr-defined]
    parts = [meta.get("License-Expression") or "", meta.get("License") or ""]
    parts += [c for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    return " | ".join(p for p in parts if p)


def main() -> int:
    offenders = []
    for dist in distributions():
        name = dist.metadata["Name"]
        text = license_text(dist)
        if name.lower() in ALLOW:
            continue
        if STRONG_COPYLEFT.search(text) and not WEAK.search(text):
            offenders.append(f"{name}: {text[:160]}")
    if offenders:
        print("Copyleft (GPL/AGPL) dependencies are not allowed:\n  " + "\n  ".join(offenders))
        return 1
    print("licence check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
