"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Verify coverage intervals, restricted SQL and database-only CLI output.
"""  # noqa: D205, D212

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pytest
from athlete_ledger.cli import run
from athlete_ledger.coverage import Coverage, merge_coverage, sync_coverage
from athlete_ledger.db.models import Base, RawResponse
from athlete_ledger.sync_types import DATASETS, ENDPOINTS, PROVIDER
from sqlalchemy import Engine, create_engine, event, insert
from sqlalchemy.exc import OperationalError


class FixedDate(date):
    """Keep the machine-local test date independent of the actual execution day."""

    @classmethod
    def today(cls) -> date:
        """Return the synthetic local date used for unsynced-tail expectations.

        Returns:
            January 20, 2026, regardless of the host clock or timezone.
        """
        return date(2026, 1, 20)


@pytest.fixture(autouse=True)
def forbid_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail on provider construction or key discovery and fix the CLI's local date.

    Args:
        monkeypatch: Fixture restoring provider, resolver and clock replacements.
    """
    monkeypatch.setattr(
        "athlete_ledger.sync_cli.PersonalProvider",
        Mock(side_effect=AssertionError("Coverage must not construct a provider.")),
    )
    monkeypatch.setattr(
        "athlete_ledger.providers.garmin_connect.resolve_api_key",
        Mock(side_effect=AssertionError("Coverage must not discover an API key.")),
    )
    monkeypatch.setattr("athlete_ledger.sync_cli.date", FixedDate)


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """Create a temporary SQLite schema shared by query and root CLI tests.

    Args:
        tmp_path: Isolated directory containing only synthetic database rows.
        monkeypatch: Fixture setting the root CLI's database URL.

    Yields:
        An engine with empty ledger tables, disposed after the test.
    """
    url = f"sqlite:///{tmp_path / 'coverage.db'}"
    monkeypatch.setenv("ATHLETE_LEDGER_DB_URL", url)
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def seed_ranges(
    engine: Engine,
    ranges: list[tuple[date | None, date | None]],
    dataset: str = "activities",
    *,
    provider: str = PROVIDER,
    endpoint: str | None = None,
    empty: bool = False,
) -> None:
    """Insert synthetic committed responses without creating normalized records.

    Args:
        engine: Temporary ledger database receiving the fixture rows.
        ranges: Inclusive chunk boundaries, including nulls for exclusion tests.
        dataset: Dataset whose endpoint is used unless explicitly overridden.
        provider: Provider identifier used to test provider filtering.
        endpoint: Optional endpoint override for exclusion tests.
        empty: Store an empty response to verify coverage without daily records.
    """
    with engine.begin() as connection:
        for start, end in ranges:
            connection.execute(
                insert(RawResponse).values(
                    provider=provider,
                    endpoint=endpoint or ENDPOINTS[dataset],
                    range_start=start,
                    range_end=end,
                    params={"private": "SYNTHETIC-PRIVATE-PARAMS"},
                    payload=[] if empty else {"private": "SYNTHETIC-PRIVATE-PAYLOAD"},
                    content_sha256="a" * 64,
                    sdk_version="synthetic-1.0",
                    is_empty=empty,
                )
            )


def day(number: int) -> date:
    """Build a January 2026 date for compact interval fixtures.

    Args:
        number: Valid day of the month in January.

    Returns:
        A calendar date in January 2026.
    """
    return date(2026, 1, number)


@pytest.mark.parametrize(
    ("ranges", "covered", "gaps"),
    [
        ([], [], []),
        ([(1, 1)], [(1, 1)], []),
        ([(1, 7), (5, 10)], [(1, 10)], []),
        ([(1, 7), (8, 14)], [(1, 14)], []),
        ([(1, 7), (1, 7)], [(1, 7)], []),
        ([(8, 14), (1, 7), (3, 4)], [(1, 14)], []),
        ([(1, 7), (9, 14)], [(1, 7), (9, 14)], [(8, 8)]),
        ([(15, 20), (1, 7), (10, 12)], [(1, 7), (10, 12), (15, 20)], [(8, 9), (13, 14)]),
        ([(10, 20), (1, 5), (6, 9), (2, 3)], [(1, 20)], []),
    ],
)
def test_merge_inclusive_intervals(ranges, covered, gaps) -> None:
    """Merge overlap, adjacency and duplicates independently of input order."""
    result = merge_coverage((day(start), day(end)) for start, end in ranges)
    assert result.intervals == tuple((day(start), day(end)) for start, end in covered)
    assert result.gaps == tuple((day(start), day(end)) for start, end in gaps)


