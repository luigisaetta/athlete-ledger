"""
Author: L. Saetta
Date Last Modified: 2026-11-17
License: MIT
Description: Replay stored raw responses into normalized rows without provider access.
"""  # noqa: D205, D212

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from athlete_ledger.db.models import Activity, RawResponse, utc_now
from athlete_ledger.normalize.garmin_connect import (
    detail_children,
    detail_identifier,
    detail_summary,
    normalize,
    normalize_leg,
)
from athlete_ledger.storage import (
    MODELS,
    Counts,
    NormalizedKey,
    NormalizedRecord,
    UpsertCache,
    upsert_row,
)
from athlete_ledger.sync_types import DATASETS, ENDPOINTS, PROVIDER

REBUILD_DATASETS = (*DATASETS, "legs")


@dataclass
class RebuildResult:
    """Safe per-dataset replay counters and optional endpoint/raw-ID failure context."""

    dataset: str
    raw_read: int = 0
    empty_skipped: int = 0
    counts: Counts = field(default_factory=Counts)
    error: str | None = None


def _values(record: NormalizedRecord) -> dict[str, Any]:
    """Snapshot normalized scalar columns without retaining a raw payload.

    Args:
        record: Loaded or pending normalized ORM record.

    Returns:
        A dictionary of scalar table-column values, including provenance metadata.
    """
    return {column.key: getattr(record, column.key) for column in record.__table__.columns}


@dataclass
class _Replay:
    """Hold normalized identities and original values while raw payloads are streamed."""

    cache: UpsertCache = field(default_factory=dict)
    originals: dict[NormalizedKey, dict[str, Any] | None] = field(default_factory=dict)

    def apply(
        self,
        session: Session,
        dataset: str,
        row: dict[str, Any],
        raw_id: int,
        clock: Callable[[], datetime],
        counts: Counts,
    ) -> None:
        """Apply every occurrence through the shared upsert without flushing intermediate values.

        Args:
            session: Dataset transaction with autoflush disabled.
            dataset: Supported normalized dataset.
            row: Current normalized occurrence, in raw/record order.
            raw_id: Raw response that produced this occurrence.
            clock: Aware timestamp supplier for changed/new rows.
            counts: Working counters whose row totals are finalized after replay.
        """
        identifier = (
            row["activity_id"] if dataset in {"activities", "legs"} else row["calendar_date"]
        )
        key = (dataset, identifier)
        if key not in self.originals:
            original = cast(
                NormalizedRecord | None, session.get(MODELS[dataset], (PROVIDER, identifier))
            )
            self.cache[key] = original
            self.originals[key] = _values(original) if original is not None else None
        upsert_row(session, dataset, row, raw_id, clock, counts, cache=self.cache)

    def finish(self, counts: Counts) -> None:
        """Count final differences and restore metadata when replay returns to the original values.

        Args:
            counts: Row counters replaced with net inserted/updated/unchanged entity counts;
                placeholder totals are retained.
        """
        counts.inserted = counts.updated = counts.unchanged = 0
        for key, record in self.cache.items():
            if record is None:
                continue
            original = self.originals[key]
            if original is None:
                counts.inserted += 1
                continue
            changed = any(
                value != original[name]
                for name, value in _values(record).items()
                if name not in {"updated_at", "source_response_id"}
            )
            if changed:
                counts.updated += 1
            else:
                record.updated_at = original["updated_at"]
                record.source_response_id = original["source_response_id"]
                counts.unchanged += 1


