"""Exercise the installed database CLI with isolated SQLite configuration."""

import os
import subprocess
from pathlib import Path

import pytest
from athlete_ledger.cli import run
from athlete_ledger.errors import DatabaseError


def test_sqlite_cli_upgrade_and_check(tmp_path: Path) -> None:
    env = dict(os.environ, ATHLETE_LEDGER_DB_URL=f"sqlite:///{tmp_path / 'nested' / 'cli.db'}")
    for args in (["--version"], ["db", "upgrade"], ["db", "upgrade"], ["db", "check"]):
        result = subprocess.run(
            ["athlete-ledger", *args], env=env, text=True, capture_output=True, timeout=15
        )
        assert result.returncode == 0, result.stderr
    assert "Backend: sqlite" in result.stdout
    assert "Server version:" in result.stdout
    assert "Alembic revision: 0001_ledger_store" in result.stdout


def test_cli_offline_ignores_incomplete_oracle_settings(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ATHLETE_LEDGER_DB_DSN", "synthetic_low")
    assert run(["db", "sql"]) == 0
    assert "CREATE TABLE raw_response" in capsys.readouterr().out


def test_cli_configuration_error_is_sanitized(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ATHLETE_LEDGER_DB_URL", "oracle+oracledb://user:synthetic-secret@host")
    assert run(["db", "check"]) == 1
    captured = capsys.readouterr()
    assert "synthetic-secret" not in captured.err + captured.out
    assert "Invalid database settings" in captured.err


def test_cli_driver_error_is_sanitized(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def failed(*args: object, **kwargs: object) -> None:
        raise DatabaseError("synthetic-password synthetic-health-payload")

    monkeypatch.setattr("athlete_ledger.cli.check_database", failed)
    assert run(["db", "check"]) == 1
    captured = capsys.readouterr()
    assert "synthetic-password" not in captured.err + captured.out
    assert "synthetic-health-payload" not in captured.err + captured.out
    assert "operation failed" in captured.err
