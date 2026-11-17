"""
Author: L. Saetta
Date Last Modified: 2026-11-17
License: MIT
Description: Verify nested leg data, shared conversions, parent-only counts and query boundaries.
"""  # noqa: D205, D212

from datetime import UTC, date, datetime

import pytest
from athlete_ledger.db.models import Activity, ActivityLeg
from athlete_ledger.sync_types import PROVIDER
from athlete_ledger_mcp import queries
from athlete_ledger_mcp.results import ActivityData, LegData, LegTrainingMetrics, TrainingMetrics
from athlete_ledger_mcp.server import create_server
from jsonschema import validate
from mcp.client import Client
from sqlalchemy import create_engine, event, insert

FIRST = date(2020, 12, 31)
LAST = date(2021, 1, 4)
METRICS = {
    "distance_m": 1234.56,
    "duration_s": 1256.7,
    "moving_duration_s": 1193.2,
    "avg_hr_bpm": 142.26,
    "max_hr_bpm": 178.96,
    "calories_kcal": 432.75,
    "avg_speed_mps": 3.14159,
    "elevation_gain_m": 123.456,
    "elevation_loss_m": 98.765,
    "aerobic_training_effect": 3.26,
    "anaerobic_training_effect": 1.18,
    "training_effect_label": "synthetic-label",
    "training_load": 87.65,
    "normalized_power_w": 254.6,
    "moderate_intensity_min": 13,
    "vigorous_intensity_min": 7,
}
EXPECTED = {
    "distance_km": 1.23,
    "duration_min": 20.9,
    "moving_duration_min": 19.9,
    "avg_hr_bpm": 142.3,
    "max_hr_bpm": 179.0,
    "calories_kcal": 432.75,
    "avg_speed_kmh": 11.3,
    "elevation_gain_m": 123.5,
    "elevation_loss_m": 98.8,
    "training": {
        "aerobic_effect": 3.3,
        "anaerobic_effect": 1.2,
        "effect_label": "synthetic-label",
        "load": 87.7,
        "normalized_power_w": 255.0,
    },
    "intensity_minutes": {"moderate": 13, "vigorous": 7},
}


@pytest.fixture
def multisport_file(ledger_file):
    """Add two synthetic multisport parents without changing the shared ledger fixture.

    Args:
        ledger_file: Seeded SQLite file with ordinary activities and wellness records.

    Returns:
        The same file with a metric-bearing and an empty-leg multisport parent.
    """
    writer = create_engine(f"sqlite:///{ledger_file}")
    try:
        with writer.begin() as connection:
            for identity, hour in ((60, 8), (70, 9)):
                connection.execute(
                    insert(Activity).values(
                        provider=PROVIDER,
                        activity_id=identity,
                        activity_type="multi_sport",
                        start_time_local=datetime(2021, 1, 2, hour),
                        activity_name="SYNTHETIC-PRIVATE-PARENT",
                        source_response_id=1,
                        updated_at=datetime(2021, 1, 6, tzinfo=UTC),
                        **METRICS,
                    )
                )
    finally:
        writer.dispose()
    return ledger_file


def add_legs(path):
    """Insert legs in reverse order, including a transition and an all-null running leg.

    Args:
        path: Synthetic SQLite file already containing multisport parents.
    """
    writer = create_engine(f"sqlite:///{path}")
    try:
        with writer.begin() as connection:
            for identity, index, sport, metrics in (
                (601, 1, "running", {}),
                (602, 0, "transition_v2", METRICS),
            ):
                connection.execute(
                    insert(ActivityLeg).values(
                        provider=PROVIDER,
                        activity_id=identity,
                        parent_activity_id=60,
                        leg_index=index,
                        activity_type=sport,
                        start_time_local=None if index else datetime(2021, 1, 2, 8),
                        source_response_id=1,
                        updated_at=datetime(2021, 1, 6, tzinfo=UTC),
                        **metrics,
                    )
                )
            # The provider filter must exclude a leg whose parent ID collides with ours.
            connection.execute(
                insert(Activity).values(
                    provider="synthetic_other",
                    activity_id=60,
                    activity_type="multi_sport",
                    source_response_id=1,
                    updated_at=datetime(2021, 1, 6, tzinfo=UTC),
                )
            )
            connection.execute(
                insert(ActivityLeg).values(
                    provider="synthetic_other",
                    activity_id=999,
                    parent_activity_id=60,
                    leg_index=0,
                    source_response_id=1,
                    updated_at=datetime(2021, 1, 6, tzinfo=UTC),
                )
            )
    finally:
        writer.dispose()


