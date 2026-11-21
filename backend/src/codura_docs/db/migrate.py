"""Programmatic Alembic: upgrade to head (owner role) and head/current inspection for /readyz."""

from __future__ import annotations

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from codura_docs.config import Settings


def alembic_config(settings: Settings, url: str | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(settings.migrations_dir))
    cfg.set_main_option("sqlalchemy.url", (url or settings.database_owner_url).replace("%", "%%"))
    return cfg


def upgrade_to_head(settings: Settings, url: str | None = None) -> None:
    command.upgrade(alembic_config(settings, url), "head")


def head_revision(settings: Settings) -> str | None:
    return ScriptDirectory.from_config(alembic_config(settings)).get_current_head()


async def current_revision(engine: AsyncEngine) -> str | None:
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT version_num FROM alembic_version"))
        row = result.first()
    return str(row[0]) if row else None
