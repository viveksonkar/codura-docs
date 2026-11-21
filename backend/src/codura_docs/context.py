"""Per-request / per-job context variables used for log correlation (NFR-09)."""

from __future__ import annotations

from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
tenant_id_var: ContextVar[str | None] = ContextVar("tenant_id", default=None)
job_id_var: ContextVar[str | None] = ContextVar("job_id", default=None)
