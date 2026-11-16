"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Verify additive activity training outputs, plain sums, units and published schemas.
"""  # noqa: D205, D212

import pytest
from athlete_ledger.db.models import Activity
from athlete_ledger.sync_types import PROVIDER
from jsonschema import validate
from mcp.client import Client
from sqlalchemy import create_engine, update

LIST_FIELDS = {
    "moving_duration_min",
    "avg_speed_kmh",
    "max_hr_bpm",
    "elevation_gain_m",
    "elevation_loss_m",
    "training",
    "intensity_minutes",
    "hr_zone_min",
}
TOTAL_FIELDS = {
    "training_load",
    "training_stress_score",
    "moving_duration_h",
    "elevation_gain_m",
    "intensity_minutes",
    "hr_zone_h",
}
MISSING_FIELDS = {
    "training_load",
    "training_stress_score",
    "moving_duration",
    "elevation_gain",
    "hr_zones",
}
ZONES = [f"zone_{zone}" for zone in range(1, 6)]


@pytest.fixture
def training_data(ledger_file):
    """Populate two synthetic running activities, leaving cycling/unknown groups all-null.

    Args:
        ledger_file: Temporary SQLite ledger created by the isolated package fixture.

    Returns:
        Path of the updated synthetic ledger, usable by the existing read-only engine.
    """
    writer = create_engine(f"sqlite:///{ledger_file}")
    with writer.begin() as connection:
        connection.execute(
            update(Activity)
            .where(Activity.provider == PROVIDER, Activity.activity_id == 10)
            .values(
                aerobic_training_effect=3.26,
                anaerobic_training_effect=0.84,
                training_effect_label="SYNTHETIC-PROVIDER-LABEL",
                training_load=25.26,
                training_stress_score=45.26,
                intensity_factor=0.876,
                normalized_power_w=250.6,
                moving_duration_s=3671,
                avg_speed_mps=3.21,
                max_hr_bpm=181.26,
                elevation_gain_m=123.26,
                elevation_loss_m=110.26,
                moderate_intensity_min=12,
                vigorous_intensity_min=7,
                hr_zone_1_s=3661,
                hr_zone_2_s=0,
                hr_zone_3_s=None,
                hr_zone_4_s=61,
                hr_zone_5_s=125,
            )
        )
        connection.execute(
            update(Activity)
            .where(Activity.provider == PROVIDER, Activity.activity_id == 20)
            .values(
                aerobic_training_effect=0,
                anaerobic_training_effect=5,
                training_load=10.16,
                normalized_power_w=0,
                avg_speed_mps=0,
                max_hr_bpm=0,
                elevation_gain_m=0,
                moderate_intensity_min=3,
                hr_zone_1_s=59,
                hr_zone_3_s=0,
                hr_zone_5_s=0,
            )
        )
    writer.dispose()
    return ledger_file


async def invoke(client, tool, **arguments):
    """Call an activity tool through the actual SDK using the fixed synthetic range.

    Args:
        client: Connected in-process MCP SDK client.
        tool: list_activities or activity_totals.
        **arguments: Tool options overriding the default inclusive date range.

    Returns:
        Validated structured content from a successful tool call.
    """
    result = await client.call_tool(tool, {"start": "2020-12-31", "end": "2021-01-04", **arguments})
    assert not result.is_error and result.structured_content is not None
    return result.structured_content


@pytest.mark.anyio
async def test_each_activity_training_field_converts_units_and_rounds_once(training_data, server):
    """Expose all nineteen columns with exact field names, units and stable nested objects."""
    async with Client(server, mode="2026-07-28") as client:
        data = await invoke(client, "list_activities", sport="running")
        schema = next(
            tool.output_schema
            for tool in (await client.list_tools()).tools
            if tool.name == "list_activities"
        )
        validate(data, schema)
    first, second = data["activities"]
    assert first["activity_id"] == 10 and second["activity_id"] == 20
    assert {key: first[key] for key in LIST_FIELDS} == {
        "moving_duration_min": 61.2,
        "avg_speed_kmh": 11.6,
        "max_hr_bpm": 181.3,
        "elevation_gain_m": 123.3,
        "elevation_loss_m": 110.3,
        "training": {
            "aerobic_effect": 3.3,
            "anaerobic_effect": 0.8,
            "effect_label": "SYNTHETIC-PROVIDER-LABEL",
            "load": 25.3,
            "stress_score": 45.3,
            "intensity_factor": 0.88,
            "normalized_power_w": 251.0,
        },
        "intensity_minutes": {"moderate": 12, "vigorous": 7},
        "hr_zone_min": {"zone_1": 61.0, "zone_2": 0, "zone_3": None, "zone_4": 1.0, "zone_5": 2.1},
    }
    assert second["avg_speed_kmh"] == 0 and second["max_hr_bpm"] == 0
    assert second["training"]["aerobic_effect"] == 0
    assert second["training"]["anaerobic_effect"] == 5
    assert second["training"]["normalized_power_w"] == 0
    assert second["intensity_minutes"] == {"moderate": 3, "vigorous": None}
    assert "activity_name" not in first and "activity_name" not in second
    assert data["total_matching"] == 2 and data["truncated"] is False


@pytest.mark.anyio
async def test_missing_values_keep_nested_objects_and_null_members(training_data, server):
    """Always emit the three required nested list objects even when every metric is missing."""
    async with Client(server, mode="2026-07-28") as client:
        data = await invoke(client, "list_activities", sport="cycling")
    row = data["activities"][0]
    assert all(
        row[key] is None for key in LIST_FIELDS - {"training", "intensity_minutes", "hr_zone_min"}
    )
    assert row["training"] == dict.fromkeys(
        (
            "aerobic_effect",
            "anaerobic_effect",
            "effect_label",
            "load",
            "stress_score",
            "intensity_factor",
            "normalized_power_w",
        )
    )
    assert row["intensity_minutes"] == {"moderate": None, "vigorous": None}
    assert row["hr_zone_min"] == dict.fromkeys(ZONES)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("group_by", "positive_key", "missing_count"),
    [
        ("sport", "running", 0),
        ("week", "2020-W53", 1),
        ("month", "2020-12", 0),
    ],
)
async def test_plain_sums_and_missing_counts_per_group_and_overall(
    training_data, server, group_by, positive_key, missing_count
):
    """Sum stored values before rounding and retain per-field null counts in every grouping."""
    async with Client(server, mode="2026-07-28") as client:
        data = await invoke(client, "activity_totals", group_by=group_by)
        schema = next(
            tool.output_schema
            for tool in (await client.list_tools()).tools
            if tool.name == "activity_totals"
        )
        validate(data, schema)
    expected = {
        "training_load": 35.4,
        "training_stress_score": 45.3,
        "moving_duration_h": 1.02,
        "elevation_gain_m": 123.3,
        "intensity_minutes": {"moderate": 15, "vigorous": 7},
        "hr_zone_h": {"zone_1": 1.03, "zone_2": 0, "zone_3": 0, "zone_4": 0.02, "zone_5": 0.03},
    }
    overall = data["overall"]
    assert {key: overall[key] for key in TOTAL_FIELDS} == expected
    assert {key: overall["missing"][key] for key in MISSING_FIELDS} == {
        "training_load": 2,
        "training_stress_score": 3,
        "moving_duration": 3,
        "elevation_gain": 2,
        "hr_zones": 2,
    }
    positive = next(group for group in data["groups"] if group["key"] == positive_key)
    assert {key: positive[key] for key in TOTAL_FIELDS} == expected
    assert {key: positive["missing"][key] for key in MISSING_FIELDS} == {
        "training_load": missing_count,
        "training_stress_score": missing_count + 1,
        "moving_duration": missing_count + 1,
        "elevation_gain": missing_count,
        "hr_zones": missing_count,
    }
    for group in data["groups"]:
        assert not {
            "aerobic_effect",
            "anaerobic_effect",
            "intensity_factor",
            "normalized_power_w",
            "effect_label",
            "elevation_loss_m",
        }.intersection(group)
        if group["key"] != positive_key:
            assert all(
                group[key] is None for key in TOTAL_FIELDS - {"intensity_minutes", "hr_zone_h"}
            )
            assert group["intensity_minutes"] == {"moderate": None, "vigorous": None}
            assert group["hr_zone_h"] == dict.fromkeys(ZONES)
            assert all(group["missing"][key] == group["activity_count"] for key in MISSING_FIELDS)
    assert (
        overall["distance_km"] == 3
        and overall["duration_h"] == 0.1
        and overall["avg_hr_bpm"] == 130
    )


@pytest.mark.anyio
async def test_empty_totals_keep_null_sum_objects_and_zero_missing_counts(server):
    """Keep empty totals distinguishable from measured zero and retain the nested shape."""
    async with Client(server, mode="2026-07-28") as client:
        data = await invoke(
            client, "activity_totals", start="2019-01-01", end="2019-01-02", group_by="week"
        )
    overall = data["overall"]
    assert data["groups"] == [] and overall["activity_count"] == 0
    assert all(overall[key] is None for key in TOTAL_FIELDS - {"intensity_minutes", "hr_zone_h"})
    assert overall["intensity_minutes"] == {"moderate": None, "vigorous": None}
    assert overall["hr_zone_h"] == dict.fromkeys(ZONES)
    assert all(overall["missing"][key] == 0 for key in MISSING_FIELDS)


@pytest.mark.anyio
async def test_zero_is_a_present_metric_in_every_new_sum(ledger_file, server):
    """Preserve zero for all list metrics and totals, counting no metric as missing."""
    zero_columns = (
        "aerobic_training_effect",
        "anaerobic_training_effect",
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
    writer = create_engine(f"sqlite:///{ledger_file}")
    with writer.begin() as connection:
        connection.execute(
            update(Activity)
            .where(Activity.provider == PROVIDER, Activity.activity_id == 40)
            .values(**dict.fromkeys(zero_columns, 0))
        )
    writer.dispose()
    async with Client(server, mode="2026-07-28") as client:
        data = await invoke(client, "list_activities", start="2021-01-04", end="2021-01-04")
        total = (
            await invoke(
                client, "activity_totals", start="2021-01-04", end="2021-01-04", group_by="sport"
            )
        )["overall"]
    row = data["activities"][0]
    assert all(
        row[key] == 0 for key in LIST_FIELDS - {"training", "intensity_minutes", "hr_zone_min"}
    )
    assert row["training"]["effect_label"] is None
    assert all(value == 0 for key, value in row["training"].items() if key != "effect_label")
    assert all(value == 0 for value in row["hr_zone_min"].values())
    assert row["intensity_minutes"] == {"moderate": 0, "vigorous": 0}
    assert all(total[key] == 0 for key in TOTAL_FIELDS - {"intensity_minutes", "hr_zone_h"})
    assert total["intensity_minutes"] == {"moderate": 0, "vigorous": 0}
    assert all(value == 0 for value in total["hr_zone_h"].values())
    assert all(total["missing"][key] == 0 for key in MISSING_FIELDS)


@pytest.mark.anyio
async def test_new_schema_fields_are_required_described_and_unit_explicit(server):
    """Publish descriptions for every new scalar/member and require stable nested object keys."""
    async with Client(server, mode="2026-07-28") as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    listing = tools["list_activities"].output_schema
    totals = tools["activity_totals"].output_schema
    for name in ("ActivityData", "NamedActivityData"):
        schema = listing["$defs"][name]
        assert LIST_FIELDS <= set(schema["required"])
        assert all(schema["properties"][key].get("description") for key in LIST_FIELDS)
        assert "km/h" in schema["properties"]["avg_speed_kmh"]["description"]
        assert "minutes" in schema["properties"]["moving_duration_min"]["description"]
    for name in ("ActivityAggregate", "ActivityGroup"):
        schema = totals["$defs"][name]
        assert TOTAL_FIELDS <= set(schema["required"])
        assert all(schema["properties"][key].get("description") for key in TOTAL_FIELDS)
        assert "hours" in schema["properties"]["moving_duration_h"]["description"]
    assert all(
        totals["$defs"]["MissingValues"]["properties"][key].get("description")
        for key in MISSING_FIELDS
    )
    for schema in (listing, totals):
        for name in (
            "TrainingMetrics",
            "IntensityMinutes",
            "HeartRateZoneMinutes",
            "HeartRateZoneHours",
        ):
            if name in schema.get("$defs", {}):
                nested = schema["$defs"][name]
                assert set(nested["properties"]) == set(nested["required"])
                assert all(
                    field.get("description")
                    and {part["type"] for part in field["anyOf"]} >= {"null"}
                    for field in nested["properties"].values()
                )
    for name in ("list_activities", "activity_totals"):
        description = tools[name].description
        assert all(section not in description for section in ("Args:", "Returns:", "Raises:"))
        assert "provider" in description.lower() and "power-meter" in description
    assert "km/h" in tools["list_activities"].description
    assert "hr_zone_min" in tools["list_activities"].description
    assert "plain sums only" in tools["activity_totals"].description
    assert "hr_zone_h" in tools["activity_totals"].description


@pytest.mark.anyio
async def test_no_private_identity_location_device_or_disabled_names_in_output(
    training_data, server, caplog
):
    """Keep private columns out of results and fixture metrics out of logs."""

    def check_keys(value):
        """Inspect nested result keys for fields forbidden by the privacy contract.

        Args:
            value: Synthetic tool-result subtree to inspect recursively.
        """
        if isinstance(value, dict):
            for key, child in value.items():
                assert not any(
                    token in key.lower()
                    for token in ("latitude", "longitude", "location", "owner", "device")
                )
                assert key != "activity_name"
                check_keys(child)
        elif isinstance(value, list):
            for child in value:
                check_keys(child)

    async with Client(server, mode="2026-07-28") as client:
        check_keys(await invoke(client, "list_activities"))
        check_keys(await invoke(client, "activity_totals", group_by="week"))
    assert caplog.text == ""
