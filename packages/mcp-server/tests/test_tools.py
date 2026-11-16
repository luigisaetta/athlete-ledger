"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Exercise all four registered tools through the modern SDK client.
"""  # noqa: D205, D212

import json

import pytest
from athlete_ledger_mcp.server import create_server
from jsonschema import validate
from mcp.client import Client
from sqlalchemy import event
from sqlalchemy.exc import OperationalError


@pytest.mark.anyio
async def test_four_tools_annotations_and_output_schemas(server) -> None:
    """Advertise exactly four titled read-only tools with structured output schemas."""
    async with Client(server, mode="2026-07-28") as client:
        assert client.protocol_version == "2026-07-28"
        tools = (await client.list_tools()).tools
        assert {tool.name for tool in tools} == {
            "ledger_status",
            "list_activities",
            "activity_totals",
            "daily_wellness",
        }
        for tool in tools:
            assert tool.title
            assert tool.output_schema and tool.output_schema["type"] == "object"
            hints = tool.annotations.model_dump(by_alias=True)
            assert hints["readOnlyHint"] is True
            assert hints["idempotentHint"] is True
            assert hints["destructiveHint"] is False
            assert hints["openWorldHint"] is False
            arguments = (
                {} if tool.name == "ledger_status" else {"start": "2020-12-31", "end": "2021-01-04"}
            )
            if tool.name == "activity_totals":
                arguments["group_by"] = "week"
            result = await client.call_tool(tool.name, arguments)
            assert not result.is_error
            validate(result.structured_content, tool.output_schema)


@pytest.mark.anyio
async def test_status_reports_coverage_progress_bounds_and_sports(server) -> None:
    """Return stable dataset metadata without confusing coverage gaps with record bounds."""
    async with Client(server, mode="2026-07-28") as client:
        data = (await client.call_tool("ledger_status")).structured_content
    assert data["local_date"] == "2021-01-06"
    assert [entry["dataset"] for entry in data["datasets"]] == [
        "activities",
        "sleep",
        "hrv",
        "stress",
    ]
    activities = data["datasets"][0]
    assert activities["coverage"]["gaps"] == [
        {"start": "2021-01-03", "end": "2021-01-03", "days": 1}
    ]
    assert activities["last_synced_date"] == "2021-01-05"
    assert activities["last_run_at"] == "2021-01-06T00:00:00Z"
    assert activities["last_status"] == "ok"
    assert activities["first_record_date"] == "2020-12-31"
    assert activities["last_record_date"] == "2021-01-04"
    assert data["datasets"][1]["first_record_date"] == "2020-12-31"
    assert data["sports"] == [
        {"sport": "cycling", "activity_count": 1},
        {"sport": "running", "activity_count": 2},
        {"sport": "walking", "activity_count": 1},
        {"sport": None, "activity_count": 1},
    ]


@pytest.mark.anyio
async def test_activity_order_limit_units_and_coverage(server) -> None:
    """Respect inclusive local dates, ID tie-breaking, truncation and unit conversions."""
    async with Client(server, mode="2026-07-28") as client:
        data = (
            await client.call_tool(
                "list_activities",
                {
                    "start": "2020-12-31",
                    "end": "2021-01-04",
                    "limit": 2,
                },
            )
        ).structured_content
    assert data["total_matching"] == 4 and data["truncated"] is True
    first, second = data["activities"]
    assert [first["activity_id"], second["activity_id"]] == [10, 20]
    assert first == {
        "activity_id": 10,
        "start_time_local": "2020-12-31T08:00:00",
        "sport": "running",
        "distance_km": 2,
        "duration_min": 3,
        "avg_hr_bpm": 140,
        "calories_kcal": None,
    }
    assert data["coverage"]["intervals"] == [
        {"start": "2020-12-31", "end": "2021-01-02", "days": 3},
        {"start": "2021-01-04", "end": "2021-01-04", "days": 1},
    ]


@pytest.mark.anyio
async def test_activity_filter_names_and_missing_values(reader, server) -> None:
    """Omit names entirely by default and include them only after server-side opt-in."""
    args = {"start": "2021-01-01", "end": "2021-01-05", "sport": "cycling"}
    async with Client(server, mode="2026-07-28") as client:
        data = (await client.call_tool("list_activities", args)).structured_content
    assert data["total_matching"] == 1 and data["truncated"] is False
    assert data["activities"][0]["distance_km"] is None
    assert "activity_name" not in data["activities"][0]
    named = create_server(reader, include_activity_names=True)
    async with Client(named, mode="2026-07-28") as client:
        result = await client.call_tool("list_activities", args)
        data = result.structured_content
        schema = next(
            tool.output_schema
            for tool in (await client.list_tools()).tools
            if tool.name == "list_activities"
        )
        validate(data, schema)
    assert data["activities"][0]["activity_name"] == "SYNTHETIC-PRIVATE-ACTIVITY"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("group_by", "keys"),
    [
        ("week", ["2020-W53", "2021-W01"]),
        ("month", ["2020-12", "2021-01"]),
        ("sport", ["cycling", "running", "unknown"]),
    ],
)
async def test_totals_year_boundary_null_counts_and_weighted_hr(server, group_by, keys) -> None:
    """Use ISO week years, stable keys, null-aware totals and duration-weighted HR."""
    async with Client(server, mode="2026-07-28") as client:
        data = (
            await client.call_tool(
                "activity_totals",
                {
                    "start": "2020-12-31",
                    "end": "2021-01-04",
                    "group_by": group_by,
                },
            )
        ).structured_content
    assert [group["key"] for group in data["groups"]] == keys
    assert data["overall"] == {
        "activity_count": 4,
        "distance_km": 3,
        "duration_h": 0.1,
        "calories_kcal": 40,
        "avg_hr_bpm": 130,
        "missing": {"distance": 1, "duration": 0, "calories": 1, "avg_hr": 1},
    }
    if group_by == "week":
        assert [g["monday"] for g in data["groups"]] == ["2020-12-28", "2021-01-04"]
        assert data["groups"][1]["avg_hr_bpm"] is None
        assert data["groups"][1]["distance_km"] == 0
    assert data["coverage"]["gaps"][0]["start"] == "2021-01-03"


@pytest.mark.anyio
async def test_daily_wellness_three_states_nulls_zero_and_units(server) -> None:
    """Distinguish no download from a downloaded empty day, keeping all requested dates."""
    async with Client(server, mode="2026-07-28") as client:
        data = (
            await client.call_tool(
                "daily_wellness",
                {
                    "start": "2020-12-31",
                    "end": "2021-01-04",
                },
            )
        ).structured_content
    assert len(data["days"]) == 5
    days = {row["calendar_date"]: row["areas"] for row in data["days"]}
    assert days["2020-12-31"]["sleep"] == {
        "state": "ok",
        "values": {
            "sleep_min": 60,
            "deep_min": 0,
            "light_min": None,
            "rem_min": 2,
            "awake_min": 1,
            "nap_min": None,
            "sleep_score": 89.3,
            "avg_hr_bpm": 50.3,
            "avg_respiration_brpm": 15.3,
        },
    }
    assert days["2021-01-01"]["sleep"] == {"state": "no_record", "values": None}
    assert all(area["state"] == "not_downloaded" for area in days["2021-01-03"].values())
    assert days["2021-01-01"]["hrv"]["values"]["last_night_avg_ms"] == 50.6
    assert days["2021-01-01"]["hrv"]["values"]["weekly_avg_ms"] is None
    assert days["2021-01-04"]["stress"]["values"]["overall_stress_level"] == 0
    assert days["2021-01-04"]["stress"]["values"]["rest_min"] == 1


@pytest.mark.anyio
@pytest.mark.parametrize("areas", [[], ["stress"], ["stress", "sleep", "stress"]])
async def test_wellness_area_subsets_are_stable(server, areas) -> None:
    """Respect empty subsets and deduplicate/reorder selected areas canonically."""
    async with Client(server, mode="2026-07-28") as client:
        data = (
            await client.call_tool(
                "daily_wellness",
                {
                    "start": "2020-12-31",
                    "end": "2020-12-31",
                    "areas": areas,
                },
            )
        ).structured_content
    assert list(data["days"][0]["areas"]) == [a for a in ["sleep", "hrv", "stress"] if a in areas]


@pytest.mark.anyio
async def test_empty_activity_range_has_null_sums_and_uncovered_edges(server) -> None:
    """Return zero matches and null sums while reporting missing request boundaries."""
    async with Client(server, mode="2026-07-28") as client:
        data = (
            await client.call_tool(
                "activity_totals",
                {
                    "start": "2019-01-01",
                    "end": "2019-01-02",
                    "group_by": "sport",
                },
            )
        ).structured_content
    assert data["groups"] == []
    assert data["overall"]["activity_count"] == 0
    assert data["overall"]["distance_km"] is None
    assert data["overall"]["avg_hr_bpm"] is None
    assert data["coverage"] == {
        "intervals": [],
        "gaps": [{"start": "2019-01-01", "end": "2019-01-02", "days": 2}],
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool", "extra"),
    [
        ("list_activities", {"start": "2021-02-30"}),
        ("list_activities", {"start": "20210101"}),
        ("list_activities", {"end": "2020-12-01"}),
        ("list_activities", {"limit": 0}),
        ("list_activities", {"limit": 201}),
        ("list_activities", {"limit": True}),
        ("list_activities", {"limit": 1.5}),
        ("activity_totals", {"group_by": "day"}),
        ("daily_wellness", {"areas": ["SYNTHETIC-PRIVATE-INVALID"]}),
        ("daily_wellness", {"start": "2021-01-01", "end": "2021-04-03"}),
    ],
)
async def test_invalid_inputs_return_tool_errors_without_traces(
    server, tool, extra, caplog
) -> None:
    """Reject malformed dates, reversed ranges, bad limits/areas and overlong wellness ranges."""
    args = {"start": "2020-12-31", "end": "2021-01-04"}
    if tool == "activity_totals":
        args["group_by"] = "week"
    args.update(extra)
    async with Client(server, mode="2026-07-28") as client:
        result = await client.call_tool(tool, args)
    assert result.is_error
    text = " ".join(item.text for item in result.content)
    assert "Traceback" not in text and "SELECT " not in text
    assert "SYNTHETIC-PRIVATE" not in caplog.text


@pytest.mark.anyio
async def test_92_day_boundary_and_maximum_limit_are_accepted(server) -> None:
    """Accept the inclusive 92-day wellness boundary and activity limit of 200."""
    async with Client(server, mode="2026-07-28") as client:
        result = await client.call_tool(
            "daily_wellness", {"start": "2021-01-01", "end": "2021-04-02"}
        )
        assert not result.is_error and len(result.structured_content["days"]) == 92
        result = await client.call_tool(
            "list_activities", {"start": "2020-12-31", "end": "2021-01-05", "limit": 200}
        )
        assert not result.is_error


@pytest.mark.anyio
async def test_queries_never_select_private_raw_columns_or_disabled_names(
    reader, server, caplog
) -> None:
    """Capture all tool SQL and logs to verify narrow projections and private diagnostics."""
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        """Record SQL text for read-only and projection assertions.

        Args:
            connection: Read-only SQLAlchemy connection.
            cursor: SQLite cursor executing a SELECT.
            statement: SQL text without synthetic bound parameter values.
            parameters: Bound provider/date filter values.
            context: SQLAlchemy execution metadata.
            executemany: Whether the cursor uses multiple parameter sets.
        """
        statements.append(statement)

    event.listen(reader, "before_cursor_execute", capture)
    async with Client(server, mode="2026-07-28") as client:
        for tool in ["ledger_status", "list_activities", "activity_totals", "daily_wellness"]:
            args = {} if tool == "ledger_status" else {"start": "2020-12-31", "end": "2021-01-04"}
            if tool == "activity_totals":
                args["group_by"] = "sport"
            assert not (await client.call_tool(tool, args)).is_error
    assert statements and all(sql.startswith("SELECT ") for sql in statements)
    assert all(
        "payload" not in sql and "params" not in sql and "activity_name" not in sql
        for sql in statements
    )
    assert caplog.text == ""


@pytest.mark.anyio
@pytest.mark.parametrize("code", ["ORA-01017", "DPY-6005", None])
async def test_database_errors_do_not_leak_sql_credentials_or_logs(
    reader, server, caplog, monkeypatch, code
) -> None:
    """Return only a recognized driver code when a tool query fails."""

    def fail(*args, **kwargs):
        """Raise a wrapped driver failure containing synthetic private diagnostics.

        Args:
            *args: Ignored connection arguments.
            **kwargs: Ignored connection options.

        Raises:
            OperationalError: Always, with private SQL/parameters that must stay hidden.
        """
        raise OperationalError(
            "SYNTHETIC-PRIVATE-SQL",
            {"private": "SYNTHETIC-PRIVATE-PARAMS"},
            Exception(f"{code or 'driver failure'} SYNTHETIC-PRIVATE-PASSWORD"),
        )

    monkeypatch.setattr(reader, "connect", fail)
    async with Client(server, mode="2026-07-28") as client:
        result = await client.call_tool("ledger_status")
    assert result.is_error
    text = json.dumps([item.model_dump(mode="json") for item in result.content])
    assert "SYNTHETIC-PRIVATE" not in text + caplog.text
    assert "Traceback" not in text
    if code:
        assert code in text


@pytest.mark.anyio
async def test_metric_rounding_and_missing_duration(reader, ledger_file, server) -> None:
    """Round unit conversions and exclude HR with missing duration from weighting."""
    from sqlalchemy import create_engine, text

    writer = create_engine(f"sqlite:///{ledger_file}")
    with writer.begin() as connection:
        connection.execute(
            text(
                "UPDATE activity SET distance_m=1234.56, duration_s=1256.7, "
                "avg_hr_bpm=100.26 WHERE provider='garmin_connect' AND activity_id=20"
            )
        )
        connection.execute(
            text(
                "UPDATE activity SET duration_s=NULL, avg_hr_bpm=200 "
                "WHERE provider='garmin_connect' AND activity_id=10"
            )
        )
    writer.dispose()
    async with Client(server, mode="2026-07-28") as client:
        result = await client.call_tool(
            "list_activities", {"start": "2020-12-31", "end": "2020-12-31"}
        )
        row = next(
            row for row in result.structured_content["activities"] if row["activity_id"] == 20
        )
        assert row["distance_km"] == 1.23
        assert row["duration_min"] == 20.9
        assert row["avg_hr_bpm"] == 100.3
        result = await client.call_tool(
            "activity_totals", {"start": "2020-12-31", "end": "2020-12-31", "group_by": "sport"}
        )
        assert result.structured_content["overall"]["avg_hr_bpm"] == 100.3
        assert result.structured_content["overall"]["duration_h"] == 0.35
        assert result.structured_content["overall"]["missing"]["duration"] == 1
