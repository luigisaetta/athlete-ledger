"""
Author: L. Saetta
Date Last Modified: 2026-11-17
License: MIT
Description: Verify ordered database-only replay, net idempotence and rollback with synthetic data.
"""  # noqa: D205, D212

from datetime import UTC, date, datetime, timedelta

import pytest
from athlete_ledger.db import create_db_engine
from athlete_ledger.db.migrate import upgrade
from athlete_ledger.db.models import Activity, DailySleep, RawResponse, SyncState
from athlete_ledger.rebuild import rebuild
from athlete_ledger.settings import Settings
from athlete_ledger.sync_types import DATASETS, ENDPOINTS, PROVIDER
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

DAY = date(2026, 1, 2)
NOW = datetime(2026, 1, 3, 12, tzinfo=UTC)


@pytest.fixture
def engine(tmp_path):
    """Provide a migrated synthetic SQLite ledger and dispose it after the test.

    Args:
        tmp_path: Isolated directory receiving the synthetic database file.

    Yields:
        An owner-style engine with all current migrations applied.
    """
    result = create_db_engine(Settings(db_url=f"sqlite:///{tmp_path / 'rebuild.db'}"))
    upgrade(result)
    try:
        yield result
    finally:
        result.dispose()


def add_raw(
    session, dataset, payload, *, empty=False, start=DAY, end=DAY, provider=PROVIDER, fetched_at=NOW
):
    """Insert one synthetic raw fixture and assign its ID immediately.

    Args:
        session: Test fixture writer session.
        dataset: Dataset supplying the endpoint name.
        payload: Synthetic dict/list document or None.
        empty: Stored empty-response flag.
        start: Stored range start; None tests invalid metadata.
        end: Stored range end; None tests invalid metadata.
        provider: Provider identifier for exclusion tests.
        fetched_at: Aware timestamp, deliberately independent of ID order in some tests.

    Returns:
        The inserted RawResponse instance with an assigned identity.
    """
    row = RawResponse(
        provider=provider,
        endpoint=ENDPOINTS[dataset],
        params={"start": 0},
        range_start=start,
        range_end=end,
        fetched_at=fetched_at,
        content_sha256="a" * 64,
        sdk_version="synthetic",
        is_empty=empty,
        payload=payload,
    )
    session.add(row)
    session.flush()
    return row


def rows(engine, model):
    """Read a synthetic table snapshot for before/after equality checks.

    Args:
        engine: Isolated ledger engine.
        model: ORM model whose scalar/JSON values are compared.

    Returns:
        All table rows as mappings, in primary-key order.
    """
    with engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                select(*model.__table__.columns).order_by(*model.__table__.primary_key.columns)
            ).mappings()
        ]


