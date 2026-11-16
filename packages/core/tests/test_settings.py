"""Configuration validation with synthetic credentials only."""

from pathlib import Path

import pytest
from athlete_ledger.settings import DEFAULT_DB_URL, Settings
from pydantic import SecretStr, ValidationError

SYNTHETIC_PASSWORD = "synthetic-owner-secret"
SYNTHETIC_WALLET_PASSWORD = "synthetic-wallet-secret"


def oracle_options() -> dict[str, object]:
    return {
        "db_user": "synthetic_owner",
        "db_password": SYNTHETIC_PASSWORD,
        "db_dsn": "synthetic_low",
        "db_wallet_dir": Path("/synthetic/wallet"),
        "db_wallet_password": SYNTHETIC_WALLET_PASSWORD,
    }


def test_defaults() -> None:
    settings = Settings()
    assert settings.backend == "sqlite"
    assert settings.sqlite_url == DEFAULT_DB_URL


def test_oracle_selection_and_secret_repr() -> None:
    settings = Settings(**oracle_options())
    assert settings.backend == "oracle"
    assert isinstance(settings.db_password, SecretStr)
    assert SYNTHETIC_PASSWORD not in repr(settings)
    assert SYNTHETIC_WALLET_PASSWORD not in repr(settings)


@pytest.mark.parametrize(
    "missing", ["db_user", "db_password", "db_wallet_dir", "db_wallet_password"]
)
def test_incomplete_oracle(missing: str) -> None:
    options = oracle_options()
    options.pop(missing)
    with pytest.raises(ValidationError) as error:
        Settings(**options)
    assert SYNTHETIC_PASSWORD not in str(error.value)
    assert SYNTHETIC_WALLET_PASSWORD not in str(error.value)


def test_conflicting_selectors() -> None:
    with pytest.raises(ValidationError, match="Set only one") as error:
        Settings(**oracle_options(), db_url="sqlite://")
    assert SYNTHETIC_PASSWORD not in str(error.value)


@pytest.mark.parametrize("url", ["oracle+oracledb://u:synthetic-secret@host", "not-a-url"])
def test_invalid_url_is_sanitized(url: str) -> None:
    with pytest.raises(ValidationError) as error:
        Settings(db_url=url)
    assert "synthetic-secret" not in str(error.value)


def test_dotenv_and_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "ATHLETE_LEDGER_DB_URL=sqlite:///dotenv.db\n"
        "ATHLETE_LEDGER_DB_DSN=\n"
        "ATHLETE_LEDGER_RO_DB_PASSWORD=synthetic-reader-secret\n"
        "OCI_GENAI_PROJECT_ID=\n"
    )
    assert Settings().sqlite_url == "sqlite:///dotenv.db"
    assert "synthetic-reader-secret" not in repr(Settings())
    monkeypatch.setenv("ATHLETE_LEDGER_DB_URL", "sqlite:///environment.db")
    assert Settings().sqlite_url == "sqlite:///environment.db"
