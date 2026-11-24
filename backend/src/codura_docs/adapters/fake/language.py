"""Script-based fake LanguageDetector for tests (no model download)."""

from __future__ import annotations

from codura_docs.ports.language import Detection


class FakeLanguageDetector:
    def detect(self, text: str) -> Detection:
        if not text.strip():
            return Detection(None, 0.0)
        if any("ऀ" <= c <= "ॿ" for c in text):
            return Detection("hi", 0.99)
        if any("؀" <= c <= "ۿ" for c in text):
            return Detection("ar", 0.99)
        if any("一" <= c <= "鿿" for c in text):
            return Detection("zh", 0.99)
        lowered = f" {text.lower()} "
        for code, words in (
            ("es", (" el ", " la ", " cuánto", " qué ")),
            ("fr", (" le ", " les ", " combien")),
            ("de", (" der ", " die ", " wie lange")),
        ):
            if any(w in lowered for w in words):
                return Detection(code, 0.8)
        return Detection("en", 0.9)
