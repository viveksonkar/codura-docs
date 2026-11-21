"""PDF parser: text layer via pypdf, OCR per page via pypdfium2 render + OcrEngine (ADR-0016)."""

from __future__ import annotations

import io
from typing import BinaryIO

import pypdfium2 as pdfium
from pypdf import PdfReader
from pypdf.errors import FileNotDecryptedError, PdfReadError

from codura_docs.adapters.parsers._text import normalise, paragraphs, strip_repeated_lines
from codura_docs.ports.errors import ParseError, UnsupportedDocument
from codura_docs.ports.parser import ParsedDocument, ParseOptions, Section

MIME = "application/pdf"


class PdfParser:
    supported_mime_types = frozenset({MIME})

    def parse(
        self, data: BinaryIO, *, filename: str, mime_type: str, options: ParseOptions
    ) -> ParsedDocument:
        raw = data.read()
        try:
            reader = PdfReader(io.BytesIO(raw))
            if reader.is_encrypted:
                raise UnsupportedDocument("encrypted PDFs are not supported")
            pages = reader.pages
            page_count = len(pages)
        except FileNotDecryptedError as exc:
            raise UnsupportedDocument("encrypted PDFs are not supported") from exc
        except PdfReadError as exc:
            raise UnsupportedDocument(f"corrupt PDF: {exc}") from exc
        if page_count > options.max_pages:
            raise UnsupportedDocument(
                f"PDF has {page_count} pages; the limit is {options.max_pages}"
            )

        texts: list[str] = []
        for page in pages:
            try:
                texts.append(normalise(page.extract_text() or ""))
            except Exception:
                texts.append("")
        texts = strip_repeated_lines(texts)

        warnings: list[str] = []
        confidences: dict[int, float] = {}
        pdf = None
        try:
            for index, text in enumerate(texts):
                if len(text) < options.text_layer_min_chars:
                    if pdf is None:
                        pdf = pdfium.PdfDocument(raw)
                    image = pdf[index].render(scale=options.render_dpi / 72).to_pil()
                    result = options.ocr.recognize(
                        image, options.ocr_languages, timeout_s=options.page_timeout_s
                    )
                    texts[index] = normalise(result.text)
                    confidences[index] = result.mean_confidence
                    if result.mean_confidence < options.low_confidence_threshold:
                        warnings.append(f"low_ocr_confidence:p{index + 1}")
                if options.on_page_done:
                    options.on_page_done(index + 1, page_count)
        except (UnsupportedDocument, ParseError):
            raise
        except pdfium.PdfiumError as exc:
            raise UnsupportedDocument(f"cannot render PDF page: {exc}") from exc
        finally:
            if pdf is not None:
                pdf.close()

        title = None
        try:
            meta = reader.metadata
            title = (meta.get("/Title") if meta else None) or None
        except Exception:
            title = None
        sections = tuple(
            Section(
                text=para,
                kind="paragraph",
                page=i + 1,
                heading_path=(),
                ocr_confidence=confidences.get(i),
            )
            for i, text in enumerate(texts)
            for para in paragraphs(text)
        )
        if not sections:
            raise UnsupportedDocument("no text could be extracted (empty or unreadable PDF)")
        return ParsedDocument(
            title=str(title) if title else None,
            sections=sections,
            page_count=page_count,
            warnings=tuple(warnings),
        )
