"""FakeOcrEngine: returns fixture text; used to test parsers without Tesseract."""

from __future__ import annotations

from codura_docs.ports.errors import UnsupportedDocument
from codura_docs.ports.ocr import OcrResult, parse_languages


class FakeOcrEngine:
    def __init__(
        self,
        text: str = "Scanned page text.",
        confidence: float = 90.0,
        languages: frozenset[str] = frozenset(
            {"eng", "hin", "spa", "fra", "deu", "ara", "chi_sim"}
        ),
    ) -> None:
        self._text = text
        self._confidence = confidence
        self._languages = languages
        self.calls = 0

    @property
    def available_languages(self) -> frozenset[str]:
        return self._languages

    def recognize(self, image: object, languages: str, *, timeout_s: float) -> OcrResult:
        unknown = [lang for lang in parse_languages(languages) if lang not in self._languages]
        if unknown or not languages:
            raise UnsupportedDocument(
                f"OCR language not installed: {', '.join(unknown) or '(none)'}"
            )
        self.calls += 1
        return OcrResult(text=self._text, mean_confidence=self._confidence)
