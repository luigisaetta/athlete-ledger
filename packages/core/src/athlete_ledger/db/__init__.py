"""Lazy database engine construction for SQLite and Oracle thin-mode mTLS."""

import sqlite3
from pathlib import Path

import oracledb
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import ConnectionPoolEntry

from athlete_ledger.errors import ConfigurationError
from athlete_ledger.settings import Settings


def _enable_foreign_keys(connection: sqlite3.Connection, record: ConnectionPoolEntry) -> None:
    """Enforce the same source-response references during SQLite development."""
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def create_db_engine(settings: Settings) -> Engine:
    """Build an engine; establishing a network connection remains deferred."""
    if settings.backend == "oracle":
        if not oracledb.is_thin_mode():
            raise ConfigurationError("Oracle requires python-oracledb thin mode.")
        if (
            not settings.db_user
            or not settings.db_user.strip()
            or settings.db_password is None
            or not settings.db_password.get_secret_value()
            or settings.db_wallet_password is None
            or not settings.db_wallet_password.get_secret_value()
            or settings.db_wallet_dir is None
        ):
            raise ConfigurationError("Oracle requires complete mTLS connection settings.")
        return create_engine(
            URL.create("oracle+oracledb"),
            connect_args={
                "user": settings.db_user,
                "password": settings.db_password.get_secret_value(),
                "dsn": settings.db_dsn,
                "config_dir": str(settings.db_wallet_dir.expanduser()),
                "wallet_location": str(settings.db_wallet_dir.expanduser()),
                "wallet_password": settings.db_wallet_password.get_secret_value(),
            },
            hide_parameters=True,
            echo=False,
        )
    url = make_url(settings.sqlite_url)
    if url.database and url.database != ":memory:" and not url.query.get("uri"):
        Path(url.database).expanduser().parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, hide_parameters=True, echo=False)
    event.listen(engine, "connect", _enable_foreign_keys)
    return engine
