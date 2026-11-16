"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Verify allowlisted activity training fields and value rules with synthetic data.
"""  # noqa: D205, D212

import copy
import math
from datetime import date
from typing import Any

import pytest
from athlete_ledger.normalize.garmin_connect import normalize

DAY = date(2026, 1, 2)
FIELDS = {
    "aerobic_training_effect": "aerobicTrainingEffect",
    "anaerobic_training_effect": "anaerobicTrainingEffect",
    "training_effect_label": "trainingEffectLabel",
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
}


def activity(values: dict[str, Any]) -> dict[str, Any]:
    """Normalize one synthetic activity without a database or provider.

    Args:
        values: Provider fields to merge with a fixed synthetic activity identifier.

    Returns:
        The single normalized activity dictionary.
    """
    return normalize("activities", [{"activityId": 7, **values}], DAY, DAY).rows[0]


def test_all_training_fields_preserve_native_units_without_mutating_payload() -> None:
    """Map all nineteen fields to their named units, rounding only intensity minutes."""
    expected = {
        "aerobic_training_effect": 3.2,
        "anaerobic_training_effect": 1.8,
        "training_effect_label": "SYNTHETIC-TRAINING-LABEL",
        "training_load": 120.5,
        "training_stress_score": 88.5,
        "intensity_factor": 0.85,
        "normalized_power_w": 250.5,
        "elevation_gain_m": 123.4,
        "elevation_loss_m": 100.2,
        "max_hr_bpm": 180,
        "avg_speed_mps": 3.25,
        "moving_duration_s": 3600.5,
        "moderate_intensity_min": 12,
        "vigorous_intensity_min": 13,
        "hr_zone_1_s": 0,
        "hr_zone_2_s": 60.5,
        "hr_zone_3_s": 120,
        "hr_zone_4_s": 180.25,
        "hr_zone_5_s": 30,
    }
    values = {source: expected[column] for column, source in FIELDS.items()}
    values.update(moderateIntensityMinutes=12.0, vigorousIntensityMinutes=12.6)
    before = copy.deepcopy(values)
    row = activity(values)
    assert {column: row[column] for column in FIELDS} == expected
    assert isinstance(row["moderate_intensity_min"], int)
    assert isinstance(row["vigorous_intensity_min"], int)
    assert values == before


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (-1, None),
        (0, 0),
        (True, None),
        (False, None),
        ("12", None),
        (math.inf, None),
        (-math.inf, None),
        (math.nan, None),
        (10**1000, None),
    ],
)
def test_all_numeric_training_fields_share_missing_sentinel_and_zero_rules(value, expected) -> None:
    """Apply the existing numeric rules to every numeric training destination."""
    row = activity(
        {source: value for column, source in FIELDS.items() if column != "training_effect_label"}
    )
    for column in FIELDS:
        if column != "training_effect_label":
            assert row[column] == expected


def test_missing_training_fields_produce_all_nineteen_nulls() -> None:
    """Keep missing training fields explicitly nullable in the normalized dictionary."""
    row = activity({})
    assert len(FIELDS) == 19
    assert all(row[column] is None for column in FIELDS)


@pytest.mark.parametrize(
    ("value", "expected"), [(0, 0), (5, 5), (5.01, None), (4.9, 4.9), (-0.01, None)]
)
def test_training_effects_enforce_both_inclusive_bounds(value, expected) -> None:
    """Accept both endpoints of 0–5 and reject values outside that interval."""
    row = activity({"aerobicTrainingEffect": value, "anaerobicTrainingEffect": value})
    assert row["aerobic_training_effect"] == expected
    assert row["anaerobic_training_effect"] == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", None),
        ("x" * 65, None),
        ("x" * 64, "x" * 64),
        (None, None),
        (True, None),
        (123, None),
        ([], None),
        ({}, None),
        (" ", " "),
    ],
)
def test_training_label_is_string_only_nonempty_bounded_and_never_truncated(
    value, expected
) -> None:
    """Reject invalid labels without coercion, trimming or silent truncation."""
    assert activity({"trainingEffectLabel": value})["training_effect_label"] == expected


@pytest.mark.parametrize(("value", "expected"), [(12.0, 12), (12.6, 13), (12.4, 12), (0.5, 0)])
def test_intensity_minutes_use_existing_integer_rounding(value, expected) -> None:
    """Use the existing integer rounding convention for both intensity-minute columns."""
    row = activity({"moderateIntensityMinutes": value, "vigorousIntensityMinutes": value})
    assert row["moderate_intensity_min"] == expected
    assert row["vigorous_intensity_min"] == expected


def test_excluded_location_identity_and_device_fields_are_never_read() -> None:
    """Fail on access to excluded raw fields and ensure no such normalized keys are emitted."""
    excluded = {
        "startLatitude",
        "startLongitude",
        "endLatitude",
        "endLongitude",
        "locationName",
        "ownerId",
        "ownerFullName",
        "ownerDisplayName",
        "ownerProfileImageUrlSmall",
        "ownerProfileImageUrlMedium",
        "ownerProfileImageUrlLarge",
        "deviceId",
        "deviceName",
    }

    class GuardedRecord(dict):
        """Reject reads of forbidden fields while preserving the normal dict interface."""

        def get(self, key, default=None):
            """Read an allowlisted field, rejecting access to excluded private keys.

            Args:
                key: Raw field requested by normalization.
                default: Value returned when an allowed field is missing.

            Returns:
                The stored or default value for an allowed field.

            Raises:
                AssertionError: If normalization attempts to read an excluded field.
            """
            assert key not in excluded
            return super().get(key, default)

    record = GuardedRecord(
        activityId=7,
        aerobicTrainingEffect=3.2,
        **dict.fromkeys(excluded, "SYNTHETIC-PRIVATE-MARKER"),
    )
    row = normalize("activities", [record], DAY, DAY).rows[0]
    assert not excluded.intersection(row)
    assert not any(
        token in key.lower()
        for key in row
        for token in ("latitude", "longitude", "location", "owner", "device")
    )
