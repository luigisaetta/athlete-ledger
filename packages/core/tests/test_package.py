"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Smoke tests for the installed athlete-ledger package.
"""  # noqa: D205, D212

import subprocess

from athlete_ledger import __version__


def test_version() -> None:
    """Verify version."""
    assert __version__ == "0.0.1"


def test_console_entry_point() -> None:
    """Verify console entry point."""
    result = subprocess.run(
        ["athlete-ledger"], capture_output=True, text=True, check=True, timeout=10
    )
    assert result.stdout == f"athlete-ledger {__version__}\n"
    assert result.stderr == ""
