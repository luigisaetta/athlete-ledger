"""
Author: L. Saetta
Date Last Modified: 2026-11-26
License: MIT
Description: Test the sync automation interface with synthetic data: --env-file, JSON
    output, and the per-database sync lock.
"""  # noqa: D205, D212

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from athlete_ledger import sync_cli
from athlete_ledger.cli import run
from athlete_ledger.locking import BUSY_EXIT_CODE, lock_path, sync_lock
from athlete_ledger.settings import DatabaseSettings
from athlete_ledger.sync_types import ENDPOINTS, ProviderError, Response

DAY = date(2026, 1, 1)
SECRETS = ("SYNTHETIC-PRIVATE", "56432", "8765.4321")


class FakeProvider:
    """Serve synthetic sleep and activity summaries without any SDK access."""

    sdk_version = "synthetic-1.0"

    def __init__(self) -> None:
        """Start with no requests, no failure and an open provider."""
        self.request_count = 0
        self.fail: tuple[str, date] | None = None
        self.closed = False

    def responses(self, dataset: str, start: date, end: date) -> Any:
        """Yield one synthetic response for the window, or fail when configured.

        Args:
            dataset: Dataset of the window.
            start: First day of the window.
            end: Last day of the window.

        Yields:
            Synthetic responses; activities end with an empty page.

        Raises:
            ProviderError: For the configured failing window.
        """
        self.request_count += 1
        if self.fail == (dataset, start):
            raise ProviderError(f"GET {ENDPOINTS[dataset]}: HTTP 503.")
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        params: dict[str, Any] = {"start_date": str(start), "end_date": str(end)}
        if dataset == "activities":
            rows: Any = [
                {
                    "activityId": day.toordinal(),
                    "activityName": "SYNTHETIC-PRIVATE-NAME",
                    "distance": 8765.4321,
                    "startTimeLocal": f"{day} 08:00:00",
                }
                for day in days
            ]
            params.update(start=0, limit=100)
            yield Response(ENDPOINTS[dataset], params, rows)
            self.request_count += 1
            yield Response(ENDPOINTS[dataset], {**params, "start": 100}, [])
            return
        rows = [{"calendarDate": str(day), "sleepTimeSeconds": 56432} for day in days]
        yield Response(ENDPOINTS[dataset], params, rows)

    def close(self) -> None:
        """Mark the provider closed."""
        self.closed = True


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a migrated SQLite ledger configured through an env file outside the cwd.

    Args:
        tmp_path: Per-test directory.
        monkeypatch: Used to run the CLI from another directory.

    Returns:
        The absolute path of the env file.
    """
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir()
    env_file = settings_dir / "ledger.env"
    database = tmp_path / "data" / "ledger.db"
    env_file.write_text(
        f"ATHLETE_LEDGER_DB_URL=sqlite:///{database}\nGARMIN_PERSONAL_API_KEY=SYNTHETIC-PRIVATE-KEY\n"
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert run(["--env-file", str(env_file), "db", "upgrade"]) == 0
    return env_file


def use_provider(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    """Replace the real provider with a synthetic one.

    Args:
        monkeypatch: Used to replace the provider class.

    Returns:
        The fake provider every sync will use.
    """
    provider = FakeProvider()
    monkeypatch.setattr(sync_cli, "PersonalProvider", lambda key: provider)
    return provider


def backfill(env_file: Path, *datasets: str) -> int:
    """Run a two-day JSON backfill through the env file.

    Args:
        env_file: Settings file of the ledger.
        *datasets: Datasets to sync.

    Returns:
        The exit code.
    """
    return run(
        [
            "--env-file",
            str(env_file),
            "sync",
            "backfill",
            "--since",
            str(DAY),
            "--until",
            str(DAY + timedelta(days=1)),
            "--dataset",
            *datasets,
            "--json",
        ]
    )


def test_env_file_is_read_outside_the_working_directory(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Read settings from --env-file while the cwd has no .env, and report status as JSON."""
    capsys.readouterr()
    assert not Path(".env").exists()
    assert run(["--env-file", str(ledger), "sync", "status", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["command"] == "status" and result["ok"] is True
    assert {item["dataset"] for item in result["datasets"]} >= {"sleep", "activities"}
    assert result["legs_stored"] == 0


def test_missing_env_file_fails_without_its_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Name only the option when the env file does not exist."""
    missing = tmp_path / "SYNTHETIC-PRIVATE-DIR" / "ledger.env"
    assert run(["--env-file", str(missing), "sync", "status", "--json"]) == 1
    output = capsys.readouterr()
    assert "Env file not found: --env-file." in output.err
    assert json.loads(output.out) == {
        "command": "status",
        "ok": False,
        "error": "Env file not found: --env-file.",
        "busy": False,
    }
    assert "SYNTHETIC-PRIVATE" not in output.out + output.err


def test_backfill_json_reports_counts_and_legs_only(
    ledger: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Print exactly one JSON object with counters, the legs step, and no values."""
    provider = use_provider(monkeypatch)
    capsys.readouterr()
    assert backfill(ledger, "activities", "sleep") == 0
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["command"] == "backfill" and result["ok"] is True
    assert [item["dataset"] for item in result["datasets"]] == ["activities", "sleep"]
    sleep = result["datasets"][1]
    assert sleep["ok"] is True and sleep["error"] is None and sleep["rows_inserted"] == 2
    assert result["legs"]["ok"] is True and result["legs"]["parents_checked"] == 0
    assert "[OK]" not in output.out
    for marker in SECRETS:
        assert marker not in output.out + output.err
    assert provider.closed


def test_failed_window_is_reported_in_json(
    ledger: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mark the failed dataset with its safe error and exit 1."""
    provider = use_provider(monkeypatch)
    provider.fail = ("sleep", DAY)
    capsys.readouterr()
    assert backfill(ledger, "sleep") == 1
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["ok"] is False
    assert result["datasets"][0]["ok"] is False
    assert "HTTP 503" in result["datasets"][0]["error"]
    assert "HTTP 503" in output.err
    assert result["legs"] is None


def test_legs_json_has_no_datasets(
    ledger: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Report the legs command with an empty dataset list."""
    use_provider(monkeypatch)
    capsys.readouterr()
    assert run(["--env-file", str(ledger), "sync", "legs", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["command"] == "legs" and result["datasets"] == []
    assert result["legs"]["detail_requests"] == 0


def test_settings_error_is_json_without_values(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Return the sanitized settings message as JSON."""
    monkeypatch.setenv("ATHLETE_LEDGER_DB_DSN", "synthetic_low")
    monkeypatch.setenv("ATHLETE_LEDGER_DB_PASSWORD", "SYNTHETIC-PRIVATE-PASSWORD")
    assert run(["sync", "status", "--json"]) == 1
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["ok"] is False and result["busy"] is False
    assert result["error"].startswith("Invalid database settings")
    assert "SYNTHETIC-PRIVATE" not in output.out + output.err


def test_held_lock_makes_a_second_sync_exit_75(
    ledger: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Refuse to start while another sync holds the lock, without touching the provider."""
    started: list[str] = []
    monkeypatch.setattr(
        sync_cli, "PersonalProvider", lambda key: started.append("provider") or FakeProvider()
    )
    settings = DatabaseSettings(_env_file=ledger)  # type: ignore[call-arg]
    capsys.readouterr()
    with sync_lock(settings):
        assert backfill(ledger, "sleep") == BUSY_EXIT_CODE
        rebuild = run(["--env-file", str(ledger), "sync", "rebuild"])
        assert rebuild == BUSY_EXIT_CODE
    output = capsys.readouterr()
    result = json.loads(output.out.splitlines()[0])
    assert result == {
        "command": "backfill",
        "ok": False,
        "error": "Another sync or rebuild is already running for this database.",
        "busy": True,
    }
    assert started == []
    assert run(["--env-file", str(ledger), "sync", "status", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert all(item["last_synced_date"] is None for item in status["datasets"])


def test_lock_is_released_after_a_sync(ledger: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Allow a following sync, and take the lock again once the run has finished."""
    use_provider(monkeypatch)
    assert backfill(ledger, "sleep") == 0
    assert backfill(ledger, "sleep") == 0
    settings = DatabaseSettings(_env_file=ledger)  # type: ignore[call-arg]
    with sync_lock(settings):
        assert lock_path(settings).is_file()


def test_lock_path_hides_the_database_identity(tmp_path: Path) -> None:
    """Name the lock by a short hash only, under the configured cache directory."""
    settings = DatabaseSettings(db_url=f"sqlite:///{tmp_path / 'SYNTHETIC-PRIVATE.db'}")
    path = lock_path(settings)
    assert path.parent == tmp_path / "cache" / "athlete-ledger"
    assert "SYNTHETIC-PRIVATE" not in path.name
    assert path.name.startswith("sync-") and path.suffix == ".lock"
