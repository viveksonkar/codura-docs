"""Anthropic ChatModel over the Messages API (FR-22, ADR-0004; M3-12).

Plain httpx against ``POST /v1/messages`` with SSE streaming, as the M3-12 spec requires (no SDK).
API version ``2026-10-21``: sampling parameters (``temperature``…) are rejected, so they are never
sent. Thinking can't be disabled on current models; depth is set with ``output_config.effort``
(profile param ``effort``, default ``low`` for short grounded answers). Only ``text_delta``
content is forwarded; thinking blocks are ignored.

Profile params (``model_profiles.params``):
- ``effort``: low | medium | high | xhigh | max
- ``prompt_caching``: bool, adds ``cache_control`` to the system block
- ``fallbacks``: bool (default true), server-side refusal fallbacks
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

PROVIDER = "anthropic"
API_VERSION = "2026-10-21"
DEFAULT_BASE_URL = "https://api.anthropic.com"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
_FINISH: dict[str, FinishReason] = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "refusal": "error",
    "pause_turn": "stop",
}


def _retry_after(headers: httpx.Headers) -> float | None:
    value = headers.get("retry-after")
    try:
        return float(value) if value else None
    except ValueError:
        return None


def map_error(status: int, body: str, headers: httpx.Headers) -> ProviderError:
    try:
        detail = json.loads(body).get("error", {}).get("message") or body[:300]
    except (ValueError, AttributeError):
        detail = body[:300]
    if status in (401, 403):
        return ProviderAuthError(PROVIDER, str(detail))
    if status == 429:
        return ProviderRateLimited(PROVIDER, str(detail), _retry_after(headers))
    if status == 404:
        return ProviderUnavailable(PROVIDER, f"model or endpoint not found: {detail}")
    if status in (400, 413, 422):
        return ProviderBadRequest(PROVIDER, str(detail))
    return ProviderUnavailable(PROVIDER, f"HTTP {status}: {detail}")  # 5xx, 529 overloaded


def _stream_error(data: dict[str, Any]) -> ProviderError:
    err = data.get("error") or {}
    kind, message = str(err.get("type", "")), str(err.get("message", "stream error"))
    if kind == "rate_limit_error":
        return ProviderRateLimited(PROVIDER, message, None)
    if kind in ("authentication_error", "permission_error"):
        return ProviderAuthError(PROVIDER, message)
    if kind == "invalid_request_error":
        return ProviderBadRequest(PROVIDER, message)
    return ProviderUnavailable(PROVIDER, f"{kind}: {message}")  # overloaded_error, api_error


class AnthropicChatModel:
    def __init__(
        self, profile: ModelProfile, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        if not profile.api_key:
            raise ValueError("anthropic profile requires an API key (encrypted credentials)")
        self._profile = profile
        self._fallbacks = bool(profile.extra.get("fallbacks", True))
        headers = {
            "x-api-key": profile.api_key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }
        if self._fallbacks:
            headers["anthropic-beta"] = FALLBACK_BETA
        self._client = httpx.AsyncClient(
            base_url=(profile.base_url or DEFAULT_BASE_URL).rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0),
            transport=transport,
        )

    @property
    def profile_name(self) -> str:
        return self._profile.id

    @property
    def is_local(self) -> bool:
        return False

    def payload(self, messages: Sequence[ChatMessage], params: ChatParams) -> dict[str, Any]:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        turns = [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]
        body: dict[str, Any] = {
            "model": self._profile.model,
            "max_tokens": params.max_output_tokens,
            "messages": turns,
            "stream": True,
            "output_config": {"effort": str(self._profile.extra.get("effort", "low"))},
        }
        if system:
            block: dict[str, Any] = {"type": "text", "text": system}
            if self._profile.extra.get("prompt_caching"):
                block["cache_control"] = {"type": "ephemeral"}
            body["system"] = [block]
        if params.stop:
            body["stop_sequences"] = list(params.stop)
        if self._fallbacks:
            body["fallbacks"] = "default"
        return body

    async def stream(
        self, messages: Sequence[ChatMessage], params: ChatParams
    ) -> AsyncIterator[ChatEvent]:
        finish: FinishReason = "stop"
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        model = self._profile.model
        event_name = ""
        try:
            async with self._client.stream(
                "POST", "/v1/messages", json=self.payload(messages, params)
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode(errors="replace")
                    raise map_error(resp.status_code, body, resp.headers)
                async for line in resp.aiter_lines():
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = json.loads(line[5:].strip())
                    kind = data.get("type") or event_name
                    if kind == "message_start":
                        msg = data.get("message") or {}
                        model = msg.get("model") or model
                        u = msg.get("usage") or {}
                        prompt_tokens = (
                            sum(
                                int(u.get(k) or 0)
                                for k in (
                                    "input_tokens",
                                    "cache_creation_input_tokens",
                                    "cache_read_input_tokens",
                                )
                            )
                            or None
                        )
                    elif kind == "content_block_delta":
                        delta = data.get("delta") or {}
                        if delta.get("type") == "text_delta" and delta.get("text"):
                            yield ChatDelta(str(delta["text"]))
                    elif kind == "message_delta":
                        reason = (data.get("delta") or {}).get("stop_reason")
                        if reason:
                            finish = _FINISH.get(str(reason), "stop")
                        out = (data.get("usage") or {}).get("output_tokens")
                        if out is not None:
                            completion_tokens = int(out)
                    elif kind == "error":
                        raise _stream_error(data)
                    elif kind == "message_stop":
                        break
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(PROVIDER, f"{type(exc).__name__}: {exc}") from exc
        yield ChatDone(finish, Usage(prompt_tokens, completion_tokens), str(model))

    async def complete(
        self, messages: Sequence[ChatMessage], params: ChatParams
    ) -> tuple[str, ChatDone]:
        return await collect(self.stream(messages, params))

    async def health(self) -> None:
        """Cheap check: the key is valid and the model id exists (GET /v1/models/{id})."""
        try:
            resp = await self._client.get(f"/v1/models/{self._profile.model}", timeout=10.0)
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(PROVIDER, f"{type(exc).__name__}: {exc}") from exc
        if resp.status_code >= 400:
            raise map_error(resp.status_code, resp.text, resp.headers)

    async def aclose(self) -> None:
        await self._client.aclose()