def test_replay_fills_metrics_last_id_wins_and_second_run_has_no_writes(engine):
    """Replay all versions, preserve absent records/state/raw and suppress net-no-change writes."""
    with Session(engine) as session, session.begin():
        earlier = add_raw(
            session,
            "activities",
            [{"activityId": 7, "distance": 1000, "aerobicTrainingEffect": 1.1}],
        )
        later = add_raw(
            session,
            "activities",
            [{"activityId": 7, "distance": 2000, "aerobicTrainingEffect": 3.2, "normPower": 210.5}],
            fetched_at=NOW - timedelta(days=1),
        )
        add_raw(session, "activities", None, empty=True)
        session.add(
            Activity(
                provider=PROVIDER,
                activity_id=7,
                source_response_id=earlier.id,
                distance_m=1000,
                file_status="synthetic-preserved",
                updated_at=NOW,
            )
        )
        session.add(
            Activity(
                provider=PROVIDER,
                activity_id=99,
                source_response_id=earlier.id,
                distance_m=0,
                updated_at=NOW,
            )
        )
        session.add(
            SyncState(
                provider=PROVIDER,
                dataset="activities",
                last_synced_date=DAY,
                last_run_at=NOW,
                last_status="failed",
            )
        )
        latest_id = later.id
    raw_before, state_before = rows(engine, RawResponse), rows(engine, SyncState)
    first = rebuild(engine, ["activities"], clock=lambda: NOW + timedelta(hours=1))[0]
    assert not first.error and first.raw_read == 3 and first.empty_skipped == 1
    assert first.counts.inserted == 0 and first.counts.updated == 1
    with Session(engine) as session:
        row = session.get(Activity, (PROVIDER, 7))
        assert row.aerobic_training_effect == 3.2 and row.normalized_power_w == 210.5
        assert row.distance_m == 2000 and row.source_response_id == latest_id
        assert row.file_status == "synthetic-preserved"
        assert session.get(Activity, (PROVIDER, 99)).distance_m == 0
    before = rows(engine, Activity)
    writes = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        """Capture actual DML during the repeated rebuild without recording values.

        Args:
            connection: Replay SQLAlchemy connection.
            cursor: SQLite cursor.
            statement: SQL text used only for DML assertions.
            parameters: Bound synthetic values, never logged.
            context: SQLAlchemy execution metadata.
            executemany: Whether multiple parameter sets are executed.
        """
        if statement.startswith(("INSERT ", "UPDATE ", "DELETE ")):
            writes.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    second = rebuild(engine, ["activities"], clock=lambda: NOW + timedelta(hours=2))[0]
    assert not second.error and second.raw_read == 3
    assert (
        second.counts.inserted == 0 and second.counts.updated == 0 and second.counts.unchanged == 1
    )
    assert writes == []
    assert rows(engine, Activity) == before
    assert rows(engine, RawResponse) == raw_before
    assert rows(engine, SyncState) == state_before


def test_replay_pending_inserts_and_duplicate_occurrences_use_last_values(engine):
    """Apply repeated IDs without intermediate flushes or duplicate primary-key insertions."""
    with Session(engine) as session, session.begin():
        add_raw(
            session,
            "activities",
            [
                {"activityId": 7, "trainingStressScore": 10},
                {"activityId": 7, "trainingStressScore": 20},
            ],
        )
        last = add_raw(session, "activities", [{"activityId": 7, "trainingStressScore": 30}])
        last_id = last.id
    result = rebuild(engine, ["activities"])[0]
    assert not result.error and result.counts.inserted == 1 and result.counts.updated == 0
    with Session(engine) as session:
        record = session.get(Activity, (PROVIDER, 7))
        assert record.training_stress_score == 30 and record.source_response_id == last_id
    assert rebuild(engine, ["activities"])[0].counts.updated == 0


@pytest.mark.parametrize(
    "kind", ["null_start", "null_end", "reversed", "bad_shape", "bad_date", "invalid_json"]
)
def test_corrupt_raw_rolls_back_only_its_dataset_and_reports_only_endpoint_id(engine, kind):
    """Rollback an entire failed dataset while committing valid rows from other datasets."""
    with Session(engine) as session, session.begin():
        original = add_raw(session, "activities", [{"activityId": 7, "distance": 1000}])
        session.add(
            Activity(
                provider=PROVIDER,
                activity_id=7,
                source_response_id=original.id,
                distance_m=0,
                updated_at=NOW,
            )
        )
        bad = add_raw(
            session,
            "activities",
            [{"activityId": 8, "distance": 12345.6789}],
            start=None if kind == "null_start" else DAY,
            end=None if kind == "null_end" else DAY,
        )
        if kind == "reversed":
            bad.range_end = DAY - timedelta(days=1)
        elif kind == "bad_shape":
            bad.payload = {"SYNTHETIC-PRIVATE-PAYLOAD": "SYNTHETIC-PRIVATE-PASSWORD"}
        elif kind == "bad_date":
            bad.payload = [{"activityId": 8, "startTimeLocal": "SYNTHETIC-PRIVATE-DATE"}]
        bad_id = bad.id
        add_raw(session, "sleep", [{"calendarDate": str(DAY), "sleepTimeSeconds": 3600}])
    if kind == "invalid_json":
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE raw_response SET payload=:payload WHERE id=:id"),
                {"payload": "SYNTHETIC-PRIVATE-INVALID-JSON", "id": bad_id},
            )
    before = rows(engine, Activity)
    results = rebuild(engine, ["activities", "sleep"])
    assert results[0].error == f"{ENDPOINTS['activities']}: raw_id={bad_id}"
    assert results[0].counts.inserted == results[0].counts.updated == 0
    assert not results[1].error and results[1].counts.inserted == 1
    assert rows(engine, Activity) == before
    assert len(rows(engine, DailySleep)) == 1


