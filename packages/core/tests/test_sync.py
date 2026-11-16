"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Test idempotence, chunk transactions, state and CLI output on SQLite.
"""  # noqa: D205, D212

import copy
from datetime import UTC, date, datetime, timedelta
from unittest.mock import Mock

import pytest
from athlete_ledger.cli import run
from athlete_ledger.db import create_db_engine
from athlete_ledger.db.migrate import upgrade
from athlete_ledger.db.models import Activity, DailySleep, RawResponse, SyncState
from athlete_ledger.settings import Settings
from athlete_ledger.storage import content_hash
from athlete_ledger.sync import synchronize
from athlete_ledger.sync_types import DATASETS, ENDPOINTS, PROVIDER, ProviderError, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

DAY = date(2026, 1, 1)
NOW = datetime(2026, 1, 29, 12, tzinfo=UTC)


class FakeProvider:
    """Generate deterministic summary records and configurable failures without SDK access."""

    sdk_version = "synthetic-1.0"

    def __init__(self):
        """Initialize request history, per-day overrides, empty-response and failure controls."""
        self.request_count = 0
        self.calls = []
        self.changes = {}
        self.extra = False
        self.empty = False
        self.fail = None
        self.closed = False

    def responses(self, dataset, start, end):
        """Generate dated summary payloads and an empty terminating activity page.

        Args:
            dataset: Supported dataset determining the synthetic record fields and envelope.
            start: Inclusive first date for generated records.
            end: Inclusive last date for generated records.

        Yields:
            Response objects with synthetic payloads and SDK-call parameters; nonempty
            activities add an empty terminal page.

        Raises:
            ProviderError: If dataset and start match the configured failing chunk.
        """
        self.request_count += 1
        self.calls.append((dataset, start, end))
        if self.fail == (dataset, start):
            raise ProviderError(f"GET {ENDPOINTS[dataset]}: HTTP 503.")
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        if dataset == "activities":
            rows = [
                {
                    "activityId": day.toordinal(),
                    "activityName": "SYNTHETIC-PRIVATE-NAME",
                    "distance": 8765.4321,
                    "startTimeLocal": str(day) + " 08:00:00",
                }
                for day in days
            ]
        elif dataset == "sleep":
            rows = [
                {"calendarDate": str(day), "sleepTimeSeconds": self.changes.get(day, 56432)}
                for day in days
            ]
        elif dataset == "hrv":
            rows = [{"calendarDate": str(day), "lastNightAvg": 221.75} for day in days]
        else:
            rows = [
                {"calendarDate": str(day), "values": {"overallStressLevel": 83}} for day in days
            ]
        if self.extra:
            for row in rows:
                row["ignoredMetadata"] = "SYNTHETIC-PRIVATE-METADATA"
        if self.empty:
            rows = []
        payload = {"hrvSummaries": rows} if dataset == "hrv" else rows
        params = {"start_date": str(start), "end_date": str(end)}
        if dataset == "activities":
            params.update(start=0, limit=100)
        yield Response(ENDPOINTS[dataset], params, copy.deepcopy(payload))
        if dataset == "activities" and rows:
            self.request_count += 1
            yield Response(ENDPOINTS[dataset], {**params, "start": 100}, [])

    def close(self):
        """Mark the fake provider closed so CLI cleanup can be asserted."""
        self.closed = True


@pytest.fixture
def engine(tmp_path):
    """Provide a migrated temporary SQLite ledger and dispose it after the test.

    Args:
        tmp_path: Per-test directory containing the synthetic.db file.

    Yields:
        A SQLAlchemy Engine with the initial ledger migration applied.
    """
    instance = create_db_engine(
        Settings(db_url=f"sqlite:///{tmp_path / 'synthetic.db'}", _env_file=None)
    )
    upgrade(instance)
    yield instance
    instance.dispose()


def backfill(engine, provider, days=14, datasets=DATASETS, clock=lambda: NOW):
    """Run a synthetic backfill starting at DAY with a deterministic local end-date ceiling.

    Args:
        engine: Migrated temporary SQLite engine.
        provider: Fake response provider and request counter.
        days: Inclusive range length starting at DAY; defaults to fourteen days.
        datasets: Dataset names to synchronize, in processing order.
        clock: Aware timestamp callable for raw provenance and sync-state updates.

    Returns:
        DatasetResult list from synchronize, with committed counters and safe failure
        information.
    """
    return synchronize(
        engine,
        provider,
        datasets,
        since=DAY,
        until=DAY + timedelta(days=days - 1),
        today=NOW.date(),
        clock=clock,
    )


def count(session, model):
    """Count rows for an ORM model in the current test transaction.

    Args:
        session: Open SQLAlchemy session used to execute the aggregate SELECT.
        model: Mapped ORM class whose table rows should be counted.

    Returns:
        The number of rows in the model table.
    """
    return session.scalar(select(func.count()).select_from(model))


def test_identical_runs_are_idempotent(engine):
    """Verify identical runs are idempotent."""
    provider = FakeProvider()
    first = backfill(engine, provider)
    assert all(not result.error for result in first)
    with Session(engine) as session:
        raw_count = count(session, RawResponse)
        source = {
            row.calendar_date: (row.source_response_id, row.updated_at)
            for row in session.scalars(select(DailySleep))
        }
    second = backfill(engine, provider, clock=lambda: NOW + timedelta(hours=1))
    assert all(
        r.counts.updated == 0 and r.counts.inserted == 0 and r.counts.raw_stored == 0
        for r in second
    )
    assert all(r.counts.raw_unchanged > 0 for r in second)
    with Session(engine) as session:
        assert count(session, RawResponse) == raw_count
        assert count(session, Activity) == 14
        assert source == {
            row.calendar_date: (row.source_response_id, row.updated_at)
            for row in session.scalars(select(DailySleep))
        }
        assert all(
            row.last_status == "ok" and row.last_synced_date == date(2026, 1, 14)
            for row in session.scalars(select(SyncState))
        )


def test_changed_payload_updates_only_changed_row_and_latest_hash(engine):
    """Verify changed payload updates only changed row and latest hash."""
    provider = FakeProvider()
    backfill(engine, provider, datasets=["sleep"])
    with Session(engine) as session:
        original = {
            row.calendar_date: (row.source_response_id, row.updated_at)
            for row in session.scalars(select(DailySleep))
        }
    provider.changes[DAY] = 12345
    now = NOW + timedelta(hours=1)
    results = backfill(engine, provider, datasets=["sleep"], clock=lambda: now)
    assert results[0].counts.updated == 1
    assert results[0].counts.raw_stored == 1
    assert results[0].counts.unchanged == 6
    with Session(engine) as session:
        changed = session.get(DailySleep, (PROVIDER, DAY))
        assert changed.sleep_time_s == 12345 and changed.updated_at == now
        assert changed.source_response_id != original[DAY][0]
        assert all(
            (row.source_response_id, row.updated_at) == original[row.calendar_date]
            for row in session.scalars(select(DailySleep))
            if row.calendar_date != DAY
        )
    provider.extra = True
    results = backfill(engine, provider, datasets=["sleep"], clock=lambda: now + timedelta(hours=1))
    assert results[0].counts.updated == 0 and results[0].counts.unchanged == 14
    with Session(engine) as session:
        changed_source = session.get(DailySleep, (PROVIDER, DAY)).source_response_id
    provider.extra = False
    provider.changes.clear()
    results = backfill(engine, provider, datasets=["sleep"])
    assert results[0].counts.raw_stored == 2  # Compare with latest response, not any historic hash.
    assert results[0].counts.updated == 1
    with Session(engine) as session:
        assert session.get(DailySleep, (PROVIDER, DAY)).source_response_id != changed_source


def test_failed_middle_chunk_keeps_previous_state_and_other_datasets_run(engine):
    """Verify failed middle chunk keeps previous state and other datasets run."""
    provider = FakeProvider()
    provider.fail = ("sleep", DAY + timedelta(days=7))
    results = backfill(engine, provider, days=29, datasets=["sleep", "stress"])
    assert results[0].error and results[0].counts.chunks == 1
    assert not results[1].error and results[1].counts.chunks == 5
    with Session(engine) as session:
        state = session.get(SyncState, (PROVIDER, "sleep"))
        assert state.last_status == "failed" and state.last_synced_date == DAY + timedelta(days=6)
        assert state.last_run_at.tzinfo is UTC
        assert count(session, DailySleep) == 7
    provider.fail = None
    resumed = backfill(engine, provider, days=29, datasets=["sleep"])
    assert resumed[0].counts.inserted == 22 and resumed[0].counts.raw_unchanged == 1
    with Session(engine) as session:
        assert count(session, DailySleep) == 29
        assert session.get(SyncState, (PROVIDER, "sleep")).last_status == "ok"


def test_failure_after_first_page_rolls_back_entire_chunk(engine):
    """Verify failure after first page rolls back entire chunk."""

    class BrokenPages(FakeProvider):
        """Fail after yielding parent pages to test rollback before the chunk commit."""

        def responses(self, dataset, start, end):
            """Yield all parent responses and then interrupt the same uncommitted chunk.

            Args:
                dataset: Dataset forwarded to the parent fake provider.
                start: Inclusive chunk start forwarded to the parent.
                end: Inclusive chunk end forwarded to the parent.

            Yields:
                The parent provider's synthetic response pages, including its activity
                terminator.

            Raises:
                RuntimeError: Always after the parent iterator completes, simulating a late
                    chunk failure.
            """
            yield from super().responses(dataset, start, end)
            raise RuntimeError("SYNTHETIC-PRIVATE-FAILURE")

    result = backfill(engine, BrokenPages(), days=7, datasets=["activities"])[0]
    assert result.error and "SYNTHETIC-PRIVATE" not in result.error
    assert result.counts.raw_stored == result.counts.inserted == result.counts.chunks == 0
    with Session(engine) as session:
        assert count(session, RawResponse) == count(session, Activity) == 0
        state = session.get(SyncState, (PROVIDER, "activities"))
        assert state.last_status == "failed" and state.last_synced_date is None


def test_incremental_window_and_missing_state_are_safe(engine):
    """Verify incremental window and missing state are safe."""
    provider = FakeProvider()
    missing = synchronize(engine, provider, ["sleep"], today=NOW.date())
    assert "sync backfill" in missing[0].error
    assert not provider.calls
    backfill(engine, provider, days=27, datasets=["sleep"])
    provider.calls.clear()
    results = synchronize(engine, provider, ["sleep"], today=NOW.date(), clock=lambda: NOW)
    assert provider.calls == [("sleep", date(2026, 1, 25), date(2026, 1, 29))]
    assert results[0].counts.inserted == 2 and results[0].counts.updated == 0
    repeated = synchronize(engine, provider, ["sleep"], today=NOW.date(), clock=lambda: NOW)
    assert repeated[0].counts.updated == 0 and repeated[0].counts.inserted == 0


def test_empty_later_response_never_deletes_rows(engine):
    """Verify empty later response never deletes rows."""
    provider = FakeProvider()
    backfill(engine, provider, days=1, datasets=["sleep"])
    provider.empty = True
    result = backfill(engine, provider, days=1, datasets=["sleep"])[0]
    assert result.counts.raw_stored == 1
    with Session(engine) as session:
        assert count(session, DailySleep) == 1
        assert session.scalars(select(RawResponse).order_by(RawResponse.id.desc())).first().is_empty


def test_canonical_hash_is_stable_and_empty_body_is_null():
    """Verify canonical hash is stable and empty body is null."""
    import hashlib

    assert content_hash({"b": 2, "a": "é"}) == content_hash({"a": "é", "b": 2})
    assert content_hash(None) == hashlib.sha256(b"null").hexdigest()


def test_activity_duplicates_across_pages_are_deduplicated(engine):
    """Verify activity duplicates across pages are deduplicated."""

    class Pages(FakeProvider):
        """Supply overlapping activity pages to exercise chunk-level ID deduplication."""

        def responses(self, dataset, start, end):
            """Yield two overlapping activity pages followed by an empty terminal page.

            Args:
                dataset: Activities dataset whose fixed endpoint name labels the pages.
                start: Requested chunk start; accepted for the provider interface but not used
                    by the fixed records.
                end: Requested chunk end; accepted for the provider interface but not used by
                    the fixed records.

            Yields:
                Three Response objects with activity IDs [1, 2], [2, 3] and an empty final
                payload.
            """
            self.request_count += 3
            for offset, ids in [(0, [1, 2]), (100, [2, 3]), (200, [])]:
                yield Response(
                    ENDPOINTS[dataset],
                    {"start": offset, "limit": 100},
                    [{"activityId": i, "distance": 0} for i in ids],
                )

    result = backfill(engine, Pages(), days=1, datasets=["activities"])[0]
    assert not result.error and result.counts.inserted == 3
    with Session(engine) as session:
        assert count(session, RawResponse) == 3
        assert count(session, Activity) == 3


def test_cli_summary_status_and_failures_do_not_leak(engine, monkeypatch, capsys):
    """Verify cli summary status and failures do not leak."""
    provider = FakeProvider()
    monkeypatch.setenv("ATHLETE_LEDGER_DB_URL", str(engine.url))
    monkeypatch.setenv("GARMIN_PERSONAL_API_KEY", "SYNTHETIC-PRIVATE-KEY")
    factory = Mock(return_value=provider)
    monkeypatch.setattr("athlete_ledger.sync_cli.PersonalProvider", factory)
    assert run(["sync", "backfill", "--since", str(DAY), "--until", "2026-01-14"]) == 0
    output = capsys.readouterr()
    assert "rows_updated=0" in output.out and "requests=" in output.out
    for marker in ("SYNTHETIC-PRIVATE", "8765.4321", "56432", "221.75"):
        assert marker not in output.out + output.err
    assert provider.closed
    factory.reset_mock()
    assert run(["sync", "status"]) == 0
    factory.assert_not_called()
    assert "last_synced_date=2026-01-14" in capsys.readouterr().out
    provider.fail = ("sleep", DAY)
    assert run(["sync", "backfill", "--since", str(DAY), "--until", str(DAY)]) == 1
    output = capsys.readouterr()
    assert "HTTP 503" in output.err
    assert "stress: chunks=1" in output.out


@pytest.mark.parametrize(
    "dates",
    [
        ["--since", "9999-01-01"],
        ["--since", "2026-01-02", "--until", "2026-01-01"],
    ],
)
def test_cli_rejects_invalid_ranges_before_provider(engine, monkeypatch, capsys, dates):
    """Verify cli rejects invalid ranges before provider."""
    monkeypatch.setenv("ATHLETE_LEDGER_DB_URL", str(engine.url))
    factory = Mock()
    monkeypatch.setattr("athlete_ledger.sync_cli.PersonalProvider", factory)
    assert run(["sync", "backfill", *dates]) == 1
    factory.assert_not_called()
    assert "Invalid sync date range" in capsys.readouterr().err


def test_cli_date_parse_error_does_not_echo_invalid_input(capsys):
    """Verify cli date parse error does not echo invalid input."""
    with pytest.raises(SystemExit):
        run(["sync", "backfill", "--since", "SYNTHETIC-PRIVATE-INPUT"])
    assert "SYNTHETIC-PRIVATE-INPUT" not in capsys.readouterr().err


def test_invalid_chunk_rolls_back_raw_and_state(engine):
    """Verify a malformed daily date cannot commit a raw response or normalized row."""

    class InvalidDates(FakeProvider):
        """Supply one valid record and one out-of-range record."""

        def responses(self, dataset, start, end):
            """Yield a synthetic invalid daily response.

            Args:
                dataset: Dataset being fetched.
                start: Requested start date.
                end: Requested end date.

            Yields:
                A response containing an invalid calendar date.
            """
            self.request_count += 1
            yield Response(
                ENDPOINTS[dataset],
                {},
                [
                    {"calendarDate": str(start)},
                    {"calendarDate": str(end + timedelta(days=1))},
                ],
            )

    result = backfill(engine, InvalidDates(), days=1, datasets=["sleep"])[0]
    assert result.error and result.counts.raw_stored == 0
    with Session(engine) as session:
        assert count(session, RawResponse) == count(session, DailySleep) == 0
        assert session.get(SyncState, (PROVIDER, "sleep")).last_status == "failed"


def test_cli_incremental_selected_datasets(engine, monkeypatch, capsys):
    """Verify repeated dataset options select only requested incremental datasets."""
    import athlete_ledger.sync_cli as cli_module

    class FrozenDate(date):
        """Freeze the local calendar without changing production UTC timestamps."""

        @classmethod
        def today(cls):
            """Return the synthetic local date.

            Returns:
                The deterministic sync end date.
            """
            return NOW.date()

    provider = FakeProvider()
    backfill(engine, provider, days=27, datasets=["sleep", "stress"])
    provider.calls.clear()
    monkeypatch.setenv("ATHLETE_LEDGER_DB_URL", str(engine.url))
    monkeypatch.setattr(cli_module, "date", FrozenDate)
    monkeypatch.setattr(cli_module, "PersonalProvider", lambda key: provider)
    assert run(["sync", "run", "--dataset", "sleep", "--dataset", "stress"]) == 0
    assert {call[0] for call in provider.calls} == {"sleep", "stress"}
    assert all(call[1:] == (date(2026, 1, 25), NOW.date()) for call in provider.calls)
    assert "rows_updated=0" in capsys.readouterr().out


def test_cli_invalid_dataset_does_not_echo_value(capsys):
    """Verify invalid dataset input cannot leak an arbitrary argument string."""
    with pytest.raises(SystemExit):
        run(["sync", "run", "--dataset", "SYNTHETIC-PRIVATE-ARGUMENT"])
    assert "SYNTHETIC-PRIVATE-ARGUMENT" not in capsys.readouterr().err


def test_older_backfill_preserves_progress_and_incremental_window(engine):
    """Keep recent progress and the same incremental range after an older backfill."""
    provider = FakeProvider()
    recent = synchronize(
        engine,
        provider,
        ["sleep"],
        since=date(2026, 1, 20),
        until=NOW.date(),
        today=NOW.date(),
        clock=lambda: NOW,
    )
    assert not recent[0].error
    provider.calls.clear()
    baseline = synchronize(engine, provider, ["sleep"], today=NOW.date(), clock=lambda: NOW)
    assert not baseline[0].error
    baseline_window = list(provider.calls)
    assert baseline_window == [("sleep", date(2026, 1, 27), NOW.date())]

    later_run_time = NOW + timedelta(hours=1)
    older = backfill(engine, provider, days=14, datasets=["sleep"], clock=lambda: later_run_time)
    assert not older[0].error and older[0].counts.chunks == 2
    with Session(engine) as session:
        state = session.get(SyncState, (PROVIDER, "sleep"))
        assert state.last_synced_date == NOW.date()
        assert state.last_status == "ok"
        assert state.last_run_at == later_run_time

    provider.calls.clear()
    following = synchronize(
        engine, provider, ["sleep"], today=NOW.date(), clock=lambda: later_run_time
    )
    assert not following[0].error
    assert provider.calls == baseline_window
    assert following[0].counts.inserted == following[0].counts.updated == 0
    with Session(engine) as session:
        assert session.get(SyncState, (PROVIDER, "sleep")).last_synced_date == NOW.date()
