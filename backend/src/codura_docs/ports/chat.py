"""ChatModel Port (specs/ports.md §1) and the platform-managed ModelProfile it is built from."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

Role = Literal["system", "user", "assistant"]
FinishReason = Literal["stop", "length", "cancelled", "error"]


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str


@dataclass(frozen=True)
class ChatParams:
    temperature: float = 0.1
    max_output_tokens: int = 800
    stop: tuple[str, ...] = ()
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChatDelta:
    text: str


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int | None
    completion_tokens: int | None


@dataclass(frozen=True)
class ChatDone:
    finish_reason: FinishReason
    usage: Usage
    model: str


ChatEvent = ChatDelta | ChatDone


@dataclass(frozen=True)
class ModelProfile:
    """A platform-managed chat model (ADR-0019). Built from a DB row or a seed entry."""

    id: str
    provider: Literal["ollama", "openai_compat", "anthropic", "fake"]
    model: str
    license: Literal["apache-2.0", "mit", "commercial-api"]
    is_local: bool
    base_url: str | None = None
    api_key: str | None = field(default=None, repr=False)
    temperature: float = 0.1
    max_output_tokens: int = 800
    disable_thinking: bool = False
    price_in_per_mtok_micros: int = 0
    price_out_per_mtok_micros: int = 0
    extra: Mapping[str, Any] = field(default_factory=dict)

    def default_params(self) -> ChatParams:
        return ChatParams(temperature=self.temperature, max_output_tokens=self.max_output_tokens)


@runtime_checkable
class ChatModel(Protocol):
    @property
    def profile_name(self) -> str: ...

    @property
    def is_local(self) -> bool: ...

    def stream(
        self, messages: Sequence[ChatMessage], params: ChatParams
    ) -> AsyncIterator[ChatEvent]:
        """Yield zero or more ChatDelta then exactly one ChatDone. ``aclose`` cancels upstream."""
        ...

    async def complete(
        self, messages: Sequence[ChatMessage], params: ChatParams
    ) -> tuple[str, ChatDone]: ...

    async def health(self) -> None:
        """Raise ProviderUnavailable when the model cannot serve requests."""
        ...


async def collect(stream: AsyncIterator[ChatEvent]) -> tuple[str, ChatDone]:
    """Helper to implement ``complete`` on top of ``stream``."""
    parts: list[str] = []
    done: ChatDone | None = None
    async for event in stream:
        if isinstance(event, ChatDelta):
            parts.append(event.text)
        else:
            done = event
    if done is None:
        raise RuntimeError("chat stream ended without ChatDone")
    return "".join(parts), done
