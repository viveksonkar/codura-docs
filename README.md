# Codura Docs

Upload documents, ask questions about them in any language. Codura Docs is a local-first,
multi-tenant RAG platform:

- **Admin side:** each Tenant's Admins upload PDFs, DOCX, text, and **scanned documents and photos**
  (OCR). The system builds that Tenant's searchable Knowledge Base in Postgres + pgvector.
- **Chat side:** Visitors chat with one Tenant's knowledge through a hosted chat page or a widget
  on any website (MVP), and later a WordPress plugin and an Android SDK. They get cited answers in
  the language they asked in.

Key properties:

- **Strict tenant isolation:** no Tenant can see another's data.
- **Free to run locally:** Ollama (`bge-m3` embeddings, Qwen 2.5 chat) and Tesseract OCR.
- **Switchable answering LLM:** move to OpenAI-compatible servers or Anthropic by config. The
  embedding model is fixed.
- **One command:** `docker compose up`.

## Status

Design phase. See [PROGRESS.md](./PROGRESS.md) and [docs/roadmap.md](./docs/roadmap.md).

## Repository map

| Path | What |
|---|---|
| `backend/` | Python API + ingestion/OCR worker (M0–M1) |
| `web/` | Admin Console, Hosted Chat Page, Widget, TS chat client (M1–M2) |
| `wordpress/` | WordPress plugin, local zip distribution (M3) |
| `android/` | Android SDK + sample app (M3) |
| `docs/` | Requirements, architecture, ADRs, specs, ledger |
| `CLAUDE.md` / `CONTEXT.md` | Agent instructions / glossary |

## Contributing (humans and agents)

Follow [docs/ledger/README.md](./docs/ledger/README.md): pick a Task (`/next-task`), read its spec,
work inside its owned paths, then close out (`/close-task`).
