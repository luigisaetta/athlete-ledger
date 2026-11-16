"""
Author: L. Saetta
Date Last Modified: 2026-11-16
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

from athlete_ledger.db.models import Activity, DailyHRV, DailySleep, DailyStress, RawResponse
from athlete_ledger.db.types import JSONValue
from athlete_ledger.normalize.garmin_connect import activity_id, normalize, records
from athlete_ledger.sync_types import PROVIDER, ProviderError, Response

NormalizedRecord = Activity | DailySleep | DailyHRV | DailyStress
MODELS: dict[str, type[NormalizedRecord]] = {
    "activities": Activity,
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
    seen_activities: set[int],
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
        seen_activities: Activity keys already encountered in this chunk.

    Raises:
        ProviderError: If payload serialization is invalid.
    """
    page = records(dataset, response.payload)
    allowed_ids: set[int] = set()
    if dataset == "activities":
        page_ids = {activity_id(record) for record in page}
        allowed_ids = page_ids - seen_activities
        seen_activities.update(page_ids)
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
        counts.raw_unchanged += 1
        return
    raw = RawResponse(
        provider=PROVIDER,
        endpoint=response.endpoint,
        params=response.params,
        range_start=start,
        range_end=end,
        fetched_at=clock(),
        content_sha256=digest,
        sdk_version=sdk_version,
        is_empty=not page,
        payload=cast(JSONValue | None, response.payload),
    )
    session.add(raw)
    session.flush()
    counts.raw_stored += 1
    batch = normalize(dataset, response.payload, start, end)
    counts.placeholders += batch.placeholders
    model = MODELS[dataset]
    for row in batch.rows:
        identifier = row["activity_id"] if dataset == "activities" else row["calendar_date"]
        if dataset == "activities" and identifier not in allowed_ids:
            continue
        existing = cast(NormalizedRecord | None, session.get(model, (PROVIDER, identifier)))
        if existing is None:
            session.add(
                model(**row, provider=PROVIDER, source_response_id=raw.id, updated_at=clock())
            )
            counts.inserted += 1
        else:
            changed = {key: value for key, value in row.items() if getattr(existing, key) != value}
            if changed:
                for key, value in changed.items():
                    setattr(existing, key, value)
                existing.source_response_id = raw.id
                existing.updated_at = clock()
                counts.updated += 1
            else:
                counts.unchanged += 1
