"""
Author: L. Saetta
Date Last Modified: 2026-11-17
License: MIT
Description: Read normalized ledger columns and derive bounded, unit-explicit tool data.
"""  # noqa: D205, D212

from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from typing import Any, Literal

from athlete_ledger.coverage import Coverage, sync_coverage
from athlete_ledger.db.models import (
    Activity,
    ActivityLeg,
    DailyHRV,
    DailySleep,
    DailyStress,
    SyncState,
)
from athlete_ledger.sync_types import PROVIDER
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import ColumnElement, Engine, func, select

from athlete_ledger_mcp.results import (
    ActivityAggregate,
    ActivityData,
    ActivityGroup,
    ActivityList,
    ActivityTotals,
    DailyWellness,
    DatasetStatus,
    DateInterval,
    DownloadCoverage,
    HeartRateZoneHours,
    HeartRateZoneMinutes,
    HRVValues,
    IntensityMinutes,
    LedgerStatus,
    LegData,
    LegTrainingMetrics,
    MissingValues,
    NamedActivityData,
    SleepValues,
    SportCount,
    StressValues,
    TrainingMetrics,
    WellnessArea,
    WellnessDay,
)

WELLNESS = ("sleep", "hrv", "stress")


def parse_range(start: str, end: str, maximum: int | None = None) -> tuple[date, date]:
    """Validate canonical ISO boundaries without echoing rejected input values.

    Args:
        start: Inclusive first date in YYYY-MM-DD format.
        end: Inclusive last date in YYYY-MM-DD format.
        maximum: Optional maximum inclusive calendar-day count.

    Returns:
        Parsed first and last dates.

    Raises:
        ToolError: If dates are malformed, reversed or exceed the allowed day count.
    """
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if first.isoformat() != start or last.isoformat() != end:
            raise ValueError
    except ValueError:
        raise ToolError("Dates must use YYYY-MM-DD.") from None
    if first > last:
        raise ToolError("start must not exceed end.")
    if maximum is not None and (last - first).days + 1 > maximum:
        raise ToolError(f"Range must contain at most {maximum} days.")
    return first, last


def interval(start: date, end: date) -> DateInterval:
    """Attach an inclusive day count to calendar boundaries.

    Args:
        start: First included calendar day.
        end: Last included calendar day.

    Returns:
        Boundaries and the count including both endpoints.
    """
    return DateInterval(start=start, end=end, days=(end - start).days + 1)


def coverage_result(
    coverage: Coverage, start: date | None = None, end: date | None = None
) -> DownloadCoverage:
    """Serialize full coverage or clip it and include uncovered request edges.

    Args:
        coverage: Sorted disjoint inclusive downloaded intervals.
        start: Optional first requested date; supplied together with end.
        end: Optional last requested date.

    Returns:
        Covered intervals and missing dates inside the request, or internal gaps
        only when no request boundaries are supplied.
    """
    if start is None or end is None:
        return DownloadCoverage(
            intervals=[interval(a, b) for a, b in coverage.intervals],
            gaps=[interval(a, b) for a, b in coverage.gaps],
        )
    clipped = [
        (max(a, start), min(b, end)) for a, b in coverage.intervals if a <= end and b >= start
    ]
    gaps: list[DateInterval] = []
    cursor = start.toordinal()
    for a, b in clipped:
        if cursor < a.toordinal():
            gaps.append(interval(date.fromordinal(cursor), a - timedelta(days=1)))
        cursor = b.toordinal() + 1
    if cursor <= end.toordinal():
        gaps.append(interval(date.fromordinal(cursor), end))
    return DownloadCoverage(intervals=[interval(a, b) for a, b in clipped], gaps=gaps)


def scaled(value: float | int | None, divisor: float = 1, digits: int = 1) -> float | None:
    """Convert a nullable stored metric to the requested unit and precision.

    Args:
        value: Stored numeric metric, with None meaning unavailable.
        divisor: Unit conversion divisor, such as 60 seconds per minute.
        digits: Number of decimal places in the returned metric.

    Returns:
        Rounded converted value, preserving None and valid zero values.
    """
    return round(value / divisor, digits) if value is not None else None


def activity_filter(start: date, end: date) -> tuple[ColumnElement[bool], ...]:
    """Build portable provider and naive local-datetime predicates.

    Args:
        start: First local calendar day to include.
        end: Last local calendar day to include.

    Returns:
        Provider and half-open datetime predicates from start midnight through
        the next midnight after end, avoiding fractional-second upper bounds.
        At date.max the upper bound is unbounded because no later date exists.
    """
    predicates: tuple[ColumnElement[bool], ...] = (
        Activity.provider == PROVIDER,
        Activity.start_time_local >= datetime.combine(start, time.min),
    )
    if end < date.max:
        predicates += (
            Activity.start_time_local < datetime.combine(end + timedelta(days=1), time.min),
        )
    return predicates


