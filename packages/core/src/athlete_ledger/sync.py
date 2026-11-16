"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Run transactional per-chunk synchronization and resumable state.
"""  # noqa: D205, D212

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from athlete_ledger.db.models import SyncState, utc_now
from athlete_ledger.errors import DatabaseError, database_error_code
from athlete_ledger.storage import Counts, store_response
from athlete_ledger.sync_types import (
    DATASETS,
    ENDPOINTS,
    PROVIDER,
    Provider,
    ProviderError,
    date_chunks,
)


@dataclass
class DatasetResult:
    """Public run summary containing no provider payloads or credentials."""

    dataset: str
    counts: Counts = field(default_factory=Counts)
    error: str | None = None


def _failure_message(dataset: str, error: Exception) -> str:
    """Reduce a failure to safe endpoint/status metadata.

    Args:
        dataset: Supported dataset name.
        error: The failure, never rendered unless it is a sanitized provider error.

    Returns:
        A safe message without driver details or payloads.
    """
    if isinstance(error, ProviderError):
        return str(error)
    code = (
        database_error_code(error) if isinstance(error, (SQLAlchemyError, DatabaseError)) else None
    )
    return f"{ENDPOINTS[dataset]}: synchronization failed" + (f" ({code})." if code else ".")


def _state(session: Session, dataset: str) -> SyncState:
    """Load or prepare a state row within the caller's transaction.

    Args:
        session: Current database session.
        dataset: Supported dataset name.

    Returns:
        The existing or pending state row.
    """
    state = session.get(SyncState, (PROVIDER, dataset))
    if state is None:
        state = SyncState(provider=PROVIDER, dataset=dataset)
        session.add(state)
    return state


def synchronize(
    engine: Engine,
    provider: Provider,
    datasets: Sequence[str] = DATASETS,
    *,
    since: date | None = None,
    until: date | None = None,
    today: date | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> list[DatasetResult]:
    """Commit each chunk atomically without moving stored progress backwards.

    Historical backfills update run metadata but preserve a later committed
    last_synced_date, keeping the next incremental overlap window unchanged.

    Args:
        engine: Ledger database engine with migrations already applied.
        provider: Injectable response provider.
        datasets: Supported datasets in desired processing order.
        since: Explicit backfill start; None selects incremental mode.
        until: Inclusive end date, defaulting to the machine's local date.
        today: Injectable local calendar date for deterministic tests.
        clock: Injectable aware timestamp source.

    Returns:
        One safe summary per dataset, continuing after dataset failures.

    Raises:
        ValueError: If dates or dataset names are invalid.
    """
    local_today = today or date.today()
    end = until or local_today
    if end > local_today or (since is not None and (since > end or since > local_today)):
        raise ValueError("Sync dates must be ordered and not in the future.")
    if any(dataset not in DATASETS for dataset in datasets):
        raise ValueError("Unsupported sync dataset.")
    results: list[DatasetResult] = []
    for dataset in dict.fromkeys(datasets):
        result = DatasetResult(dataset)
        first_request = provider.request_count
        try:
            start = since
            if start is None:
                with Session(engine) as session:
                    state = session.get(SyncState, (PROVIDER, dataset))
                    if state is None or state.last_synced_date is None:
                        raise ProviderError(f"{ENDPOINTS[dataset]}: run sync backfill first.")
                    start = state.last_synced_date - timedelta(days=2)
            for chunk_start, chunk_end in date_chunks(start, end):
                chunk = Counts(chunks=1)
                with Session(engine) as session, session.begin():
                    seen: set[int] = set()
                    for response in provider.responses(dataset, chunk_start, chunk_end):
                        store_response(
                            session,
                            dataset,
                            response,
                            chunk_start,
                            chunk_end,
                            provider.sdk_version,
                            clock,
                            chunk,
                            seen,
                        )
                    state = _state(session, dataset)
                    if state.last_synced_date is None or chunk_end > state.last_synced_date:
                        state.last_synced_date = chunk_end
                    state.last_run_at = clock()
                    state.last_status = "ok"
                result.counts.add(chunk)
        except Exception as error:
            # Failed chunk counters are discarded along with its rolled-back rows.
            result.error = _failure_message(dataset, error)
            try:
                with Session(engine) as session, session.begin():
                    state = _state(session, dataset)
                    state.last_status = "failed"
                    state.last_run_at = clock()
            except Exception as state_error:
                result.error = _failure_message(dataset, state_error)
        result.counts.requests = provider.request_count - first_request
        results.append(result)
    return results


def sync_status(engine: Engine, datasets: Sequence[str] = DATASETS) -> list[SyncState]:
    """Read per-dataset progress without constructing a provider or reading API keys.

    Args:
        engine: Configured ledger engine.
        datasets: Dataset names to inspect.

    Returns:
        Detached progress rows, including empty state for datasets never run.
    """
    with Session(engine) as session:
        rows = []
        for dataset in datasets:
            state = session.get(SyncState, (PROVIDER, dataset))
            if state is None:
                state = SyncState(provider=PROVIDER, dataset=dataset)
            else:
                session.expunge(state)
            rows.append(state)
        return rows
