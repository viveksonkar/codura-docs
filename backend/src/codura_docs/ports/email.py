"""EmailSender Port (specs/ports.md §5a, ADR-0020)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    text: str
    html: str | None = None


@runtime_checkable
class EmailSender(Protocol):
    async def send(self, message: EmailMessage) -> None:
        """Raise ProviderUnavailable when the relay cannot accept the message."""
        ...
