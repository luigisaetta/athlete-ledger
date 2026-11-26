"""
Author: L. Saetta
Date Last Modified: 2026-11-26
License: MIT
Description: Isolate every core test from local credentials and real databases.
"""  # noqa: D205, D212

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate each core test from process credentials, real databases and repository settings.

    Args:
        monkeypatch: Pytest fixture restoring environment, cwd and driver replacements
            after the test.
        tmp_path: Per-test working directory for synthetic settings and SQLite files.
    """
    import os

    for name in os.environ:
        if name.startswith("ATHLETE_LEDGER_"):
            monkeypatch.delenv(name)
    monkeypatch.delenv("GARMIN_PERSONAL_API_KEY", raising=False)
    # Sync locks go under the test's own cache, never the user's ~/.cache.
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.chdir(tmp_path)

    def reject_oracle_connection(*args: object, **kwargs: object) -> None:
        """Fail immediately if a test attempts an Oracle connection or client initialization.

        Args:
            *args: Ignored positional driver arguments; no values are rendered.
            **kwargs: Ignored keyword driver arguments, which may contain synthetic
                credentials.

        Raises:
            pytest.fail.Exception: Always, because live Oracle connections are forbidden in
                unit tests.
        """
        pytest.fail("Tests must never connect to Oracle.")

    monkeypatch.setattr("oracledb.connect", reject_oracle_connection)
    monkeypatch.setattr("oracledb.init_oracle_client", reject_oracle_connection)


@pytest.fixture(autouse=True)
def block_provider_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent accidental network requests and real credential discovery.

    Args:
        monkeypatch: Temporary replacements restored after each test.
    """

    def reject(*args: object, **kwargs: object) -> None:
        """Reject external I/O during synthetic tests.

        Args:
            *args: Ignored request arguments.
            **kwargs: Ignored request keyword arguments.

        Raises:
            AssertionError: Always, because tests must inject fakes.
        """
        raise AssertionError("Tests must inject a fake provider and credential resolver.")

    monkeypatch.setattr("requests.sessions.Session.request", reject)
    monkeypatch.setattr("garmin_personal_api.PersonalApiAuthenticator.obtain_token", reject)
    monkeypatch.setattr("athlete_ledger.providers.garmin_connect.resolve_api_key", reject)
