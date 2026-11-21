# ADR-0008 — Permissively licensed parsers behind a Parser Port

- **Status:** Accepted, amended by ADR-0016 (OCR moved into the MVP via Tesseract)
- **Date:** 2026-11-18
- **Relates to:** FR-02, FR-03, SEC-03, SEC-13

## Context

We need reliable text + structure (pages, headings) from PDF, DOCX, TXT, MD and HTML, possibly
OCR later. The product may be sold or hosted for clients, so licences matter.

## Decision

- `Parser` Port returns a `ParsedDocument` (specs/ports.md). Adapters selected by sniffed MIME type.
- Defaults: `pypdf` (BSD) with `pdfplumber` (MIT) for table-heavy pages, `python-docx` (MIT),
  `beautifulsoup4` (MIT) for HTML, built-in for TXT/MD.
- OCR/layout-aware parsing via **docling** (MIT) as an optional `ocr` compose profile in M3,
  run as a separate service so its heavy ML dependencies stay out of the core image.
- **No AGPL/GPL dependencies** in the backend image (e.g. PyMuPDF is AGPL — excluded).

## Alternatives considered

### PyMuPDF — rejected
Best speed/quality, but AGPL would oblige source release for network use of a modified stack, and
commercial licences cost money.

### `unstructured` as the default — rejected
Very heavy install; we want a small fast core image. Can be added as another Adapter.

### Apache Tika server — rejected for default
JVM container for something Python libraries cover; weaker structure output.

## Consequences

Small core image and clean licensing. Scanned PDFs aren't supported until M3 (flagged as
"possibly scanned" in the meantime). Parser quality is covered by fixture tests per type.
