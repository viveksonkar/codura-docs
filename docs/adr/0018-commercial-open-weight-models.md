# ADR-0018 — Only commercially licensed open-weight models; local default `qwen3:4b`

- **Status:** Accepted, amended by ADR-0024 (default tag is the non-thinking instruct variant; VRAM settings)
- **Date:** 2026-11-19
- **Relates to:** NFR-01, NFR-04, PROGRESS Q2, ADR-0004, ADR-0015, ADR-0019

## Context

Owner decision (2026-11-19): use open-source models that may be used commercially. Local
development runs on the owner's machine (GTX 1060 **6 GB VRAM**, i5-8400 6 cores, 64 GB RAM,
Windows 11). Production will use larger open models and, for some tiers, Anthropic/OpenAI.

The previous default `qwen2.5:3b` is published under the **`qwen-research`** licence (Hugging Face
model card, checked 2026-11-19). That licence does not allow commercial use, so it is excluded.

## Decision

1. **Licence allowlist** for any self-hosted model the platform serves: `apache-2.0`, `mit`
   (others need a new ADR). Each Model Profile records `license` and `license_url`. The registry
   refuses a self-hosted profile whose licence isn't on the allowlist. Remote commercial APIs
   (Anthropic, OpenAI) are `license: commercial-api` and are covered by their terms of service.
2. **Local defaults (6 GB GPU):**

   | Role | Model | Licence | Size (Q4) | Notes |
   |---|---|---|---|---|
   | Chat (default) | `qwen3:4b` | Apache-2.0 | 2.5 GB | Multilingual. **Thinking disabled** (Ollama `think: false`) for latency and cost |
   | Chat (quality, slower) | `qwen2.5:7b` | Apache-2.0 | ~4.7 GB | Partly offloads to CPU when bge-m3 is loaded; fine with 64 GB RAM |
   | Embeddings (fixed) | `bge-m3` | MIT | 1.2 GB | ADR-0015 |

   With `qwen3:4b` + `bge-m3` (≈ 3.7 GB) and `num_ctx 8192`, both stay resident in VRAM. Ollama
   env: `OLLAMA_MAX_LOADED_MODELS=2`, `OLLAMA_KEEP_ALIVE=30m`, `OLLAMA_NUM_PARALLEL=2`.
   **GPU profile is the default on this machine** (Docker Desktop + WSL2 + NVIDIA driver). CPU-only
   still works, just slower.
3. **Production candidates** (self-hosted via vLLM, or open-weight hosting providers): Qwen3
   14B / 32B / 30B-A3B (Apache-2.0), chosen per plan tier by the Platform Admin (ADR-0019).
   Anthropic/OpenAI profiles are allowed for premium tiers. Their cost is metered and priced in
   (ADR-0021).
4. Exact Ollama tags are verified when the compose stack is built (M0-01) and pinned in
   `.env.example`.

## Alternatives considered

### `qwen2.5:3b` — rejected
The `qwen-research` licence is not commercial.

### Llama 3.x / Gemma — rejected for the default
Custom licences with use restrictions that aren't OSI open source. Each would need legal review
per deployment.

### `qwen3:8b` locally — rejected for the default
5.2 GB doesn't fit next to bge-m3 in 6 GB VRAM, so it would spill to CPU on every request.

### `phi4-mini` — kept as an option
Comparable size, but its multilingual quality and licence still need checking. It can be added as
a profile after confirming the model card licence.

## Consequences

Model choice becomes a controlled, auditable list instead of whatever happens to be pulled. Qwen3's
thinking mode has to be explicitly disabled in the Ollama adapter (contract test). The quality
gap between local 4B and production models is expected and measured by the eval set per profile.
