"""Ollama adapters against a mocked HTTP transport: streaming, thinking removal, errors."""

from __future__ import annotations

import json

import httpx
import pytest

from codura_docs.adapters.ollama.chat import OllamaChatModel, ThinkFilter
from codura_docs.adapters.ollama.embedding import OllamaEmbeddingModel
from codura_docs.ports.chat import ChatDelta, ChatDone, ChatMessage, ChatParams, ModelProfile
from codura_docs.ports.errors import ProviderBadRequest, ProviderUnavailable

PROFILE = ModelProfile(
    id="local-default",
    provider="ollama",
    model="qwen3:4b",
    license="apache-2.0",
    is_local=True,
    base_url="http://ollama:11434",
    disable_thinking=True,
    extra={"num_ctx": 8192},
)


def ndjson(*objs: dict[str, object]) -> bytes:
    return b"".join(json.dumps(o).encode() + b"\n" for o in objs)


@pytest.mark.parametrize(
    "chunks",
    [
        ["<think>plan</think>Hello", " world"],
        ["<thi", "nk>secret plan</th", "ink>Hello world"],
        ["Hello", " <think>x</think>world"],
    ],
)
def test_think_filter_removes_thinking_across_chunks(chunks: list[str]) -> None:
    f = ThinkFilter()
    out = "".join(f.feed(c) for c in chunks) + f.flush()
    assert out.replace("  ", " ") == "Hello world"
    assert "plan" not in out


async def test_chat_stream_sends_think_false_and_reports_usage() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        body = ndjson(
            {"message": {"role": "assistant", "content": "", "thinking": "hidden reasoning"}},
            {"message": {"role": "assistant", "content": "Namaste"}},
            {"message": {"role": "assistant", "content": " दुनिया"}},
            {
                "done": True,
                "done_reason": "stop",
                "model": "qwen3:4b",
                "prompt_eval_count": 12,
                "eval_count": 3,
            },
        )
        return httpx.Response(200, content=body)

    model = OllamaChatModel(PROFILE, transport=httpx.MockTransport(handler))
    events = [
        e async for e in model.stream([ChatMessage("user", "hi")], ChatParams(max_output_tokens=50))
    ]
    assert seen["think"] is False
    assert seen["options"] == {"num_ctx": 8192, "temperature": 0.1, "num_predict": 50}
    text = "".join(e.text for e in events if isinstance(e, ChatDelta))
    assert text == "Namaste दुनिया"
    assert "hidden" not in text
    done = events[-1]
    assert isinstance(done, ChatDone)
    assert (done.usage.prompt_tokens, done.usage.completion_tokens) == (12, 3)


async def test_chat_maps_missing_model_and_connection_errors() -> None:
    missing = OllamaChatModel(
        PROFILE,
        transport=httpx.MockTransport(
            lambda r: httpx.Response(404, json={"error": "model 'qwen3:4b' not found"})
        ),
    )
    with pytest.raises(ProviderUnavailable, match="not available"):
        await missing.complete([ChatMessage("user", "hi")], ChatParams())

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    down = OllamaChatModel(PROFILE, transport=httpx.MockTransport(refuse))
    with pytest.raises(ProviderUnavailable, match="ConnectError"):
        await down.complete([ChatMessage("user", "hi")], ChatParams())


async def test_embedding_batches_normalises_and_checks_dimension() -> None:
    batches: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["input"]
        batches.append(len(texts))
        return httpx.Response(200, json={"embeddings": [[3.0, 4.0] for _ in texts]})

    model = OllamaEmbeddingModel(
        "http://ollama:11434", "bge-m3", 2, max_batch=2, transport=httpx.MockTransport(handler)
    )
    vectors = await model.embed(["a", "b", "c"], "document")
    assert batches == [2, 1]
    assert vectors[0] == pytest.approx([0.6, 0.8])

    wrong = OllamaEmbeddingModel(
        "http://ollama:11434",
        "bge-m3",
        1024,
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"embeddings": [[1.0, 0.0]]})
        ),
    )
    with pytest.raises(ProviderBadRequest, match="expected 1024"):
        await wrong.embed(["a"], "query")


async def test_health_requires_pulled_model() -> None:
    tags = {"models": [{"name": "bge-m3:latest"}, {"name": "qwen3:4b"}]}
    model = OllamaEmbeddingModel(
        "http://ollama:11434",
        "bge-m3",
        1024,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=tags)),
    )
    await model.health()
    other = OllamaChatModel(
        ModelProfile(**{**PROFILE.__dict__, "model": "qwen2.5:7b"}),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=tags)),
    )
    with pytest.raises(ProviderUnavailable, match="not pulled"):
        await other.health()
