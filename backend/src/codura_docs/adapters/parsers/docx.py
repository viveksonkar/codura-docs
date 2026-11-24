"""DOCX parser (python-docx): headings from 'Heading N' styles, tables as Markdown rows (SEC-03)."""

from __future__ import annotations

import io
import zipfile
from typing import BinaryIO

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from codura_docs.adapters.parsers._text import normalise
from codura_docs.ports.errors import UnsupportedDocument
from codura_docs.ports.parser import ParsedDocument, ParseOptions, Section, SectionKind

MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MAX_UNCOMPRESSED = 200 * 1024 * 1024
MAX_ENTRIES = 2000


def _guard_zip(raw: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            infos = zf.infolist()
    except zipfile.BadZipFile as exc:
        raise UnsupportedDocument("corrupt DOCX") from exc
    if len(infos) > MAX_ENTRIES or sum(i.file_size for i in infos) > MAX_UNCOMPRESSED:
        raise UnsupportedDocument("DOCX expands beyond safe limits (possible zip bomb)")


class DocxParser:
    supported_mime_types = frozenset({MIME})

    def parse(
        self, data: BinaryIO, *, filename: str, mime_type: str, options: ParseOptions
    ) -> ParsedDocument:
        raw = data.read()
        _guard_zip(raw)
        try:
            doc = Document(io.BytesIO(raw))
        except Exception as exc:
            raise UnsupportedDocument(f"unreadable DOCX: {exc}") from exc
        sections: list[Section] = []
        heading_path: list[str] = []
        for block in doc.iter_inner_content():
            if isinstance(block, Paragraph):
                text = normalise(block.text)
                if not text:
                    continue
                style = (block.style.name or "") if block.style is not None else ""
                if style.startswith("Heading") or style == "Title":
                    level = int(style.split()[-1]) if style.split()[-1].isdigit() else 1
                    heading_path = [*heading_path[: level - 1], text]
                    sections.append(Section(text, "heading", None, tuple(heading_path)))
                else:
                    kind: SectionKind = "list" if "List" in style else "paragraph"
                    sections.append(Section(text, kind, None, tuple(heading_path)))
            elif isinstance(block, Table):
                rows = []
                for row in block.rows:
                    cells = [normalise(c.text).replace("|", "/") for c in row.cells]
                    rows.append("| " + " | ".join(cells) + " |")
                if rows:
                    sections.append(Section("\n".join(rows), "table", None, tuple(heading_path)))
        if not sections:
            raise UnsupportedDocument("the DOCX contains no text")
        title = doc.core_properties.title or None
        return ParsedDocument(title=title, sections=tuple(sections), page_count=None)
