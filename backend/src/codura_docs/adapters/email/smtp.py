"""SMTP EmailSender (Mailpit locally, any relay in production), ADR-0020."""

from __future__ import annotations

from email.message import EmailMessage as MimeMessage

import aiosmtplib

from codura_docs.ports.email import EmailMessage
from codura_docs.ports.errors import ProviderUnavailable


class SmtpEmailSender:
    def __init__(
        self,
        host: str,
        port: int,
        sender: str,
        *,
        username: str | None = None,
        password: str | None = None,
        starttls: bool = False,
        timeout_s: float = 10.0,
    ) -> None:
        self._host, self._port, self._sender = host, port, sender
        self._username, self._password = username, password
        self._starttls, self._timeout = starttls, timeout_s

    async def send(self, message: EmailMessage) -> None:
        mime = MimeMessage()
        mime["From"] = self._sender
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime.set_content(message.text)
        if message.html:
            mime.add_alternative(message.html, subtype="html")
        try:
            await aiosmtplib.send(
                mime,
                hostname=self._host,
                port=self._port,
                username=self._username,
                password=self._password,
                start_tls=self._starttls,
                timeout=self._timeout,
            )
        except (aiosmtplib.SMTPException, OSError) as exc:
            raise ProviderUnavailable("smtp", f"{type(exc).__name__}: {exc}") from exc
