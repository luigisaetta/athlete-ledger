"""Database configuration loaded without opening connections."""

from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

DEFAULT_DB_URL = "sqlite:///./data/athlete_ledger.db"


class Settings(BaseSettings):
    """Select SQLite by default, or Oracle mTLS through an explicit DSN."""

    model_config = SettingsConfigDict(
        env_prefix="ATHLETE_LEDGER_",
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
        hide_input_in_errors=True,
    )

    db_url: str | None = Field(default=None, repr=False)
    db_user: str | None = Field(default=None, repr=False)
    db_password: SecretStr | None = None
    db_dsn: str | None = Field(default=None, repr=False)
    db_wallet_dir: Path | None = Field(default=None, repr=False)
    db_wallet_password: SecretStr | None = None
    ro_db_user: str | None = Field(default=None, repr=False)
    ro_db_password: SecretStr | None = None

    @property
    def backend(self) -> str:
        """Return the configured backend without revealing its address."""
        return "oracle" if self.db_dsn else "sqlite"

    @property
    def sqlite_url(self) -> str:
        """Return the explicit SQLite URL or the local default."""
        return self.db_url or DEFAULT_DB_URL

    @model_validator(mode="after")
    def validate_database(self) -> Self:
        """Reject incomplete Oracle settings and incompatible selectors."""
        if self.db_dsn:
            if self.db_url:
                raise ValueError("Set only one of ATHLETE_LEDGER_DB_URL and ATHLETE_LEDGER_DB_DSN.")
            if not all(
                (
                    self.db_user and self.db_user.strip(),
                    self.db_password and self.db_password.get_secret_value(),
                    self.db_wallet_dir,
                    self.db_wallet_password and self.db_wallet_password.get_secret_value(),
                )
            ):
                raise ValueError(
                    "Oracle requires user, password, wallet directory and wallet password."
                )
        else:
            try:
                url = make_url(self.sqlite_url)
            except (ArgumentError, ValueError):
                raise ValueError("Invalid SQLite database URL.") from None
            if (
                url.drivername not in {"sqlite", "sqlite+pysqlite"}
                or url.username
                or url.password
                or url.host
            ):
                raise ValueError("DB_URL must be a SQLite URL; configure Oracle with DB_DSN.")
        return self
