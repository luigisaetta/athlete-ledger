"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Configure reader-only SQLite and Oracle engines without owner credentials.
"""  # noqa: D205, D212

import re
from pathlib import Path
from typing import Self

import oracledb
from athlete_ledger.errors import ConfigurationError, SettingsIssue
from athlete_ledger.settings import DEFAULT_DB_URL
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.pool import ConnectionPoolEntry


class MCPSettings(BaseSettings):
    """Reader configuration that never loads owner passwords or provider API keys."""

    model_config = SettingsConfigDict(
        env_prefix="ATHLETE_LEDGER_",
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
        hide_input_in_errors=True,
    )
    db_url: str | None = Field(default=None, repr=False)
    db_dsn: str | None = Field(default=None, repr=False)
    db_wallet_dir: Path | None = Field(default=None, repr=False)
    db_wallet_password: SecretStr | None = None
    ro_db_user: str | None = Field(default=None, repr=False)
    ro_db_password: SecretStr | None = None
    db_schema: str = Field(default="ATHLETE_LEDGER", pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    mcp_include_activity_names: bool = False

    @model_validator(mode="after")
    def validate_reader(self) -> Self:
        """Require reader-only Oracle credentials or a supported SQLite URL.

        Returns:
            This configuration after backend-specific checks.

        Raises:
            SettingsIssue: If selectors conflict or reader/wallet fields are missing.
        """
        if self.db_dsn:
            if self.db_url:
                raise SettingsIssue("Choose one database backend.", ("db_url", "db_dsn"))
            required = {
                "ro_db_user": self.ro_db_user and self.ro_db_user.strip(),
                "ro_db_password": self.ro_db_password and self.ro_db_password.get_secret_value(),
                "db_wallet_dir": self.db_wallet_dir,
                "db_wallet_password": self.db_wallet_password
                and self.db_wallet_password.get_secret_value(),
            }
            missing = tuple(name for name, value in required.items() if not value)
            if missing:
                raise SettingsIssue("Oracle requires reader and wallet settings.", missing)
        else:
            try:
                url = make_url(self.db_url or DEFAULT_DB_URL)
                valid = url.drivername in {"sqlite", "sqlite+pysqlite"} and not (
                    url.username or url.password or url.host
                )
            except (ArgumentError, ValueError):
                valid = False
            if not valid:
                raise SettingsIssue("DB_URL must select SQLite.", ("db_url",))
        return self


def create_reader_engine(settings: MCPSettings) -> Engine:
    """Build a lazy engine with no write-capable SQLite or owner credential fallback.

    Args:
        settings: Validated reader credentials, wallet location and owner schema name.

    Returns:
        A parameter-hiding engine with pre-ping; Oracle sets CURRENT_SCHEMA on connect.

    Raises:
        ConfigurationError: If the SQLite file is missing, reader settings are incomplete,
            thick mode is active or the schema identifier is invalid.
    """
    if not settings.db_dsn:
        url = make_url(settings.db_url or DEFAULT_DB_URL)
        path = Path(url.database or "").expanduser()
        if not url.database or url.database == ":memory:" or not path.is_file():
            raise ConfigurationError("SQLite requires an existing database file.")
        return create_engine(
            URL.create(
                "sqlite+pysqlite",
                database=path.absolute().as_uri(),
                query={"mode": "ro", "uri": "true"},
            ),
            pool_pre_ping=True,
            hide_parameters=True,
            echo=False,
        )
    if (
        not settings.ro_db_user
        or not settings.ro_db_user.strip()
        or settings.ro_db_password is None
        or not settings.ro_db_password.get_secret_value()
        or settings.db_wallet_dir is None
        or settings.db_wallet_password is None
        or not settings.db_wallet_password.get_secret_value()
        or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", settings.db_schema)
        or not oracledb.is_thin_mode()
    ):
        raise ConfigurationError("Oracle requires complete reader-only mTLS settings.")
    engine = create_engine(
        URL.create("oracle+oracledb"),
        connect_args={
            "user": settings.ro_db_user,
            "password": settings.ro_db_password.get_secret_value(),
            "dsn": settings.db_dsn,
            "config_dir": str(settings.db_wallet_dir.expanduser()),
            "wallet_location": str(settings.db_wallet_dir.expanduser()),
            "wallet_password": settings.db_wallet_password.get_secret_value(),
        },
        pool_pre_ping=True,
        hide_parameters=True,
        echo=False,
    )

    def set_schema(connection: oracledb.Connection, record: ConnectionPoolEntry) -> None:
        """Resolve unqualified tables in the validated owner schema for each connection.

        Args:
            connection: Newly opened reader-only Oracle DB-API connection.
            record: Pool entry supplied by SQLAlchemy; unused by this hook.
        """
        with connection.cursor() as cursor:
            cursor.execute(f"ALTER SESSION SET CURRENT_SCHEMA = {settings.db_schema}")

    event.listen(engine, "connect", set_schema)
    return engine
