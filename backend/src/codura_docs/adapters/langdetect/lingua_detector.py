"""lingua-based LanguageDetector restricted to the launch set (M1-16, Apache-2.0)."""

from __future__ import annotations

from lingua import Language, LanguageDetectorBuilder

from codura_docs.ports.language import Detection

_MAP = {
    Language.ENGLISH: "en",
    Language.HINDI: "hi",
    Language.SPANISH: "es",
    Language.FRENCH: "fr",
    Language.GERMAN: "de",
    Language.ARABIC: "ar",
    Language.CHINESE: "zh",
}


class LinguaDetector:
    def __init__(self, min_confidence: float = 0.5) -> None:
        self._detector = LanguageDetectorBuilder.from_languages(*_MAP).build()
        self._min = min_confidence

    def detect(self, text: str) -> Detection:
        sample = text.strip()[:2000]
        if not sample:
            return Detection(None, 0.0)
        values = self._detector.compute_language_confidence_values(sample)
        if not values:
            return Detection(None, 0.0)
        best = values[0]
        code = _MAP.get(best.language)
        if code is None or best.value < self._min:
            return Detection(None, best.value)
        return Detection(code, best.value)