def test_merge_handles_calendar_boundaries_and_date_limits() -> None:
    """Merge across months/leap days and accept date.max without overflowing."""
    assert merge_coverage(
        [(date(2024, 3, 1), date.max), (date.min, date(2024, 2, 29))]
    ) == Coverage(((date.min, date.max),), ())


def test_reversed_interval_is_rejected() -> None:
    """Reject an invalid range instead of reporting negative day counts."""
    with pytest.raises(ValueError, match="must not end before"):
        merge_coverage([(day(7), day(1))])


def test_query_filters_rows_and_projects_only_distinct_dates(ledger: Engine) -> None:
    """Exclude foreign/null ranges without ever selecting either JSON column."""
    seed_ranges(ledger, [(day(1), day(7)), (day(1), day(7)), (day(8), day(14))])
    seed_ranges(ledger, [(day(15), day(20))], provider="synthetic_other")
    seed_ranges(ledger, [(day(15), day(20))], endpoint="synthetic_other_endpoint")
    seed_ranges(ledger, [(None, day(20)), (day(15), None), (None, None)])
    seed_ranges(ledger, [(day(10), day(12))], dataset="sleep")
    statements: list[str] = []

    def capture(connection, cursor, statement, parameters, context, executemany) -> None:
        """Capture emitted SQL for projection and read-only assertions.

        Args:
            connection: SQLAlchemy connection executing the SELECT.
            cursor: SQLite cursor used by the execution event.
            statement: SQL text, excluding bound parameter values.
            parameters: Bound synthetic provider/endpoint filters.
            context: SQLAlchemy execution metadata.
            executemany: Whether this execution sends multiple parameter sets.
        """
        statements.append(" ".join(statement.split()))

    event.listen(ledger, "before_cursor_execute", capture)
    result = sync_coverage(ledger, ["activities", "sleep", "hrv"])
    assert result == {
        "activities": Coverage(((day(1), day(14)),), ()),
        "sleep": Coverage(((day(10), day(12)),), ()),
        "hrv": Coverage((), ()),
    }
    assert len(statements) == 3
    for sql in statements:
        projection = sql.split(" FROM ")[0]
        assert projection == "SELECT DISTINCT raw_response.range_start, raw_response.range_end"
        assert "payload" not in sql and "params" not in sql
        assert "raw_response.provider =" in sql and "raw_response.endpoint =" in sql
        assert "raw_response.range_start IS NOT NULL" in sql
        assert "raw_response.range_end IS NOT NULL" in sql


def test_empty_response_counts_as_covered_without_normalized_rows(ledger: Engine) -> None:
    """Count successfully downloaded empty days as covered, even without records."""
    seed_ranges(ledger, [(day(1), day(7))], empty=True)
    assert sync_coverage(ledger, ["activities"])["activities"].intervals == ((day(1), day(7)),)


def test_query_defaults_to_all_datasets(ledger: Engine) -> None:
    """Return an empty coverage entry for every supported area on a new ledger."""
    assert sync_coverage(ledger) == dict.fromkeys(DATASETS, Coverage((), ()))


