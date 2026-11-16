"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Typed structured data returned by the four read-only ledger tools.
"""  # noqa: D205, D212

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class DateInterval(BaseModel):
    """Inclusive calendar boundaries with their number of calendar days."""

    start: date
    end: date
    days: int


class DownloadCoverage(BaseModel):
    """Downloaded intervals and missing dates, clipped to a requested range when supplied."""

    intervals: list[DateInterval]
    gaps: list[DateInterval]


class DatasetStatus(BaseModel):
    """Download coverage, sync progress and actual record boundaries for one data area."""

    dataset: str
    coverage: DownloadCoverage
    last_synced_date: date | None
    last_run_at: datetime | None
    last_status: str | None
    first_record_date: date | None
    last_record_date: date | None


class SportCount(BaseModel):
    """Number of stored activities for a sport; null denotes an unknown activity type."""

    sport: str | None
    activity_count: int


class LedgerStatus(BaseModel):
    """Local current date, dataset availability and sports represented in the ledger."""

    local_date: date
    datasets: list[DatasetStatus]
    sports: list[SportCount]


class ActivityData(BaseModel):
    """Activity summary with explicit units and provider-local naive start time."""

    activity_id: int
    start_time_local: datetime
    sport: str | None
    distance_km: float | None
    duration_min: float | None
    avg_hr_bpm: float | None
    calories_kcal: float | None


class NamedActivityData(ActivityData):
    """Activity summary including its possibly sensitive name only after explicit opt-in."""

    activity_name: str | None


class ActivityList(BaseModel):
    """Ordered activity page with total match count and requested-range download coverage."""

    activities: list[NamedActivityData | ActivityData]
    total_matching: int
    truncated: bool
    coverage: DownloadCoverage


class MissingValues(BaseModel):
    """Activity counts lacking each input metric before summation or HR weighting."""

    distance: int
    duration: int
    calories: int
    avg_hr: int


class ActivityAggregate(BaseModel):
    """Totals excluding null inputs and duration-weighted HR over eligible activities."""

    activity_count: int
    distance_km: float | None
    duration_h: float | None
    calories_kcal: float | None
    avg_hr_bpm: float | None
    missing: MissingValues


class ActivityGroup(ActivityAggregate):
    """One stable week/month/sport group with its ISO key and optional week Monday."""

    key: str
    monday: date | None = None


class ActivityTotals(BaseModel):
    """Grouped and overall activity aggregates with coverage for the inclusive request."""

    group_by: Literal["week", "month", "sport"]
    groups: list[ActivityGroup]
    overall: ActivityAggregate
    coverage: DownloadCoverage


class SleepValues(BaseModel):
    """Sleep durations in minutes, score and mean heart/respiration rates."""

    sleep_min: float | None
    deep_min: float | None
    light_min: float | None
    rem_min: float | None
    awake_min: float | None
    nap_min: float | None
    sleep_score: float | None
    avg_hr_bpm: float | None
    avg_respiration_brpm: float | None


class HRVValues(BaseModel):
    """Daily and baseline heart-rate variability in milliseconds, plus provider status."""

    last_night_avg_ms: float | None
    weekly_avg_ms: float | None
    last_night_5min_high_ms: float | None
    baseline_low_ms: float | None
    baseline_upper_ms: float | None
    status: str | None


class StressValues(BaseModel):
    """Overall stress level on 0–100 and per-level durations in minutes."""

    overall_stress_level: int | None
    rest_min: float | None
    low_min: float | None
    medium_min: float | None
    high_min: float | None


class WellnessArea(BaseModel):
    """Download/record state and nullable metrics for one area on one date."""

    state: Literal["not_downloaded", "no_record", "ok"]
    values: SleepValues | HRVValues | StressValues | None = None


class WellnessDay(BaseModel):
    """One calendar day with states for exactly the requested wellness areas."""

    calendar_date: date
    areas: dict[str, WellnessArea]


class DailyWellness(BaseModel):
    """Every day in an inclusive range of at most 92 days, in date order."""

    days: list[WellnessDay]
