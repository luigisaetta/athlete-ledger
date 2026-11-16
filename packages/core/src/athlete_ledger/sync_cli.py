"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Parse sync commands and display only safe counters and state.
"""  # noqa: D205, D212

import argparse
import logging
import sys
from datetime import date, timedelta
from typing import Any

from sqlalchemy import Engine

from athlete_ledger.console import info, ok
from athlete_ledger.coverage import sync_coverage
from athlete_ledger.errors import ConfigurationError
from athlete_ledger.providers.garmin_connect import PersonalProvider
from athlete_ledger.rebuild import rebuild
from athlete_ledger.settings import DatabaseSettings, Settings
from athlete_ledger.sync import sync_status, synchronize
from athlete_ledger.sync_types import DATASETS


def _date(value: str) -> date:
    """Parse an ISO date without echoing invalid argument values.

    Args:
        value: Calendar date argument.

    Returns:
        A calendar date.

    Raises:
        argparse.ArgumentTypeError: If the supplied format is invalid.
    """
    try:
        result = date.fromisoformat(value)
        if result.isoformat() != value:
            raise ValueError
        return result
    except ValueError:
        raise argparse.ArgumentTypeError("Use a calendar date in YYYY-MM-DD format.") from None


def _dataset(value: str) -> str:
    """Validate a dataset without echoing an invalid argument value.

    Args:
        value: Requested dataset.

    Returns:
        A supported dataset name.

    Raises:
        argparse.ArgumentTypeError: If the value is not a supported dataset.
    """
    if value not in DATASETS:
        raise argparse.ArgumentTypeError("Choose activities, sleep, hrv or stress.")
    return value


def add_sync_commands(commands: Any) -> None:
    """Register the sync command family without loading settings or credentials.

    Args:
        commands: Root argparse subparser collection.
    """
    parser = commands.add_parser("sync", help="Synchronize summary data")
    subcommands = parser.add_subparsers(dest="sync_command", required=True)
    backfill = subcommands.add_parser("backfill", help="Fetch an inclusive historical range")
    backfill.add_argument("--since", type=_date, required=True)
    backfill.add_argument("--until", type=_date)
    incremental = subcommands.add_parser("run", help="Refresh the last three synced days onward")
    coverage = subcommands.add_parser("coverage", help="Show downloaded intervals and gaps")
    rebuild_parser = subcommands.add_parser(
        "rebuild", help="Rebuild normalized rows from stored raw responses"
    )
    for command in (backfill, incremental, coverage, rebuild_parser):
        command.add_argument("--dataset", nargs="+", action="extend", type=_dataset, metavar="NAME")
    subcommands.add_parser("status", help="Show progress without accessing the provider")


def run_sync(args: argparse.Namespace, engine: Engine, settings: DatabaseSettings) -> int:
    """Run selected datasets and report counts, dates and safe errors only.

    Args:
        args: Parsed command options.
        engine: Configured ledger database.
        settings: Database configuration; provider-enabled Settings only for network sync.

    Returns:
        Zero only if every requested dataset completed.
    """
    if args.sync_command == "rebuild":
        return _run_rebuild(engine, args.dataset or DATASETS)
    if args.sync_command == "coverage":
        _print_coverage(engine, args.dataset or DATASETS, date.today())
        return 0
    if args.sync_command == "status":
        for state in sync_status(engine):
            stamp = state.last_run_at.isoformat() if state.last_run_at else "-"
            info(
                f"{state.dataset}: last_synced_date={state.last_synced_date or '-'} "
                f"last_run_at={stamp} last_status={state.last_status or '-'}"
            )
        return 0
    today = date.today()
    since = args.since if args.sync_command == "backfill" else None
    until = (args.until or today) if args.sync_command == "backfill" else today
    if until > today or (since is not None and since > until):
        print("Invalid sync date range; use ordered dates no later than today.", file=sys.stderr)
        return 1
    # SDK warnings can contain credential-cache paths; the CLI uses its own safe diagnostics.
    logger = logging.getLogger("garmin_personal_api")
    logger.setLevel(logging.WARNING)
    logger.handlers = [logging.NullHandler()]
    logger.propagate = False
    if not isinstance(settings, Settings):
        raise ConfigurationError("Provider sync requires provider-enabled settings.")
    provider = PersonalProvider(settings.personal_api_key)
    failed = False
    try:
        results = synchronize(
            engine,
            provider,
            args.dataset or DATASETS,
            since=since,
            until=until,
            today=today,
        )
        for result in results:
            c = result.counts
            message = (
                f"{result.dataset}: chunks={c.chunks} requests={c.requests} "
                f"raw_stored={c.raw_stored} raw_unchanged={c.raw_unchanged} "
                f"rows_inserted={c.inserted} rows_updated={c.updated} "
                f"rows_unchanged={c.unchanged} placeholders_skipped={c.placeholders}"
            )
            if result.error:
                failed = True
                info(message)
                print(result.error, file=sys.stderr)
            else:
                ok(message)
    finally:
        provider.close()
    return 1 if failed else 0


def _print_coverage(engine: Engine, datasets: tuple[str, ...] | list[str], today: date) -> None:
    """Display downloaded intervals, internal gaps and an unsynced tail.

    Args:
        engine: Ledger database engine used exclusively for reading chunk dates.
        datasets: Supported dataset names selected by the command line.
        today: Machine-local date delimiting the not-yet-synced tail.

    Raises:
        sqlalchemy.exc.SQLAlchemyError: If the coverage query fails; the root
            dispatcher converts it to sanitized database diagnostics.
        ValueError: If a stored range ends before it starts.
    """
    for dataset, coverage in sync_coverage(engine, datasets).items():
        if not coverage.intervals:
            info(f"{dataset}: nothing downloaded")
            continue
        emit = ok if len(coverage.intervals) == 1 and not coverage.gaps else info
        for start, end in coverage.intervals:
            emit(f"{dataset}: covered from={start} to={end} days={(end - start).days + 1}")
        for start, end in coverage.gaps:
            info(f"{dataset}: gap from={start} to={end} days={(end - start).days + 1}")
        for start, end in coverage.gaps:
            info(f"athlete-ledger sync backfill --since {start} --until {end} --dataset {dataset}")
        last = coverage.intervals[-1][1]
        if last < today:
            start = last + timedelta(days=1)
            info(f"{dataset}: tail from={start} to={today} days={(today - last).days}")


def _run_rebuild(engine: Engine, datasets: tuple[str, ...] | list[str]) -> int:
    """Rebuild selected datasets and print only safe counters or endpoint/raw-ID context.

    Args:
        engine: Owner engine for reading raw rows and writing normalized tables.
        datasets: Supported datasets to replay in the supplied order.

    Returns:
        Zero only if every selected dataset committed its normalized changes.
    """
    failed = False
    for result in rebuild(engine, datasets):
        counts = result.counts
        message = (
            f"{result.dataset}: raw_read={result.raw_read} empty_skipped={result.empty_skipped} "
            f"rows_inserted={counts.inserted} rows_updated={counts.updated} "
            f"rows_unchanged={counts.unchanged} placeholders_skipped={counts.placeholders}"
        )
        if result.error:
            failed = True
            info(message)
            print(result.error, file=sys.stderr)
        else:
            ok(message)
    return 1 if failed else 0
