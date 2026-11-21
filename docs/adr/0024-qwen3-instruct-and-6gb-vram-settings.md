# ADR-0024 — Local default is `qwen3:4b-instruct-2507`; Ollama tuned for a 6 GB GPU

- **Status:** Accepted
- **Date:** 2026-11-19
- **Amends:** ADR-0018 (local default chat model and Ollama settings)
- **Relates to:** NFR-04, ADR-0015

## Context

Measured on the dev box (GTX 1060 6 GB, Ollama 0.12.6) while closing M0:

- The `qwen3:4b` tag is the **Qwen3-4B-Thinking-2507** model (262k context). With `think: false`
  it still produced about 400 reasoning tokens ending in a lone `</think>`. That took 49.8 s for a
  one-line reply.
- At `num_ctx` 8192 with 2 parallel slots, it used 5.4 GB and ran 25 % on CPU. That evicted
  `bge-m3`, so every question reloaded a model.

## Decision

- Local default chat model: **`qwen3:4b-instruct-2507-q4_K_M`** (Apache-2.0, non-thinking),
  `num_ctx` 4096. `disable_thinking` stays on as a guard against stray tags.
- Ollama on 6 GB GPUs: `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_FLASH_ATTENTION=1`,
  `OLLAMA_KV_CACHE_TYPE=q8_0`, `OLLAMA_MAX_LOADED_MODELS=2`. The embedding adapter requests
  `num_ctx` 1024 for `bge-m3`, since chunks are at most 512 tokens plus a header.
- Result (measured): chat 3.3 GB and `bge-m3` 1.6 GB, both 100 % GPU. A short answer takes about
  1–2 s; the golden eval has a median of 1.7 s per answer.

## Alternatives considered

### Keep `qwen3:4b` and strip reasoning — rejected
The tokens are still generated (latency, GPU time), and a lone closing tag can't be streamed
safely.

### `qwen2.5:3b` — rejected
Non-commercial `qwen-research` licence (ADR-0018).

## Consequences

Bigger GPUs should raise `OLLAMA_NUM_PARALLEL` for concurrency. Production uses larger models
through Model Profiles (ADR-0019), not these local settings.
