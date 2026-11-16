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
    legacy = {
        key: first[key]
        for key in (
            "activity_id",
            "start_time_local",
            "sport",
            "distance_km",
            "duration_min",
            "avg_hr_bpm",
            "calories_kcal",
        )
    }
    assert legacy == {
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
    overall = data["overall"]
    legacy = {
        key: overall[key]
        for key in (
            "activity_count",
            "distance_km",
            "duration_h",
            "calories_kcal",
            "avg_hr_bpm",
        )
    }
    legacy["missing"] = {
        key: overall["missing"][key]
        for key in (
            "distance",
            "duration",
            "calories",
            "avg_hr",
        )
    }
    assert legacy == {
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
    for statement in statements:
        sql = " ".join(statement.split())
        if " FROM raw_response " in sql:
            assert sql.split(" FROM ")[0] == (
                "SELECT DISTINCT raw_response.range_start, raw_response.range_end"
            )
        if sql.startswith("SELECT activity.activity_id, "):
            projection = sql.split(" FROM ")[0]
            selected = {column.removeprefix("SELECT ").strip() for column in projection.split(",")}
            assert selected == {
                f"activity.{name}"
                for name in (
                    "activity_id",
                    "start_time_local",
                    "activity_type",
                    "distance_m",
                    "duration_s",
                    "avg_hr_bpm",
                    "calories_kcal",
                    "moving_duration_s",
                    "avg_speed_mps",
                    "max_hr_bpm",
                    "elevation_gain_m",
                    "elevation_loss_m",
                    "aerobic_training_effect",
                    "anaerobic_training_effect",
                    "training_effect_label",
                    "training_load",
                    "training_stress_score",
                    "intensity_factor",
                    "normalized_power_w",
                    "moderate_intensity_min",
                    "vigorous_intensity_min",
                    "hr_zone_1_s",
                    "hr_zone_2_s",
                    "hr_zone_3_s",
                    "hr_zone_4_s",
                    "hr_zone_5_s",
                )
            }
        if sql.startswith("SELECT activity.start_time_local, activity.activity_type, "):
            projection = sql.split(" FROM ")[0]
            selected = {column.removeprefix("SELECT ").strip() for column in projection.split(",")}
            assert selected == {
                f"activity.{name}"
                for name in (
                    "start_time_local",
                    "activity_type",
                    "distance_m",
                    "duration_s",
                    "avg_hr_bpm",
                    "calories_kcal",
                    "training_load",
                    "training_stress_score",
                    "moving_duration_s",
                    "elevation_gain_m",
                    "moderate_intensity_min",
                    "vigorous_intensity_min",
                    "hr_zone_1_s",
                    "hr_zone_2_s",
                    "hr_zone_3_s",
                    "hr_zone_4_s",
                    "hr_zone_5_s",
                )
            }
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


@pytest.mark.anyio
async def test_published_input_schemas_and_model_descriptions(server) -> None:
    """Publish parameter guidance, ISO patterns and wellness enums without developer sections."""
    async with Client(server, mode="2026-07-28") as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    for tool in tools.values():
        assert tool.description and tool.description == tool.description.strip()
        for section in ("Args:", "Returns:", "Raises:"):
            assert section not in tool.description
        assert all(not line.startswith(" ") for line in tool.description.splitlines())
        properties = tool.input_schema["properties"]
        for name, parameter in properties.items():
            assert parameter.get("description"), (tool.name, name)
        for name in ("start", "end"):
            if name in properties:
                assert properties[name]["type"] == "string"
                assert properties[name]["pattern"] == r"^\d{4}-\d{2}-\d{2}$"
                assert "Inclusive" in properties[name]["description"]
    activity = tools["list_activities"].input_schema["properties"]
    assert "ledger_status" in activity["sport"]["description"]
    assert activity["limit"]["minimum"] == 1
    assert activity["limit"]["maximum"] == 200
    assert activity["limit"]["default"] == 50
    areas = tools["daily_wellness"].input_schema["properties"]["areas"]["anyOf"]
    assert {entry["type"] for entry in areas} == {"array", "null"}
    assert next(entry for entry in areas if entry["type"] == "array")["items"]["enum"] == [
        "sleep",
        "hrv",
        "stress",
    ]
    assert "92" in tools["daily_wellness"].input_schema["properties"]["start"]["description"]


@pytest.mark.anyio
async def test_date_schema_preserves_sanitized_date_validation(server, caplog) -> None:
    """Reject invalid date strings without repeating their values or logging private inputs."""
    async with Client(server, mode="2026-07-28") as client:
        for stamp in ("SYNTHETIC-PRIVATE-DATE", "2021-02-30"):
            result = await client.call_tool(
                "list_activities", {"start": stamp, "end": "2021-03-01"}
            )
            assert result.is_error
            text = " ".join(item.text for item in result.content)
            assert "Dates must use YYYY-MM-DD" in text
            assert stamp not in text
    assert caplog.text == ""


@pytest.mark.anyio
async def test_activity_range_includes_end_day_last_second_and_excludes_next_midnight(
    server, ledger_file
) -> None:
    """Include end-day seconds/fractions in lists and totals while excluding exterior dates."""
    from datetime import datetime

    from athlete_ledger.db.models import Activity
    from athlete_ledger.sync_types import PROVIDER
    from sqlalchemy import create_engine, insert

    writer = create_engine(f"sqlite:///{ledger_file}")
    with writer.begin() as connection:
        for identity, stamp in [
            (60, datetime(2020, 12, 31, 0, 0)),
            (61, datetime(2020, 12, 30, 23, 59, 59)),
            (62, datetime(2021, 1, 4, 23, 59, 59)),
            (63, datetime(2021, 1, 4, 23, 59, 59, 999999)),
            (64, datetime(2021, 1, 5, 0, 0)),
        ]:
            connection.execute(
                insert(Activity).values(
                    provider=PROVIDER,
                    activity_id=identity,
                    start_time_local=stamp,
                    activity_type="synthetic-boundary",
                    source_response_id=1,
                )
            )
    writer.dispose()
    async with Client(server, mode="2026-07-28") as client:
        args = {"start": "2020-12-31", "end": "2021-01-04"}
        result = await client.call_tool("list_activities", args)
        assert not result.is_error
        data = result.structured_content
        assert data["total_matching"] == 7
        assert [row["activity_id"] for row in data["activities"]] == [60, 10, 20, 30, 40, 62, 63]
        assert data["activities"][-2]["start_time_local"] == "2021-01-04T23:59:59"
        result = await client.call_tool("activity_totals", {**args, "group_by": "sport"})
        assert not result.is_error
        assert result.structured_content["overall"]["activity_count"] == 7
        boundary = next(
            row for row in result.structured_content["groups"] if row["key"] == "synthetic-boundary"
        )
        assert boundary["activity_count"] == 3


def test_oracle_activity_date_predicate_binds_only_midnight_boundaries() -> None:
    """Compile the actual Oracle predicate without fractional precision or an ADB connection."""
    from datetime import date, datetime

    from athlete_ledger.db.models import Activity
    from athlete_ledger_mcp.queries import activity_filter
    from sqlalchemy import select
    from sqlalchemy.dialects import oracle

    statement = select(Activity.activity_id).where(
        *activity_filter(date(2020, 12, 31), date(2021, 1, 4))
    )
    compiled = statement.compile(dialect=oracle.dialect())
    assert "activity.start_time_local >= " in str(compiled)
    assert "activity.start_time_local < " in str(compiled)
    assert "activity.start_time_local <= " not in str(compiled)
    boundaries = [value for value in compiled.params.values() if isinstance(value, datetime)]
    assert boundaries == [datetime(2020, 12, 31), datetime(2021, 1, 5)]


@pytest.mark.anyio
async def test_activity_maximum_date_does_not_overflow(server) -> None:
    """Handle the final representable inclusive date without computing an invalid next day."""
    async with Client(server, mode="2026-07-28") as client:
        for tool, extra in [("list_activities", {}), ("activity_totals", {"group_by": "week"})]:
            result = await client.call_tool(
                tool, {"start": "9999-12-31", "end": "9999-12-31", **extra}
            )
            assert not result.is_error
            count = result.structured_content.get("total_matching")
            if count is None:
                count = result.structured_content["overall"]["activity_count"]
            assert count == 0