def ledger_status(engine: Engine, today: date) -> LedgerStatus:
    """Read coverage, progress, actual record bounds and sport counts.

    Args:
        engine: Read-only ledger engine.
        today: Server's local current calendar date.

    Returns:
        Availability metadata in dataset order and sport counts in stable order.
    """
    coverage = sync_coverage(engine)
    datasets: list[DatasetStatus] = []
    with engine.connect() as connection:
        progress = {
            row.dataset: row
            for row in connection.execute(
                select(
                    SyncState.dataset,
                    SyncState.last_synced_date,
                    SyncState.last_run_at,
                    SyncState.last_status,
                ).where(SyncState.provider == PROVIDER)
            )
        }
        for dataset, model, column in (
            ("activities", Activity, Activity.start_time_local),
            ("sleep", DailySleep, DailySleep.calendar_date),
            ("hrv", DailyHRV, DailyHRV.calendar_date),
            ("stress", DailyStress, DailyStress.calendar_date),
        ):
            first, last = connection.execute(
                select(func.min(column), func.max(column)).where(model.provider == PROVIDER)
            ).one()
            state = progress.get(dataset)
            datasets.append(
                DatasetStatus(
                    dataset=dataset,
                    coverage=coverage_result(coverage[dataset]),
                    last_synced_date=state.last_synced_date if state else None,
                    last_run_at=state.last_run_at if state else None,
                    last_status=state.last_status if state else None,
                    first_record_date=first.date() if isinstance(first, datetime) else first,
                    last_record_date=last.date() if isinstance(last, datetime) else last,
                )
            )
        sports = [
            SportCount(sport=sport, activity_count=count)
            for sport, count in connection.execute(
                select(Activity.activity_type, func.count())
                .where(Activity.provider == PROVIDER)
                .group_by(Activity.activity_type)
            )
        ]
    return LedgerStatus(
        local_date=today,
        datasets=datasets,
        sports=sorted(sports, key=lambda item: (item.sport is None, item.sport or "")),
    )


def common_training_values(row: Any) -> dict[str, Any]:
    """Convert training and movement values shared by activity summaries and legs.

    Args:
        row: Selected normalized columns in provider-native units.

    Returns:
        Converted scalar fields and stable training/intensity objects with nullable members.
    """
    return {
        "moving_duration_min": scaled(row.moving_duration_s, 60),
        "avg_speed_kmh": scaled(row.avg_speed_mps * 3.6) if row.avg_speed_mps is not None else None,
        "max_hr_bpm": scaled(row.max_hr_bpm),
        "elevation_gain_m": scaled(row.elevation_gain_m),
        "elevation_loss_m": scaled(row.elevation_loss_m),
        "training": LegTrainingMetrics(
            aerobic_effect=scaled(row.aerobic_training_effect),
            anaerobic_effect=scaled(row.anaerobic_training_effect),
            effect_label=row.training_effect_label,
            load=scaled(row.training_load),
            normalized_power_w=scaled(row.normalized_power_w, digits=0),
        ),
        "intensity_minutes": IntensityMinutes(
            moderate=row.moderate_intensity_min,
            vigorous=row.vigorous_intensity_min,
        ),
    }


def activity_training_values(row: Any) -> dict[str, Any]:
    """Convert only the selected stored training columns into the additive list fields.

    Args:
        row: Selected activity columns, with nullable metrics in provider-native units.

    Returns:
        Unit-converted scalar fields and always-present nested result objects.
    """
    values = common_training_values(row)
    return {
        **values,
        "training": TrainingMetrics(
            **values["training"].model_dump(),
            stress_score=scaled(row.training_stress_score),
            intensity_factor=scaled(row.intensity_factor, digits=2),
        ),
        "hr_zone_min": HeartRateZoneMinutes(
            **{
                f"zone_{zone}": scaled(getattr(row, f"hr_zone_{zone}_s"), 60)
                for zone in range(1, 6)
            }
        ),
    }


def activity_summary_values(row: Any) -> dict[str, Any]:
    """Convert the identity, local time and base metrics shared by summaries and legs.

    Args:
        row: Selected normalized columns, with nullable metrics in stored units.

    Returns:
        Common result fields using kilometres, minutes, beats/minute and kilocalories.
    """
    return dict(
        activity_id=row.activity_id,
        start_time_local=row.start_time_local,
        sport=row.activity_type,
        distance_km=scaled(row.distance_m, 1000, 2),
        duration_min=scaled(row.duration_s, 60),
        avg_hr_bpm=scaled(row.avg_hr_bpm),
        calories_kcal=row.calories_kcal,
    )


