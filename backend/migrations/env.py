"""Alembic environment. Migrations run as the database owner (never the app role)."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = config.get_main_option("sqlalchemy.url")
    assert url, "sqlalchemy.url must be set (DATABASE_OWNER_URL)"
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
