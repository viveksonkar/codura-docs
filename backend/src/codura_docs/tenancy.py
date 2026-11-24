"""TenantContext: the only way Tenant-scoped code learns which Tenant it serves (ADR-0014).

It is created from verified credentials (session, ``sk_``, ``pk_``) or from a Job row, never from
request data. Pure module: imported by ports, db and services alike.
"""

from __future__ import annotations

from dataclasses import dataclass

from codura_docs.ids import is_id


@dataclass(frozen=True, slots=True)
class TenantContext:
    tenant_id: str

    def __post_init__(self) -> None:
        if not is_id("tnt", self.tenant_id):
            raise ValueError("TenantContext requires a valid tenant id")
