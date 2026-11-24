"""Structure-aware, deterministic, multilingual chunking (rag-pipeline.md §1.4, M1-03).

Pure: no I/O. Never crosses a top-level heading or a page boundary of an OCR'd page.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from codura_docs.ports.parser import ParsedDocument, Section

# Sentence ends for Latin, Devanagari (।), CJK (。！？) and Arabic (؟ ۔) scripts.
_SENTENCE_END = re.compile(r"(?<=[.!?।。！？؟۔])\s+|(?<=[。！？])")

TokenCounter = Callable[[str], int]


def estimate_tokens(text: str) -> int:
    """Conservative estimate when the model tokenizer is unavailable (len/3, rag-pipeline §1.4)."""
    return max(1, len(text) // 3)


@dataclass(frozen=True)
class ChunkSettings:
    target_tokens: int = 400
    max_tokens: int = 512
    overlap_tokens: int = 60


@dataclass(frozen=True)
class ChunkDraft:
    ordinal: int
    text: str
    embed_text: str
    token_count: int
    page_start: int | None
    page_end: int | None
    heading_path: tuple[str, ...]
    ocr_confidence: float | None


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text) if s and s.strip()]


def _split_long(text: str, max_tokens: int, count: TokenCounter) -> list[str]:
    """Split an oversized paragraph by sentences, then by characters as a last resort."""
    if count(text) <= max_tokens:
        return [text]
    pieces: list[str] = []
    current = ""
    for sentence in split_sentences(text) or [text]:
        candidate = f"{current} {sentence}".strip() if current else sentence
        if count(candidate) <= max_tokens:
            current = candidate
            continue
        if current:
            pieces.append(current)
        if count(sentence) <= max_tokens:
            current = sentence
        else:  # a single giant "sentence" (tables, CJK without punctuation)
            step = max(1, len(sentence) * max_tokens // max(1, count(sentence)))
            pieces.extend(sentence[i : i + step] for i in range(0, len(sentence), step))
            current = ""
    if current:
        pieces.append(current)
    return pieces


@dataclass
class _Unit:
    text: str
    page: int | None
    heading_path: tuple[str, ...]
    ocr: float | None


def _group_key(section: Section) -> tuple[str, int | None]:
    top = section.heading_path[0] if section.heading_path else ""
    page = section.page if section.ocr_confidence is not None else None
    return top, page


def chunk(
    parsed: ParsedDocument,
    *,
    title: str,
    settings: ChunkSettings = ChunkSettings(),
    count_tokens: TokenCounter = estimate_tokens,
) -> list[ChunkDraft]:
    # 1. Expand sections into units no larger than max_tokens.
    groups: list[tuple[tuple[str, int | None], list[_Unit]]] = []
    for section in parsed.sections:
        key = _group_key(section)
        units = [
            _Unit(piece, section.page, section.heading_path, section.ocr_confidence)
            for piece in _split_long(section.text, settings.max_tokens, count_tokens)
        ]
        if groups and groups[-1][0] == key:
            groups[-1][1].extend(units)
        else:
            groups.append((key, units))

    # 2. Pack units greedily up to target_tokens inside each group; overlap with the previous tail.
    drafts: list[ChunkDraft] = []
    for _, units in groups:
        current: list[_Unit] = []
        tokens = 0
        for unit in units:
            unit_tokens = count_tokens(unit.text)
            if current and tokens + unit_tokens > settings.target_tokens:
                drafts.append(_draft(len(drafts), current, title, count_tokens))
                current = _overlap_tail(current, settings.overlap_tokens, count_tokens)
                tokens = sum(count_tokens(u.text) for u in current)
                if tokens + unit_tokens > settings.max_tokens:
                    current, tokens = [], 0
            current.append(unit)
            tokens += unit_tokens
        if current:
            drafts.append(_draft(len(drafts), current, title, count_tokens))
    return drafts


def _overlap_tail(units: Sequence[_Unit], overlap: int, count: TokenCounter) -> list[_Unit]:
    if overlap <= 0 or not units:
        return []
    last = units[-1]
    sentences = split_sentences(last.text)
    tail: list[str] = []
    for sentence in reversed(sentences):
        if count(" ".join([sentence, *tail])) > overlap:
            break
        tail.insert(0, sentence)
    return [_Unit(" ".join(tail), last.page, last.heading_path, last.ocr)] if tail else []


def _draft(ordinal: int, units: Sequence[_Unit], title: str, count: TokenCounter) -> ChunkDraft:
    text = "\n\n".join(u.text for u in units)
    pages = [u.page for u in units if u.page is not None]
    heading = next((u.heading_path for u in reversed(units) if u.heading_path), ())
    ocr = [u.ocr for u in units if u.ocr is not None]
    header = f"{title} › {' › '.join(heading)}" if heading else title
    return ChunkDraft(
        ordinal=ordinal,
        text=text,
        embed_text=f"{header}\n{text}",
        token_count=count(text),
        page_start=min(pages) if pages else None,
        page_end=max(pages) if pages else None,
        heading_path=heading,
        ocr_confidence=round(sum(ocr) / len(ocr), 1) if ocr else None,
    )
