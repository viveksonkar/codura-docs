"""FakeEmailSender: records messages instead of sending."""

from __future__ import annotations

from codura_docs.ports.email import EmailMessage


class FakeEmailSender:
    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        if "@" not in message.to:
            raise ValueError("invalid recipient")
        self.sent.append(message)
