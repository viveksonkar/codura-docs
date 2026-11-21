"""RLS DDL helpers used by every migration that creates a tenant table (specs/db.md §1)."""

from __future__ import annotations

import re

APP_ROLES = ("codura_app", "codura_worker")
PLATFORM_ROLE = "codura_platform"
_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")

# Tables that carry tenant_id but are deliberately cross-tenant (documented in specs/db.md).
RLS_EXEMPT = frozenset({"jobs"})


def _ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"unsafe identifier: {name!r}")
    return name


def tenant_rls_sql(table: str) -> list[str]:
    """ENABLE + FORCE RLS, fail-closed tenant policy, and an explicit platform policy."""
    t = _ident(table)
    return [
        f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY",
        f"""CREATE POLICY tenant_isolation ON {t}
            USING (tenant_id = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('app.tenant_id', true))""",
        f"CREATE POLICY platform_access ON {t} TO {PLATFORM_ROLE} USING (true) WITH CHECK (true)",
    ]


def grant_sql(table: str, privileges: str, roles: tuple[str, ...]) -> str:
    return f"GRANT {privileges} ON {_ident(table)} TO {', '.join(_ident(r) for r in roles)}"
