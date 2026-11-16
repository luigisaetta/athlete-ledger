"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Isolate every core test from local credentials and real databases.
"""  # noqa: D205, D212

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Handle isolated configuration.

    Args:
        monkeypatch: Monkeypatch for this operation.
        tmp_path: Tmp path for this operation.
    """
    import os

    for name in os.environ:
        if name.startswith("ATHLETE_LEDGER_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)

    def reject_oracle_connection(*args: object, **kwargs: object) -> None:
        """Handle reject oracle connection.

        Args:
            *args: Args for this operation.
            **kwargs: Kwargs for this operation.
        """
        pytest.fail("Tests must never connect to Oracle.")

    monkeypatch.setattr("oracledb.connect", reject_oracle_connection)
    monkeypatch.setattr("oracledb.init_oracle_client", reject_oracle_connection)
