"""OpenAI-compatible ChatModel (OpenAI, vLLM, LM Studio, Groq, LiteLLM proxy…), M1-09.

Plain httpx against ``/chat/completions`` with SSE streaming; no vendor SDK (ADR-0004).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from codura_docs.ports.chat import (
    ChatDelta,
    ChatDone,
    ChatEvent,
    ChatMessage,
    ChatParams,
    FinishReason,
    ModelProfile,
    Usage,
    collect,
)
from codura_docs.ports.errors import (
    ProviderAuthError,
    ProviderBadRequest,
    ProviderError,
    ProviderRateLimited,
    ProviderUnavailable,
)

PROVIDER = "openai_compat"


def _map(status: int, body: str, headers: httpx.Headers) -> ProviderError:
    detail = body[:300]
    if status in (401, 403):
        return ProviderAuthError(PROVIDER, detail)
    if status == 429:
        retry = headers.get("retry-after")
        return ProviderRateLimited(
            PROVIDER, detail, float(retry) if retry and retry.isdigit() else None
        )
    if status == 404:
        return ProviderUnavailable(PROVIDER, f"model or endpoint not found: {detail}")
    if 400 <= status < 500:
        return ProviderBadRequest(PROVIDER, detail)
    return ProviderUnavailable(PROVIDER, f"HTTP {status}: {detail}")


class OpenAICompatChatModel:
    def __init__(
        self, profile: ModelProfile, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        if not profile.base_url:
            raise ValueError("openai_compat profile requires base_url")
        self._profile = profile
        headers = {"Authorization": f"Bearer {profile.api_key}"} if profile.api_key else {}
        self._client = httpx.AsyncClient(
            base_url=profile.base_url.rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=10.0),
            transport=transport,
        )

    @property
    def profile_name(self) -> str:
        return self._profile.id

    @property
    def is_local(self) -> bool:
        return self._profile.is_local

    def _payload(self, messages: Sequence[ChatMessage], params: ChatParams) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._profile.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": params.temperature,
            "max_tokens": params.max_output_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
            **{k: v for k, v in self._profile.extra.items() if k not in {"num_ctx"}},
        }
        if params.stop:
            payload["stop"] = list(params.stop)
        return payload

    async def stream(
        self, messages: Sequence[ChatMessage], params: ChatParams
    ) -> AsyncIterator[ChatEvent]:
        finish: FinishReason = "stop"
        usage = Usage(None, None)
        model = self._profile.model
        try:
            async with self._client.stream(
                "POST", "/chat/completions", json=self._payload(messages, params)
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode(errors="replace")
                    raise _map(resp.status_code, body, resp.headers)
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    event = json.loads(data)
                    model = event.get("model") or model
                    if event.get("usage"):
                        u = event["usage"]
                        usage = Usage(u.get("prompt_tokens"), u.get("completion_tokens"))
                    for choice in event.get("choices") or []:
                        text = (choice.get("delta") or {}).get("content")
                        if text:
                            yield ChatDelta(text)
                        if choice.get("finish_reason") == "length":
                            finish = "length"
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(PROVIDER, f"{type(exc).__name__}: {exc}") from exc
        yield ChatDone(finish, usage, str(model))

    async def complete(
        self, messages: Sequence[ChatMessage], params: ChatParams
    ) -> tuple[str, ChatDone]:
        return await collect(self.stream(messages, params))

    async def health(self) -> None:
        try:
            resp = await self._client.get("/models", timeout=5.0)
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(PROVIDER, f"{type(exc).__name__}: {exc}") from exc
        if resp.status_code >= 400:
            raise _map(resp.status_code, resp.text, resp.headers)

    async def aclose(self) -> None:
        await self._client.aclose()
