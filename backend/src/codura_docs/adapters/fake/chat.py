"""FakeChatModel: deterministic chat for tests and offline runs (testing.md §3)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence

from codura_docs.ports.chat import (
    ChatDelta,
    ChatDone,
    ChatEvent,
    ChatMessage,
    ChatParams,
    FinishReason,
    Usage,
    collect,
)

Script = Callable[[Sequence[ChatMessage]], str]


def _echo(messages: Sequence[ChatMessage]) -> str:
    last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
    return f"You asked: {last_user} [1]"


class FakeChatModel:
    def __init__(
        self, profile_name: str = "fake", script: Script | None = None, *, delay_s: float = 0.0
    ) -> None:
        self._name = profile_name
        self._script = script or _echo
        self._delay = delay_s
        self.calls: list[Sequence[ChatMessage]] = []
        self.cancelled = False

    @property
    def profile_name(self) -> str:
        return self._name

    @property
    def is_local(self) -> bool:
        return True

    async def stream(
        self, messages: Sequence[ChatMessage], params: ChatParams
    ) -> AsyncIterator[ChatEvent]:
        self.calls.append(list(messages))
        words = self._script(messages).split(" ")
        limit = max(1, params.max_output_tokens)
        emitted = 0
        try:
            for i, word in enumerate(words[:limit]):
                if self._delay:
                    await asyncio.sleep(self._delay)
                yield ChatDelta(word if i == 0 else " " + word)
                emitted += 1
        except GeneratorExit:
            self.cancelled = True
            raise
        prompt_tokens = sum(len(m.content.split()) for m in messages)
        reason: FinishReason = "length" if len(words) > limit else "stop"
        yield ChatDone(reason, Usage(prompt_tokens, emitted), self._name)

    async def complete(
        self, messages: Sequence[ChatMessage], params: ChatParams
    ) -> tuple[str, ChatDone]:
        return await collect(self.stream(messages, params))

    async def health(self) -> None:
        return None
