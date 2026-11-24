"""Document parsers (M1-02, M1-15). ``all_parsers`` is the registry's discovery hook."""

from __future__ import annotations

from codura_docs.ports.parser import Parser


def all_parsers() -> list[Parser]:
    from codura_docs.adapters.parsers.docx import DocxParser
    from codura_docs.adapters.parsers.image import ImageParser
    from codura_docs.adapters.parsers.pdf import PdfParser
    from codura_docs.adapters.parsers.text import HtmlParser, TextParser

    return [PdfParser(), DocxParser(), TextParser(), HtmlParser(), ImageParser()]
