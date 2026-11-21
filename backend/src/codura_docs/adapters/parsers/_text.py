"""Text normalisation shared by parsers (rag-pipeline.md §1.1)."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")
_SPACES = re.compile(r"[ \t ]+")
_BLANKS = re.compile(r"\n{3,}")


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    text = _HYPHEN_BREAK.sub(r"\1\2", text)  # de-hyphenate words split across lines
    text = "\n".join(_SPACES.sub(" ", line).strip() for line in text.split("\n"))
    return _BLANKS.sub("\n\n", text).strip()


def strip_repeated_lines(pages: list[str], min_pages: int = 3, ratio: float = 0.5) -> list[str]:
    """Remove header/footer lines that appear on more than ``ratio`` of pages."""
    if len(pages) < min_pages:
        return pages
    counts: Counter[str] = Counter()
    for page in pages:
        uniq = {ln.strip() for ln in page.split("\n") if ln.strip()}
        edge = list(uniq)
        counts.update(edge)
    repeated = {ln for ln, n in counts.items() if n / len(pages) > ratio and len(ln) < 120}
    # Page numbers like "Page 3 of 10" / "3" are always noise at page edges.
    number_like = re.compile(r"^(page\s*)?\d+(\s*(of|/)\s*\d+)?$", re.IGNORECASE)
    cleaned = []
    for page in pages:
        lines = page.split("\n")
        kept = [
            ln for ln in lines if ln.strip() not in repeated and not number_like.match(ln.strip())
        ]
        cleaned.append("\n".join(kept))
    return cleaned


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
