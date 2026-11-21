"""ChatModel contract (specs/ports.md §1, testing.md §2)."""

from __future__ import annotations

from codura_docs.ports.chat import ChatDelta, ChatDone, ChatMessage, ChatModel, ChatParams

PROMPT = [
    ChatMessage("system", "You are concise."),
    ChatMessage("user", "Say hello in one short sentence."),
]


async def test_stream_yields_deltas_then_exactly_one_done(chat_model: ChatModel) -> None:
    events = [e async for e in chat_model.stream(PROMPT, ChatParams(max_output_tokens=60))]
    assert events, "stream produced nothing"
    dones = [e for e in events if isinstance(e, ChatDone)]
    assert len(dones) == 1
    assert isinstance(events[-1], ChatDone)
    assert any(isinstance(e, ChatDelta) and e.text for e in events)
    assert events[-1].finish_reason in {"stop", "length"}


async def test_no_thinking_leaks_into_answer(chat_model: ChatModel) -> None:
    text, done = await chat_model.complete(PROMPT, ChatParams(max_output_tokens=80))
    assert "<think>" not in text
    assert "</think>" not in text
    assert text.strip()
    assert done.usage.completion_tokens is None or done.usage.completion_tokens > 0


async def test_aclose_cancels_stream(chat_model: ChatModel) -> None:
    stream = chat_model.stream(PROMPT, ChatParams(max_output_tokens=200))
    first = await anext(stream)
    assert isinstance(first, ChatDelta | ChatDone)
    await stream.aclose()  # must not raise and must release the upstream request


async def test_health(chat_model: ChatModel) -> None:
    await chat_model.health()
    assert isinstance(chat_model.is_local, bool)
