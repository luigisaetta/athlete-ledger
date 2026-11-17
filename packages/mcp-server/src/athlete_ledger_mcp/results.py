"""
Author: L. Saetta
Date Last Modified: 2026-11-17
License: MIT
Description: Typed structured data returned by the four read-only ledger tools.
"""  # noqa: D205, D212

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class DateInterval(BaseModel):
    """Inclusive calendar boundaries with their number of calendar days."""

    start: date = Field(description="First included calendar date, ISO YYYY-MM-DD.")
    end: date = Field(description="Last included calendar date, ISO YYYY-MM-DD.")
    days: int = Field(description="Number of calendar days including both boundaries.")


class DownloadCoverage(BaseModel):
    """Downloaded intervals and missing dates, clipped to a requested range when supplied."""

    intervals: list[DateInterval] = Field(
        description="Downloaded inclusive calendar-date intervals, with day counts."
    )
    gaps: list[DateInterval] = Field(
        description="Missing inclusive calendar-date intervals, with day counts."
    )


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


class TrainingMetrics(BaseModel):
    """Provider-owned per-activity training values with no interpretation or derived scores."""

    aerobic_effect: float | None = Field(
        description="Provider aerobic effect, dimensionless 0–5; 1 decimal; null if missing."
    )
    anaerobic_effect: float | None = Field(
        description="Provider anaerobic effect, dimensionless 0–5; 1 decimal; null if missing."
    )
    effect_label: str | None = Field(
        description="Provider label as stored, unitless text; null if missing; no interpretation."
    )
    load: float | None = Field(
        description="Provider training-load units, 1 decimal; null if missing."
    )
    stress_score: float | None = Field(
        description="Provider training stress score in TSS points, 1 decimal; null if missing."
    )
    intensity_factor: float | None = Field(
        description="Provider intensity factor, dimensionless ratio, 2 decimals; null if missing."
    )
    normalized_power_w: float | None = Field(
        description="Provider normalized power in watts, 0 decimals; null if missing."
    )


class IntensityMinutes(BaseModel):
    """Separate moderate/vigorous minutes, never combined or weighted into a derived score."""

    moderate: int | None = Field(
        description="Moderate-intensity minutes, integer; null if no value is available."
    )
    vigorous: int | None = Field(
        description="Vigorous-intensity minutes, integer; null if no value is available."
    )


class HeartRateZoneMinutes(BaseModel):
    """Per-activity time in each provider heart-rate zone, converted to minutes."""

    zone_1: float | None = Field(
        description="Time in heart-rate zone 1, minutes, 1 decimal; null if missing."
    )
    zone_2: float | None = Field(
        description="Time in heart-rate zone 2, minutes, 1 decimal; null if missing."
    )
    zone_3: float | None = Field(
        description="Time in heart-rate zone 3, minutes, 1 decimal; null if missing."
    )
    zone_4: float | None = Field(
        description="Time in heart-rate zone 4, minutes, 1 decimal; null if missing."
    )
    zone_5: float | None = Field(
        description="Time in heart-rate zone 5, minutes, 1 decimal; null if missing."
    )


class LegTrainingMetrics(BaseModel):
    """Provider training values available for legs, without TSS or intensity factor."""

    aerobic_effect: float | None = Field(
        description="Provider aerobic effect, dimensionless 0–5; 1 decimal; null if missing."
    )

    anaerobic_effect: float | None = Field(
        description="Provider anaerobic effect, dimensionless 0–5; 1 decimal; null if missing."
    )

    effect_label: str | None = Field(
        description="Provider label as stored, unitless text; null if missing; no interpretation."
    )

    load: float | None = Field(
        description="Provider training-load units, 1 decimal; null if missing."
    )

    normalized_power_w: float | None = Field(
        description="Provider normalized power in watts, 0 decimals; null if missing."
    )


