"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Register exactly four read-only data tools with the official MCP server.
"""  # noqa: D205, D212

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
from textwrap import dedent
from typing import Annotated, Any, Literal

import oracledb
from athlete_ledger.errors import database_error_code
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field, WithJsonSchema
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from athlete_ledger_mcp import __version__, queries
from athlete_ledger_mcp.results import ActivityList, ActivityTotals, DailyWellness, LedgerStatus

# Keep date errors in parse_range, which never echoes rejected input values.
ISODate = Annotated[str, WithJsonSchema({"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"})]


@contextmanager
def database_errors() -> Iterator[None]:
    """Convert database exceptions into tool errors without SQL or bound values.

    Yields:
        Control to a read-only query operation.

    Raises:
        ToolError: If a database operation fails, containing only a fixed message
            and an optional recognized ORA/DPY code.
    """
    try:
        yield
    except (SQLAlchemyError, oracledb.Error, OSError) as error:
        code = database_error_code(error)
        raise ToolError(f"Database operation failed{': ' + code if code else ''}.") from None


def create_server(
    engine: Engine, *, include_activity_names: bool = False, today: Callable[[], date] = date.today
) -> MCPServer[Any]:
    """Register the four data-only tools on an unstarted stdio-capable server.

    Args:
        engine: Reader-only ledger engine; the caller owns its lifecycle.
        include_activity_names: Explicit opt-in to returning potentially sensitive names.
        today: Local-date supplier, injectable for deterministic tests.

    Returns:
        Official MCPServer with typed structured outputs and read-only annotations.
    """
    # Dependency diagnostics can include SQL, payloads or tracebacks even at WARNING.
    for namespace in ("mcp", "sqlalchemy", "oracledb"):
        logger = logging.getLogger(namespace)
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False
    server: MCPServer[Any] = MCPServer("athlete-ledger", version=__version__, log_level="WARNING")
    annotations = ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, destructive_hint=False, open_world_hint=False
    )

    @server.tool(
        title="Ledger availability",
        description=dedent("""\
        Get the server's local today, downloaded intervals and gaps, sync progress,
        first/last record dates per dataset, and stored sports with activity counts.
        Use this before choosing dates or a sport filter for other tools. Download
        coverage includes successfully fetched empty days; record absence does not
        mean a missing download. Activity dates are provider-local naive calendar
        dates. Null progress or bounds mean none are recorded. Ordering is stable.
        """).strip(),
        annotations=annotations,
        structured_output=True,
    )
    def ledger_status() -> LedgerStatus:
        """Get local today, download intervals/gaps, progress, record dates and sport counts.

        Download coverage includes successfully fetched empty days; a missing record
        does not imply a missing download. Activity dates use provider-local naive time.
        Null progress or bounds mean none are recorded. Dataset and sport order is stable.

        Returns:
            Per-dataset availability and the stored sports with activity counts.

        Raises:
            ToolError: If the database read fails, without SQL or private diagnostics.
        """
        with database_errors():
            return queries.ledger_status(engine, today())

    @server.tool(
        title="Activity summaries",
        description=dedent("""\
        List activities by inclusive ISO local dates, ordered by local start then ID.
        Sport values come from ledger_status; null selects all. Limit 1–200 (default
        50), with total_matching, truncated and clipped coverage. Times are local wall
        time without a timezone. distance_km is km (2 decimals), duration_min is minutes (1),
        avg_hr_bpm is beats/minute (1), calories_kcal is kcal. moving_duration_min is
        minutes (1), avg_speed_kmh
        is km/h (m/s × 3.6, 1), max_hr_bpm is beats/minute (1), and
        elevation_gain_m/elevation_loss_m are metres (1).
        training contains provider-owned per-activity aerobic_effect/anaerobic_effect
        (0–5, 1), effect_label as stored, load (provider units, 1), stress_score (TSS, 1),
        intensity_factor (ratio, 2) and normalized_power_w (watts, 0). TSS/IF/normalized
        power are mostly available for power-meter activities. intensity_minutes has
        separate moderate/vigorous integers; hr_zone_min has zone_1–zone_5 minutes (1).
        No scores or label interpretations are derived. Null means unavailable; zero
        is valid; nested objects are always present. Names require server opt-in.
        """).strip(),
        annotations=annotations,
        structured_output=True,
    )
    def list_activities(
        start: Annotated[
            ISODate,
            Field(
                description=(
                    "Inclusive first provider-local calendar date, YYYY-MM-DD; must not exceed end."
                )
            ),
        ],
        end: Annotated[
            ISODate,
            Field(
                description=(
                    "Inclusive last provider-local date, YYYY-MM-DD; includes the entire day."
                )
            ),
        ],
        sport: Annotated[
            str | None,
            Field(
                description=(
                    "Exact sport value from ledger_status.sports; null includes all sports."
                )
            ),
        ] = None,
        limit: Annotated[
            int,
            Field(
                strict=True,
                ge=1,
                le=200,
                description=(
                    "Maximum number of activity summaries, 1–200; default 50. "
                    "total_matching and truncated indicate omitted matches; no offset pagination."
                ),
            ),
        ] = 50,
    ) -> ActivityList:
        """List activities in inclusive YYYY-MM-DD local dates, ordered by start then ID.

        Start times are provider-local wall time without a timezone. Distances are km
        (2 decimals), durations minutes (1), average HR beats/minute (1), calories kcal.
        Null means unavailable, zero is valid. Names are absent unless explicitly enabled.
        Coverage is clipped to the requested range and includes uncovered request edges.
        New provider training fields use the explicit nested units in the output schema;
        missing metrics leave null members inside always-present nested objects.

        Args:
            start: Inclusive first ISO calendar date, YYYY-MM-DD.
            end: Inclusive last ISO calendar date, YYYY-MM-DD.
            sport: Exact activity type, or null for all sports.
            limit: Maximum returned rows, 1–200; default 50. No offset pagination.

        Returns:
            Ordered summaries, total_matching, truncated and downloaded coverage.

        Raises:
            ToolError: If dates/limit are invalid or the database read fails.
        """
        first, last = queries.parse_range(start, end)
        if isinstance(limit, bool) or not 1 <= limit <= 200:
            raise ToolError("limit must be between 1 and 200.")
        with database_errors():
            return queries.list_activities(
                engine, first, last, sport, limit, include_activity_names
            )

    @server.tool(
        title="Activity totals",
        description=dedent("""\
        Return grouped/overall activity totals for inclusive ISO local dates. Group by
        ISO week (YYYY-Www and Monday), month (YYYY-MM), or sport (unknown for null),
        in key order. Return activity_count, distance_km (km, 2), duration_h (hours, 2),
        calories_kcal (kcal) and duration-weighted avg_hr_bpm (beats/minute, 1).
        Training and intensity values are plain sums only:
        training_load (provider units, 1), training_stress_score (TSS points, 1),
        moving_duration_h (hours, 2), elevation_gain_m (metres, 1), intensity_minutes with
        separate moderate/vigorous integer sums, and hr_zone_h zone_1–zone_5 hours (2).
        Provider-owned per-activity load is summed without interpretation; TSS is
        mostly available for power-meter activities. No effect/IF averages or derived
        scores. Nulls are skipped; empty/all-null sums are null. Zero is valid and
        nested objects are always present. Missing counts include load, TSS, moving
        duration, elevation gain and all-five-null HR zones. Return clipped coverage.
        """).strip(),
        annotations=annotations,
        structured_output=True,
    )
    def activity_totals(
        start: Annotated[
            ISODate,
            Field(
                description=(
                    "Inclusive first provider-local calendar date, YYYY-MM-DD; must not exceed end."
                )
            ),
        ],
        end: Annotated[
            ISODate,
            Field(
                description=(
                    "Inclusive last provider-local date, YYYY-MM-DD; includes the entire day."
                )
            ),
        ],
        group_by: Annotated[
            Literal["week", "month", "sport"],
            Field(
                description=(
                    "Grouping: week uses ISO year/week and Monday date; month uses YYYY-MM; "
                    "sport uses the stored type, or unknown for null. Totals use km, hours, kcal "
                    "and duration-weighted HR in beats/minute."
                )
            ),
        ],
    ) -> ActivityTotals:
        """Return grouped and overall totals for inclusive ISO local dates.

        Weeks use ISO year/week keys and a Monday date, months use YYYY-MM, unknown
        sports use unknown. Sums skip nulls (all-null/empty sums are null); missing counts
        report unavailable distance, duration, calories and HR separately. HR is weighted
        by duration only over activities having both values; zero total weight gives null.
        Distance is km (2 decimals), duration hours (2), HR beats/minute (1), calories kcal.
        Training metrics are plain sums with missing counts, never effects/IF averages.

        Args:
            start: Inclusive first ISO calendar date, YYYY-MM-DD.
            end: Inclusive last ISO calendar date, YYYY-MM-DD.
            group_by: week, month or sport; groups have stable key ordering.

        Returns:
            Groups, overall totals and clipped requested-range download coverage.

        Raises:
            ToolError: If dates are invalid or the database read fails.
        """
        first, last = queries.parse_range(start, end)
        with database_errors():
            return queries.activity_totals(engine, first, last, group_by)

    @server.tool(
        title="Daily wellness summaries",
        description=dedent("""\
        Return every calendar day in an inclusive ISO range of at most 92 days,
        including days with no records. Select sleep, hrv and/or stress; null means
        all and [] means none. Each area's state is not_downloaded (outside download
        coverage), no_record (downloaded but no row), or ok (row exists; metrics may
        be null). Sleep/stress durations are minutes (1 decimal), HR beats/minute,
        respiration breaths/minute, and HRV milliseconds; averages have 1 decimal.
        Stress level is 0–100. Null means unavailable; zero is valid. Dates and areas
        have stable ordering. Return data only, without inferred analysis.
        """).strip(),
        annotations=annotations,
        structured_output=True,
    )
    def daily_wellness(
        start: Annotated[
            ISODate,
            Field(
                description=(
                    "Inclusive first calendar date, YYYY-MM-DD; must not exceed end. "
                    "The inclusive range may contain at most 92 days."
                )
            ),
        ],
        end: Annotated[
            ISODate,
            Field(
                description=(
                    "Inclusive last calendar date, YYYY-MM-DD; at most 91 days after start."
                )
            ),
        ],
        areas: Annotated[
            list[Literal["sleep", "hrv", "stress"]] | None,
            Field(
                description=(
                    "Select sleep, hrv and/or stress. Null selects all; [] selects none. "
                    "Durations are minutes, HR beats/minute, respiration breaths/minute and HRV "
                    "milliseconds; stress level is 0–100."
                )
            ),
        ] = None,
    ) -> DailyWellness:
        """Return every day in an inclusive ISO range of at most 92 days.

        Each selected area has state not_downloaded (outside download coverage),
        no_record (downloaded but no row), or ok (row exists; metrics may be null).
        Sleep/stress durations are minutes (1 decimal); sleep HR is beats/minute,
        respiration breaths/minute, HRV milliseconds, all averages 1 decimal. Stress
        level is 0–100. Null means missing, zero is valid; no analysis is inferred.

        Args:
            start: Inclusive first ISO calendar date, YYYY-MM-DD.
            end: Inclusive last ISO calendar date; at most 92 days including endpoints.
            areas: Subset of sleep, hrv, stress; null selects all and [] selects none.

        Returns:
            Date-ordered rows, including days without records, in canonical area order.

        Raises:
            ToolError: If dates, range length or area names are invalid, or a read fails.
        """
        first, last = queries.parse_range(start, end, maximum=92)
        selected = list(queries.WELLNESS) if areas is None else areas
        if any(area not in queries.WELLNESS for area in selected):
            raise ToolError("areas must contain only sleep, hrv or stress.")
        ordered = [area for area in queries.WELLNESS if area in selected]
        with database_errors():
            return queries.daily_wellness(engine, first, last, ordered)

    return server
