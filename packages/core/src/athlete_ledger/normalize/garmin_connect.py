"""
Author: L. Saetta
Date Last Modified: 2026-11-17
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


def _training_metrics(record: dict[str, Any]) -> dict[str, Any]:
    """Read only the allowlisted activity training fields using existing numeric rules.

    Args:
        record: One raw activity summary; location, owner and device keys are never read.

    Returns:
        All nineteen training columns, with invalid/missing metrics mapped to None.
        Training effects are bounded to 0–5; labels must be nonempty strings of
        at most 64 characters, and intensity minutes use integer rounding.
    """
    result = {
        dest: _metric(record.get(src), integer=dest.endswith("_min"))
        for dest, src in {
            "training_load": "activityTrainingLoad",
            "training_stress_score": "trainingStressScore",
            "intensity_factor": "intensityFactor",
            "normalized_power_w": "normPower",
            "elevation_gain_m": "elevationGain",
            "elevation_loss_m": "elevationLoss",
            "max_hr_bpm": "maxHR",
            "avg_speed_mps": "averageSpeed",
            "moving_duration_s": "movingDuration",
            "moderate_intensity_min": "moderateIntensityMinutes",
            "vigorous_intensity_min": "vigorousIntensityMinutes",
            "hr_zone_1_s": "hrTimeInZone_1",
            "hr_zone_2_s": "hrTimeInZone_2",
            "hr_zone_3_s": "hrTimeInZone_3",
            "hr_zone_4_s": "hrTimeInZone_4",
            "hr_zone_5_s": "hrTimeInZone_5",
        }.items()
    }
    for dest, src in {
        "aerobic_training_effect": "aerobicTrainingEffect",
        "anaerobic_training_effect": "anaerobicTrainingEffect",
    }.items():
        value = _metric(record.get(src))
        result[dest] = value if value is not None and value <= 5 else None
    label = record.get("trainingEffectLabel")
    return {
        **result,
        "training_effect_label": label
        if isinstance(label, str) and 1 <= len(label) <= 64
        else None,
    }


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
    seen: set[date] = set()
    for record in records(dataset, payload):
        row: dict[str, Any]
        if dataset == "activities":
            key = activity_id(record)
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
                **_training_metrics(record),
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


def detail_identifier(value: Any) -> int:
    """Validate a detail identifier without exposing its value.

    Args:
        value: Provider identifier from parameters, childIds or parentId.

    Returns:
        A nonnegative integer identifier.

    Raises:
        UnexpectedResponseError: If the identifier is invalid.
    """
    try:
        return activity_id({"activityId": value})
    except UnexpectedResponseError:
        raise UnexpectedResponseError("get_activity") from None


def detail_summary(payload: Any) -> dict[str, Any]:
    """Validate the detail envelope and its naive local timestamp.

    Args:
        payload: Unmodified decoded detail response.

    Returns:
        The summary object.

    Raises:
        UnexpectedResponseError: If shape or timestamp is invalid.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("summaryDTO"), dict):
        raise UnexpectedResponseError("get_activity")
    summary: dict[str, Any] = payload["summaryDTO"]
    stamp = summary.get("startTimeLocal")
    if stamp is not None:
        try:
            if not isinstance(stamp, str) or datetime.fromisoformat(stamp).tzinfo is not None:
                raise ValueError
        except ValueError:
            raise UnexpectedResponseError("get_activity") from None
    return summary


def detail_children(payload: Any) -> list[int]:
    """Read the ordered leg identifiers from a validated parent detail.

    Args:
        payload: Parent detail response.

    Returns:
        Ordered child identifiers, empty when metadata or childIds are absent.

    Raises:
        UnexpectedResponseError: If childIds or the detail envelope is malformed.
    """
    detail_summary(payload)
    metadata = payload.get("metadataDTO")
    if metadata is None:
        return []
    if not isinstance(metadata, dict):
        raise UnexpectedResponseError("get_activity")
    children = metadata.get("childIds")
    if children is None:
        return []
    if not isinstance(children, list):
        raise UnexpectedResponseError("get_activity")
    identifiers = [detail_identifier(value) for value in children]
    if len(set(identifiers)) != len(identifiers):
        raise UnexpectedResponseError("get_activity")
    return identifiers


def normalize_leg(payload: Any, identifier: int, parent_id: int, leg_index: int) -> dict[str, Any]:
    """Derive only allowlisted leg columns from a complete detail response.

    Args:
        payload: Raw detail; excluded fields are never read.
        identifier: Requested leg identifier from the parent childIds.
        parent_id: Stored parent activity identifier.
        leg_index: Zero-based position in childIds.

    Returns:
        Leg columns without provider or provenance metadata.

    Raises:
        UnexpectedResponseError: If identifiers, shape or timestamp are invalid.
    """
    summary = detail_summary(payload)
    if detail_identifier(payload.get("parentId")) != parent_id:
        raise UnexpectedResponseError("get_activity")
    if detail_identifier(payload.get("activityId")) != identifier:
        raise UnexpectedResponseError("get_activity")
    stamp = summary.get("startTimeLocal")
    metrics = _training_metrics(
        {
            **{
                key: summary.get(key)
                for key in (
                    "anaerobicTrainingEffect",
                    "trainingEffectLabel",
                    "activityTrainingLoad",
                    "elevationGain",
                    "elevationLoss",
                    "maxHR",
                    "averageSpeed",
                    "movingDuration",
                    "moderateIntensityMinutes",
                    "vigorousIntensityMinutes",
                )
            },
            "aerobicTrainingEffect": summary.get("trainingEffect"),
            "normPower": summary.get("normalizedPower"),
        }
    )
    allowed_training = (
        "aerobic_training_effect",
        "anaerobic_training_effect",
        "training_effect_label",
        "training_load",
        "normalized_power_w",
        "moderate_intensity_min",
        "vigorous_intensity_min",
        "elevation_gain_m",
        "elevation_loss_m",
        "max_hr_bpm",
        "avg_speed_mps",
        "moving_duration_s",
    )
    return {
        "activity_id": identifier,
        "parent_activity_id": parent_id,
        "leg_index": leg_index,
        "start_time_local": datetime.fromisoformat(stamp) if stamp is not None else None,
        "activity_type": _text(_nested(payload, "activityTypeDTO", "typeKey")),
        **{
            dest: _metric(summary.get(src))
            for dest, src in {
                "distance_m": "distance",
                "duration_s": "duration",
                "avg_hr_bpm": "averageHR",
                "calories_kcal": "calories",
            }.items()
        },
        **{name: metrics[name] for name in allowed_training},
    }