class LegData(BaseModel):
    """Ordered multisport leg or transition with the same units as its parent summary."""

    activity_id: int = Field(description="Provider activity identifier, unitless integer.")

    leg_index: int = Field(
        description="Zero-based position in the parent's childIds, unitless integer."
    )

    sport: str | None = Field(description="Provider sport type, unitless text; null if unknown.")

    start_time_local: datetime | None = Field(
        description="Activity start as ISO local wall time, without a timezone."
    )

    distance_km: float | None = Field(
        description="Distance in kilometres, 2 decimals; null if missing."
    )

    duration_min: float | None = Field(
        description="Duration in minutes, 1 decimal; null if missing."
    )

    moving_duration_min: float | None = Field(
        description="Moving duration in minutes, 1 decimal; null if missing."
    )

    avg_hr_bpm: float | None = Field(
        description="Average heart rate in beats/minute, 1 decimal; null if missing."
    )

    max_hr_bpm: float | None = Field(
        description="Maximum heart rate in beats/minute, 1 decimal; null if missing."
    )

    calories_kcal: float | None = Field(description="Energy in kilocalories; null if missing.")

    avg_speed_kmh: float | None = Field(
        description="Average speed in km/h (stored m/s × 3.6), 1 decimal; null if missing."
    )

    elevation_gain_m: float | None = Field(
        description="Elevation gained in metres, 1 decimal; null if missing."
    )

    elevation_loss_m: float | None = Field(
        description="Elevation lost in metres, 1 decimal; null if missing."
    )

    training: LegTrainingMetrics = Field(
        description=(
            "Provider effects (0–5), label, load units and watts; "
            "always present with nullable members."
        )
    )

    intensity_minutes: IntensityMinutes = Field(
        description="Moderate/vigorous integer minutes; always present with nullable members."
    )


class ActivityData(BaseModel):
    """Activity summary with explicit units and provider-local naive start time."""

    activity_id: int = Field(description="Provider activity identifier, unitless integer.")
    start_time_local: datetime = Field(
        description="Activity start as ISO local wall time, without a timezone."
    )
    sport: str | None = Field(description="Provider sport type, unitless text; null if unknown.")
    distance_km: float | None = Field(
        description="Distance in kilometres, 2 decimals; null if missing."
    )
    duration_min: float | None = Field(
        description="Duration in minutes, 1 decimal; null if missing."
    )
    avg_hr_bpm: float | None = Field(
        description="Average heart rate in beats/minute, 1 decimal; null if missing."
    )
    calories_kcal: float | None = Field(description="Energy in kilocalories; null if missing.")
    moving_duration_min: float | None = Field(
        description="Moving duration in minutes, 1 decimal; null if missing."
    )
    avg_speed_kmh: float | None = Field(
        description="Average speed in km/h (stored m/s × 3.6), 1 decimal; null if missing."
    )
    max_hr_bpm: float | None = Field(
        description="Maximum heart rate in beats/minute, 1 decimal; null if missing."
    )
    elevation_gain_m: float | None = Field(
        description="Elevation gained in metres, 1 decimal; null if missing."
    )
    elevation_loss_m: float | None = Field(
        description="Elevation lost in metres, 1 decimal; null if missing."
    )
    training: TrainingMetrics = Field(
        description=(
            "Provider effects (0–5), load units, TSS points, IF ratio and watts; "
            "always present with nullable members."
        )
    )
    intensity_minutes: IntensityMinutes = Field(
        description="Moderate/vigorous integer minutes; always present with nullable members."
    )
    hr_zone_min: HeartRateZoneMinutes = Field(
        description="Per-zone time in minutes, 1 decimal; always present with nullable members."
    )

    legs: list[LegData] = Field(
        description=(
            "Multisport legs ordered by leg_index; transitions have sport transition_v2. "
            "Empty for non-multisport activities or until athlete-ledger sync legs has run. "
            "Distances km, durations/intensity minutes, HR beats/minute, energy kcal, "
            "speed km/h, elevation metres; training effects 0–5, load units and watts."
        )
    )


class NamedActivityData(ActivityData):
    """Activity summary including its possibly sensitive name only after explicit opt-in."""

    activity_name: str | None = Field(
        description="Activity name as stored, unitless text; returned only with server opt-in."
    )


class ActivityList(BaseModel):
    """Ordered activity page with total match count and requested-range download coverage."""

    activities: list[NamedActivityData | ActivityData] = Field(
        description="Activities ordered by local start time then identifier, with metric units."
    )
    total_matching: int = Field(
        description="Count of all activities matching the date and sport filters before the limit."
    )
    truncated: bool = Field(
        description="True when the matching activity count exceeds the number returned."
    )
    coverage: DownloadCoverage = Field(
        description="Downloaded and missing calendar-date intervals within the requested range."
    )