@pytest.mark.anyio
async def test_legs_order_conversions_nulls_and_excluded_fields(multisport_file, reader, server):
    """Expose exact leg shapes and conversions without names, provenance, TSS, IF or zones."""
    add_legs(multisport_file)
    async with Client(server, mode="2026-07-28") as client:
        result = await client.call_tool("list_activities", {"start": str(FIRST), "end": str(LAST)})
        assert not result.is_error
        schema = next(
            tool.output_schema
            for tool in (await client.list_tools()).tools
            if tool.name == "list_activities"
        )
    data = result.structured_content
    validate(data, schema)
    by_id = {activity["activity_id"]: activity for activity in data["activities"]}
    parent = by_id[60]
    assert [leg["activity_id"] for leg in parent["legs"]] == [602, 601]
    assert [leg["leg_index"] for leg in parent["legs"]] == [0, 1]
    converted, empty = parent["legs"]
    assert converted == {
        "activity_id": 602,
        "leg_index": 0,
        "sport": "transition_v2",
        "start_time_local": "2021-01-02T08:00:00",
        **EXPECTED,
    }
    assert empty == {
        "activity_id": 601,
        "leg_index": 1,
        "sport": "running",
        "start_time_local": None,
        **{key: None for key in EXPECTED if key not in {"training", "intensity_minutes"}},
        "training": dict.fromkeys(EXPECTED["training"]),
        "intensity_minutes": {"moderate": None, "vigorous": None},
    }
    for key, value in EXPECTED.items():
        if key == "training":
            assert {name: parent[key][name] for name in value} == value
        else:
            assert parent[key] == value
    assert by_id[70]["legs"] == []
    assert all(activity["legs"] == [] for identity, activity in by_id.items() if identity != 60)
    assert data["total_matching"] == 6 and data["truncated"] is False
    for leg in parent["legs"]:
        assert set(leg) == set(LegData.model_fields)
        assert set(leg["training"]) == set(LegTrainingMetrics.model_fields)
        assert (
            not {
                "activity_name",
                "name",
                "hr_zone_min",
                "source_response_id",
                "updated_at",
                "parent_activity_id",
                "provider",
                "training_stress_score",
                "intensity_factor",
            }
            & leg.keys()
        )
        assert "stress_score" not in leg["training"] and "intensity_factor" not in leg["training"]


def test_empty_legs_before_detail_download(multisport_file, reader):
    """All parent types have an empty list when no details have been stored."""
    data = queries.list_activities(reader, FIRST, LAST, None, 200, False)
    assert all(activity.legs == [] for activity in data.activities)


def test_parent_filters_limits_and_unchanged_tools(multisport_file, reader):
    """Legs never affect parent filters, truncation, totals, sports, coverage or wellness."""
    baseline_totals = {
        group: queries.activity_totals(reader, FIRST, LAST, group).model_dump()
        for group in ("week", "month", "sport")
    }
    baseline_status = queries.ledger_status(reader, LAST).model_dump()
    baseline_wellness = queries.daily_wellness(
        reader, FIRST, LAST, list(queries.WELLNESS)
    ).model_dump()
    baseline_page = queries.list_activities(reader, FIRST, LAST, None, 200, False).model_dump()
    add_legs(multisport_file)
    running = queries.list_activities(reader, FIRST, LAST, "running", 200, False)
    assert [activity.activity_id for activity in running.activities] == [10, 20]
    assert running.total_matching == 2 and running.truncated is False
    assert all(not activity.legs for activity in running.activities)
    limited = queries.list_activities(reader, FIRST, LAST, "multi_sport", 1, False)
    assert limited.total_matching == 2 and limited.truncated is True
    assert [activity.activity_id for activity in limited.activities] == [60]
    assert len(limited.activities[0].legs) == 2
    after_page = queries.list_activities(reader, FIRST, LAST, None, 200, False).model_dump()
    for page in (baseline_page, after_page):
        for activity in page["activities"]:
            activity.pop("legs")
    assert after_page == baseline_page
    assert {
        group: queries.activity_totals(reader, FIRST, LAST, group).model_dump()
        for group in baseline_totals
    } == baseline_totals
    assert queries.ledger_status(reader, LAST).model_dump() == baseline_status
    assert (
        queries.daily_wellness(reader, FIRST, LAST, list(queries.WELLNESS)).model_dump()
        == baseline_wellness
    )


