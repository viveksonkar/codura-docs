"""Anthropic adapter specifics beyond the shared ChatModel contract (M3-12)."""

from __future__ import annotations

import json

import httpx
import pytest

from codura_docs.adapters.anthropic.chat import API_VERSION, AnthropicChatModel
from codura_docs.ports.chat import ChatMessage, ChatParams, ModelProfile
from codura_docs.ports.errors import (
    ProviderAuthError,
    ProviderBadRequest,
    ProviderRateLimited,
    ProviderUnavailable,
)

PROMPT = [ChatMessage("system", "Answer only from the context."), ChatMessage("user", "Hi")]


def _profile(**extra: object) -> ModelProfile:
    return ModelProfile(
        id="premium",
        provider="anthropic",
        model="claude-opus-5-5",
        license="commercial-api",
        is_local=False,
        api_key="sk-ant-test",
        extra=extra,
    )


def _model(handler: object, **extra: object) -> AnthropicChatModel:
    return AnthropicChatModel(_profile(**extra), transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


async def test_request_shape_has_no_sampling_params_and_caches_system() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        sse = (
            'event: message_start\ndata: {"type":"message_start","message":{"model":"claude-opus-5-5",'
            '"usage":{"input_tokens":10,"cache_read_input_tokens":5}}}\n\n'
            'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"max_tokens"},'
            '"usage":{"output_tokens":3}}\n\nevent: message_stop\ndata: {"type":"message_stop"}\n\n'
        )
        return httpx.Response(200, content=sse.encode())

    model = _model(handler, prompt_caching=True, effort="medium")
    text, done = await model.complete(PROMPT, ChatParams(temperature=0.7, max_output_tokens=50))
    await model.aclose()
    body = seen["body"]
    assert isinstance(body, dict)
    assert "temperature" not in body  # rejected by API version 2026-10-21
    assert body["max_tokens"] == 50
    assert body["output_config"] == {"effort": "medium"}
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["messages"] == [{"role": "user", "content": "Hi"}]
    assert body["fallbacks"] == "default"
    headers = seen["headers"]
    assert isinstance(headers, dict)
    assert headers["anthropic-version"] == API_VERSION
    assert headers["x-api-key"] == "sk-ant-test"
    assert text == ""
    assert done.finish_reason == "length"
    assert done.usage.prompt_tokens == 15
    assert done.usage.completion_tokens == 3


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, ProviderAuthError),
        (429, ProviderRateLimited),
        (400, ProviderBadRequest),
        (529, ProviderUnavailable),
        (500, ProviderUnavailable),
    ],
)
async def test_http_errors_map_to_port_errors(status: int, error: type[Exception]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            json={"type": "error", "error": {"type": "x", "message": "nope"}},
            headers={"retry-after": "7"},
        )

    model = _model(handler)
    with pytest.raises(error) as info:
        await model.complete(PROMPT, ChatParams())
    await model.aclose()
    if status == 429:
        assert info.value.retry_after_s == 7.0  # type: ignore[attr-defined]


async def test_mid_stream_overloaded_error_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        sse = (
            'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hi"}}\n\n'
            'event: error\ndata: {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}\n\n'
        )
        return httpx.Response(200, content=sse.encode())

    model = _model(handler)
    with pytest.raises(ProviderUnavailable):
        await model.complete(PROMPT, ChatParams())
    await model.aclose()


def test_requires_credentials() -> None:
    no_key = ModelProfile(
        id="p", provider="anthropic", model="m", license="commercial-api", is_local=False
    )
    with pytest.raises(ValueError, match="API key"):
        AnthropicChatModel(no_key)