def test_cli_gap_and_exact_dataset_backfill_command(
    ledger: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """Print inclusive internal gaps and commands scoped to the affected dataset."""
    seed_ranges(ledger, [(day(10), day(20)), (day(1), day(7))], dataset="sleep")
    assert run(["sync", "coverage", "--dataset", "sleep"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert output.out.splitlines() == [
        "  [INFO] sleep: covered from=2026-01-01 to=2026-01-07 days=7",
        "  [INFO] sleep: covered from=2026-01-10 to=2026-01-20 days=11",
        "  [INFO] sleep: gap from=2026-01-08 to=2026-01-09 days=2",
        (
            "  [INFO] athlete-ledger sync backfill --since 2026-01-08 "
            "--until 2026-01-09 --dataset sleep"
        ),
    ]


def test_cli_full_coverage_is_ok_and_does_not_print_tail(
    ledger: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """Label adjacent ranges ending today as one OK interval with inclusive days."""
    seed_ranges(ledger, [(day(1), day(7)), (day(8), day(20))])
    assert run(["sync", "coverage", "--dataset", "activities"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert output.out == "  [OK] activities: covered from=2026-01-01 to=2026-01-20 days=20\n"


def test_cli_empty_dataset_has_no_invented_tail(
    ledger: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """Report no downloads without guessing a start date for an empty dataset."""
    assert run(["sync", "coverage", "--dataset", "hrv"]) == 0
    assert capsys.readouterr().out == "  [INFO] hrv: nothing downloaded\n"


def test_cli_unsynced_tail_is_separate_from_internal_gaps(
    ledger: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """Show dates after the final interval through local today as a separate tail."""
    seed_ranges(ledger, [(day(1), day(7)), (day(10), day(14))], dataset="stress")
    assert run(["sync", "coverage", "--dataset", "stress"]) == 0
    output = capsys.readouterr().out
    assert "stress: gap from=2026-01-08 to=2026-01-09 days=2" in output
    assert "stress: tail from=2026-01-15 to=2026-01-20 days=6" in output
    assert output.count("sync backfill") == 1


def test_cli_single_interval_with_tail_keeps_ok_interval_label(
    ledger: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """Keep a contiguous dataset OK while marking its unsynced tail informational."""
    seed_ranges(ledger, [(day(1), day(19))])
    assert run(["sync", "coverage", "--dataset", "activities"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "  [OK] activities: covered from=2026-01-01 to=2026-01-19 days=19",
        "  [INFO] activities: tail from=2026-01-20 to=2026-01-20 days=1",
    ]


def test_cli_dataset_filter_and_repeated_selection(
    ledger: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """Inspect only selected datasets and print repeated selections just once."""
    for dataset in DATASETS:
        seed_ranges(ledger, [(day(1), day(20))], dataset=dataset)
    assert run(["sync", "coverage", "--dataset", "sleep", "hrv", "--dataset", "sleep"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("  [OK] sleep:")
    assert lines[1].startswith("  [OK] hrv:")


def test_cli_defaults_without_key_provider_or_payload_output(
    ledger: Engine, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read all datasets without discovering keys, constructing providers or leaking JSON."""
    monkeypatch.delenv("GARMIN_PERSONAL_API_KEY", raising=False)
    seed_ranges(ledger, [(day(1), day(20))])
    statements: list[str] = []

    def capture(connection, cursor, statement, parameters, context, executemany) -> None:
        """Record every CLI SQL statement to detect accidental database writes.

        Args:
            connection: Connection used by the root CLI dispatcher.
            cursor: SQLite cursor executing the statement.
            statement: SQL text to check for read-only operation.
            parameters: Bound synthetic query filters.
            context: SQLAlchemy execution metadata.
            executemany: Whether multiple parameter sets are being executed.
        """
        statements.append(statement)

    event.listen(ledger, "before_cursor_execute", capture)
    monkeypatch.setattr("athlete_ledger.cli.create_db_engine", lambda settings: ledger)
    # Interactive coverage uses the same banner as the other sync commands.
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setenv("NO_COLOR", "1")
    assert run(["sync", "coverage"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert len([line for line in output.out.splitlines() if line.startswith("  [")]) == 4
    assert "[OK] activities:" in output.out
    for dataset in DATASETS[1:]:
        assert f"[INFO] {dataset}: nothing downloaded" in output.out
    assert "SYNTHETIC-PRIVATE" not in output.out
    assert "ATHLETE LEDGER" in output.out
    assert "Summary sync" in output.out
    assert len(statements) == 4
    assert all(sql.startswith("SELECT DISTINCT ") for sql in statements)


def test_cli_database_failure_is_nonzero_and_sanitized(
    ledger: Engine, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reuse root diagnostics without exposing failed SQL or bound private values."""

    def fail_query(*args: object, **kwargs: object) -> None:
        """Raise a synthetic database error with private markers in SQL and parameters.

        Args:
            *args: Ignored coverage-query positional arguments.
            **kwargs: Ignored coverage-query keyword arguments.

        Raises:
            OperationalError: Always, with a driver code and synthetic private details.
        """
        raise OperationalError(
            "SYNTHETIC-PRIVATE-SQL",
            {"secret": "SYNTHETIC-PRIVATE-PARAMS"},
            Exception("ORA-01017: SYNTHETIC-PRIVATE-DRIVER"),
        )

    monkeypatch.setattr("athlete_ledger.sync_cli.sync_coverage", fail_query)
    assert run(["sync", "coverage"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "Database operation failed: ORA-01017.\n"
