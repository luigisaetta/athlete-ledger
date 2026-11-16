"""Engine construction must not connect to Oracle or expose authentication."""

from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy
from athlete_ledger.db import create_db_engine
from athlete_ledger.errors import ConfigurationError
from athlete_ledger.settings import Settings
from pydantic import SecretStr


def test_sqlite_parent_and_foreign_keys(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "ledger.db"
    engine = create_db_engine(Settings(db_url=f"sqlite:///{path}"))
    try:
        assert path.parent.is_dir()
        assert not path.exists()
        with engine.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    finally:
        engine.dispose()


def test_oracle_arguments_without_connecting() -> None:
    settings = Settings(
        db_dsn="synthetic_low",
        db_user="synthetic_owner",
        db_password="synthetic-password",
        db_wallet_dir=Path("/synthetic/wallet"),
        db_wallet_password="synthetic-wallet-password",
    )
    with patch("athlete_ledger.db.create_engine", wraps=sqlalchemy.create_engine) as factory:
        engine = create_db_engine(settings)
    try:
        assert factory.call_args.kwargs["connect_args"] == {
            "user": "synthetic_owner",
            "password": "synthetic-password",
            "dsn": "synthetic_low",
            "config_dir": "/synthetic/wallet",
            "wallet_location": "/synthetic/wallet",
            "wallet_password": "synthetic-wallet-password",
        }
        assert factory.call_args.kwargs["hide_parameters"] is True
        assert "synthetic-password" not in repr(engine)
        assert engine.url.drivername == "oracle+oracledb"
        assert engine.url.password is None
    finally:
        engine.dispose()


def test_thick_mode_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("oracledb.is_thin_mode", lambda: False)
    settings = Settings(
        db_dsn="synthetic_low",
        db_user="synthetic_owner",
        db_password="synthetic-password",
        db_wallet_dir=Path("/synthetic/wallet"),
        db_wallet_password="synthetic-wallet-password",
    )
    with pytest.raises(ConfigurationError, match="thin mode"):
        create_db_engine(settings)


@pytest.mark.parametrize(
    "updates",
    [
        {"db_user": None},
        {"db_user": " "},
        {"db_password": None},
        {"db_password": SecretStr("")},
        {"db_wallet_dir": None},
        {"db_wallet_password": None},
        {"db_wallet_password": SecretStr("")},
    ],
)
def test_invalid_constructed_settings_raise_explicit_error(updates: dict[str, object]) -> None:
    # Bypass validation to prove the engine guard remains effective even with python -O.
    settings = Settings.model_construct(
        db_dsn="synthetic-private-alias",
        db_user="synthetic-private-user",
        db_password=SecretStr("synthetic-private-password"),
        db_wallet_dir=Path("/synthetic/private/wallet"),
        db_wallet_password=SecretStr("synthetic-private-wallet-password"),
    ).model_copy(update=updates)
    with patch("athlete_ledger.db.create_engine") as factory:
        with pytest.raises(ConfigurationError) as error:
            create_db_engine(settings)
    factory.assert_not_called()
    assert str(error.value) == "Oracle requires complete mTLS connection settings."
