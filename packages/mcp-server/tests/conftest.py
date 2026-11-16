"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Isolate MCP tests from real configuration, network and Oracle connections.
"""  # noqa: D205, D212

import os
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from athlete_ledger.db.models import (
    Activity,
    Base,
    DailyHRV,
    DailySleep,
    DailyStress,
    RawResponse,
    SyncState,
)
from athlete_ledger.sync_types import DATASETS, ENDPOINTS, PROVIDER
from athlete_ledger_mcp.database import MCPSettings, create_reader_engine
from athlete_ledger_mcp.server import create_server
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def anyio_backend() -> str:
    """Select asyncio so asynchronous MCP tests need no optional Trio dependency.

    Returns:
        The name of AnyIO's installed asyncio backend.
    """
    return "asyncio"


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Remove inherited secrets and prohibit external I/O during every MCP test.

    Args:
        monkeypatch: Fixture restoring environment, directory and I/O replacements.
        tmp_path: Working directory containing synthetic configuration only.
    """
    for name in os.environ:
        if name.startswith(("ATHLETE_LEDGER_", "GARMIN_", "OCI_")):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    reject = Mock(side_effect=AssertionError("External I/O is forbidden in MCP tests."))
    monkeypatch.setattr("oracledb.connect", reject)
    monkeypatch.setattr("oracledb.init_oracle_client", reject)
    monkeypatch.setattr("requests.sessions.Session.request", reject)
    monkeypatch.setattr("anyio.connect_tcp", reject)
    monkeypatch.setattr("socket.create_connection", reject)
    monkeypatch.setattr("garmin_personal_api.resolve_api_key", reject)
    monkeypatch.setattr("garmin_personal_api.PersonalApiAuthenticator.obtain_token", reject)


@pytest.fixture
def ledger_file(tmp_path: Path) -> Path:
    """Seed an on-disk SQLite ledger with gaps, nulls and a year-boundary activity set.

    Args:
        tmp_path: Isolated temporary directory receiving the synthetic database.

    Returns:
        Path of the seeded database after its writer engine is disposed.
    """
    path = tmp_path / "synthetic ledger #1.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session, session.begin():
        raw_id = 1
        for dataset in DATASETS:
            for start, end in [
                (date(2020, 12, 28), date(2021, 1, 2)),
                (date(2021, 1, 4), date(2021, 1, 5)),
            ]:
                session.add(
                    RawResponse(
                        id=raw_id,
                        provider=PROVIDER,
                        endpoint=ENDPOINTS[dataset],
                        range_start=start,
                        range_end=end,
                        params={"private": "SYNTHETIC-PRIVATE-PARAMS"},
                        payload={"private": "SYNTHETIC-PRIVATE-PAYLOAD"},
                        fetched_at=datetime(2021, 1, 6, tzinfo=UTC),
                        content_sha256="a" * 64,
                        sdk_version="synthetic",
                        is_empty=False,
                    )
                )
                raw_id += 1
            session.add(
                SyncState(
                    provider=PROVIDER,
                    dataset=dataset,
                    last_synced_date=date(2021, 1, 5),
                    last_run_at=datetime(2021, 1, 6, tzinfo=UTC),
                    last_status="ok",
                )
            )
        session.flush()
        for identity, stamp, sport, distance, duration, hr, calories in [
            (20, datetime(2020, 12, 31, 8), "running", 1000, 60, 100, 10),
            (10, datetime(2020, 12, 31, 8), "running", 2000, 180, 140, None),
            (30, datetime(2021, 1, 1, 9), "cycling", None, 120, None, 30),
            (40, datetime(2021, 1, 4, 9), None, 0, 0, 0, 0),
            (50, None, "walking", None, None, None, None),
        ]:
            session.add(
                Activity(
                    provider=PROVIDER,
                    activity_id=identity,
                    start_time_local=stamp,
                    activity_type=sport,
                    distance_m=distance,
                    duration_s=duration,
                    avg_hr_bpm=hr,
                    calories_kcal=calories,
                    activity_name="SYNTHETIC-PRIVATE-ACTIVITY",
                    source_response_id=1,
                )
            )
        session.add(
            Activity(
                provider="other",
                activity_id=20,
                start_time_local=datetime(2021, 1, 1),
                activity_type="foreign",
                distance_m=999999,
                source_response_id=1,
            )
        )
        session.add(
            DailySleep(
                provider=PROVIDER,
                calendar_date=date(2020, 12, 31),
                source_response_id=3,
                sleep_time_s=3601,
                deep_sleep_s=0,
                light_sleep_s=None,
                rem_sleep_s=120,
                awake_s=60,
                nap_time_s=None,
                sleep_score=89.26,
                avg_hr_bpm=50.26,
                avg_respiration_brpm=15.26,
            )
        )
        session.add(
            DailyHRV(
                provider=PROVIDER,
                calendar_date=date(2021, 1, 1),
                source_response_id=5,
                last_night_avg_ms=50.56,
                weekly_avg_ms=None,
                last_night_5min_high_ms=60,
                baseline_balanced_low_ms=30,
                baseline_balanced_upper_ms=70,
                hrv_status="SYNTHETIC-PRIVATE-HRV",
            )
        )
        session.add(
            DailyStress(
                provider=PROVIDER,
                calendar_date=date(2021, 1, 4),
                source_response_id=7,
                overall_stress_level=0,
                rest_s=60,
                low_stress_s=0,
                medium_stress_s=None,
                high_stress_s=None,
            )
        )
    engine.dispose()
    return path


@pytest.fixture
def reader(ledger_file: Path) -> Iterator[Engine]:
    """Open the seeded file using production SQLite read-only settings.

    Args:
        ledger_file: Path of the synthetic ledger created by the writer fixture.

    Yields:
        A read-only engine, disposed after each test.
    """
    engine = create_reader_engine(MCPSettings(db_url=f"sqlite:///{ledger_file}"))
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def server(reader: Engine):
    """Create the real registered MCP server with a deterministic local clock.

    Args:
        reader: Read-only engine for the synthetic ledger.

    Returns:
        Official MCPServer ready for the SDK's in-process client.
    """
    return create_server(reader, today=lambda: date(2021, 1, 6))
