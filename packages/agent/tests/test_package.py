"""Smoke tests for the installed athlete-ledger-agent package."""

import subprocess

from athlete_ledger_agent import __version__


def test_version() -> None:
    assert __version__ == "0.0.1"


def test_console_entry_point() -> None:
    result = subprocess.run(
        ["athlete-ledger-agent"], capture_output=True, text=True, check=True, timeout=10
    )
    assert result.stdout == f"athlete-ledger-agent {__version__}\n"
    assert result.stderr == ""