def test_filters_empty_rows_placeholders_and_streamed_payloads(engine):
    """Filter datasets/provider, skip empty payload reads and stream raw metadata incrementally."""
    with Session(engine) as session, session.begin():
        add_raw(session, "activities", [{"activityId": 7}], provider="synthetic-other")
        add_raw(
            session,
            "sleep",
            [{"calendarDate": None}, {"calendarDate": str(DAY), "sleepTimeSeconds": 0}],
        )
        add_raw(session, "sleep", {"unvalidated": "SYNTHETIC-PRIVATE-EMPTY"}, empty=True)
        add_raw(session, "hrv", {"hrvSummaries": []})
    metadata_options = []
    selected = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        """Record projections/options to verify streamed metadata and one-at-a-time payload reads.

        Args:
            connection: Replay database connection.
            cursor: SQLite cursor.
            statement: SQL text checked for projection scope.
            parameters: Bound raw IDs; no values are logged.
            context: Execution context holding streaming options.
            executemany: Whether multiple parameter sets are executed.
        """
        if statement.startswith(("INSERT ", "UPDATE ", "DELETE ")):
            assert "raw_response" not in statement and "sync_state" not in statement
            assert not statement.startswith("DELETE ")
        if statement.startswith("SELECT raw_response."):
            selected.append(statement)
            if "raw_response.range_start" in statement:
                metadata_options.append(context.execution_options)

    event.listen(engine, "before_cursor_execute", capture)
    result = rebuild(engine, ["sleep"])[0]
    assert not result.error and result.raw_read == 2 and result.empty_skipped == 1
    assert result.counts.inserted == 1 and result.counts.placeholders == 1
    assert metadata_options and all(option["yield_per"] == 1 for option in metadata_options)
    assert sum(sql.startswith("SELECT raw_response.payload") for sql in selected) == 1
    assert all("params" not in sql for sql in selected)
    assert rebuild(engine, ["activities"])[0].raw_read == 0


def test_empty_database_rebuild_succeeds_without_creating_rows(engine):
    """Return zero counts for all areas when no raw responses have been stored."""
    results = rebuild(engine)
    assert [result.dataset for result in results] == [*DATASETS, "legs"]
    assert all(
        not result.error and result.raw_read == result.counts.inserted == result.counts.updated == 0
        for result in results
    )
    assert rows(engine, RawResponse) == rows(engine, SyncState) == []


