"""
Author: L. Saetta
Date Last Modified: 2026-11-17
License: MIT
Description: Download multisport details atomically per parent without dataset state.
"""  # noqa: D205, D212

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from athlete_ledger.db.models import Activity, ActivityLeg, RawResponse, utc_now
from athlete_ledger.normalize.garmin_connect import detail_children, normalize_leg
from athlete_ledger.storage import Counts, store_detail_response, upsert_row
from athlete_ledger.sync_types import (
    PROVIDER,
    DetailProvider,
    ProviderError,
    UnexpectedResponseError,
)


@dataclass
class LegsResult:
    """Committed work counters and distinct safe diagnostics in first-occurrence order."""

    parents_checked: int = 0
    requests: int = 0
    counts: Counts = field(default_factory=Counts)
    parents_failed: int = 0
    errors: list[str] = field(default_factory=list)

    def add_error(self, error: Exception) -> None:
        """Retain a distinct safe diagnostic without rendering unexpected exceptions.

        Args:
            error: Provider-safe failure or an unexpected exception whose type alone
                may be reported.
        """
        message = str(error) if isinstance(error, ProviderError) else type(error).__name__
        if message not in self.errors:
            self.errors.append(message)


def sync_legs(
    engine: Engine,
    provider: DetailProvider,
    *,
    refresh: bool = False,
    clock: Callable[[], datetime] = utc_now,
) -> LegsResult:
    """Fetch unexamined multisport parents, committing each complete family together.

    Args:
        engine: Owner engine with the legs migration applied.
        provider: Injectable detail provider with safe transport diagnostics.
        refresh: Fetch all stored multisport parents, including previously examined ones.
        clock: Aware fetch/update timestamp source.

    Returns:
        Counts for selected parents, requests including retries, committed legs and
        failed parents. Initial failures remain eligible automatically; failed refreshes
        retain the previous family and can be retried with refresh=True. Selection
        failures return a failed result with safe diagnostics, leaving other datasets
        free to continue. KeyboardInterrupt and other BaseException subclasses propagate.
    """
    result = LegsResult()
    first_request = provider.request_count
    try:
        with Session(engine) as session:
            # JSON params are compared in Python: Oracle stores them as CLOBs.
            checked = set()
            if not refresh:
                for params in session.scalars(
                    select(RawResponse.params).where(
                        RawResponse.provider == PROVIDER, RawResponse.endpoint == "get_activity"
                    )
                ):
                    if isinstance(params, dict):
                        identifier = params.get("activity_id")
                        if isinstance(identifier, int) and not isinstance(identifier, bool):
                            checked.add(identifier)
            parents = session.execute(
                select(Activity.activity_id, Activity.start_time_local)
                .where(Activity.provider == PROVIDER, Activity.activity_type == "multi_sport")
                .order_by(Activity.start_time_local, Activity.activity_id)
            ).all()
    except Exception as error:
        result.add_error(error)
        return result
    for parent_id, stamp in parents:
        if parent_id in checked:
            continue
        result.parents_checked += 1
        counts = Counts()
        try:
            with Session(engine) as session, session.begin():
                if stamp is None:
                    raise UnexpectedResponseError("get_activity")
                parent_date = stamp.date()
                parent = provider.activity_detail(parent_id)
                children = detail_children(parent.payload)
                store_detail_response(session, parent, parent_date, provider.sdk_version, clock)
                for index, child_id in enumerate(children):
                    detail = provider.activity_detail(child_id)
                    row = normalize_leg(detail.payload, child_id, parent_id, index)
                    raw = store_detail_response(
                        session, detail, parent_date, provider.sdk_version, clock
                    )
                    upsert_row(session, "legs", row, raw.id, clock, counts)
            result.counts.add(counts)
        except Exception as error:
            result.parents_failed += 1
            result.add_error(error)
    result.requests = provider.request_count - first_request
    return result


def stored_leg_count(engine: Engine) -> int:
    """Count stored legs without reading details or constructing a provider.

    Args:
        engine: Ledger database engine.

    Returns:
        Number of legs stored for the configured provider.
    """
    with Session(engine) as session:
        return (
            session.scalar(
                select(func.count())
                .select_from(ActivityLeg)
                .where(ActivityLeg.provider == PROVIDER)
            )
            or 0
        )
