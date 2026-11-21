"""Async engines. The app/worker use non-owner roles so RLS always applies (ADR-0014)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from codura_docs.config import Settings


def make_engine(url: str, *, pool_size: int = 10) -> AsyncEngine:
    return create_async_engine(url, pool_size=pool_size, max_overflow=5, pool_pre_ping=True)


def app_engine(settings: Settings) -> AsyncEngine:
    return make_engine(settings.database_app_url)


def sessionmaker(engine: AsyncEngine) -> async_sessionmaker:  # type: ignore[type-arg]
    return async_sessionmaker(engine, expire_on_commit=False)
