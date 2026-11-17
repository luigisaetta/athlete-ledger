"""
Author: L. Saetta
Date Last Modified: 2026-11-17
License: MIT
Description: Verify safe leg diagnostics and isolate parent-selection failures from datasets.
"""  # noqa: D205, D212

from datetime import date

import pytest
from athlete_ledger.cli import run
from athlete_ledger.db.models import (
    ActivityLeg,
    DailyHRV,
    DailySleep,
    DailyStress,
    RawResponse,
    SyncState,
)
from athlete_ledger.legs import sync_legs
from athlete_ledger.sync_types import DATASETS, ENDPOINTS, PROVIDER, ProviderError, Response
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from .test_legs import DAY, MARKER, NOW, FakeDetails, detail, seed, snapshot
from .test_legs import engine as engine


@pytest.mark.parametrize("status", [401, 403])
def test_provider_failure_is_printed_once_and_commits_nothing(engine, monkeypatch, capsys, status):
    """Every parent fails independently while repeated authentication diagnostics print once."""
    identifiers = (100, 200, 300, 400, 500)
    seed(engine, identifiers)
    before = snapshot(engine, RawResponse)
    message = f"GET get_activity: HTTP {status}; check personal API key validity/expiry."
    provider = FakeDetails({identifier: ProviderError(message) for identifier in identifiers})
    monkeypatch.setenv("ATHLETE_LEDGER_DB_URL", str(engine.url))
    monkeypatch.setattr("athlete_ledger.sync_cli.PersonalProvider", lambda key: provider)
    assert run(["sync", "legs"]) == 1
    output = capsys.readouterr()
    assert output.err == message + "\n"
    assert "parents_checked=5 detail_requests=5" in output.out
    assert "parents_failed=5" in output.out and provider.calls == list(identifiers)
    assert snapshot(engine, RawResponse) == before
    assert snapshot(engine, ActivityLeg) == []
    assert MARKER not in output.out + output.err


def test_distinct_diagnostics_keep_first_occurrence_order_and_safe_types(
    engine, monkeypatch, capsys
):
    """Keep safe errors verbatim, deduplicate by message and hide unexpected exception text."""
    seed(engine, (100, 200, 300, 400, 500))
    before = snapshot(engine, RawResponse)
    message = "GET get_activity: transport retries exhausted."
    provider = FakeDetails(
        {
            100: ProviderError(message),
            200: ValueError(MARKER),
            300: None,
            400: ProviderError(message),
            500: ValueError(MARKER + "-other"),
        }
    )
    result = sync_legs(engine, provider)
    assert result.errors == [message, "ValueError", "get_activity"]
    assert result.parents_failed == 5
    monkeypatch.setenv("ATHLETE_LEDGER_DB_URL", str(engine.url))
    monkeypatch.setattr("athlete_ledger.sync_cli.PersonalProvider", lambda key: provider)
    assert run(["sync", "legs"]) == 1
    output = capsys.readouterr()
    assert output.err.splitlines() == result.errors
    assert MARKER not in output.out + output.err and "Traceback" not in output.err
    assert snapshot(engine, RawResponse) == before
    assert snapshot(engine, ActivityLeg) == []


class WellnessDetails(FakeDetails):
    """Supply real synthetic daily rows so continuation must commit normalized data."""

    def responses(self, dataset, start, end):
        """Yield one synthetic daily record, with the correct HRV envelope.

        Args:
            dataset: Selected download dataset.
            start: Requested range start.
            end: Requested range end.

        Yields:
            An empty activities page or a dated wellness record.
        """
        self.request_count += 1
        rows = [] if dataset == "activities" else [{"calendarDate": str(DAY)}]
        payload = {"hrvSummaries": rows} if dataset == "hrv" else rows
        yield Response(ENDPOINTS[dataset], {}, payload)


@pytest.mark.parametrize(
    "query_prefix",
    [
        "SELECT raw_response.params",
        "SELECT activity.activity_id, activity.start_time_local",
    ],
)
@pytest.mark.parametrize("standalone", [False, True])
def test_selection_query_failure_is_safe_and_other_datasets_commit(
    engine, monkeypatch, capsys, query_prefix, standalone
):
    """Both selection queries return failed results in standalone and incremental commands."""
    seed(engine)
    with Session(engine) as session, session.begin():
        session.add_all(
            [
                SyncState(
                    provider=PROVIDER,
                    dataset=dataset,
                    last_synced_date=DAY,
                    last_status="ok",
                    last_run_at=NOW,
                )
                for dataset in DATASETS
            ]
        )

    def fail_selection(conn, cursor, statement, parameters, context, executemany):
        """Fail only the targeted initial legs query, without external I/O.

        Args:
            conn: SQLAlchemy connection.
            cursor: SQLite cursor.
            statement: SQL used solely to identify the selection query.
            parameters: Ignored synthetic parameters.
            context: SQLAlchemy execution context.
            executemany: Unused multiple-execution flag.

        Raises:
            RuntimeError: With poisoned text that must never be rendered.
        """
        if statement.startswith(query_prefix):
            raise RuntimeError(MARKER)

    class FrozenDate(date):
        """Keep the incremental test window short."""

        @classmethod
        def today(cls):
            """Return the synthetic local date."""
            return DAY

    provider = WellnessDetails({100: detail(100)})
    monkeypatch.setenv("ATHLETE_LEDGER_DB_URL", str(engine.url))
    monkeypatch.setattr("athlete_ledger.cli.create_db_engine", lambda settings: engine)
    monkeypatch.setattr("athlete_ledger.sync_cli.PersonalProvider", lambda key: provider)
    monkeypatch.setattr("athlete_ledger.sync_cli.date", FrozenDate)
    before = snapshot(engine, RawResponse)
    event.listen(engine, "before_cursor_execute", fail_selection)
    try:
        assert run(["sync", "legs" if standalone else "run"]) == 1
    finally:
        event.remove(engine, "before_cursor_execute", fail_selection)
    output = capsys.readouterr()
    assert output.err == "RuntimeError\n"
    assert MARKER not in output.out + output.err and "Traceback" not in output.err
    assert provider.calls == [] and provider.closed
    assert snapshot(engine, ActivityLeg) == []
    if standalone:
        assert snapshot(engine, RawResponse) == before
    else:
        for dataset in ("sleep", "hrv", "stress"):
            assert f"{dataset}: chunks=1" in output.out
        with Session(engine) as session:
            for model in (DailySleep, DailyHRV, DailyStress):
                assert session.get(model, (PROVIDER, DAY)) is not None
            for dataset in DATASETS:
                assert session.get(SyncState, (PROVIDER, dataset)).last_status == "ok"
            assert set(session.scalars(select(RawResponse.endpoint))) == set(ENDPOINTS.values())


@pytest.mark.parametrize("during_selection", [False, True])
def test_keyboard_interrupt_is_not_caught(engine, monkeypatch, during_selection):
    """Interrupts propagate from both selection and per-parent processing."""
    seed(engine)
    provider = FakeDetails({100: detail(100)})

    def interrupt(*args, **kwargs):
        """Interrupt without rendering any call arguments.

        Args:
            *args: Ignored synthetic method arguments.
            **kwargs: Ignored synthetic method options.

        Raises:
            KeyboardInterrupt: Always, to simulate user cancellation.
        """
        raise KeyboardInterrupt

    if during_selection:
        monkeypatch.setattr("athlete_ledger.legs.Session", interrupt)
    else:
        provider.activity_detail = interrupt
    with pytest.raises(KeyboardInterrupt):
        sync_legs(engine, provider)