@pytest.mark.anyio
async def test_names_opt_in_never_adds_leg_names(multisport_file, reader):
    """Enable parent names through the server while leg names remain absent."""
    add_legs(multisport_file)
    named = create_server(reader, include_activity_names=True)
    async with Client(named, mode="2026-07-28") as client:
        result = await client.call_tool(
            "list_activities",
            {
                "start": str(FIRST),
                "end": str(LAST),
                "sport": "multi_sport",
            },
        )
    assert not result.is_error
    parent = result.structured_content["activities"][0]
    assert parent["activity_name"] == "SYNTHETIC-PRIVATE-PARENT"
    assert all("activity_name" not in leg for leg in parent["legs"])


@pytest.mark.parametrize(
    ("sport", "limit", "expected_ids"),
    [
        (None, 200, (10, 20, 30, 60, 70, 40)),
        ("multi_sport", 1, (60,)),
        ("running", 1, (10,)),
        ("missing_sport", 50, ()),
    ],
)
def test_one_narrow_leg_query_for_returned_page_only(
    multisport_file, reader, sport, limit, expected_ids
):
    """Capture exactly one provider-filtered leg SELECT for returned parent IDs, or none."""
    add_legs(multisport_file)
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        """Capture SQL and synthetic binds to verify the extra query's scope.

        Args:
            connection: Read-only connection executing the query.
            cursor: SQLite cursor.
            statement: Generated SQL projection and predicates.
            parameters: Synthetic provider and parent-ID binds.
            context: SQLAlchemy execution context.
            executemany: Unused multiple-execution flag.
        """
        if "FROM activity_leg" in statement:
            statements.append((statement, parameters))

    event.listen(reader, "before_cursor_execute", capture)
    try:
        result = queries.list_activities(reader, FIRST, LAST, sport, limit, False)
    finally:
        event.remove(reader, "before_cursor_execute", capture)
    assert tuple(activity.activity_id for activity in result.activities) == expected_ids
    assert len(statements) == int(bool(expected_ids))
    if statements:
        sql, parameters = statements[0]
        assert parameters == (PROVIDER, *expected_ids)
        assert "ORDER BY activity_leg.parent_activity_id, activity_leg.leg_index" in sql
        projection = sql.split("\nFROM ")[0]
        selected = {column.removeprefix("SELECT ").strip() for column in projection.split(",")}
        assert selected == {
            f"activity_leg.{column}"
            for column in ActivityLeg.__table__.c.keys()
            if column not in {"provider", "source_response_id", "updated_at"}
        }
        assert "raw_response" not in sql


@pytest.mark.anyio
async def test_leg_schema_descriptions_units_and_tool_descriptions(server):
    """Publish the additive schema for both name modes with matching existing field descriptions."""
    async with Client(server, mode="2026-07-28") as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    schema = tools["list_activities"].output_schema
    definitions = schema["$defs"]
    for name in ("ActivityData", "NamedActivityData"):
        leg_field = definitions[name]["properties"]["legs"]
        assert leg_field["items"] == {"$ref": "#/$defs/LegData"}
        assert "legs" in definitions[name]["required"]
        for phrase in ("leg_index", "transition_v2", "athlete-ledger sync legs"):
            assert phrase in leg_field["description"]
    assert set(definitions["LegData"]["properties"]) == set(LegData.model_fields)
    assert set(definitions["LegTrainingMetrics"]["properties"]) == {
        "aerobic_effect",
        "anaerobic_effect",
        "effect_label",
        "load",
        "normalized_power_w",
    }
    assert (
        definitions["LegData"]["properties"]["intensity_minutes"]["$ref"]
        == "#/$defs/IntensityMinutes"
    )
    for name, field in LegData.model_fields.items():
        assert field.description
        if name in ActivityData.model_fields and name != "training":
            assert field.description == ActivityData.model_fields[name].description
    for name, field in LegTrainingMetrics.model_fields.items():
        assert field.description == TrainingMetrics.model_fields[name].description
    for phrase in ("legs", "leg_index", "transition_v2", "athlete-ledger sync legs", "km/h"):
        assert phrase in tools["list_activities"].description
    assert tools["activity_totals"].description.endswith(
        "A multisport activity counts once, with the totals of all its legs."
    )
