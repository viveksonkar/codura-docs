"""Server-Sent Events for answers (specs/api.md §3.3, ADR-0012): typed events + 15 s keep-alive pings."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Any

from starlette.responses import StreamingResponse

from codura_docs.services.answering import AnswerEvent, Citations, Debug, Delta, Done, Failed, Meta

PING_S = 15.0
HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}


def encode(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n".encode()


def to_sse(event: AnswerEvent, *, include_debug: bool) -> bytes | None:
    if isinstance(event, Meta):
        return encode("meta", asdict(event))
    if isinstance(event, Delta):
        return encode("delta", {"text": event.text})
    if isinstance(event, Citations):
        return encode("citations", {"items": event.items})
    if isinstance(event, Done):
        return encode("done", asdict(event))
    if isinstance(event, Failed):
        return encode("error", asdict(event))
    if isinstance(event, Debug):
        return encode("debug", asdict(event)) if include_debug else None
    return None


async def _with_pings(
    events: AsyncIterator[AnswerEvent], include_debug: bool
) -> AsyncIterator[bytes]:
    """Interleave keep-alive comments without cancelling the producer mid-step."""
    iterator = events.__aiter__()
    pending: asyncio.Task[AnswerEvent] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(iterator.__anext__())
            done, _ = await asyncio.wait({pending}, timeout=PING_S)
            if not done:
                yield b": ping\n\n"
                continue
            try:
                event = pending.result()
            except StopAsyncIteration:
                return
            finally:
                pending = None
            chunk = to_sse(event, include_debug=include_debug)
            if chunk:
                yield chunk
    finally:
        if pending is not None:
            pending.cancel()
            with contextlib.suppress(BaseException):
                await pending
        aclose = getattr(iterator, "aclose", None)
        if aclose is not None:
            await aclose()  # client disconnected → cancel generation (persisted as "cancelled")


def sse_response(
    events: AsyncIterator[AnswerEvent], *, include_debug: bool = False
) -> StreamingResponse:
    return StreamingResponse(
        _with_pings(events, include_debug), media_type="text/event-stream", headers=HEADERS
    )
