"""Structured JSON logging with correlation IDs and secret redaction (NFR-09, SEC-10)."""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

from codura_docs.context import job_id_var, request_id_var, tenant_id_var

_REDACTED = "[REDACTED]"
_PATTERNS = [
    re.compile(r"\b(?:pk|sk)_[A-Za-z0-9_]{16,}\b"),  # Channel / Secret Keys
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)(password|passwd|secret|api[_-]?key|token)(\s*[=:]\s*)([^\s,;&\"']+)"),
]
_SENSITIVE_KEYS = re.compile(r"(?i)password|secret|api[_-]?key|token|authorization|credential")
_STANDARD_ATTRS = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}


def redact(text: str) -> str:
    text = _PATTERNS[0].sub(_REDACTED, text)
    text = _PATTERNS[1].sub("Bearer " + _REDACTED, text)
    return _PATTERNS[2].sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}", text)


def _redact_value(key: str, value: Any) -> Any:
    if _SENSITIVE_KEYS.search(key):
        return _REDACTED
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _redact_value(str(k), v) for k, v in value.items()}
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        for key, var in (
            ("request_id", request_id_var),
            ("tenant_id", tenant_id_var),
            ("job_id", job_id_var),
        ):
            value = var.get()
            if value:
                payload[key] = value
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = _redact_value(key, value)
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, ensure_ascii=False)


def setup_logging(level: str = "info") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("uvicorn.access", "httpx", "httpcore", "botocore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
