"""Isolate every core test from local credentials and real databases."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import os

    for name in os.environ:
        if name.startswith("ATHLETE_LEDGER_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)

    def reject_oracle_connection(*args: object, **kwargs: object) -> None:
        pytest.fail("Tests must never connect to Oracle.")

    monkeypatch.setattr("oracledb.connect", reject_oracle_connection)
    monkeypatch.setattr("oracledb.init_oracle_client", reject_oracle_connection)
