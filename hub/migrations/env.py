"""Alembic environment: Postgres only.

The URL comes from the app (`run_migrations` sets it explicitly) or from `DATABASE_URL` for the CLI.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.db import sync_database_url
from app.models import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def database_url() -> str:
    """The app's URL when it set one; otherwise DATABASE_URL of the shell; else the ini placeholder."""
    if config.attributes.get("url_from_app"):
        return str(config.get_main_option("sqlalchemy.url"))
    env_url = (os.environ.get("DATABASE_URL") or "").strip()
    if env_url:
        return sync_database_url(env_url)
    return str(config.get_main_option("sqlalchemy.url"))


def run_migrations_offline() -> None:
    context.configure(
        url=sync_database_url(database_url()),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(sync_database_url(database_url()), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
