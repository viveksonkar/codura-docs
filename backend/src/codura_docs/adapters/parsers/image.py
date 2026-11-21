"""Image parser: PNG/JPEG/TIFF (multi-page)/WebP → OCR per frame (ADR-0016, SEC-16)."""

from __future__ import annotations

from typing import BinaryIO

from PIL import Image, ImageOps, UnidentifiedImageError

from codura_docs.adapters.parsers._text import normalise, paragraphs
from codura_docs.ports.errors import UnsupportedDocument
from codura_docs.ports.parser import ParsedDocument, ParseOptions, Section

Image.MAX_IMAGE_PIXELS = 60_000_000  # decompression-bomb guard (~7700x7700)
MIMES = frozenset({"image/png", "image/jpeg", "image/tiff", "image/webp"})


class ImageParser:
    supported_mime_types = MIMES

    def parse(
        self, data: BinaryIO, *, filename: str, mime_type: str, options: ParseOptions
    ) -> ParsedDocument:
        try:
            img = Image.open(data)
            frames = getattr(img, "n_frames", 1)
        except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
            raise UnsupportedDocument(f"unreadable image: {exc}") from exc
        if frames > options.max_pages:
            raise UnsupportedDocument(f"image has {frames} pages; the limit is {options.max_pages}")
        sections: list[Section] = []
        warnings: list[str] = []
        for index in range(frames):
            try:
                img.seek(index)
                frame = ImageOps.exif_transpose(img.convert("RGB"))
            except (OSError, Image.DecompressionBombError) as exc:
                raise UnsupportedDocument(f"unreadable image page {index + 1}: {exc}") from exc
            # Upscale small images (phone crops) so Tesseract sees ~300 DPI glyph sizes.
            if frame.width < 1200:
                scale = 1200 / frame.width
                frame = frame.resize((int(frame.width * scale), int(frame.height * scale)))
            result = options.ocr.recognize(
                frame, options.ocr_languages, timeout_s=options.page_timeout_s
            )
            if result.mean_confidence < options.low_confidence_threshold:
                warnings.append(f"low_ocr_confidence:p{index + 1}")
            sections += [
                Section(
                    text=p,
                    kind="paragraph",
                    page=index + 1,
                    heading_path=(),
                    ocr_confidence=result.mean_confidence,
                )
                for p in paragraphs(normalise(result.text))
            ]
            if options.on_page_done:
                options.on_page_done(index + 1, frames)
        if not sections:
            raise UnsupportedDocument("no text was recognised in the image")
        return ParsedDocument(
            title=None, sections=tuple(sections), page_count=frames, warnings=tuple(warnings)
        )
