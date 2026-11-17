"""
Author: L. Saetta
Date Last Modified: 2026-11-17
License: MIT
Description: Exercise the installed database CLI with isolated SQLite configuration.
"""  # noqa: D205, D212

import os
import subprocess
from pathlib import Path

import pytest
from athlete_ledger.cli import run
from athlete_ledger.errors import DatabaseError


def test_sqlite_cli_upgrade_and_check(tmp_path: Path) -> None:
    """Reach the current Alembic head on a fresh SQLite file and permit repeated upgrades."""
    env = dict(os.environ, ATHLETE_LEDGER_DB_URL=f"sqlite:///{tmp_path / 'nested' / 'cli.db'}")
    for args in (["--version"], ["db", "upgrade"], ["db", "upgrade"], ["db", "check"]):
        result = subprocess.run(
            ["athlete-ledger", *args], env=env, text=True, capture_output=True, timeout=15
        )
        assert result.returncode == 0, result.stderr
    assert "Backend: sqlite" in result.stdout
    assert "Server version:" in result.stdout
    assert "Alembic revision: 0003_activity_legs" in result.stdout


def test_cli_offline_ignores_incomplete_oracle_settings(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify cli offline ignores incomplete oracle settings."""
    monkeypatch.setenv("ATHLETE_LEDGER_DB_DSN", "synthetic_low")
    assert run(["db", "sql"]) == 0
    assert "CREATE TABLE raw_response" in capsys.readouterr().out


def test_cli_configuration_error_is_sanitized(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify cli configuration error is sanitized."""
    monkeypatch.setenv("ATHLETE_LEDGER_DB_URL", "oracle+oracledb://user:synthetic-secret@host")
    assert run(["db", "check"]) == 1
    captured = capsys.readouterr()
    assert "synthetic-secret" not in captured.err + captured.out
    assert "Invalid database settings" in captured.err


def test_installed_cli_validation_failure_never_prints_values() -> None:
    """Verify installed cli validation failure never prints values."""
    env = dict(
        os.environ,
        ATHLETE_LEDGER_DB_URL="oracle+oracledb://synthetic-user:synthetic-password@synthetic-host",
    )
    result = subprocess.run(
        ["athlete-ledger", "db", "check"], env=env, text=True, capture_output=True, timeout=15
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "Invalid database settings: ATHLETE_LEDGER_DB_URL.\n"


def test_cli_driver_error_is_sanitized(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify cli driver error is sanitized."""

    def failed(*args: object, **kwargs: object) -> None:
        """Raise a database error carrying synthetic private text for output-redaction checks.

        Args:
            *args: Ignored arguments from the mocked database check.
            **kwargs: Ignored database-check keyword arguments.

        Raises:
            DatabaseError: Always, with synthetic password/payload markers that must not
                reach CLI output.
        """
        raise DatabaseError("synthetic-password synthetic-health-payload")

    monkeypatch.setattr("athlete_ledger.cli.check_database", failed)
    assert run(["db", "check"]) == 1
    captured = capsys.readouterr()
    assert "synthetic-password" not in captured.err + captured.out
    assert "synthetic-health-payload" not in captured.err + captured.out
    assert "operation failed" in captured.err


@pytest.mark.parametrize("command", ["check", "upgrade"])
def test_cli_lists_missing_oracle_environment_names(
    command: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify cli lists missing oracle environment names."""
    monkeypatch.setenv("ATHLETE_LEDGER_DB_DSN", "synthetic-private-alias")
    assert run(["db", command]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == (
        "Invalid database settings: ATHLETE_LEDGER_DB_PASSWORD, ATHLETE_LEDGER_DB_USER, "
        "ATHLETE_LEDGER_DB_WALLET_DIR, ATHLETE_LEDGER_DB_WALLET_PASSWORD.\n"
    )


@pytest.mark.parametrize(
    "missing", ["DB_USER", "DB_PASSWORD", "DB_WALLET_DIR", "DB_WALLET_PASSWORD"]
)
def test_cli_names_only_the_missing_oracle_variable(
    missing: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify cli names only the missing oracle variable."""
    values = {
        "DB_DSN": "synthetic-private-alias",
        "DB_USER": "synthetic-private-user",
        "DB_PASSWORD": "synthetic-private-password",
        "DB_WALLET_DIR": "/synthetic/private/wallet",
        "DB_WALLET_PASSWORD": "synthetic-private-wallet-password",
    }
    for name, value in values.items():
        if name != missing:
            monkeypatch.setenv("ATHLETE_LEDGER_" + name, value)
    assert run(["db", "check"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == f"Invalid database settings: ATHLETE_LEDGER_{missing}.\n"
    assert all(value not in output.err for value in values.values())


def test_cli_names_conflicting_selectors_without_values(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify cli names conflicting selectors without values."""
    monkeypatch.setenv("ATHLETE_LEDGER_DB_URL", "sqlite:///synthetic-private-path.db")
    monkeypatch.setenv("ATHLETE_LEDGER_DB_DSN", "synthetic-private-alias")
    monkeypatch.setenv("ATHLETE_LEDGER_DB_PASSWORD", "synthetic-private-password")
    assert run(["db", "check"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == (
        "Invalid database settings: ATHLETE_LEDGER_DB_DSN, ATHLETE_LEDGER_DB_URL.\n"
    )


@pytest.mark.parametrize("field", ["db_password", "db_dsn", "db_wallet_dir", "ro_db_password"])
def test_cli_field_validation_prints_only_allowlisted_names(
    field: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify cli field validation prints only allowlisted names."""
    from athlete_ledger.settings import Settings
    from pydantic import ValidationError

    def invalid() -> Settings:
        """Trigger validation for a list supplied to a scalar setting.

        Raises:
            ValidationError: Always, for the selected field containing synthetic private
                values.
        """
        return Settings(**{field: ["synthetic-password", "synthetic-value"]})

    monkeypatch.setattr("athlete_ledger.cli.Settings", invalid)
    assert run(["db", "check"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == f"Invalid database settings: ATHLETE_LEDGER_{field.upper()}.\n"

    # Unknown error locations and contexts must not be echoed as environment names.
    def unknown() -> None:
        """Raise a validation error with an unrecognized location and private context.

        Raises:
            ValidationError: Always, to verify that unknown locations and context values are
                not echoed.
        """
        raise ValidationError.from_exception_data(
            "Settings",
            [
                {
                    "type": "value_error",
                    "loc": ("synthetic-password",),
                    "input": "synthetic-value",
                    "ctx": {"error": ValueError("synthetic-secret")},
                }
            ],
        )

    monkeypatch.setattr("athlete_ledger.cli.Settings", unknown)
    assert run(["db", "check"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "Invalid database settings.\n"


@pytest.mark.parametrize("command", ["check", "upgrade"])
@pytest.mark.parametrize("code", ["ORA-01017", "DPY-6005", None])
def test_cli_preserves_only_database_codes_through_wrappers(
    command: str,
    code: str | None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify cli preserves only database codes through wrappers."""
    from sqlalchemy.exc import OperationalError

    def fail_connect(*args: object, **kwargs: object) -> None:
        """Raise a wrapped driver error containing both a code and private diagnostic markers.

        Args:
            *args: Ignored SQLAlchemy connection arguments.
            **kwargs: Ignored SQLAlchemy connection options.

        Raises:
            OperationalError: Always, wrapping synthetic driver text, SQL and bound
                parameters.
        """
        message = f"{code or 'driver failure'}: synthetic-password synthetic-private-dsn"
        raise OperationalError(
            "SELECT 'synthetic-sensitive-sql'",
            {"payload": "synthetic-health-value ORA-99999"},
            Exception(message),
        )

    # Exercise the real migration/check wrapper, which used to discard the code.
    monkeypatch.setattr("sqlalchemy.engine.Engine.connect", fail_connect)
    assert run(["db", command]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    suffix = f": {code}" if code else ""
    assert output.err == f"Database operation failed{suffix}.\n"


@pytest.mark.parametrize("code", ["ORA-01017", "DPY-6005"])
@pytest.mark.parametrize("wrapped", [False, True])
def test_cli_structured_driver_codes_without_message_or_sql(
    code: str,
    wrapped: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify cli structured driver codes without message or sql."""
    from types import SimpleNamespace

    import oracledb
    from sqlalchemy.exc import OperationalError

    def failed(*args: object, **kwargs: object) -> None:
        """Raise a structured driver error, optionally wrapped by SQLAlchemy.

        Args:
            *args: Ignored database-check arguments.
            **kwargs: Ignored database-check options.

        Raises:
            OperationalError: If the test requests a SQLAlchemy wrapper around the driver
                error.
            oracledb.DatabaseError: Otherwise, with a structured code and synthetic private
                message.
        """
        original = oracledb.DatabaseError(
            SimpleNamespace(
                full_code=code,
                message="synthetic-password synthetic-host synthetic-health-value",
            )
        )
        if wrapped:
            raise OperationalError(
                "synthetic-private-sql", {"password": "synthetic-secret"}, original
            )
        raise original

    monkeypatch.setattr("athlete_ledger.cli.check_database", failed)
    assert run(["db", "check"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == f"Database operation failed: {code}.\n"


@pytest.mark.parametrize(
    "error_type", [PermissionError, FileNotFoundError, IsADirectoryError, OSError]
)
def test_cli_os_error_type_without_message_or_filename(
    error_type: type[OSError], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify cli os error type without message or filename."""

    def failed(*args: object, **kwargs: object) -> None:
        """Raise the parameterized filesystem error with private message/path markers.

        Args:
            *args: Ignored engine-factory arguments.
            **kwargs: Ignored engine-factory options.

        Raises:
            OSError: Always, using the selected subclass to test type-only filesystem
                diagnostics.
        """
        raise error_type(
            None, "synthetic-password synthetic-health-value", "/synthetic/private/wallet"
        )

    monkeypatch.setattr("athlete_ledger.cli.create_db_engine", failed)
    assert run(["db", "check"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == f"Database operation failed: {error_type.__name__}.\n"
