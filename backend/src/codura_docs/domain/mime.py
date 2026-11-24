"""Content-type sniffing by magic bytes, never by extension alone (SEC-03)."""

from __future__ import annotations

import io
import zipfile

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TXT = "text/plain"
MD = "text/markdown"
HTML = "text/html"
PNG = "image/png"
JPEG = "image/jpeg"
TIFF = "image/tiff"
WEBP = "image/webp"

SUPPORTED = frozenset({PDF, DOCX, TXT, MD, HTML, PNG, JPEG, TIFF, WEBP})
IMAGES = frozenset({PNG, JPEG, TIFF, WEBP})


def sniff(head: bytes, filename: str) -> str | None:
    """Return a supported MIME type for the first bytes of a file, or None if unsupported."""
    name = filename.lower()
    if head.startswith(b"%PDF-"):
        return PDF
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if head.startswith(b"\xff\xd8\xff"):
        return JPEG
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return TIFF
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return WEBP
    if head.startswith(b"PK\x03\x04"):
        return DOCX if name.endswith(".docx") else None  # verified by is_docx() on the full file
    if b"\x00" in head[:4096]:
        return None  # binary, not something we parse
    try:
        sample = head[:4096].decode("utf-8")
    except UnicodeDecodeError:
        # A multi-byte character may be cut at the boundary; retry without the last 3 bytes.
        try:
            sample = head[:4093].decode("utf-8")
        except UnicodeDecodeError:
            return None
    lowered = sample.lstrip().lower()
    if name.endswith((".html", ".htm")) or lowered.startswith(("<!doctype html", "<html")):
        return HTML
    if name.endswith((".md", ".markdown")):
        return MD
    return TXT


def is_docx(data: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            return "word/document.xml" in zf.namelist()
    except zipfile.BadZipFile:
        return False
