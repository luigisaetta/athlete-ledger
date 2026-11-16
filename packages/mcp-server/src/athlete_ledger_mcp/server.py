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
from typing import Any, Literal

import oracledb
from athlete_ledger.errors import database_error_code
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from athlete_ledger_mcp import __version__, queries
from athlete_ledger_mcp.results import ActivityList, ActivityTotals, DailyWellness, LedgerStatus


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

    @server.tool(title="Ledger availability", annotations=annotations, structured_output=True)
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

    @server.tool(title="Activity summaries", annotations=annotations, structured_output=True)
    def list_activities(
        start: str, end: str, sport: str | None = None, limit: int = 50
    ) -> ActivityList:
        """List activities in inclusive YYYY-MM-DD local dates, ordered by start then ID.

        Start times are provider-local wall time without a timezone. Distances are km
        (2 decimals), durations minutes (1), average HR beats/minute (1), calories kcal.
        Null means unavailable, zero is valid. Names are absent unless explicitly enabled.
        Coverage is clipped to the requested range and includes uncovered request edges.

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

    @server.tool(title="Activity totals", annotations=annotations, structured_output=True)
    def activity_totals(
        start: str, end: str, group_by: Literal["week", "month", "sport"]
    ) -> ActivityTotals:
        """Return grouped and overall totals for inclusive ISO local dates.

        Weeks use ISO year/week keys and a Monday date, months use YYYY-MM, unknown
        sports use unknown. Sums skip nulls (all-null/empty sums are null); missing counts
        report unavailable distance, duration, calories and HR separately. HR is weighted
        by duration only over activities having both values; zero total weight gives null.
        Distance is km (2 decimals), duration hours (2), HR beats/minute (1), calories kcal.

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

    @server.tool(title="Daily wellness summaries", annotations=annotations, structured_output=True)
    def daily_wellness(start: str, end: str, areas: list[str] | None = None) -> DailyWellness:
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
