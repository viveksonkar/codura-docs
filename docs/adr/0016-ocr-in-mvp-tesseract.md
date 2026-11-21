# ADR-0016 — OCR in the MVP: Tesseract behind an OcrEngine Port

- **Status:** Accepted
- **Date:** 2026-11-18
- **Amends:** ADR-0008 (OCR moved from optional M3 docling profile into M1)
- **Relates to:** FR-02, FR-03, FR-17, NFR-06, SEC-03

## Context

Product owner decision (2026-11-18): scanned documents must work in the MVP. This includes scanned
PDFs, PDFs with some scanned pages, and image files (PNG, JPEG, TIFF, WebP), in multiple languages.
It must run locally, free, on CPU.

## Decision

- New Port **`OcrEngine`** (`recognize(image, languages) -> OcrResult`), see specs/ports.md §4.
- Default Adapter: **Tesseract 5** (Apache-2.0) via `tesserocr`/`pytesseract`, installed in the
  backend image with language packs chosen by build arg `OCR_LANG_PACKS` (default
  `eng hin spa fra deu ara chi_sim`, `tessdata_fast`).
- PDF pages are rendered with **pypdfium2** (Apache-2.0/BSD) at 300 DPI. OCR runs **per page**
  only when the page's text layer is below a threshold (default 30 chars), so mixed PDFs work.
- Images are opened with Pillow (decompression-bomb limit on) and lightly preprocessed (EXIF
  rotation, grayscale; orientation via Tesseract OSD).
- OCR languages: a per-Tenant default (`tenants.ocr_languages`, e.g. `eng+hin`), with an optional
  override per upload. Pages with mean confidence below 60 are flagged as warnings on the Document.
- OCR text gets the same chunking and embedding as any other text; Citations keep page numbers.

## Alternatives considered

### docling as the default (previous ADR-0008 plan) — rejected for MVP
Better layout and table recovery, but a heavy PyTorch dependency that slows the image build and CPU
runs. It stays a candidate second `OcrEngine`/`Parser` Adapter in M4.

### Vision LLM via Ollama (e.g. Qwen-VL) — rejected as default
Very good on messy scans, but minutes per page on CPU and it can paraphrase text. Possible later as an
Adapter for GPU installs.

### OCRmyPDF — rejected
Good tool (MPL-2.0), but it produces a new PDF. We only need text per page, and calling
Tesseract directly gives us confidence values and control over each page.

### Cloud OCR (Google/Azure/Textract) — rejected
Costs money and sends data off the machine (NFR-01). Possible later as an opt-in Adapter.

## Consequences

- The backend image grows by about 150–250 MB with the language packs. Only `worker` needs them;
  we still keep one image (ADR-0013). Split it out later if image size starts to matter.
- OCR is the slowest ingestion step (around 2–5 s per page on CPU). It runs inside the Job with a
  per-page timeout. Progress (`pages_done/page_count`) is shown in the Admin Console.
- OCR quality depends on picking the right languages. A wrong Tenant language setting gives
  garbage text, so the Admin Console shows OCR warnings prominently.
