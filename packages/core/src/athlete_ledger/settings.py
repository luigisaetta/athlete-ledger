"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Database configuration loaded without opening connections.
"""  # noqa: D205, D212

from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from athlete_ledger.errors import SettingsIssue

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

    personal_api_key: SecretStr | None = Field(
        default=None, validation_alias="GARMIN_PERSONAL_API_KEY", repr=False
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
        """Return the configured backend without revealing its address.

        Returns:
            The operation result.
        """
        return "oracle" if self.db_dsn else "sqlite"

    @property
    def sqlite_url(self) -> str:
        """Return the explicit SQLite URL or the local default.

        Returns:
            The operation result.
        """
        return self.db_url or DEFAULT_DB_URL

    @model_validator(mode="after")
    def validate_database(self) -> Self:
        """Reject incomplete Oracle settings and incompatible selectors.

        Returns:
            The operation result.

        Raises:
            SettingsIssue: If the operation cannot be completed.
        """
        if self.db_dsn:
            if self.db_url:
                raise SettingsIssue(
                    "Set only one of ATHLETE_LEDGER_DB_URL and ATHLETE_LEDGER_DB_DSN.",
                    ("db_url", "db_dsn"),
                )
            required = {
                "db_user": self.db_user and self.db_user.strip(),
                "db_password": self.db_password and self.db_password.get_secret_value(),
                "db_wallet_dir": self.db_wallet_dir,
                "db_wallet_password": (
                    self.db_wallet_password and self.db_wallet_password.get_secret_value()
                ),
            }
            missing = tuple(name for name, value in required.items() if not value)
            if missing:
                raise SettingsIssue(
                    "Oracle requires user, password, wallet directory and wallet password.", missing
                )
        else:
            try:
                url = make_url(self.sqlite_url)
            except (ArgumentError, ValueError):
                raise SettingsIssue("Invalid SQLite database URL.", ("db_url",)) from None
            if (
                url.drivername not in {"sqlite", "sqlite+pysqlite"}
                or url.username
                or url.password
                or url.host
            ):
                raise SettingsIssue(
                    "DB_URL must be a SQLite URL; configure Oracle with DB_DSN.", ("db_url",)
                )
        return self


def validation_environment_names(error: ValidationError) -> list[str]:
    """Return allowlisted environment names without rendering messages or inputs.

    Args:
        error: Error for this operation.

    Returns:
        The operation result.
    """
    allowed = {
        name: (
            "GARMIN_PERSONAL_API_KEY"
            if name == "personal_api_key"
            else f"ATHLETE_LEDGER_{name.upper()}"
        )
        for name in Settings.model_fields
    }
    fields: set[str] = set()
    for detail in error.errors(include_input=False, include_url=False):
        location = detail["loc"]
        if location and isinstance(location[0], str) and location[0] in allowed:
            fields.add(location[0])
        issue = detail.get("ctx", {}).get("error")
        if isinstance(issue, SettingsIssue):
            fields.update(name for name in issue.fields if name in allowed)
    return sorted(allowed[name] for name in fields)