def list_activities(
    engine: Engine, start: date, end: date, sport: str | None, limit: int, names: bool
) -> ActivityList:
    """Read an ordered activity page without selecting names unless explicitly enabled.

    Args:
        engine: Read-only ledger engine.
        start: First local date included in the page's filter.
        end: Last local date included in the page's filter.
        sport: Exact activity type filter, or None for every sport.
        limit: Maximum number of activities to return.
        names: Whether privacy-sensitive activity names may be selected and returned.

    Returns:
        Activity data, total matching count, truncation flag and clipped coverage.
    """
    predicates = activity_filter(start, end)
    if sport is not None:
        predicates += (Activity.activity_type == sport,)
    columns = [
        Activity.activity_id,
        Activity.start_time_local,
        Activity.activity_type,
        Activity.distance_m,
        Activity.duration_s,
        Activity.avg_hr_bpm,
        Activity.calories_kcal,
        Activity.moving_duration_s,
        Activity.avg_speed_mps,
        Activity.max_hr_bpm,
        Activity.elevation_gain_m,
        Activity.elevation_loss_m,
        Activity.aerobic_training_effect,
        Activity.anaerobic_training_effect,
        Activity.training_effect_label,
        Activity.training_load,
        Activity.training_stress_score,
        Activity.intensity_factor,
        Activity.normalized_power_w,
        Activity.moderate_intensity_min,
        Activity.vigorous_intensity_min,
        Activity.hr_zone_1_s,
        Activity.hr_zone_2_s,
        Activity.hr_zone_3_s,
        Activity.hr_zone_4_s,
        Activity.hr_zone_5_s,
    ]
    if names:
        columns.append(Activity.activity_name)
    activities: list[NamedActivityData | ActivityData] = []
    with engine.connect() as connection:
        total = connection.execute(
            select(func.count()).select_from(Activity).where(*predicates)
        ).scalar_one()
        rows = connection.execute(
            select(*columns)
            .where(*predicates)
            .order_by(Activity.start_time_local, Activity.activity_id)
            .limit(limit)
        ).all()
        legs: dict[int, list[LegData]] = defaultdict(list)
        if rows:
            leg_rows = connection.execute(
                select(
                    ActivityLeg.parent_activity_id,
                    ActivityLeg.activity_id,
                    ActivityLeg.leg_index,
                    ActivityLeg.start_time_local,
                    ActivityLeg.activity_type,
                    ActivityLeg.distance_m,
                    ActivityLeg.duration_s,
                    ActivityLeg.avg_hr_bpm,
                    ActivityLeg.calories_kcal,
                    ActivityLeg.moving_duration_s,
                    ActivityLeg.avg_speed_mps,
                    ActivityLeg.max_hr_bpm,
                    ActivityLeg.elevation_gain_m,
                    ActivityLeg.elevation_loss_m,
                    ActivityLeg.aerobic_training_effect,
                    ActivityLeg.anaerobic_training_effect,
                    ActivityLeg.training_effect_label,
                    ActivityLeg.training_load,
                    ActivityLeg.normalized_power_w,
                    ActivityLeg.moderate_intensity_min,
                    ActivityLeg.vigorous_intensity_min,
                )
                .where(
                    ActivityLeg.provider == PROVIDER,
                    ActivityLeg.parent_activity_id.in_([row.activity_id for row in rows]),
                )
                .order_by(ActivityLeg.parent_activity_id, ActivityLeg.leg_index)
            )
            for leg in leg_rows:
                legs[leg.parent_activity_id].append(
                    LegData(
                        **activity_summary_values(leg),
                        **common_training_values(leg),
                        leg_index=leg.leg_index,
                    )
                )
        for row in rows:
            values = dict(
                **activity_summary_values(row),
                **activity_training_values(row),
                legs=legs[row.activity_id],
            )
            activities.append(
                NamedActivityData(**values, activity_name=row.activity_name)
                if names
                else ActivityData(**values)
            )
    return ActivityList(
        activities=activities,
        total_matching=total,
        truncated=total > len(activities),
        coverage=coverage_result(sync_coverage(engine, ["activities"])["activities"], start, end),
    )