def test_cli_rebuild_uses_no_provider_or_key_settings_and_prints_counts_only(
    engine, monkeypatch, capsys, caplog
):
    """Use database-only settings and print no fixture values on first or repeated rebuild."""
    from unittest.mock import Mock

    from athlete_ledger.cli import run
    from athlete_ledger.settings import DatabaseSettings

    with Session(engine) as session, session.begin():
        add_raw(
            session,
            "activities",
            [
                {
                    "activityId": 7,
                    "aerobicTrainingEffect": 3.2,
                    "normPower": 9876.54321,
                    "activityName": "SYNTHETIC-PRIVATE-ACTIVITY",
                }
            ],
        )
    monkeypatch.setenv("ATHLETE_LEDGER_DB_URL", str(engine.url))
    monkeypatch.setenv("GARMIN_PERSONAL_API_KEY", "SYNTHETIC-PRIVATE-KEY")
    key_settings = Mock(side_effect=AssertionError("Rebuild must not load API-key settings."))
    provider = Mock(side_effect=AssertionError("Rebuild must not construct a provider."))
    monkeypatch.setattr("athlete_ledger.cli.Settings", key_settings)
    monkeypatch.setattr("athlete_ledger.sync_cli.PersonalProvider", provider)
    assert "personal_api_key" not in DatabaseSettings.model_fields
    assert run(["sync", "rebuild"]) == 0
    first = capsys.readouterr()
    assert first.err == "" and len(first.out.splitlines()) == 5
    assert "activities: raw_read=1 empty_skipped=0 rows_inserted=1 rows_updated=0" in first.out
    for marker in ("SYNTHETIC-PRIVATE", "9876.54321", "3.2"):
        assert marker not in first.out + first.err + caplog.text
    assert run(["sync", "rebuild", "--dataset", "activities"]) == 0
    second = capsys.readouterr()
    assert second.err == "" and len(second.out.splitlines()) == 1
    assert "rows_inserted=0 rows_updated=0 rows_unchanged=1" in second.out
    key_settings.assert_not_called()
    provider.assert_not_called()


def test_cli_failure_reports_only_endpoint_and_raw_id_and_continues(engine, monkeypatch, capsys):
    """Return nonzero for corrupt activity raw data while rebuilding another selected area."""
    from athlete_ledger.cli import run

    with Session(engine) as session, session.begin():
        invalid = add_raw(session, "activities", {"private": "SYNTHETIC-PRIVATE-PAYLOAD"})
        invalid_id = invalid.id
        add_raw(session, "sleep", [{"calendarDate": str(DAY), "sleepTimeSeconds": 34567}])
    monkeypatch.setenv("ATHLETE_LEDGER_DB_URL", str(engine.url))
    assert run(["sync", "rebuild", "--dataset", "activities", "sleep"]) == 1
    result = capsys.readouterr()
    assert result.err == f"{ENDPOINTS['activities']}: raw_id={invalid_id}\n"
    assert "[INFO] activities:" in result.out and "[OK] sleep:" in result.out
    assert "SYNTHETIC-PRIVATE" not in result.out + result.err
    assert "34567" not in result.out + result.err
    assert len(rows(engine, DailySleep)) == 1


class PayloadProvider:
    """Serve mutable synthetic summaries through the sync interface without any SDK access."""

    sdk_version = "synthetic"

    def __init__(self):
        """Initialize raw response payloads, request counters and close state."""
        self.request_count = 0
        self.closed = False
        self.payloads = {
            "activities": [{"activityId": 7, "startTimeLocal": f"{DAY} 08:00:00", "distance": 0}],
            "sleep": [{"calendarDate": str(DAY), "sleepTimeSeconds": 3600}],
            "hrv": {"hrvSummaries": [{"calendarDate": str(DAY), "lastNightAvg": 50.5}]},
            "stress": [{"calendarDate": str(DAY), "values": {"overallStressLevel": 0}}],
        }

    def responses(self, dataset, start, end):
        """Yield a synthetic raw response for the selected dataset.

        Args:
            dataset: Supported area selecting its mutable fixture payload.
            start: Inclusive range start passed through as stored request metadata.
            end: Inclusive range end passed through as stored request metadata.

        Yields:
            A Response with synthetic data and no network or token access.
        """
        from athlete_ledger.sync_types import Response

        self.request_count += 1
        yield Response(
            ENDPOINTS[dataset],
            {"start_date": str(start), "end_date": str(end)},
            self.payloads[dataset],
        )

    def close(self):
        """Record that the CLI released the synthetic provider."""
        self.closed = True


