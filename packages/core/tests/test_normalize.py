"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Verify pure normalization using synthetic summary payloads.
"""  # noqa: D205, D212

import copy
import math
from datetime import date, datetime

import pytest
from athlete_ledger.normalize.garmin_connect import normalize
from athlete_ledger.sync_types import UnexpectedResponseError

DAY = date(2026, 1, 2)


def norm(dataset, payload):
    """Normalize one synthetic response for the fixed single-day fixture range.

    Args:
        dataset: Dataset whose field mapping should be exercised.
        payload: Synthetic decoded response with that dataset's envelope.

    Returns:
        NormalizedBatch for DAY, containing derived row dictionaries and placeholder
        count.
    """
    return normalize(dataset, payload, DAY, DAY)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (-1, None),
        (0, 0),
        (True, None),
        (False, None),
        (None, None),
        ("12", None),
        (math.inf, None),
        (math.nan, None),
        (12.0, 12),
        (12.6, 13),
        (12.4, 12),
    ],
)
def test_numeric_rules_and_integer_rounding(value, expected):
    """Verify numeric rules and integer rounding."""
    row = norm("sleep", [{"calendarDate": str(DAY), "sleepTimeSeconds": value}]).rows[0]
    assert row["sleep_time_s"] == expected
    assert row["deep_sleep_s"] is None


def test_sleep_nested_nulls_and_full_mapping():
    """Verify sleep nested nulls and full mapping."""
    payload = [
        {
            "calendarDate": str(DAY),
            "sleepTimeSeconds": 200,
            "deepSleepSeconds": 30,
            "lightSleepSeconds": 40,
            "remSleepSeconds": 50,
            "awakeSleepSeconds": 5,
            "napTimeSeconds": 0,
            "sleepScores": {"overall": {"value": 77}},
            "avgHeartRate": 45,
            "averageRespirationValue": 14.5,
        }
    ]
    before = copy.deepcopy(payload)
    row = norm("sleep", payload).rows[0]
    assert row == {
        "calendar_date": DAY,
        "sleep_time_s": 200,
        "deep_sleep_s": 30,
        "light_sleep_s": 40,
        "rem_sleep_s": 50,
        "awake_s": 5,
        "nap_time_s": 0,
        "sleep_score": 77,
        "avg_hr_bpm": 45,
        "avg_respiration_brpm": 14.5,
    }
    assert payload == before
    for scores in (None, {}, {"overall": None}, True):
        assert (
            norm("sleep", [{"calendarDate": str(DAY), "sleepScores": scores}]).rows[0][
                "sleep_score"
            ]
            is None
        )


def test_hrv_nested_mapping_and_placeholders():
    """Verify hrv nested mapping and placeholders."""
    payload = {
        "hrvSummaries": [
            {"calendarDate": None},
            {
                "calendarDate": str(DAY),
                "lastNightAvg": 0,
                "weeklyAvg": -1,
                "lastNight5MinHigh": 32.5,
                "baseline": {"balancedLow": 20, "balancedUpper": 60},
                "status": "SYNTHETIC",
            },
        ]
    }
    batch = norm("hrv", payload)
    assert batch.placeholders == 1
    assert batch.rows == [
        {
            "calendar_date": DAY,
            "last_night_avg_ms": 0,
            "weekly_avg_ms": None,
            "last_night_5min_high_ms": 32.5,
            "baseline_balanced_low_ms": 20,
            "baseline_balanced_upper_ms": 60,
            "hrv_status": "SYNTHETIC",
        }
    ]
    assert (
        norm("hrv", {"hrvSummaries": [{"calendarDate": str(DAY), "baseline": None}]}).rows[0][
            "baseline_balanced_low_ms"
        ]
        is None
    )


@pytest.mark.parametrize(
    ("score", "expected"),
    [(0, 0), (100, 100), (100.1, None), (101, None), (-1, None), (True, None), (99.6, 100)],
)
def test_stress_bounds_and_duration_location(score, expected):
    """Verify stress bounds and duration location."""
    row = norm(
        "stress",
        [
            {
                "calendarDate": str(DAY),
                "values": {
                    "overallStressLevel": score,
                    "restStressDuration": 0,
                    "lowStressDuration": 1.6,
                    "mediumStressDuration": -1,
                    "highStressDuration": None,
                },
            }
        ],
    ).rows[0]
    assert row == {
        "calendar_date": DAY,
        "overall_stress_level": expected,
        "rest_s": 0,
        "low_stress_s": 2,
        "medium_stress_s": None,
        "high_stress_s": None,
    }
    assert norm("stress", [{"calendarDate": str(DAY), "values": None}]).rows[0]["rest_s"] is None


def test_activity_mapping_preserves_local_wall_time_and_zero():
    """Verify activity mapping preserves local wall time and zero."""
    payload = [
        {
            "activityId": 1234567890123,
            "startTimeLocal": "2026-01-02 08:10:20",
            "activityName": "SYNTHETIC-NAME",
            "activityType": {"typeKey": "synthetic-sport"},
            "distance": 0,
            "duration": 12.5,
            "averageHR": -1,
            "calories": True,
        }
    ]
    row = norm("activities", payload).rows[0]
    assert row == {
        "activity_id": 1234567890123,
        "start_time_local": datetime(2026, 1, 2, 8, 10, 20),
        "activity_name": "SYNTHETIC-NAME",
        "activity_type": "synthetic-sport",
        "distance_m": 0,
        "duration_s": 12.5,
        "avg_hr_bpm": None,
        "calories_kcal": None,
        **dict.fromkeys(
            (
                "aerobic_training_effect",
                "anaerobic_training_effect",
                "training_effect_label",
                "training_load",
                "training_stress_score",
                "intensity_factor",
                "normalized_power_w",
                "elevation_gain_m",
                "elevation_loss_m",
                "max_hr_bpm",
                "avg_speed_mps",
                "moving_duration_s",
                "moderate_intensity_min",
                "vigorous_intensity_min",
                "hr_zone_1_s",
                "hr_zone_2_s",
                "hr_zone_3_s",
                "hr_zone_4_s",
                "hr_zone_5_s",
            )
        ),
    }
    assert row["start_time_local"].tzinfo is None
    assert len(norm("activities", payload * 2).rows) == 1


@pytest.mark.parametrize("identifier", [None, True, -1, "private", 12.5])
def test_invalid_activity_identifier(identifier):
    """Verify invalid activity identifier."""
    with pytest.raises(UnexpectedResponseError):
        norm("activities", [{"activityId": identifier}])


@pytest.mark.parametrize("dataset", ["sleep", "hrv", "stress"])
@pytest.mark.parametrize("invalid", ["2026-01-03", "invalid-date", "20260102", True])
def test_daily_out_of_range_or_invalid_date(dataset, invalid):
    """Verify daily out of range or invalid date."""
    rows = [{"calendarDate": invalid}]
    payload = {"hrvSummaries": rows} if dataset == "hrv" else rows
    with pytest.raises(UnexpectedResponseError):
        norm(dataset, payload)


@pytest.mark.parametrize("dataset", ["sleep", "hrv", "stress"])
def test_duplicate_dates_are_fatal_but_null_placeholders_skip(dataset):
    """Verify duplicate dates are fatal but null placeholders skip."""
    rows = [{"calendarDate": str(DAY)}] * 2
    payload = {"hrvSummaries": rows} if dataset == "hrv" else rows
    with pytest.raises(UnexpectedResponseError):
        norm(dataset, payload)
    rows = [{"calendarDate": None}, {}]
    payload = {"hrvSummaries": rows} if dataset == "hrv" else rows
    batch = norm(dataset, payload)
    assert batch.rows == [] and batch.placeholders == 2


@pytest.mark.parametrize(
    ("stamp", "microsecond"),
    [
        ("2026-01-02 08:10:20", 0),
        ("2026-01-02T08:10:20", 0),
        ("2026-01-02 08:10:20.123456", 123456),
        ("2026-01-02T08:10:20.123", 123000),
    ],
)
def test_activity_local_iso_datetime_forms(stamp, microsecond):
    """Accept space/T separators and preserve fractional seconds in naive local time."""
    row = norm("activities", [{"activityId": 7, "startTimeLocal": stamp}]).rows[0]
    assert row["start_time_local"] == datetime(2026, 1, 2, 8, 10, 20, microsecond)
    assert row["start_time_local"].tzinfo is None


@pytest.mark.parametrize(
    "stamp",
    [
        "2026-01-02T08:10:20Z",
        "2026-01-02T08:10:20+00:00",
        "2026-01-02 08:10:20.123456+02:00",
        "2026-01-02T08:10:20-05:30",
    ],
)
def test_activity_local_timestamp_rejects_timezones(stamp):
    """Reject timezone-bearing timestamps without exposing their original values."""
    with pytest.raises(UnexpectedResponseError, match="must not carry a timezone") as error:
        norm("activities", [{"activityId": 7, "startTimeLocal": stamp}])
    assert stamp not in str(error.value)
    assert "search_activities" in str(error.value)


def test_activity_local_timestamp_parse_failure_is_sanitized():
    """Keep invalid timestamp text out of unexpected-response diagnostics."""
    marker = "SYNTHETIC-PRIVATE-TIMESTAMP"
    with pytest.raises(UnexpectedResponseError, match="invalid local timestamp") as error:
        norm("activities", [{"activityId": 7, "startTimeLocal": marker}])
    assert marker not in str(error.value)
