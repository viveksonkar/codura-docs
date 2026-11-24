"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from codura_docs.api.errors import install_error_handlers
from codura_docs.api.middleware import MetricsMiddleware, RequestIdMiddleware
from codura_docs.api.readiness import Readiness
from codura_docs.api.routers import health
from codura_docs.config import Settings, get_settings
from codura_docs.db.engine import app_engine
from codura_docs.db.guards import check_embedding_guard
from codura_docs.db.migrate import current_revision, head_revision
from codura_docs.log import setup_logging
from codura_docs.registry import Registry

log = logging.getLogger(__name__)


def _register_core_checks(
    app: FastAPI, settings: Settings, engine: AsyncEngine, registry: Registry
) -> None:
    readiness: Readiness = app.state.readiness

    async def database() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def migrations() -> None:
        current, head = await current_revision(engine), head_revision(settings)
        if current != head:
            raise RuntimeError(f"database at {current}, code expects {head}")

    async def embedding_guard() -> None:
        await check_embedding_guard(engine, registry.models.embedding)

    async def embedding_model() -> None:
        await registry.embedding().health()

    async def chat_model() -> None:
        await registry.chat(registry.default_profile()).health()

    for name, check in (
        ("database", database),
        ("migrations", migrations),
        ("embedding_guard", embedding_guard),
        ("embedding_model", embedding_model),
        ("chat_model", chat_model),
    ):
        readiness.register(name, check)


def create_app(
    settings: Settings | None = None,
    *,
    registry: Registry | None = None,
    engine: AsyncEngine | None = None,
    startup_guard: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    settings.validate_for_production()
    setup_logging(settings.log_level)
    registry = registry or Registry.from_settings(settings)
    engine = engine or app_engine(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if startup_guard:
            # FR-25: refuse to serve with a different embedding model than the data was built with.
            await check_embedding_guard(engine, registry.models.embedding)
        log.info("api started", extra={"app_env": settings.app_env})
        yield
        await engine.dispose()

    app = FastAPI(
        title="Codura Docs API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if settings.app_env == "production" else "/api/docs",
        openapi_url=None if settings.app_env == "production" else "/api/openapi.json",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.registry = registry
    app.state.engine = engine
    app.state.readiness = Readiness()
    install_error_handlers(app)
    from codura_docs.api.cors import PublicCorsMiddleware
    from codura_docs.ratelimit import InMemoryConcurrencyLimiter, InMemoryRateLimiter

    app.state.rate_limiter = InMemoryRateLimiter()
    app.state.concurrency = InMemoryConcurrencyLimiter()
    app.add_middleware(PublicCorsMiddleware)
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(RequestIdMiddleware)
    from codura_docs.api.routers.admin import auth as auth_routes
    from codura_docs.api.routers.admin import (
        channel_keys,
        content,
        conversations,
        insights,
        tenant,
        visitor_emails,
    )
    from codura_docs.api.routers.platform import routes as platform_routes
    from codura_docs.api.routers.public import chat, hosted
    from codura_docs.crypto import Keyring
    from codura_docs.db.engine import make_engine as _make_engine

    app.state.keyring = Keyring.from_settings(settings)
    app.state.platform_engine = _make_engine(settings.database_platform_url, pool_size=3)
    app.include_router(health.router)
    for r in (
        auth_routes.router,
        auth_routes.public,
        tenant.router,
        content.router,
        channel_keys.router,
        conversations.router,
        visitor_emails.router,
        insights.router,
        platform_routes.router,
        chat.router,
        hosted.router,
    ):
        app.include_router(r)
    if settings.app_env != "production":
        from codura_docs.api.routers import debug
        from codura_docs.db.engine import make_engine

        app.state.debug_worker_engine = make_engine(settings.database_worker_url, pool_size=1)
        app.include_router(debug.router)
    _register_core_checks(app, settings, engine, registry)
    return app


def app_factory() -> FastAPI:
    """Entry point for ``uvicorn --factory``."""
    return create_app()