class MissingValues(BaseModel):
    """Activity counts lacking each input metric before summation or HR weighting."""

    distance: int = Field(description="Count of activities missing distance in metres.")
    duration: int = Field(description="Count of activities missing duration in seconds.")
    calories: int = Field(description="Count of activities missing energy in kilocalories.")
    avg_hr: int = Field(
        description="Count of activities missing average heart rate in beats/minute."
    )
    training_load: int = Field(
        description="Count of activities missing provider training-load units."
    )
    training_stress_score: int = Field(description="Count of activities missing TSS points.")
    moving_duration: int = Field(
        description="Count of activities missing moving duration in seconds."
    )
    elevation_gain: int = Field(description="Count of activities missing elevation gain in metres.")
    hr_zones: int = Field(
        description="Count of activities missing all five zone durations in seconds."
    )


class HeartRateZoneHours(BaseModel):
    """Plain per-zone sums converted from stored seconds to hours."""

    zone_1: float | None = Field(
        description="Zone 1 time sum in hours, 2 decimals; null if all missing."
    )
    zone_2: float | None = Field(
        description="Zone 2 time sum in hours, 2 decimals; null if all missing."
    )
    zone_3: float | None = Field(
        description="Zone 3 time sum in hours, 2 decimals; null if all missing."
    )
    zone_4: float | None = Field(
        description="Zone 4 time sum in hours, 2 decimals; null if all missing."
    )
    zone_5: float | None = Field(
        description="Zone 5 time sum in hours, 2 decimals; null if all missing."
    )


class ActivityAggregate(BaseModel):
    """Totals excluding null inputs and duration-weighted HR over eligible activities."""

    activity_count: int = Field(description="Number of activities included in this aggregate.")
    distance_km: float | None = Field(
        description="Distance sum in kilometres, 2 decimals; null if all missing or empty."
    )
    duration_h: float | None = Field(
        description="Duration sum in hours, 2 decimals; null if all missing or empty."
    )
    calories_kcal: float | None = Field(
        description="Energy sum in kilocalories; null if all missing or empty."
    )
    avg_hr_bpm: float | None = Field(
        description=(
            "Duration-weighted heart rate in beats/minute, 1 decimal; "
            "requires both inputs and positive total duration."
        )
    )
    training_load: float | None = Field(
        description="Provider training-load units sum, 1 decimal; null if all missing."
    )
    training_stress_score: float | None = Field(
        description="Training stress sum in TSS points, 1 decimal; null if all missing."
    )
    moving_duration_h: float | None = Field(
        description="Moving duration sum in hours, 2 decimals; null if all missing."
    )
    elevation_gain_m: float | None = Field(
        description="Elevation gain sum in metres, 1 decimal; null if all missing."
    )
    intensity_minutes: IntensityMinutes = Field(
        description="Plain sums of integer minutes; always present, all-missing members null."
    )
    hr_zone_h: HeartRateZoneHours = Field(
        description="Plain zone-time sums in hours, 2 decimals; always present, members nullable."
    )
    missing: MissingValues = Field(
        description="Activity counts lacking each input metric before aggregation."
    )


class ActivityGroup(ActivityAggregate):
    """One stable week/month/sport group with its ISO key and optional week Monday."""

    key: str = Field(
        description="ISO week YYYY-Www, month YYYY-MM or sport; unknown for null sport."
    )
    monday: date | None = Field(
        default=None,
        description="ISO calendar date of the week Monday; null for month or sport groups.",
    )


class ActivityTotals(BaseModel):
    """Grouped and overall activity aggregates with coverage for the inclusive request."""

    group_by: Literal["week", "month", "sport"] = Field(
        description="Grouping dimension: ISO week, calendar month or sport."
    )
    groups: list[ActivityGroup] = Field(
        description="Aggregates in group-key order, with metric units and missing-input counts."
    )
    overall: ActivityAggregate = Field(
        description="Overall aggregate with metric units and missing-input counts."
    )
    coverage: DownloadCoverage = Field(
        description="Downloaded and missing calendar-date intervals within the requested range."
    )


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
