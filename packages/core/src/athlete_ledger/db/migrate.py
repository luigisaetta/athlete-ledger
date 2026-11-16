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
    """Locate migrations shipped inside the installed package, without an ini URL.

    Returns:
        The operation result.
    """
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    return config


def upgrade(engine: Engine, revision: str = "head") -> None:
    """Apply migrations to the given engine without logging SQL parameters.

    Args:
        engine: Engine for this operation.
        revision: Revision for this operation.

    Raises:
        DatabaseError: If the operation cannot be completed.
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
    """Downgrade a database, primarily for disposable development databases.

    Args:
        engine: Engine for this operation.
        revision: Revision for this operation.

    Raises:
        DatabaseError: If the operation cannot be completed.
    """
    try:
        with engine.begin() as connection:
            config = migration_config()
            config.attributes["connection"] = connection
            command.downgrade(config, revision)
    except SQLAlchemyError as error:
        raise DatabaseError("Database downgrade failed.", code=database_error_code(error)) from None


def offline_sql(dialect: DialectName = "oracle") -> str:
    """Render all upgrades without settings, credentials or a database connection.

    Args:
        dialect: Dialect for this operation.

    Returns:
        The operation result.
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
    """Connect and report backend, server version and the applied migration.

    Args:
        engine: Engine for this operation.

    Returns:
        The operation result.

    Raises:
        DatabaseError: If the operation cannot be completed.
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
