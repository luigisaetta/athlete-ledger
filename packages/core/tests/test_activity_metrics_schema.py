"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Verify activity metric schema parity, reversible upgrades and Oracle DDL.
"""  # noqa: D205, D212

import ast
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from athlete_ledger.db import create_db_engine
from athlete_ledger.db.migrate import (
    check_database,
    downgrade,
    migration_config,
    offline_sql,
    upgrade,
)
from athlete_ledger.db.models import Activity, Base, RawResponse
from athlete_ledger.settings import Settings
from sqlalchemy import Column, Engine, Float, Integer, MetaData, String, Table, inspect, select

HEAD = "0002_activity_training_metrics"
PREVIOUS = "0001_ledger_store"
METRIC_TYPES = {
    "aerobic_training_effect": Float,
    "anaerobic_training_effect": Float,
    "training_effect_label": String,
    "training_load": Float,
    "training_stress_score": Float,
    "intensity_factor": Float,
    "normalized_power_w": Float,
    "elevation_gain_m": Float,
    "elevation_loss_m": Float,
    "max_hr_bpm": Float,
    "avg_speed_mps": Float,
    "moving_duration_s": Float,
    "moderate_intensity_min": Integer,
    "vigorous_intensity_min": Integer,
    "hr_zone_1_s": Float,
    "hr_zone_2_s": Float,
    "hr_zone_3_s": Float,
    "hr_zone_4_s": Float,
    "hr_zone_5_s": Float,
}
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def engine() -> Iterator[Engine]:
    """Provide an empty SQLite engine with the normal foreign-key enforcement enabled.

    Yields:
        A disposable in-memory engine, closed after each synthetic migration test.
    """
    result = create_db_engine(Settings(db_url="sqlite://"))
    try:
        yield result
    finally:
        result.dispose()


def seed_previous_schema(engine: Engine) -> None:
    """Insert synthetic provenance/activity values using the reflected pre-migration table.

    Args:
        engine: SQLite engine already upgraded to 0001_ledger_store.
    """
    table = Table("activity", MetaData(), autoload_with=engine)
    with engine.begin() as connection:
        connection.execute(
            RawResponse.__table__.insert().values(
                id=101,
                provider="synthetic",
                endpoint="synthetic_summary",
                range_start=date(2026, 1, 2),
                range_end=date(2026, 1, 2),
                params={"start_date": "2026-01-02"},
                payload={"activityId": 7, "activityName": "SYNTHETIC-PRIVATE-ACTIVITY"},
                fetched_at=datetime(2026, 1, 3, tzinfo=UTC),
                content_sha256="a" * 64,
                sdk_version="synthetic",
                is_empty=False,
            )
        )
        connection.execute(
            table.insert().values(
                provider="synthetic",
                activity_id=7,
                start_time_local=datetime(2026, 1, 2, 8),
                activity_name="SYNTHETIC-PRIVATE-ACTIVITY",
                activity_type="synthetic_sport",
                distance_m=0,
                duration_s=3600,
                avg_hr_bpm=None,
                calories_kcal=123.5,
                file_status="synthetic-preserved",
                source_response_id=101,
                updated_at=datetime(2026, 1, 3),
            )
        )


def table_rows(engine: Engine, table: Table) -> list[dict[str, object]]:
    """Snapshot a synthetic table through explicit column selection for exact comparisons.

    Args:
        engine: Disposable test engine whose rows are being compared across migrations.
        table: Reflected table or immutable existing-table metadata.

    Returns:
        Dictionaries containing every selected column value in primary-key order.
    """
    with engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                select(*table.c).order_by(*table.primary_key.columns)
            ).mappings()
        ]


def test_fresh_head_has_exact_nullable_metric_types_without_new_schema_rules(
    engine: Engine,
) -> None:
    """Add exactly nineteen correctly typed columns while preserving all existing schema rules."""
    upgrade(engine, PREVIOUS)
    before = inspect(engine)
    original_names = {column["name"] for column in before.get_columns("activity")}
    checks = before.get_check_constraints("activity")
    indexes = before.get_indexes("activity")
    foreign_keys = before.get_foreign_keys("activity")
    primary_key = before.get_pk_constraint("activity")
    upgrade(engine)
    after = inspect(engine)
    columns = {column["name"]: column for column in after.get_columns("activity")}
    assert len(METRIC_TYPES) == 19
    assert set(columns) - original_names == set(METRIC_TYPES)
    assert original_names <= columns.keys()
    for name, kind in METRIC_TYPES.items():
        assert isinstance(columns[name]["type"], kind)
        assert columns[name]["nullable"] is True and columns[name]["default"] is None
        model_column: Column = Activity.__table__.c[name]
        assert isinstance(model_column.type, kind)
        assert model_column.nullable is True
        assert model_column.default is None and model_column.server_default is None
        assert not model_column.index and not model_column.unique and not model_column.foreign_keys
    assert columns["training_effect_label"]["type"].length == 64
    assert Activity.__table__.c.training_effect_label.type.length == 64
    assert after.get_check_constraints("activity") == checks
    assert after.get_indexes("activity") == indexes
    assert after.get_foreign_keys("activity") == foreign_keys
    assert after.get_pk_constraint("activity") == primary_key
    assert check_database(engine).revision == HEAD


def test_empty_database_upgrades_directly_to_head(engine: Engine) -> None:
    """Apply both frozen revisions to a completely empty database with no special setup path."""
    assert inspect(engine).get_table_names() == []
    upgrade(engine)
    assert check_database(engine).revision == HEAD
    assert set(METRIC_TYPES) <= {
        column["name"] for column in inspect(engine).get_columns("activity")
    }


def test_upgrade_preserves_existing_values_and_initializes_new_metrics_to_null(
    engine: Engine,
) -> None:
    """Preserve raw/activity records byte-for-value while leaving every added metric NULL."""
    upgrade(engine, PREVIOUS)
    seed_previous_schema(engine)
    original = Table("activity", MetaData(), autoload_with=engine)
    activities_before = table_rows(engine, original)
    raw_before = table_rows(engine, RawResponse.__table__)
    upgrade(engine)
    assert table_rows(engine, original) == activities_before
    assert table_rows(engine, RawResponse.__table__) == raw_before
    with engine.connect() as connection:
        row = connection.execute(
            select(*(Activity.__table__.c[name] for name in METRIC_TYPES))
        ).one()
    assert all(value is None for value in row)


def test_downgrade_preserves_rows_removes_only_metrics_and_can_upgrade_again(
    engine: Engine,
) -> None:
    """Exercise SQLite batch recreation with foreign keys, preserving all pre-existing values."""
    upgrade(engine, PREVIOUS)
    seed_previous_schema(engine)
    original = Table("activity", MetaData(), autoload_with=engine)
    before = table_rows(engine, original)
    raw_before = table_rows(engine, RawResponse.__table__)
    upgrade(engine)
    current = Table("activity", MetaData(), autoload_with=engine)
    with engine.begin() as connection:
        connection.execute(
            current.update().values(
                aerobic_training_effect=3.5,
                moderate_intensity_min=12,
                training_effect_label="synthetic-label",
                hr_zone_1_s=0,
            )
        )
    downgrade(engine, PREVIOUS)
    assert check_database(engine).revision == PREVIOUS
    assert {column["name"] for column in inspect(engine).get_columns("activity")} == set(
        original.c.keys()
    )
    assert table_rows(engine, original) == before
    assert table_rows(engine, RawResponse.__table__) == raw_before
    upgrade(engine)
    assert check_database(engine).revision == HEAD
    assert table_rows(engine, original) == before
    assert table_rows(engine, RawResponse.__table__) == raw_before
    with engine.connect() as connection:
        assert all(
            value is None
            for value in connection.execute(
                select(*(Activity.__table__.c[name] for name in METRIC_TYPES))
            ).one()
        )
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []


def test_upgraded_schema_matches_live_metadata_with_types_and_defaults(engine: Engine) -> None:
    """Detect model/migration drift using Alembic comparison of columns, types and constraints."""
    upgrade(engine)
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={
                "compare_type": True,
            },
        )
        assert compare_metadata(context, Base.metadata) == []


def test_oracle_metric_ddl_is_generated_nullable_and_does_not_change_grants() -> None:
    """Verify both revisions, exact Oracle ADD statements and the generated snapshot."""
    ddl = offline_sql("oracle")
    initial, additions = ddl.split(f"-- Running upgrade {PREVIOUS} -> {HEAD}", maxsplit=1)
    assert f"-- Running upgrade  -> {PREVIOUS}" in initial
    alters = [line for line in additions.splitlines() if line.startswith("ALTER TABLE ")]
    assert alters == [
        f"ALTER TABLE activity ADD {name} "
        + ("VARCHAR2(64 CHAR)" if kind is String else "INTEGER" if kind is Integer else "FLOAT")
        for name, kind in METRIC_TYPES.items()
    ]
    for forbidden in ("GRANT ", "REVOKE ", "CREATE INDEX", "CONSTRAINT", "DEFAULT", "NOT NULL"):
        assert forbidden not in additions
    assert ddl == (ROOT / "sql/oracle/schema.sql").read_text()
    assert f"version_num='{HEAD}'" in additions


def test_activity_schema_excludes_location_owner_and_device_fields(engine: Engine) -> None:
    """Keep precise location, owner identity and device identifiers out of normalized columns."""
    upgrade(engine)
    for column in inspect(engine).get_columns("activity"):
        assert not any(
            token in column["name"].lower()
            for token in ("lat", "lon", "location", "owner", "device")
        )


def test_revision_is_single_head_and_does_not_import_live_models() -> None:
    """Keep the migration chain explicit and the new revision independent of live model changes."""
    scripts = ScriptDirectory.from_config(migration_config())
    assert scripts.get_heads() == [HEAD]
    revision = scripts.get_revision(HEAD)
    assert revision.down_revision == PREVIOUS
    source = Path(revision.path).read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module is None or "models" not in node.module.split(".")
        elif isinstance(node, ast.Import):
            assert all("models" not in alias.name.split(".") for alias in node.names)
    assert "ATHLETE_LEDGER_READER" in ast.get_docstring(tree)
