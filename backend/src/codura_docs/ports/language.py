"""LanguageDetector Port (M1-16). Added so services never import a detection library directly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# Launch language set (NFR-13): BCP-47 code → (English name, Tesseract pack)
SUPPORTED_LANGUAGES: dict[str, tuple[str, str]] = {
    "en": ("English", "eng"),
    "hi": ("Hindi", "hin"),
    "es": ("Spanish", "spa"),
    "fr": ("French", "fra"),
    "de": ("German", "deu"),
    "ar": ("Arabic", "ara"),
    "zh": ("Chinese", "chi_sim"),
}


@dataclass(frozen=True)
class Detection:
    language: str | None  # BCP-47 code from SUPPORTED_LANGUAGES, or None if unsure
    confidence: float


@runtime_checkable
class LanguageDetector(Protocol):
    def detect(self, text: str) -> Detection: ...
