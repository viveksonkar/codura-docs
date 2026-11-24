"""Tesseract 5 OcrEngine via pytesseract (ADR-0016, Apache-2.0). Requires the tesseract binary."""

from __future__ import annotations

import pytesseract
from PIL import Image

from codura_docs.ports.errors import ParseError, UnsupportedDocument
from codura_docs.ports.ocr import OcrResult, parse_languages


class TesseractOcrEngine:
    def __init__(self) -> None:
        try:
            langs = pytesseract.get_languages(config="")
        except (pytesseract.TesseractNotFoundError, OSError) as exc:
            raise ImportError("tesseract binary is not installed") from exc
        self._languages = frozenset(lang for lang in langs if lang != "osd")

    @property
    def available_languages(self) -> frozenset[str]:
        return self._languages

    def _rotate(self, image: Image.Image) -> tuple[Image.Image, int]:
        try:
            osd = pytesseract.image_to_osd(image, output_type=pytesseract.Output.DICT, timeout=10)
        except (pytesseract.TesseractError, RuntimeError):
            return image, 0  # too little text for orientation detection: keep as is
        angle = int(osd.get("rotate", 0) or 0)
        return (image.rotate(-angle, expand=True), angle) if angle else (image, 0)

    def recognize(self, image: object, languages: str, *, timeout_s: float) -> OcrResult:
        wanted = parse_languages(languages)
        unknown = [lang for lang in wanted if lang not in self._languages]
        if not wanted or unknown:
            raise UnsupportedDocument(
                f"OCR language not installed: {', '.join(unknown) or '(none)'}"
            )
        if not isinstance(image, Image.Image):
            raise TypeError("TesseractOcrEngine expects a PIL image")
        gray, angle = self._rotate(image.convert("L"))
        try:
            data = pytesseract.image_to_data(
                gray,
                lang="+".join(wanted),
                config="--psm 3",
                output_type=pytesseract.Output.DICT,
                timeout=timeout_s,
            )
        except RuntimeError as exc:  # pytesseract raises RuntimeError on timeout
            raise ParseError(f"OCR timed out after {timeout_s}s") from exc
        except pytesseract.TesseractError as exc:
            raise ParseError(f"tesseract failed: {exc}") from exc

        lines: dict[tuple[int, int, int], list[str]] = {}
        confidences: list[float] = []
        for i, word in enumerate(data["text"]):
            word = (word or "").strip()
            conf = float(data["conf"][i])
            if not word or conf < 0:
                continue
            key = (int(data["block_num"][i]), int(data["par_num"][i]), int(data["line_num"][i]))
            lines.setdefault(key, []).append(word)
            confidences.append(conf)
        paragraphs: dict[tuple[int, int], list[str]] = {}
        for (block, par, _line), words in sorted(lines.items()):
            paragraphs.setdefault((block, par), []).append(" ".join(words))
        text = "\n\n".join("\n".join(par_lines) for _, par_lines in sorted(paragraphs.items()))
        mean = sum(confidences) / len(confidences) if confidences else 0.0
        return OcrResult(text=text, mean_confidence=mean, rotation_applied=angle)
