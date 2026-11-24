"""Answer Language rule (rag-pipeline.md §3, FR-18) and per-language text selection (FR-19)."""

from __future__ import annotations

import re
from collections.abc import Mapping

from codura_docs.ports.language import SUPPORTED_LANGUAGES, Detection

_WORDS = re.compile(r"\w+", re.UNICODE)
_NON_LATIN = re.compile(r"[؀-ۿऀ-ॿ一-鿿]")


def is_ambiguous(text: str) -> bool:
    """Very short or number-only questions cannot be language-detected reliably."""
    words = _WORDS.findall(text)
    if _NON_LATIN.search(text):
        return False  # script alone is a strong signal
    return len(words) <= 3 or all(w.isdigit() for w in words)


def answer_language(
    question: str, detection: Detection, *, conversation_last: str | None, assistant_default: str
) -> str:
    if not is_ambiguous(question) and detection.language in SUPPORTED_LANGUAGES:
        return detection.language
    if conversation_last in SUPPORTED_LANGUAGES:
        return conversation_last
    if detection.language in SUPPORTED_LANGUAGES:
        return detection.language
    return assistant_default if assistant_default in SUPPORTED_LANGUAGES else "en"


def language_name(code: str) -> str:
    return SUPPORTED_LANGUAGES.get(code, ("English", "eng"))[0]


def localized(texts: Mapping[str, str], language: str, fallback: str) -> str:
    """Pick ``texts[language]``, else ``texts['default']``, else ``fallback``."""
    return texts.get(language) or texts.get("default") or fallback


def ocr_languages_valid(spec: str, available: frozenset[str]) -> bool:
    parts = [p for p in spec.split("+") if p]
    return bool(parts) and all(p in available for p in parts)
