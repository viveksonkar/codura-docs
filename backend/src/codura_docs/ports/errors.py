"""Provider-neutral errors (specs/ports.md §6). Adapters must never leak vendor exceptions."""

from __future__ import annotations


class ProviderError(Exception):
    def __init__(self, provider: str, message: str) -> None:
        super().__init__(f"[{provider}] {message}")
        self.provider = provider


class ProviderUnavailable(ProviderError):
    """Connection refused, 5xx, timeout, or model not pulled."""


class ProviderRateLimited(ProviderError):
    def __init__(self, provider: str, message: str, retry_after_s: float | None = None) -> None:
        super().__init__(provider, message)
        self.retry_after_s = retry_after_s


class ProviderBadRequest(ProviderError):
    """4xx such as context too long or invalid parameters."""


class ProviderAuthError(ProviderError):
    """Missing or rejected credentials."""


class UnsupportedDocument(Exception):
    """Deterministic: the document can never be processed (no retry)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ParseError(Exception):
    """Parsing failed; may be transient (timeouts)."""