def nullable_sum(rows: Sequence[Any], column: str) -> int | float | None:
    """Sum a selected scalar column while preserving an empty/all-null sum as None.

    Args:
        rows: Activity rows already restricted to the requested provider/date range.
        column: Name of a selected numeric activity column.

    Returns:
        The unrounded sum of non-null values, including valid zero, or None.
    """
    values = [getattr(row, column) for row in rows if getattr(row, column) is not None]
    return sum(values) if values else None


def aggregate(rows: Sequence[Any]) -> ActivityAggregate:
    """Sum available metrics and compute HR weighted only by eligible durations.

    Args:
        rows: Selected activity rows with existing metrics and the additive plain-sum columns.

    Returns:
        Existing totals/weighted HR, added plain sums, and per-input null counts.
        Rounding follows summation and unit conversion; no effect/IF averages are derived.
    """
    distances = [r.distance_m for r in rows if r.distance_m is not None]
    durations = [r.duration_s for r in rows if r.duration_s is not None]
    calories = [r.calories_kcal for r in rows if r.calories_kcal is not None]
    eligible = [r for r in rows if r.avg_hr_bpm is not None and r.duration_s is not None]
    weight = sum(r.duration_s for r in eligible)
    return ActivityAggregate(
        activity_count=len(rows),
        distance_km=scaled(sum(distances), 1000, 2) if distances else None,
        duration_h=scaled(sum(durations), 3600, 2) if durations else None,
        calories_kcal=sum(calories) if calories else None,
        avg_hr_bpm=scaled(sum(r.avg_hr_bpm * r.duration_s for r in eligible) / weight)
        if weight
        else None,
        training_load=scaled(nullable_sum(rows, "training_load")),
        training_stress_score=scaled(nullable_sum(rows, "training_stress_score")),
        moving_duration_h=scaled(nullable_sum(rows, "moving_duration_s"), 3600, 2),
        elevation_gain_m=scaled(nullable_sum(rows, "elevation_gain_m")),
        intensity_minutes=IntensityMinutes.model_validate(
            {
                "moderate": nullable_sum(rows, "moderate_intensity_min"),
                "vigorous": nullable_sum(rows, "vigorous_intensity_min"),
            }
        ),
        hr_zone_h=HeartRateZoneHours(
            **{
                f"zone_{zone}": scaled(nullable_sum(rows, f"hr_zone_{zone}_s"), 3600, 2)
                for zone in range(1, 6)
            }
        ),
        missing=MissingValues(
            distance=len(rows) - len(distances),
            duration=len(rows) - len(durations),
            calories=len(rows) - len(calories),
            avg_hr=sum(r.avg_hr_bpm is None for r in rows),
            training_load=sum(r.training_load is None for r in rows),
            training_stress_score=sum(r.training_stress_score is None for r in rows),
            moving_duration=sum(r.moving_duration_s is None for r in rows),
            elevation_gain=sum(r.elevation_gain_m is None for r in rows),
            hr_zones=sum(
                all(getattr(r, f"hr_zone_{zone}_s") is None for zone in range(1, 6)) for r in rows
            ),
        ),
    )


def activity_totals(
    engine: Engine, start: date, end: date, group_by: Literal["week", "month", "sport"]
) -> ActivityTotals:
    """Group selected activities by ISO week, calendar month or exact sport.

    Args:
        engine: Read-only ledger engine.
        start: First included local date.
        end: Last included local date.
        group_by: Grouping dimension; unknown sports use the key unknown.

    Returns:
        Stable groups, overall totals and clipped download coverage.
    """
    with engine.connect() as connection:
        rows = list(
            connection.execute(
                select(
                    Activity.start_time_local,
                    Activity.activity_type,
                    Activity.distance_m,
                    Activity.duration_s,
                    Activity.avg_hr_bpm,
                    Activity.calories_kcal,
                    Activity.training_load,
                    Activity.training_stress_score,
                    Activity.moving_duration_s,
                    Activity.elevation_gain_m,
                    Activity.moderate_intensity_min,
                    Activity.vigorous_intensity_min,
                    Activity.hr_zone_1_s,
                    Activity.hr_zone_2_s,
                    Activity.hr_zone_3_s,
                    Activity.hr_zone_4_s,
                    Activity.hr_zone_5_s,
                )
                .where(*activity_filter(start, end))
                .order_by(Activity.start_time_local, Activity.activity_id)
            )
        )
    grouped: dict[str, list[Any]] = defaultdict(list)
    mondays: dict[str, date] = {}
    for row in rows:
        day = row.start_time_local.date()
        if group_by == "week":
            year, week, _ = day.isocalendar()
            key = f"{year:04d}-W{week:02d}"
            mondays[key] = day - timedelta(days=day.weekday())
        elif group_by == "month":
            key = f"{day.year:04d}-{day.month:02d}"
        else:
            key = row.activity_type if row.activity_type is not None else "unknown"
        grouped[key].append(row)
    return ActivityTotals(
        group_by=group_by,
        groups=[
            ActivityGroup(key=key, monday=mondays.get(key), **aggregate(grouped[key]).model_dump())
            for key in sorted(grouped)
        ],
        overall=aggregate(rows),
        coverage=coverage_result(sync_coverage(engine, ["activities"])["activities"], start, end),
    )


