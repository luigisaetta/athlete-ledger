"""SQLite storage, migrations and dialect-only DDL tests with synthetic records."""

import re
from datetime import UTC, date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from athlete_ledger.db import create_db_engine
from athlete_ledger.db.migrate import (
    check_database,
    downgrade,
    migration_config,
    offline_sql,
    upgrade,
)
from athlete_ledger.db.models import (
    Activity,
    Base,
    DailyHRV,
    DailySleep,
    DailyStress,
    RawResponse,
    SyncState,
)
from athlete_ledger.db.types import JSONDocument, UTCDateTime
from athlete_ledger.settings import Settings
from sqlalchemy import JSON, Column, MetaData, Table, inspect, select
from sqlalchemy.dialects import oracle, sqlite
from sqlalchemy.exc import CompileError, IntegrityError, StatementError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

ROOT = Path(__file__).resolve().parents[3]


def raw_response(**kwargs: object) -> RawResponse:
    values = dict(
        provider="synthetic",
        endpoint="/synthetic/summary",
        params={"limit": 0},
        fetched_at=datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=UTC),
        content_sha256="a" * 64,
        sdk_version="0.0.synthetic",
        is_empty=False,
        payload={"records": [{"value": 0}, None, "caffè"], "missing": None},
    )
    values.update(kwargs)
    return RawResponse(**values)


def test_migration_roundtrip_and_metadata_parity(tmp_path: Path) -> None:
    engine = create_db_engine(Settings(db_url=f"sqlite:///{tmp_path / 'store.db'}"))
    try:
        upgrade(engine)
        upgrade(engine)
        assert set(inspect(engine).get_table_names()) == {*Base.metadata.tables, "alembic_version"}
        assert check_database(engine).revision == "0001_ledger_store"
        with engine.connect() as connection:
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        downgrade(engine)
        assert inspect(engine).get_table_names() == ["alembic_version"]
        upgrade(engine)
        assert check_database(engine).revision == "0001_ledger_store"
    finally:
        engine.dispose()


def test_all_models_and_documents_roundtrip() -> None:
    engine = create_db_engine(Settings(db_url="sqlite://"))
    upgrade(engine)
    instant = datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=timezone(timedelta(hours=2)))
    day = date(2026, 1, 2)
    try:
        with Session(engine) as session:
            first = raw_response(fetched_at=instant)
            repeated = raw_response(payload=[{"list": []}, 0, None])
            empty = raw_response(payload=None, is_empty=True)
            session.add_all([first, repeated, empty])
            session.flush()
            # Equal hashes are append-only observations, not a uniqueness key.
            assert first.id != repeated.id
            session.add_all(
                [
                    Activity(
                        provider="synthetic",
                        activity_id=2**40,
                        source_response_id=first.id,
                        start_time_local=datetime(2026, 1, 2, 8),
                        distance_m=0,
                        updated_at=instant,
                    ),
                    DailySleep(
                        provider="synthetic",
                        calendar_date=day,
                        source_response_id=first.id,
                        sleep_time_s=0,
                        updated_at=instant,
                    ),
                    DailyHRV(
                        provider="synthetic",
                        calendar_date=day,
                        source_response_id=first.id,
                        last_night_avg_ms=0,
                        updated_at=instant,
                    ),
                    DailyStress(
                        provider="synthetic",
                        calendar_date=day,
                        source_response_id=first.id,
                        overall_stress_level=0,
                        updated_at=instant,
                    ),
                    SyncState(
                        provider="synthetic",
                        dataset="activities",
                        last_run_at=instant,
                        last_synced_date=day,
                        last_status="ok",
                    ),
                ]
            )
            ids = first.id, repeated.id, empty.id
            session.commit()
        with Session(engine) as session:
            first = session.get(RawResponse, ids[0])
            assert first is not None
            assert first.params == {"limit": 0}
            assert first.payload == {"records": [{"value": 0}, None, "caffè"], "missing": None}
            assert first.fetched_at == instant.astimezone(UTC)
            assert first.fetched_at.tzinfo is UTC
            assert session.get(RawResponse, ids[1]).payload == [{"list": []}, 0, None]
            assert session.get(RawResponse, ids[2]).payload is None
            activity = session.get(Activity, ("synthetic", 2**40))
            assert activity.file_status == "not_downloaded"
            assert activity.distance_m == 0
            assert activity.start_time_local.tzinfo is None
            for model in (Activity, DailySleep, DailyHRV, DailyStress):
                record = session.scalars(select(model)).one()
                assert record.updated_at == instant.astimezone(UTC)
                assert record.source_response_id == ids[0]
            sleep = session.get(DailySleep, ("synthetic", day))
            assert sleep.sleep_time_s == 0
            assert sleep.deep_sleep_s is None
            assert session.get(SyncState, ("synthetic", "activities")).last_run_at.tzinfo is UTC
    finally:
        engine.dispose()