def rebuild(
    engine: Engine,
    datasets: Sequence[str] = REBUILD_DATASETS,
    *,
    clock: Callable[[], datetime] = utc_now,
) -> list[RebuildResult]:
    """Replay every nonempty raw response in ID order with one transaction per dataset.

    Args:
        engine: Owner engine used to read raw responses and write normalized rows only.
        datasets: Supported datasets to rebuild, defaulting to all four and legs.
        clock: Aware timestamp supplier, injectable for deterministic tests.

    Returns:
        Per-dataset counters and safe errors. Row counts describe final changes from
        transaction entry, so replaying old then newer values again is idempotent.
        Failed datasets roll back all normalized changes and report zero row counters;
        other datasets continue. Raw responses and sync state are never mutated.

    Raises:
        ValueError: If an unsupported dataset is requested.
    """
    if any(dataset not in REBUILD_DATASETS for dataset in datasets):
        raise ValueError("Unsupported rebuild dataset.")
    results: list[RebuildResult] = []
    ordered = list(dict.fromkeys(datasets))
    if "legs" in ordered:
        ordered.remove("legs")
        ordered.append("legs")
    for dataset in ordered:
        if dataset == "legs":
            results.append(_rebuild_legs(engine, clock))
            continue
        result = RebuildResult(dataset)
        active_id: int | None = None
        try:
            with Session(engine, autoflush=False) as session, session.begin():
                replay = _Replay()
                statement = select(
                    RawResponse.id,
                    RawResponse.range_start,
                    RawResponse.range_end,
                    RawResponse.is_empty,
                ).where(
                    RawResponse.provider == PROVIDER, RawResponse.endpoint == ENDPOINTS[dataset]
                )
                metadata = session.execute(
                    statement.order_by(RawResponse.id).execution_options(yield_per=1)
                )
                for raw in metadata:
                    active_id = raw.id
                    result.raw_read += 1
                    if (
                        raw.range_start is None
                        or raw.range_end is None
                        or raw.range_start > raw.range_end
                    ):
                        raise ValueError("Invalid raw range.")
                    if raw.is_empty:
                        result.empty_skipped += 1
                        continue
                    # Fetch one payload after its ID is known, so even JSON decoding
                    # failures can identify the raw row without rendering its content.
                    payload = session.scalar(
                        select(RawResponse.payload).where(RawResponse.id == raw.id)
                    )
                    batch = normalize(dataset, payload, raw.range_start, raw.range_end)
                    result.counts.placeholders += batch.placeholders
                    for row in batch.rows:
                        replay.apply(session, dataset, row, raw.id, clock, result.counts)
                replay.finish(result.counts)
        except Exception:
            result.counts = Counts()
            context = f": raw_id={active_id}" if active_id is not None else ""
            result.error = f"{ENDPOINTS[dataset]}{context}"
        results.append(result)
    return results


def _rebuild_legs(engine: Engine, clock: Callable[[], datetime]) -> RebuildResult:
    """Replay detail values with final parent ordering in one legs transaction.

    Parent order and leg values have independent histories: an unchanged leg detail
    can be reused after a refreshed parent changes childIds order. Two streamed
    passes retain only parent/child identifiers, never a collection of payloads.

    Args:
        engine: Owner engine for reading raw details and writing legs.
        clock: Aware update timestamp supplier.

    Returns:
        Net counters or an endpoint/raw-ID diagnostic after complete rollback.
    """
    result = RebuildResult("legs")
    active_id: int | None = None
    try:
        with Session(engine, autoflush=False) as session, session.begin():
            parents = set(
                session.scalars(
                    select(Activity.activity_id).where(
                        Activity.provider == PROVIDER, Activity.activity_type == "multi_sport"
                    )
                )
            )
            order: dict[int, dict[int, int]] = {}
            statement = (
                select(RawResponse.id, RawResponse.params)
                .where(RawResponse.provider == PROVIDER, RawResponse.endpoint == "get_activity")
                .order_by(RawResponse.id)
                .execution_options(yield_per=1)
            )
            for raw_id, params in session.execute(statement):
                active_id = raw_id
                result.raw_read += 1
                if not isinstance(params, dict):
                    raise ValueError
                identifier = detail_identifier(params.get("activity_id"))
                payload = session.scalar(
                    select(RawResponse.payload).where(RawResponse.id == raw_id)
                )
                detail_summary(payload)
                if identifier in parents:
                    order.setdefault(identifier, {}).update(
                        {child: index for index, child in enumerate(detail_children(payload))}
                    )
            replay = _Replay()
            for raw_id, params in session.execute(statement):
                active_id = raw_id
                identifier = detail_identifier(params.get("activity_id"))
                if identifier in parents:
                    continue
                payload = session.scalar(
                    select(RawResponse.payload).where(RawResponse.id == raw_id)
                )
                if not isinstance(payload, dict):
                    raise ValueError
                parent_id = detail_identifier(payload.get("parentId"))
                if parent_id not in order or identifier not in order[parent_id]:
                    raise ValueError
                row = normalize_leg(payload, identifier, parent_id, order[parent_id][identifier])
                replay.apply(session, "legs", row, raw_id, clock, result.counts)
            replay.finish(result.counts)
    except Exception:
        result.counts = Counts()
        context = f": raw_id={active_id}" if active_id is not None else ""
        result.error = f"get_activity{context}"
    return result