def daily_wellness(engine: Engine, start: date, end: date, areas: list[str]) -> DailyWellness:
    """Read selected wellness metrics and distinguish uncovered dates from missing rows.

    Args:
        engine: Read-only ledger engine.
        start: First requested calendar day.
        end: Last requested calendar day, at most 91 days after start.
        areas: Validated wellness area names in canonical order.

    Returns:
        One dated row for every requested day, with each area's state and nullable metrics.
    """
    coverage = sync_coverage(engine, areas)
    records: dict[str, dict[date, SleepValues | HRVValues | StressValues]] = {}
    with engine.connect() as connection:
        for area in areas:
            model: Any = {"sleep": DailySleep, "hrv": DailyHRV, "stress": DailyStress}[area]
            fields = {
                "sleep": [
                    "sleep_time_s",
                    "deep_sleep_s",
                    "light_sleep_s",
                    "rem_sleep_s",
                    "awake_s",
                    "nap_time_s",
                    "sleep_score",
                    "avg_hr_bpm",
                    "avg_respiration_brpm",
                ],
                "hrv": [
                    "last_night_avg_ms",
                    "weekly_avg_ms",
                    "last_night_5min_high_ms",
                    "baseline_balanced_low_ms",
                    "baseline_balanced_upper_ms",
                    "hrv_status",
                ],
                "stress": [
                    "overall_stress_level",
                    "rest_s",
                    "low_stress_s",
                    "medium_stress_s",
                    "high_stress_s",
                ],
            }[area]
            rows = connection.execute(
                select(model.calendar_date, *(getattr(model, f) for f in fields))
                .where(
                    model.provider == PROVIDER,
                    model.calendar_date >= start,
                    model.calendar_date <= end,
                )
                .order_by(model.calendar_date)
            )
            records[area] = {}
            for row in rows:
                if area == "sleep":
                    metrics: SleepValues | HRVValues | StressValues = SleepValues(
                        sleep_min=scaled(row.sleep_time_s, 60),
                        deep_min=scaled(row.deep_sleep_s, 60),
                        light_min=scaled(row.light_sleep_s, 60),
                        rem_min=scaled(row.rem_sleep_s, 60),
                        awake_min=scaled(row.awake_s, 60),
                        nap_min=scaled(row.nap_time_s, 60),
                        sleep_score=scaled(row.sleep_score),
                        avg_hr_bpm=scaled(row.avg_hr_bpm),
                        avg_respiration_brpm=scaled(row.avg_respiration_brpm),
                    )
                elif area == "hrv":
                    metrics = HRVValues(
                        last_night_avg_ms=scaled(row.last_night_avg_ms),
                        weekly_avg_ms=scaled(row.weekly_avg_ms),
                        last_night_5min_high_ms=scaled(row.last_night_5min_high_ms),
                        baseline_low_ms=scaled(row.baseline_balanced_low_ms),
                        baseline_upper_ms=scaled(row.baseline_balanced_upper_ms),
                        status=row.hrv_status,
                    )
                else:
                    metrics = StressValues(
                        overall_stress_level=row.overall_stress_level,
                        rest_min=scaled(row.rest_s, 60),
                        low_min=scaled(row.low_stress_s, 60),
                        medium_min=scaled(row.medium_stress_s, 60),
                        high_min=scaled(row.high_stress_s, 60),
                    )
                records[area][row.calendar_date] = metrics
    days: list[WellnessDay] = []
    for ordinal in range(start.toordinal(), end.toordinal() + 1):
        day = date.fromordinal(ordinal)
        values: dict[str, WellnessArea] = {}
        for area in areas:
            if not any(a <= day <= b for a, b in coverage[area].intervals):
                values[area] = WellnessArea(state="not_downloaded")
            elif day not in records[area]:
                values[area] = WellnessArea(state="no_record")
            else:
                values[area] = WellnessArea(state="ok", values=records[area][day])
        days.append(WellnessDay(calendar_date=day, areas=values))
    return DailyWellness(days=days)
