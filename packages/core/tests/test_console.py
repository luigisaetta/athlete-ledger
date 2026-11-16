"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Terminal presentation must preserve plain output and safe diagnostics.
"""  # noqa: D205, D212

import io

import pytest
from athlete_ledger.cli import run
from athlete_ledger.console import banner, info, ok
from athlete_ledger.db.migrate import offline_sql


class Terminal(io.StringIO):
    """Provide an in-memory stdout stream that reports interactive-terminal capabilities."""

    def isatty(self) -> bool:
        """Report an interactive stream to exercise ANSI color and banner branches.

        Returns:
            True regardless of the host test runner's actual terminal.
        """
        return True


def test_interactive_banner_and_green_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify interactive banner and green success."""
    output = Terminal()
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("NO_COLOR", raising=False)
    banner("Database check")
    ok("Connection verified.")
    assert "ATHLETE LEDGER" in output.getvalue()
    assert "Database check" in output.getvalue()
    assert "\033[1;32m[OK]\033[0m Connection verified." in output.getvalue()


@pytest.mark.parametrize("condition", ["no_color", "no_color_empty", "dumb", "redirected"])
def test_plain_output_when_color_is_disabled(
    condition: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify plain output when color is disabled."""
    output = io.StringIO() if condition == "redirected" else Terminal()
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setenv("TERM", "dumb" if condition == "dumb" else "xterm-256color")
    monkeypatch.delenv("NO_COLOR", raising=False)
    if condition.startswith("no_color"):
        monkeypatch.setenv("NO_COLOR", "" if condition == "no_color_empty" else "1")
    banner("Database check")
    ok("Connection verified.")
    assert "\033[" not in output.getvalue()
    assert "[OK] Connection verified." in output.getvalue()
    if condition == "redirected":
        assert "ATHLETE LEDGER" not in output.getvalue()


def test_unversioned_schema_is_neutral(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify unversioned schema is neutral."""
    monkeypatch.setenv("ATHLETE_LEDGER_DB_URL", "sqlite://")
    assert run(["db", "check"]) == 0
    output = capsys.readouterr().out
    assert "[OK] Database connection verified." in output
    assert "[INFO] Alembic revision: unversioned" in output
    assert "[OK] Alembic revision" not in output


def test_sql_and_version_are_clean_even_on_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify sql and version are clean even on terminal."""
    output = Terminal()
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert run(["db", "sql"]) == 0
    assert output.getvalue() == offline_sql("oracle")
    output.seek(0)
    output.truncate()
    with pytest.raises(SystemExit) as error:
        run(["--version"])
    assert error.value.code == 0
    assert output.getvalue() == "athlete-ledger 0.0.1\n"


def test_failed_validation_has_no_success_label_or_values(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify failed validation has no success label or values."""
    output = Terminal()
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setenv("ATHLETE_LEDGER_DB_DSN", "synthetic-private-alias")
    monkeypatch.setenv("ATHLETE_LEDGER_DB_PASSWORD", "synthetic-private-password")
    assert run(["db", "check"]) == 1
    stderr = capsys.readouterr().err
    assert "[OK]" not in output.getvalue()
    assert "ATHLETE_LEDGER_DB_WALLET_PASSWORD" in stderr
    for secret in ("synthetic-private-password", "synthetic-private-alias"):
        assert secret not in output.getvalue() + stderr


def test_info_has_no_success_marker(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify info has no success marker."""
    info("No migration revision found.")
    output = capsys.readouterr().out
    assert "[INFO]" in output
    assert "[OK]" not in output
