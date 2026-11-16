"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Verify SQLite read-only enforcement and Oracle reader configuration offline.
"""  # noqa: D205, D212

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from athlete_ledger.errors import ConfigurationError
from athlete_ledger_mcp.database import MCPSettings, create_reader_engine
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import OperationalError


def test_sqlite_engine_rejects_writes_and_uses_pre_ping(reader) -> None:
    """Enforce read-only operation at the SQLite driver, beyond tool annotations."""
    assert reader.pool._pre_ping is True
    assert reader.hide_parameters is True
    assert reader.url.query == {"mode": "ro", "uri": "true"}
    with reader.connect() as connection:
        with pytest.raises(OperationalError, match="readonly"):
            connection.execute(text("DELETE FROM activity"))
        assert connection.execute(text("SELECT count(*) FROM activity")).scalar_one() == 6


@pytest.mark.parametrize("url", ["sqlite:///missing.db", "sqlite:///:memory:", "sqlite://"])
def test_missing_sqlite_is_not_created(url, tmp_path) -> None:
    """Reject missing/non-file storage without creating a database or its parent directory."""
    before = list(tmp_path.iterdir())
    with pytest.raises(ConfigurationError):
        create_reader_engine(MCPSettings(db_url=url))
    assert list(tmp_path.iterdir()) == before


@pytest.mark.parametrize("schema", ["owner", "A;DROP TABLE X", "A B", "1OWNER", "A" * 129, ""])
def test_schema_identifier_rejected_before_use(schema) -> None:
    """Validate owner schema names as bounded uppercase unquoted SQL identifiers."""
    with pytest.raises(ValidationError):
        MCPSettings(db_schema=schema)


def test_oracle_uses_only_reader_credentials_and_validated_schema(monkeypatch) -> None:
    """Build reader mTLS arguments and invoke the connect hook without contacting Oracle."""
    monkeypatch.setenv("ATHLETE_LEDGER_DB_USER", "SYNTHETIC-OWNER")
    monkeypatch.setenv("ATHLETE_LEDGER_DB_PASSWORD", "SYNTHETIC-OWNER-PASSWORD")
    settings = MCPSettings(
        db_dsn="synthetic_low",
        ro_db_user="SYNTHETIC_READER",
        ro_db_password="synthetic-reader-password",
        db_wallet_dir=Path("/synthetic/wallet"),
        db_wallet_password="synthetic-wallet-password",
        db_schema="SYNTHETIC_OWNER",
    )
    factory = Mock()
    listener = Mock()
    monkeypatch.setattr("athlete_ledger_mcp.database.create_engine", factory)
    monkeypatch.setattr("athlete_ledger_mcp.database.event", SimpleNamespace(listen=listener))
    engine = create_reader_engine(settings)
    kwargs = factory.call_args.kwargs
    assert engine is factory.return_value
    assert kwargs["pool_pre_ping"] is True and kwargs["hide_parameters"] is True
    assert kwargs["echo"] is False
    assert kwargs["connect_args"] == {
        "user": "SYNTHETIC_READER",
        "password": "synthetic-reader-password",
        "dsn": "synthetic_low",
        "config_dir": "/synthetic/wallet",
        "wallet_location": "/synthetic/wallet",
        "wallet_password": "synthetic-wallet-password",
    }
    assert "SYNTHETIC-OWNER-PASSWORD" not in repr(settings)
    assert "synthetic-reader-password" not in repr(settings)
    assert listener.call_args.args[:2] == (engine, "connect")
    hook = listener.call_args.args[2]
    first, second = MagicMock(), MagicMock()
    hook(first, Mock())
    hook(second, Mock())
    for connection in (first, second):
        connection.cursor.return_value.__enter__.return_value.execute.assert_called_once_with(
            "ALTER SESSION SET CURRENT_SCHEMA = SYNTHETIC_OWNER"
        )


@pytest.mark.parametrize("missing", ["ro_db_user", "ro_db_password"])
def test_owner_credentials_never_satisfy_missing_reader_settings(monkeypatch, missing) -> None:
    """Require both reader fields even if complete owner credentials are present."""
    monkeypatch.setenv("ATHLETE_LEDGER_DB_USER", "SYNTHETIC_OWNER")
    monkeypatch.setenv("ATHLETE_LEDGER_DB_PASSWORD", "SYNTHETIC-OWNER-PASSWORD")
    values = dict(
        db_dsn="synthetic_low",
        ro_db_user="READER",
        ro_db_password="reader-password",
        db_wallet_dir=Path("/synthetic/wallet"),
        db_wallet_password="wallet-password",
    )
    del values[missing]
    with pytest.raises(ValidationError) as error:
        MCPSettings(**values)
    assert missing in error.value.errors()[0]["ctx"]["error"].fields


def test_oracle_engine_defensively_rejects_unvalidated_schema(monkeypatch) -> None:
    """Reject SQL injection even if a caller bypasses Pydantic validation."""
    settings = MCPSettings.model_construct(
        db_dsn="synthetic_low",
        ro_db_user="READER",
        ro_db_password=Mock(),
        db_wallet_dir=Path("/synthetic/wallet"),
        db_wallet_password=Mock(),
        db_schema="A; DROP USER B",
    )
    factory = Mock()
    monkeypatch.setattr("athlete_ledger_mcp.database.create_engine", factory)
    with pytest.raises(ConfigurationError):
        create_reader_engine(settings)
    factory.assert_not_called()
