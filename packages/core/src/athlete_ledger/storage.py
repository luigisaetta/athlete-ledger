"""
Author: L. Saetta
Date Last Modified: 2026-11-17
License: MIT
Description: Store append-only raw responses and portable ORM updates.
"""  # noqa: D205, D212

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, fields
from datetime import date, datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from athlete_ledger.db.models import (
    Activity,
    ActivityLeg,
    DailyHRV,
    DailySleep,
    DailyStress,
    RawResponse,
)
from athlete_ledger.db.types import JSONValue
from athlete_ledger.normalize.garmin_connect import normalize, records
from athlete_ledger.sync_types import PROVIDER, ProviderError, Response, UnexpectedResponseError

NormalizedRecord = Activity | ActivityLeg | DailySleep | DailyHRV | DailyStress
NormalizedKey = tuple[str, int | date]
UpsertCache = dict[NormalizedKey, NormalizedRecord | None]
MODELS: dict[str, type[NormalizedRecord]] = {
    "activities": Activity,
    "legs": ActivityLeg,
    "sleep": DailySleep,
    "hrv": DailyHRV,
    "stress": DailyStress,
}


@dataclass
class Counts:
    """Committed work counters; data request counts include retries."""

    chunks: int = 0
    requests: int = 0
    raw_stored: int = 0
    raw_unchanged: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    placeholders: int = 0

    def add(self, other: "Counts") -> None:
        """Accumulate counts after a successful chunk commit.

        Args:
            other: Counts for a committed chunk.
        """
        for field in fields(self):
            setattr(self, field.name, getattr(self, field.name) + getattr(other, field.name))


def content_hash(payload: Any) -> str:
    """Hash canonical compact UTF-8 JSON, including literal null for empty bodies.

    Args:
        payload: Decoded response value.

    Returns:
        The lowercase SHA-256 digest.
    """
    data = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def store_response(
    session: Session,
    dataset: str,
    response: Response,
    start: date,
    end: date,
    sdk_version: str,
    clock: Callable[[], datetime],
    counts: Counts,
) -> None:
    """Persist new raw content before deriving and comparing normalized rows.

    Args:
        session: Current chunk transaction.
        dataset: Dataset being normalized.
        response: Raw response and exact request parameters.
        start: Inclusive chunk start.
        end: Inclusive chunk end.
        sdk_version: Installed provider SDK version.
        clock: Aware timestamp source.
        counts: Counts local to this transaction.

    Raises:
        ProviderError: If payload serialization is invalid.
    """
    page = records(dataset, response.payload)
    raw, stored = _store_raw(session, response, start, end, sdk_version, clock, not page)
    if not stored:
        counts.raw_unchanged += 1
        return
    counts.raw_stored += 1
    batch = normalize(dataset, response.payload, start, end)
    counts.placeholders += batch.placeholders
    for row in batch.rows:
        upsert_row(session, dataset, row, raw.id, clock, counts)


def _store_raw(
    session: Session,
    response: Response,
    start: date,
    end: date,
    sdk_version: str,
    clock: Callable[[], datetime],
    is_empty: bool,
) -> tuple[RawResponse, bool]:
    """Persist changed content or return the latest identical response.

    Args:
        session: Caller-owned transaction.
        response: Raw payload and exact request parameters.
        start: Inclusive range start.
        end: Inclusive range end.
        sdk_version: Installed SDK version.
        clock: Aware fetch timestamp supplier.
        is_empty: Endpoint-specific empty flag.

    Returns:
        Stored response and whether a new append-only row was inserted.

    Raises:
        ProviderError: If the payload is not serializable JSON.
    """
    try:
        digest = content_hash(response.payload)
    except (ValueError, TypeError, OverflowError):
        raise ProviderError(f"GET {response.endpoint}: invalid JSON document.") from None
    candidates = session.scalars(
        select(RawResponse)
        .where(
            RawResponse.provider == PROVIDER,
            RawResponse.endpoint == response.endpoint,
            RawResponse.range_start == start,
            RawResponse.range_end == end,
        )
        .order_by(RawResponse.id.desc())
    )
    latest = next((raw for raw in candidates if raw.params == response.params), None)
    if latest is not None and latest.content_sha256 == digest:
        return latest, False
    raw = RawResponse(
        provider=PROVIDER,
        endpoint=response.endpoint,
        params=response.params,
        range_start=start,
        range_end=end,
        fetched_at=clock(),
        content_sha256=digest,
        sdk_version=sdk_version,
        is_empty=is_empty,
        payload=cast(JSONValue | None, response.payload),
    )
    session.add(raw)
    session.flush()
    return raw, True


def store_detail_response(
    session: Session,
    response: Response,
    parent_date: date,
    sdk_version: str,
    clock: Callable[[], datetime],
) -> RawResponse:
    """Store a detail beside dataset responses without deriving coverage or sync state.

    Args:
        session: Per-parent transaction.
        response: Parent or leg get_activity response.
        parent_date: Parent's local calendar date for both raw range boundaries.
        sdk_version: Installed provider SDK version.
        clock: Aware fetch timestamp supplier.

    Returns:
        New or latest identical raw response, used as leg provenance.

    Raises:
        ProviderError: If the payload is not serializable JSON.
    """
    raw, _ = _store_raw(
        session,
        response,
        parent_date,
        parent_date,
        sdk_version,
        clock,
        response.payload is None,
    )
    return raw


def upsert_row(
    session: Session,
    dataset: str,
    row: dict[str, Any],
    source_response_id: int,
    clock: Callable[[], datetime],
    counts: Counts,
    cache: UpsertCache | None = None,
) -> None:
    """Insert a normalized row or assign only changed values without committing.

    Args:
        session: Caller-owned transaction for normalized records.
        dataset: Supported dataset selecting the normalized ORM model and primary key.
        row: Normalizer-produced values, including the activity ID or calendar date.
        source_response_id: Stored raw row that produced these values.
        clock: Aware timestamp source, called only for an inserted or changed row.
        counts: Caller-owned counters incremented for insertion, change or no change.
        cache: Optional identity cache for deferred-flush replay, including pending
            inserts; None preserves ordinary sync session lookups.

    Raises:
        UnexpectedResponseError: If an existing leg belongs to another parent.
    """
    model = MODELS[dataset]
    identifier = row["activity_id"] if dataset in {"activities", "legs"} else row["calendar_date"]
    cache_key = (dataset, identifier)
    existing = (
        cache[cache_key]
        if cache is not None and cache_key in cache
        else cast(NormalizedRecord | None, session.get(model, (PROVIDER, identifier)))
    )
    if (
        isinstance(existing, ActivityLeg)
        and existing.parent_activity_id != row["parent_activity_id"]
    ):
        raise UnexpectedResponseError("get_activity")
    if existing is None:
        existing = model(
            **row, provider=PROVIDER, source_response_id=source_response_id, updated_at=clock()
        )
        session.add(existing)
        counts.inserted += 1
    else:
        changed = {key: value for key, value in row.items() if getattr(existing, key) != value}
        if changed:
            for key, value in changed.items():
                setattr(existing, key, value)
            existing.source_response_id = source_response_id
            existing.updated_at = clock()
            counts.updated += 1
        else:
            counts.unchanged += 1
    if cache is not None:
        cache[cache_key] = existing
