"""Shared fixtures.

Integration tests need Postgres (pgvector image): set ``CODURA_TEST_PG_ADMIN_URL`` to an owner
connection on the ``postgres`` database, e.g.
``postgresql+psycopg://codura_owner:codura@localhost:5432/postgres``. A fresh database is
created per test session, migrated and bootstrapped, then dropped.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from codura_docs.config import Settings
from codura_docs.db.engine import make_engine
from codura_docs.registry import Registry

if sys.platform == "win32":  # psycopg async cannot use the Proactor loop (host-only; CI is Linux)
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

FAKE_MODELS_YAML = """
default_profile: fake-default
chat_profiles:
  fake-default: { provider: fake, model: fake-chat, license: apache-2.0 }
embedding: { provider: fake, model: bge-m3, dimension: 1024 }
ocr: { engine: fake }
"""


@pytest.fixture
def models_file(tmp_path: Path) -> Path:
    path = tmp_path / "models.yaml"
    path.write_text(FAKE_MODELS_YAML, encoding="utf-8")
    return path


@pytest.fixture
def settings(models_file: Path, tmp_path: Path) -> Settings:
    return Settings(
        app_env="test",
        model_config_file=models_file,
        blob_store="memory",
        blob_local_path=tmp_path / "blobs",
        email_backend="fake",
        language_detector="fake",
    )


@pytest.fixture
def registry(settings: Settings) -> Registry:
    return Registry.from_settings(settings)


# --- Postgres (integration) ----------------------------------------------------------------------

PG_ADMIN_URL = os.environ.get("CODURA_TEST_PG_ADMIN_URL")


def _with_db(
    url: str, database: str, *, user: str | None = None, password: str | None = None
) -> str:
    u = make_url(url).set(database=database)
    if user:
        u = u.set(username=user, password=password)
    return u.render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def pg_database(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Settings]:
    if not PG_ADMIN_URL:
        pytest.skip("integration: set CODURA_TEST_PG_ADMIN_URL to run against Postgres")
    name = f"codura_test_{uuid.uuid4().hex[:10]}"
    admin = create_engine(PG_ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    models = tmp_path_factory.mktemp("models") / "models.yaml"
    models.write_text(FAKE_MODELS_YAML, encoding="utf-8")
    settings = Settings(
        app_env="test",
        database_owner_url=_with_db(PG_ADMIN_URL, name),
        database_app_url=_with_db(PG_ADMIN_URL, name, user="codura_app", password="codura_app"),
        database_worker_url=_with_db(
            PG_ADMIN_URL, name, user="codura_worker", password="codura_worker"
        ),
        database_platform_url=_with_db(
            PG_ADMIN_URL, name, user="codura_platform", password="codura_platform"
        ),
        model_config_file=models,
        blob_store="memory",
        email_backend="fake",
        language_detector="fake",
        seed_demo_tenant=True,
    )
    from codura_docs.bootstrap import run_bootstrap

    run_bootstrap(settings)
    try:
        yield settings
    finally:
        with admin.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"
                ),
                {"n": name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


@pytest.fixture
async def owner_engine(pg_database: Settings) -> AsyncIterator[AsyncEngine]:
    engine = make_engine(pg_database.database_owner_url, pool_size=2)
    yield engine
    await engine.dispose()


@pytest.fixture
async def app_engine(pg_database: Settings) -> AsyncIterator[AsyncEngine]:
    engine = make_engine(pg_database.database_app_url, pool_size=4)
    yield engine
    await engine.dispose()


@pytest.fixture
async def worker_engine(pg_database: Settings) -> AsyncIterator[AsyncEngine]:
    engine = make_engine(pg_database.database_worker_url, pool_size=4)
    yield engine
    await engine.dispose()
