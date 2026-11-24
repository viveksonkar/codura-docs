"""OcrEngine Port (specs/ports.md §3a, ADR-0016)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class OcrResult:
    text: str
    mean_confidence: float  # 0–100
    rotation_applied: int = 0


@runtime_checkable
class OcrEngine(Protocol):
    @property
    def available_languages(self) -> frozenset[str]: ...

    def recognize(self, image: object, languages: str, *, timeout_s: float) -> OcrResult:
        """``image`` is a ``PIL.Image.Image``. Unknown languages raise UnsupportedDocument."""
        ...


def parse_languages(spec: str) -> list[str]:
    """``"eng+hin"`` → ``["eng", "hin"]``."""
    return [part for part in spec.split("+") if part]
