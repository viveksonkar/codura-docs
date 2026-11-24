"""lingua-based LanguageDetector restricted to the launch set (M1-16, Apache-2.0)."""

from __future__ import annotations

from collections.abc import Sequence

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


def pick(
    ranked: Sequence[tuple[str | None, float]],
    *,
    min_confidence: float,
    min_lead_confidence: float,
    min_lead: float,
) -> Detection:
    """Accept the top language if it is confident, or clearly ahead of the runner-up.

    Short questions spread confidence across related languages ("And how do I reset it?" scores
    en 0.50 / de 0.36). Rejecting those would answer in the previous turn's language although the
    Visitor switched (rag-pipeline.md §3), so a clear lead also counts.
    """
    if not ranked:
        return Detection(None, 0.0)
    code, value = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    if code is None:
        return Detection(None, value)
    if value >= min_confidence:
        return Detection(code, value)
    if value >= min_lead_confidence and value - runner_up >= min_lead:
        return Detection(code, value)
    return Detection(None, value)


class LinguaDetector:
    def __init__(
        self,
        min_confidence: float = 0.5,
        min_lead_confidence: float = 0.35,
        min_lead: float = 0.1,
    ) -> None:
        self._detector = LanguageDetectorBuilder.from_languages(*_MAP).build()
        self._min = min_confidence
        self._lead_min = min_lead_confidence
        self._lead = min_lead

    def detect(self, text: str) -> Detection:
        sample = text.strip()[:2000]
        if not sample:
            return Detection(None, 0.0)
        values = self._detector.compute_language_confidence_values(sample)
        ranked = [(_MAP.get(v.language), v.value) for v in values]
        return pick(
            ranked,
            min_confidence=self._min,
            min_lead_confidence=self._lead_min,
            min_lead=self._lead,
        )
