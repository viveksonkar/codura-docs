"""Parser Port (specs/ports.md §3)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import BinaryIO, Literal, Protocol, runtime_checkable

from codura_docs.ports.ocr import OcrEngine

SectionKind = Literal["heading", "paragraph", "table", "list", "code"]


@dataclass(frozen=True)
class Section:
    text: str
    kind: SectionKind
    page: int | None
    heading_path: tuple[str, ...]
    ocr_confidence: float | None = None


@dataclass(frozen=True)
class ParsedDocument:
    title: str | None
    sections: tuple[Section, ...]
    page_count: int | None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParseOptions:
    ocr_languages: str
    ocr: OcrEngine
    max_pages: int
    text_layer_min_chars: int = 30
    low_confidence_threshold: float = 60
    page_timeout_s: float = 20
    render_dpi: int = 300
    on_page_done: Callable[[int, int], None] | None = None


@runtime_checkable
class Parser(Protocol):
    supported_mime_types: frozenset[str]

    def parse(
        self, data: BinaryIO, *, filename: str, mime_type: str, options: ParseOptions
    ) -> ParsedDocument: ...