@pytest.mark.parametrize("model", [Activity, DailySleep, DailyHRV, DailyStress, SyncState])
def test_composite_keys_reject_duplicates_and_allow_other_providers(model: type[Base]) -> None:
    engine = create_db_engine(Settings(db_url="sqlite://"))
    upgrade(engine)
    try:
        with Session(engine) as session:
            source = raw_response()
            session.add(source)
            session.flush()
            if model is SyncState:
                values = {"dataset": "activities"}
            else:
                values = {"source_response_id": source.id}
                values.update(
                    {"activity_id": 7} if model is Activity else {"calendar_date": date(2026, 1, 1)}
                )
            session.add_all(
                [model(provider="synthetic", **values), model(provider="other", **values)]
            )
            session.commit()
        with Session(engine) as session:
            session.add(model(provider="synthetic", **values))
            with pytest.raises(IntegrityError):
                session.commit()
    finally:
        engine.dispose()


def test_utc_rejects_naive_and_keeps_fractional_seconds() -> None:
    kind = UTCDateTime()
    for dialect in (sqlite.dialect(), oracle.dialect()):
        with pytest.raises(ValueError, match="timezone-aware"):
            kind.process_bind_param(datetime(2026, 1, 1), dialect)
    engine = create_db_engine(Settings(db_url="sqlite://"))
    upgrade(engine)
    try:
        with Session(engine) as session:
            session.add(raw_response(fetched_at=datetime(2026, 1, 1)))
            with pytest.raises(StatementError, match="timezone-aware"):
                session.commit()
        assert "TIMESTAMP" in str(UTCDateTime().compile(dialect=oracle.dialect()))
    finally:
        engine.dispose()


@pytest.mark.parametrize("document", [{}, [], {"a": [0, None, "é"]}, [1, 2]])
def test_json_documents(document: object) -> None:
    kind = JSONDocument()
    dialect = sqlite.dialect()
    encoded = kind.process_bind_param(document, dialect)
    assert kind.process_result_value(encoded, dialect) == document
    assert kind.process_result_value(document, dialect) == document


@pytest.mark.parametrize("document", ["scalar", 1, {"bad": float("nan")}, {"bad": object()}])
def test_invalid_documents_are_rejected_without_values(document: object) -> None:
    with pytest.raises(ValueError) as error:
        JSONDocument().process_bind_param(document, sqlite.dialect())
    assert "scalar" not in str(error.value)


def test_oracle_json_fallback_evidence() -> None:
    assert not hasattr(oracle, "JSON")
    table = Table("json_probe", MetaData(), Column("payload", JSON()))
    with pytest.raises(CompileError):
        str(CreateTable(table).compile(dialect=oracle.dialect()))
    assert str(JSONDocument().compile(dialect=oracle.dialect())) == "CLOB"


def test_offline_oracle_ddl_and_snapshot() -> None:
    ddl = offline_sql("oracle")
    for table in Base.metadata.tables:
        assert f"CREATE TABLE {table} (" in ddl
        assert f"GRANT SELECT ON {table} TO ATHLETE_LEDGER_READER" in ddl
    assert "GRANT SELECT ON alembic_version TO ATHLETE_LEDGER_READER" in ddl
    assert "GENERATED BY DEFAULT AS IDENTITY" in ddl
    assert "params CLOB" in ddl and "payload CLOB" in ddl
    assert "params IS JSON" in ddl and "payload IS JSON" in ddl
    assert ":ret_" not in ddl
    assert '"' not in ddl
    assert "IS JSON" not in offline_sql("sqlite")
    assert ddl == (ROOT / "sql/oracle/schema.sql").read_text()
    # The model's Oracle metadata compiles to the same JSON constraints.
    table_ddl = str(
        CreateTable(Base.metadata.tables["raw_response"]).compile(dialect=oracle.dialect())
    )
    assert "params IS JSON" in table_ddl and "payload IS JSON" in table_ddl
    assert all(re.fullmatch("[a-z][a-z0-9_]*", name) for name in Base.metadata.tables)


def test_offline_oracle_downgrade_revokes_before_dropping() -> None:
    buffer = StringIO()
    config = migration_config()
    config.output_buffer = buffer
    config.attributes["dialect_name"] = "oracle"
    command.downgrade(config, "head:base", sql=True)
    ddl = buffer.getvalue()
    for table in Base.metadata.tables:
        assert ddl.index(f"REVOKE SELECT ON {table} FROM ATHLETE_LEDGER_READER") < ddl.index(
            f"DROP TABLE {table}"
        )


def test_large_document_roundtrip() -> None:
    engine = create_db_engine(Settings(db_url="sqlite://"))
    upgrade(engine)
    document = {"synthetic": "é" * 40000, "values": [0, None, {}]}
    try:
        with Session(engine) as session:
            response = raw_response(payload=document)
            session.add(response)
            session.commit()
            response_id = response.id
        with Session(engine) as session:
            assert session.get(RawResponse, response_id).payload == document
    finally:
        engine.dispose()


def test_stress_range_and_foreign_key() -> None:
    engine = create_db_engine(Settings(db_url="sqlite://"))
    upgrade(engine)
    try:
        with Session(engine) as session:
            session.add(
                DailySleep(
                    provider="synthetic", calendar_date=date(2026, 1, 1), source_response_id=999
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
        with Session(engine) as session:
            response = raw_response()
            session.add(response)
            session.flush()
            session.add(
                DailyStress(
                    provider="synthetic",
                    calendar_date=date(2026, 1, 1),
                    source_response_id=response.id,
                    overall_stress_level=101,
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
    finally:
        engine.dispose()
