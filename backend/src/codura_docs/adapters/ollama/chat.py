"""Ollama ChatModel over ``/api/chat`` NDJSON streaming (ADR-0018: Qwen3 with thinking disabled)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from codura_docs.adapters.ollama._http import (
    PROVIDER,
    make_client,
    map_status,
    map_transport_error,
    require_model,
)
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
from codura_docs.ports.errors import ProviderBadRequest, ProviderUnavailable


class ThinkFilter:
    """Remove ``<think>…</think>`` spans from a streamed text, even when tags straddle chunks."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self._buf = ""
        self._inside = False

    def feed(self, text: str) -> str:
        self._buf += text
        out: list[str] = []
        while self._buf:
            tag = self.CLOSE if self._inside else self.OPEN
            idx = self._buf.find(tag)
            if idx >= 0:
                if not self._inside:
                    out.append(self._buf[:idx])
                self._buf = self._buf[idx + len(tag) :]
                self._inside = not self._inside
                continue
            # Keep a tail that could be the start of a tag split across chunks.
            keep = 0
            for k in range(min(len(tag) - 1, len(self._buf)), 0, -1):
                if tag.startswith(self._buf[-k:]):
                    keep = k
                    break
            emit, self._buf = self._buf[: len(self._buf) - keep], self._buf[len(self._buf) - keep :]
            if not self._inside:
                out.append(emit)
            break
        return "".join(out)

    def flush(self) -> str:
        rest, self._buf = ("" if self._inside else self._buf), ""
        return rest


class OllamaChatModel:
    def __init__(
        self, profile: ModelProfile, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        if not profile.base_url:
            raise ValueError("ollama profile requires base_url")
        self._profile = profile
        self._client = make_client(profile.base_url, transport)

    @property
    def profile_name(self) -> str:
        return self._profile.id

    @property
    def is_local(self) -> bool:
        return True

    def _payload(self, messages: Sequence[ChatMessage], params: ChatParams) -> dict[str, Any]:
        options: dict[str, Any] = {
            **self._profile.extra,
            "temperature": params.temperature,
            "num_predict": params.max_output_tokens,
        }
        options.update(params.extra)
        if params.stop:
            options["stop"] = list(params.stop)
        payload: dict[str, Any] = {
            "model": self._profile.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "options": options,
        }
        if self._profile.disable_thinking:
            payload["think"] = False
        return payload

    async def stream(
        self, messages: Sequence[ChatMessage], params: ChatParams
    ) -> AsyncIterator[ChatEvent]:
        think = ThinkFilter()
        try:
            async with self._client.stream(
                "POST", "/api/chat", json=self._payload(messages, params)
            ) as resp:
                if resp.status_code >= 400:
                    raise map_status(
                        resp.status_code, (await resp.aread()).decode(errors="replace")
                    )
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if "error" in data:
                        raise ProviderBadRequest(PROVIDER, str(data["error"])[:300])
                    content = (data.get("message") or {}).get("content") or ""
                    # ``message.thinking`` (separate field) is deliberately ignored.
                    text = think.feed(content)
                    if text:
                        yield ChatDelta(text)
                    if data.get("done"):
                        tail = think.flush()
                        if tail:
                            yield ChatDelta(tail)
                        reason: FinishReason = (
                            "length" if data.get("done_reason") == "length" else "stop"
                        )
                        yield ChatDone(
                            reason,
                            Usage(data.get("prompt_eval_count"), data.get("eval_count")),
                            str(data.get("model") or self._profile.model),
                        )
                        return
        except httpx.HTTPError as exc:
            raise map_transport_error(exc) from exc
        raise ProviderUnavailable(PROVIDER, "stream ended without done=true")

    async def complete(
        self, messages: Sequence[ChatMessage], params: ChatParams
    ) -> tuple[str, ChatDone]:
        return await collect(self.stream(messages, params))

    async def health(self) -> None:
        await require_model(self._client, self._profile.model)

    async def aclose(self) -> None:
        await self._client.aclose()
