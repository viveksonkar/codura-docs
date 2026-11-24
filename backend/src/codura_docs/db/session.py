"""Tenant-scoped transactions: every request and Job runs inside exactly one Tenant (ADR-0014).

``SET LOCAL``-style ``set_config(..., true)`` lasts only for the transaction, so pooled connections
never carry a Tenant into the next request.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from codura_docs.context import tenant_id_var
from codura_docs.tenancy import TenantContext


@asynccontextmanager
async def tenant_session(engine: AsyncEngine, ctx: TenantContext) -> AsyncIterator[AsyncSession]:
    """Open a transaction bound to *ctx*; commits on success, rolls back on error."""
    token = tenant_id_var.set(ctx.tenant_id)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": ctx.tenant_id}
            )
            yield session
    finally:
        tenant_id_var.reset(token)


@asynccontextmanager
async def global_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A transaction with NO tenant: RLS hides every tenant row (fail closed).

    For global tables only (users, system_settings, jobs claiming) and for code that resolves a
    Tenant from a credential before opening a tenant_session.
    """
    async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
        yield session


def rowcount(result: object) -> int:
    """Rows affected by an UPDATE/DELETE executed through an AsyncSession (typed helper)."""
    return int(getattr(result, "rowcount", 0) or 0)