def test_unchanged_wellness_replay_and_incremental_sync_populates_training_columns(
    engine, monkeypatch, capsys
):
    """Keep wellness unchanged while new incremental raw activity metrics reach the model."""
    from unittest.mock import Mock

    from athlete_ledger.cli import run
    from athlete_ledger.db.models import DailyHRV, DailyStress
    from athlete_ledger.sync import synchronize

    provider = PayloadProvider()
    initial = synchronize(
        engine, provider, DATASETS, since=DAY, until=DAY, today=DAY, clock=lambda: NOW
    )
    assert all(not result.error for result in initial)
    models = (DailySleep, DailyHRV, DailyStress)
    before = [rows(engine, model) for model in models]
    results = rebuild(engine, ["sleep", "hrv", "stress"])
    assert all(
        not result.error
        and result.counts.updated == result.counts.inserted == 0
        and result.counts.unchanged == 1
        for result in results
    )
    assert [rows(engine, model) for model in models] == before

    class FixedDate(date):
        """Pin the CLI's local today to the synthetic ledger day."""

        @classmethod
        def today(cls):
            """Return the fixed synthetic calendar date.

            Returns:
                The date already represented by the seeded sync state.
            """
            return DAY

    provider.payloads["activities"] = [
        {
            "activityId": 7,
            "startTimeLocal": f"{DAY} 08:00:00",
            "aerobicTrainingEffect": 4.1,
            "moderateIntensityMinutes": 12.6,
            "averageSpeed": 3.75,
        },
    ]
    monkeypatch.setenv("ATHLETE_LEDGER_DB_URL", str(engine.url))
    monkeypatch.setattr("athlete_ledger.sync_cli.PersonalProvider", Mock(return_value=provider))
    monkeypatch.setattr("athlete_ledger.sync_cli.date", FixedDate)
    assert run(["sync", "run", "--dataset", "activities"]) == 0
    assert "rows_updated=1" in capsys.readouterr().out
    with Session(engine) as session:
        row = session.get(Activity, (PROVIDER, 7))
        assert row.aerobic_training_effect == 4.1
        assert row.moderate_intensity_min == 13 and row.avg_speed_mps == 3.75
    before = rows(engine, Activity)
    assert rebuild(engine, ["activities"])[0].counts.updated == 0
    assert rows(engine, Activity) == before


def test_sync_and_rebuild_apply_the_same_later_page_values(engine):
    """Use identical stored pages to prove last-wins in sync, rebuild and repeated replay."""
    from athlete_ledger.sync import synchronize
    from athlete_ledger.sync_types import Response

    class OverlappingPages(PayloadProvider):
        """Emit changing values for one ID and a second ID that preserves pagination progress."""

        def responses(self, dataset, start, end):
            """Yield two overlapping activity pages and an empty terminating response.

            Args:
                dataset: Activities dataset supplying the endpoint label.
                start: Stored inclusive first date.
                end: Stored inclusive last date.

            Yields:
                Raw responses in the exact order replayed later by rebuild.
            """
            self.request_count += 3
            for offset, payload in [
                (0, [{"activityId": 1, "aerobicTrainingEffect": 1.2}]),
                (1, [{"activityId": 1, "aerobicTrainingEffect": 4.2}, {"activityId": 2}]),
                (3, []),
            ]:
                yield Response(ENDPOINTS[dataset], {"start": offset}, payload)

    synced = synchronize(
        engine,
        OverlappingPages(),
        ["activities"],
        since=DAY,
        until=DAY,
        today=DAY,
        clock=lambda: NOW,
    )[0]
    assert not synced.error and synced.counts.inserted == 2 and synced.counts.updated == 1
    with Session(engine) as session:
        assert session.get(Activity, (PROVIDER, 1)).aerobic_training_effect == 4.2
    with engine.begin() as connection:
        connection.execute(Activity.__table__.update().values(aerobic_training_effect=None))
    replayed = rebuild(engine, ["activities"])[0]
    assert not replayed.error and replayed.counts.updated == 1
    with Session(engine) as session:
        assert session.get(Activity, (PROVIDER, 1)).aerobic_training_effect == 4.2
    before = rows(engine, Activity)
    repeated = rebuild(engine, ["activities"])[0]
    assert not repeated.error and repeated.counts.inserted == repeated.counts.updated == 0
    assert rows(engine, Activity) == before
