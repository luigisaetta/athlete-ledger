"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Normalize summary payloads without database or network access.
"""  # noqa: D205, D212

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from athlete_ledger.sync_types import ENDPOINTS, UnexpectedResponseError


@dataclass
class NormalizedBatch:
    """Normalized row dictionaries and the number of skipped placeholders."""

    rows: list[dict[str, Any]]
    placeholders: int = 0


def records(dataset: str, payload: Any) -> list[dict[str, Any]]:
    """Validate the endpoint envelope without changing the raw payload.

    Args:
        dataset: Supported dataset name.
        payload: Decoded raw JSON or an empty body.

    Returns:
        Record objects in provider order.

    Raises:
        UnexpectedResponseError: If the envelope or any record has the wrong shape.
    """
    if payload is None:
        return []
    data = (
        payload.get("hrvSummaries") if dataset == "hrv" and isinstance(payload, dict) else payload
    )
    if dataset == "hrv" and not isinstance(payload, dict):
        raise UnexpectedResponseError(f"GET {ENDPOINTS[dataset]}: unexpected response shape.")
    if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
        raise UnexpectedResponseError(f"GET {ENDPOINTS[dataset]}: unexpected response shape.")
    return data


def activity_id(record: dict[str, Any]) -> int:
    """Read a stable integer activity key without coercing metric values.

    Args:
        record: Raw activity object.

    Returns:
        A nonnegative activity identifier.

    Raises:
        UnexpectedResponseError: If the identifier is absent or invalid.
    """
    value = record.get("activityId")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        return int(value)
    raise UnexpectedResponseError("GET search_activities: missing or invalid activity identifier.")


def _nested(record: Any, *keys: str) -> Any:
    """Read nested object keys, treating missing/non-object parents as null.

    Args:
        record: Starting JSON value.
        *keys: Object keys to traverse.

    Returns:
        The value or None.
    """
    for key in keys:
        if not isinstance(record, dict):
            return None
        record = record.get(key)
    return record


def _metric(value: Any, integer: bool = False) -> int | float | None:
    """Preserve finite nonnegative numbers and round integer metrics.

    Args:
        value: Raw metric value.
        integer: Whether the destination column is integral.

    Returns:
        A normalized number, or None for missing/sentinel/invalid values.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        if not math.isfinite(value) or value < 0:
            return None
        return int(round(value)) if integer else float(value)
    except OverflowError:
        return None


def _text(value: Any) -> str | None:
    """Accept strings without converting other JSON types.

    Args:
        value: Raw descriptive value.

    Returns:
        The string or None.
    """
    return value if isinstance(value, str) else None


def normalize(dataset: str, payload: Any, start: date, end: date) -> NormalizedBatch:
    """Derive row dictionaries solely from one response and its date range.

    Args:
        dataset: Supported dataset.
        payload: Raw response JSON.
        start: Inclusive requested first day.
        end: Inclusive requested last day.

    Returns:
        Rows with normalized units and the skipped-placeholder count.

    Raises:
        UnexpectedResponseError: If dates, identifiers or record shapes are invalid,
            or a local activity timestamp is malformed or carries a timezone.
    """
    batch = NormalizedBatch([])
    seen: set[date | int] = set()
    for record in records(dataset, payload):
        row: dict[str, Any]
        if dataset == "activities":
            key = activity_id(record)
            if key in seen:
                continue
            seen.add(key)
            stamp = record.get("startTimeLocal")
            try:
                local = datetime.fromisoformat(stamp) if isinstance(stamp, str) else None
            except ValueError:
                raise UnexpectedResponseError(
                    "GET search_activities: invalid local timestamp."
                ) from None
            if local is not None and local.tzinfo is not None:
                raise UnexpectedResponseError(
                    "GET search_activities: local timestamp must not carry a timezone."
                )
            row = {
                "activity_id": key,
                "start_time_local": local,
                "activity_name": _text(record.get("activityName")),
                "activity_type": _text(_nested(record, "activityType", "typeKey")),
                **{
                    dest: _metric(record.get(src))
                    for dest, src in {
                        "distance_m": "distance",
                        "duration_s": "duration",
                        "avg_hr_bpm": "averageHR",
                        "calories_kcal": "calories",
                    }.items()
                },
            }
        else:
            calendar = record.get("calendarDate")
            if calendar is None:
                batch.placeholders += 1
                continue
            try:
                day = date.fromisoformat(calendar) if isinstance(calendar, str) else None
            except ValueError:
                day = None
            if day is None or day.isoformat() != calendar or not start <= day <= end or day in seen:
                raise UnexpectedResponseError(
                    f"GET {ENDPOINTS[dataset]}: invalid or duplicate date."
                )
            seen.add(day)
            row = {"calendar_date": day}
            if dataset == "sleep":
                for dest, src in {
                    "sleep_time_s": "sleepTimeSeconds",
                    "deep_sleep_s": "deepSleepSeconds",
                    "light_sleep_s": "lightSleepSeconds",
                    "rem_sleep_s": "remSleepSeconds",
                    "awake_s": "awakeSleepSeconds",
                    "nap_time_s": "napTimeSeconds",
                    "avg_hr_bpm": "avgHeartRate",
                    "avg_respiration_brpm": "averageRespirationValue",
                }.items():
                    row[dest] = _metric(record.get(src), dest.endswith("_s"))
                row["sleep_score"] = _metric(_nested(record, "sleepScores", "overall", "value"))
            elif dataset == "hrv":
                for dest, src in {
                    "last_night_avg_ms": "lastNightAvg",
                    "weekly_avg_ms": "weeklyAvg",
                    "last_night_5min_high_ms": "lastNight5MinHigh",
                }.items():
                    row[dest] = _metric(record.get(src))
                row["baseline_balanced_low_ms"] = _metric(
                    _nested(record, "baseline", "balancedLow")
                )
                row["baseline_balanced_upper_ms"] = _metric(
                    _nested(record, "baseline", "balancedUpper")
                )
                row["hrv_status"] = _text(record.get("status"))
            else:
                for dest, src in {
                    "overall_stress_level": "overallStressLevel",
                    "rest_s": "restStressDuration",
                    "low_stress_s": "lowStressDuration",
                    "medium_stress_s": "mediumStressDuration",
                    "high_stress_s": "highStressDuration",
                }.items():
                    row[dest] = _metric(_nested(record, "values", src), integer=True)
                score = _metric(_nested(record, "values", "overallStressLevel"))
                if score is not None and score > 100:
                    row["overall_stress_level"] = None
        batch.rows.append(row)
    return batch
