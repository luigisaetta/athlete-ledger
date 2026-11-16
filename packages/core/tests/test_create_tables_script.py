"""Validate the Oracle setup launcher without a real Oracle connection."""

import os
import runpy
import subprocess
from pathlib import Path
from unittest.mock import call, patch

import pytest
from athlete_ledger.cli import run

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "create_tables.py"


@pytest.mark.parametrize(
    ("statuses", "expected_status", "expected_calls"),
    [([0, 0], 0, 2), ([1], 1, 1), ([0, 1], 1, 2)],
)
def test_script_sequences_migration_and_check(
    statuses: list[int],
    expected_status: int,
    expected_calls: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("sys.argv", [str(SCRIPT)])
    with patch("athlete_ledger.cli.run", side_effect=statuses) as dispatch:
        with pytest.raises(SystemExit) as error:
            runpy.run_path(str(SCRIPT), run_name="__main__")
    assert error.value.code == expected_status
    assert ("[OK] Oracle schema setup completed." in capsys.readouterr().out) == (
        statuses == [0, 0]
    )
    assert (
        dispatch.call_args_list
        == [
            call(["db", "upgrade"], require_oracle=True, show_banner=False),
            call(["db", "check"], require_oracle=True, show_banner=False),
        ][:expected_calls]
    )


@pytest.mark.parametrize("command", ["upgrade", "check"])
def test_oracle_guard_prevents_sqlite_creation(
    command: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "must-not-exist.db"
    monkeypatch.setenv("ATHLETE_LEDGER_DB_URL", f"sqlite:///{database}")
    with patch("athlete_ledger.cli.create_db_engine") as factory:
        assert run(["db", command], require_oracle=True) == 1
    factory.assert_not_called()
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "Invalid database settings: ATHLETE_LEDGER_DB_DSN.\n"
    assert not database.exists()


def test_installed_script_refuses_missing_oracle_configuration(tmp_path: Path) -> None:
    # No local .env or real process settings are used by the core fixture.
    env = dict(os.environ, ATHLETE_LEDGER_DB_PASSWORD="synthetic-private-password")
    result = subprocess.run(
        ["python", str(SCRIPT)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "Invalid database settings: ATHLETE_LEDGER_DB_DSN.\n"
    assert not (tmp_path / "data").exists()


def test_script_help_does_not_load_settings_or_connect() -> None:
    with patch("athlete_ledger.cli.run") as dispatch, patch("sys.argv", [str(SCRIPT), "--help"]):
        with pytest.raises(SystemExit) as error:
            runpy.run_path(str(SCRIPT), run_name="__main__")
    assert error.value.code == 0
    dispatch.assert_not_called()
