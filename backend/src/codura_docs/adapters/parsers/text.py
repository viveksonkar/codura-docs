"""Plain text, Markdown and HTML parsers."""

from __future__ import annotations

import re
from typing import BinaryIO

from bs4 import BeautifulSoup

from codura_docs.adapters.parsers._text import normalise, paragraphs
from codura_docs.ports.errors import UnsupportedDocument
from codura_docs.ports.parser import ParsedDocument, ParseOptions, Section, SectionKind

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise UnsupportedDocument("text is not valid UTF-8")


class TextParser:
    supported_mime_types = frozenset({"text/plain", "text/markdown"})

    def parse(
        self, data: BinaryIO, *, filename: str, mime_type: str, options: ParseOptions
    ) -> ParsedDocument:
        text = normalise(_decode(data.read()))
        if not text:
            raise UnsupportedDocument("the file is empty")
        sections: list[Section] = []
        heading_path: list[str] = []
        buffer: list[str] = []

        def flush() -> None:
            for para in paragraphs("\n".join(buffer)):
                sections.append(Section(para, "paragraph", None, tuple(heading_path)))
            buffer.clear()

        for line in text.split("\n"):
            match = _MD_HEADING.match(line) if mime_type == "text/markdown" else None
            if match:
                flush()
                level = len(match.group(1))
                heading_path[:] = [*heading_path[: level - 1], match.group(2)]
                sections.append(Section(match.group(2), "heading", None, tuple(heading_path)))
            else:
                buffer.append(line)
        flush()
        title = next((s.text for s in sections if s.kind == "heading"), None)
        return ParsedDocument(title=title, sections=tuple(sections), page_count=None)


class HtmlParser:
    supported_mime_types = frozenset({"text/html"})

    def parse(
        self, data: BinaryIO, *, filename: str, mime_type: str, options: ParseOptions
    ) -> ParsedDocument:
        soup = BeautifulSoup(_decode(data.read()), "html.parser")
        for tag in soup(
            ["script", "style", "noscript", "nav", "footer", "header", "form", "svg", "iframe"]
        ):
            tag.decompose()
        root = soup.find("main") or soup.find("article") or soup.body or soup
        sections: list[Section] = []
        heading_path: list[str] = []
        for el in root.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "pre"]):
            text = normalise(el.get_text(" "))
            if not text:
                continue
            if el.name and el.name[0] == "h" and el.name[1:].isdigit():
                level = int(el.name[1:])
                heading_path = [*heading_path[: level - 1], text]
                sections.append(Section(text, "heading", None, tuple(heading_path)))
            else:
                kind: SectionKind = (
                    "list" if el.name == "li" else "code" if el.name == "pre" else "paragraph"
                )
                sections.append(Section(text, kind, None, tuple(heading_path)))
        if not sections:
            raise UnsupportedDocument("the HTML contains no readable text")
        title = soup.title.get_text(strip=True) if soup.title else None
        return ParsedDocument(title=title or None, sections=tuple(sections), page_count=None)
