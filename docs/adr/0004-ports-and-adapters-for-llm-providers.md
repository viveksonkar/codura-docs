# ADR-0004 — Ports & Adapters for LLM Providers; Ollama default

- **Status:** Accepted, amended by ADR-0019 (Model Profiles are platform-managed in the DB; Tenants don't choose)
- **Date:** 2026-11-18
- **Relates to:** G-1, G-2, FR-20…23, NFR-01, SEC-11

## Context

The brief: run everything locally with Ollama to avoid LLM costs, and "design in such a way that
tomorrow if I need to switch to different LLM I should be able to switch easily." Chat and
embeddings may switch independently (e.g. remote chat, local embeddings).

## Decision

- Define small, provider-neutral Ports in `codura_docs/ports/` (frozen in
  [specs/ports.md](../specs/ports.md)): `ChatModel` (`stream`, `complete`), `EmbeddingModel`
  (`embed`, `dimension`, `model_id`), plus `Parser`, `OcrEngine`, `BlobStore`, `Reranker`.
- Adapters in `codura_docs/adapters/<provider>/` implement them using plain `httpx` against the
  Provider's HTTP API where practical (fewer SDK upgrades), mapping errors to `ProviderError`
  subclasses.
- Ship `ollama` (M0), `openai_compat` (M1) and `anthropic` (M4) Adapters, plus `fake` for tests.
- Model Profiles in config map a name → (provider, model, params). Assistants store the profile
  name. `registry.py` builds Adapters from config; nothing else knows which Provider is in use.
- import-linter forbids vendor imports outside `adapters/`. Each Adapter passes the shared
  contract suite (testing.md §2).

## Alternatives considered

### LangChain / LlamaIndex as the core abstraction — rejected
Large, fast-moving APIs; they'd become the de-facto domain model and their upgrades our
migrations. Our needs (stream a chat completion, embed a batch) are a few dozen lines per
Provider. We may still borrow individual utilities (text splitters) inside Adapters or domain code.

### LiteLLM as a required proxy — rejected as a requirement, supported as a target
An extra container and a dependency on its model naming. Because LiteLLM speaks the OpenAI
protocol, anyone who wants it can point an `openai_compat` profile at it.

### Call Ollama directly, refactor later — rejected
"Later" means every call site; the Port costs almost nothing now.

## Consequences

- Switching chat model = edit/choose a Model Profile. Switching embedding model = config + rebuild
  (ADR-0005). No code changes either way.
- We own a little Adapter code per Provider and must keep contract tests running against them
  (nightly for remote ones).
- Provider-specific features (tool calling, JSON mode) are only exposed if expressible in the Port.
