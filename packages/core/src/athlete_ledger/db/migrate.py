"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Programmatic migrations and credential-free offline DDL generation.
"""  # noqa: D205, D212

from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Literal

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from athlete_ledger.errors import DatabaseError, database_error_code

DialectName = Literal["oracle", "sqlite"]


def migration_config() -> Config:
    """Locate the packaged Alembic environment without reading an ini connection URL.

    Returns:
        An Alembic Config with script_location pointing to this package's migrations
        directory.
    """
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    return config


def upgrade(engine: Engine, revision: str = "head") -> None:
    """Apply ledger migrations in a transaction using the supplied engine.

    Args:
        engine: SQLAlchemy engine connected as the schema owner.
        revision: Alembic target revision to apply to; defaults to head.

    Raises:
        DatabaseError: If database connection or migration SQL fails; only a sanitized
            message and optional ORA/DPY code are retained.
    """
    try:
        with engine.begin() as connection:
            config = migration_config()
            config.attributes["connection"] = connection
            command.upgrade(config, revision)
    except SQLAlchemyError as error:
        raise DatabaseError(
            "Database upgrade failed; check connectivity and schema privileges.",
            code=database_error_code(error),
        ) from None


def downgrade(engine: Engine, revision: str = "base") -> None:
    """Revert ledger migrations, intended for disposable development databases.

    Args:
        engine: SQLAlchemy engine connected as the schema owner.
        revision: Alembic target revision to revert to; defaults to base.

    Raises:
        DatabaseError: If database connection or migration SQL fails; only a sanitized
            message and optional ORA/DPY code are retained.
    """
    try:
        with engine.begin() as connection:
            config = migration_config()
            config.attributes["connection"] = connection
            command.downgrade(config, revision)
    except SQLAlchemyError as error:
        raise DatabaseError("Database downgrade failed.", code=database_error_code(error)) from None


def offline_sql(dialect: DialectName = "oracle") -> str:
    """Render the complete upgrade path without connecting to a database.

    Args:
        dialect: Target SQL dialect: oracle by default, or sqlite.

    Returns:
        The Alembic upgrade SQL with trailing whitespace removed and exactly one final
        newline.
    """
    buffer = StringIO()
    config = migration_config()
    config.output_buffer = buffer
    config.attributes["dialect_name"] = dialect
    command.upgrade(config, "head", sql=True)
    return "\n".join(line.rstrip() for line in buffer.getvalue().splitlines()).rstrip() + "\n"


@dataclass(frozen=True)
class DatabaseStatus:
    """Only non-sensitive database status fields suitable for printing."""

    backend: str
    server_version: str
    revision: str | None


def check_database(engine: Engine) -> DatabaseStatus:
    """Read backend version and Alembic revision without accessing ledger payloads.

    Args:
        engine: SQLAlchemy engine whose connectivity and current schema revision should
            be checked.

    Returns:
        DatabaseStatus containing backend name, server version and current revision, or
        None for the revision of an unversioned schema.

    Raises:
        DatabaseError: If connection or Alembic revision inspection fails; raw driver
            diagnostics are discarded.
    """
    try:
        with engine.connect() as connection:
            revision = MigrationContext.configure(connection).get_current_revision()
            version = ".".join(str(part) for part in (engine.dialect.server_version_info or ()))
            return DatabaseStatus(engine.dialect.name, version or "unknown", revision)
    except SQLAlchemyError as error:
        raise DatabaseError(
            "Database check failed; check connectivity and schema privileges.",
            code=database_error_code(error),
        ) from None
