"""
Author: L. Saetta
Date Last Modified: 2026-11-17
License: MIT
Description: Verify multisport schema, detail mapping, atomic sync and offline replay.
"""  # noqa: D205, D212

import copy
from datetime import UTC, date, datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from athlete_ledger.cli import run
from athlete_ledger.coverage import sync_coverage
from athlete_ledger.db import create_db_engine
from athlete_ledger.db.migrate import downgrade, migration_config, offline_sql, upgrade
from athlete_ledger.db.models import Activity, ActivityLeg, Base, RawResponse, SyncState
from athlete_ledger.legs import sync_legs
from athlete_ledger.normalize.garmin_connect import detail_children, normalize_leg
from athlete_ledger.providers.garmin_connect import PersonalProvider
from athlete_ledger.rebuild import rebuild
from athlete_ledger.settings import Settings
from athlete_ledger.storage import Counts, store_response
from athlete_ledger.sync_types import DATASETS, PROVIDER, Response, UnexpectedResponseError
from athlete_ledger_mcp.queries import ledger_status
from garmin_personal_api import HttpError
from pydantic import SecretStr
from sqlalchemy import event, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

DAY = date(2026, 1, 2)
NOW = datetime(2026, 1, 3, tzinfo=UTC)
MARKER = "SYNTHETIC-PRIVATE-DETAIL"
FLOAT_FIELDS = {
    "distance_m": "distance",
    "duration_s": "duration",
    "moving_duration_s": "movingDuration",
    "avg_hr_bpm": "averageHR",
    "max_hr_bpm": "maxHR",
    "calories_kcal": "calories",
    "avg_speed_mps": "averageSpeed",
    "elevation_gain_m": "elevationGain",
    "elevation_loss_m": "elevationLoss",
    "training_load": "activityTrainingLoad",
    "normalized_power_w": "normalizedPower",
}


def detail(identifier, parent=None, children=None):
    """Build one synthetic parent or leg response with private excluded fields.

    Args:
        identifier: Requested provider ID.
        parent: Owning activity ID for legs.
        children: Ordered IDs for a parent, or None for omitted childIds.

    Returns:
        A fresh complete detail object.
    """
    payload = {
        "activityId": identifier,
        "activityName": MARKER,
        "activityTypeDTO": {"typeKey": "transition_v2" if parent else "multi_sport"},
        "summaryDTO": {
            "startTimeLocal": "2026-01-02T08:00:00.123456",
            **{src: 123.25 for src in FLOAT_FIELDS.values()},
            "trainingEffect": 3.2,
            "anaerobicTrainingEffect": 1.1,
            "trainingEffectLabel": "synthetic-label",
            "moderateIntensityMinutes": 4.6,
            "vigorousIntensityMinutes": 2.0,
            "startLatitude": MARKER,
            "locationName": MARKER,
            "ownerId": MARKER,
            "deviceId": MARKER,
            "hrTimeInZone_1": 999,
            "trainingStressScore": 999,
            "intensityFactor": 999,
        },
    }
    if parent is not None:
        payload["parentId"] = parent
    if children is not None:
        payload["metadataDTO"] = {"childIds": children}
    return payload


class FakeDetails:
    """Record fake requests and return independently mutable synthetic details."""

    sdk_version = "synthetic"

    def __init__(self, payloads):
        """Configure response objects indexed by requested ID.

        Args:
            payloads: Fake decoded responses or exceptions.
        """
        self.payloads = payloads
        self.request_count = 0
        self.calls = []
        self.closed = False

    def activity_detail(self, activity_id):
        """Return a synthetic detail without I/O.

        Args:
            activity_id: ID requested by the sync engine.

        Returns:
            An independently copied response.

        Raises:
            ValueError: For a deliberately poisoned fixture.
        """
        self.calls.append(activity_id)
        self.request_count += 1
        payload = self.payloads[activity_id]
        if isinstance(payload, Exception):
            raise payload
        return Response("get_activity", {"activity_id": activity_id}, copy.deepcopy(payload))

    def responses(self, dataset, start, end):
        """Yield an empty dataset response to exercise automatic legs orchestration.

        Args:
            dataset: Requested download dataset.
            start: Inclusive start date.
            end: Inclusive end date.

        Yields:
            One empty raw page.
        """
        from athlete_ledger.sync_types import ENDPOINTS

        self.request_count += 1
        yield Response(ENDPOINTS[dataset], {}, [])

    def close(self):
        """Record provider cleanup."""
        self.closed = True


@pytest.fixture
def engine(tmp_path):
    """Provide a migrated, isolated SQLite ledger.

    Args:
        tmp_path: Synthetic test database directory.

    Yields:
        Owner engine, disposed after each test.
    """
    instance = create_db_engine(Settings(db_url=f"sqlite:///{tmp_path / 'legs.db'}"))
    upgrade(instance)
    try:
        yield instance
    finally:
        instance.dispose()


def seed(engine, identifiers=(100,)):
    """Store synthetic list parents through the existing dataset path.

    Args:
        engine: Disposable SQLite ledger.
        identifiers: IDs to insert as multisport parents.
    """
    with Session(engine) as session, session.begin():
        store_response(
            session,
            "activities",
            Response(
                "search_activities",
                {},
                [
                    {
                        "activityId": identifier,
                        "activityType": {"typeKey": "multi_sport"},
                        "startTimeLocal": "2026-01-02 08:00:00",
                    }
                    for identifier in identifiers
                ],
            ),
            DAY,
            DAY,
            "synthetic",
            lambda: NOW,
            Counts(),
        )


def snapshot(engine, model):
    """Snapshot all columns to verify exact value and provenance preservation.

    Args:
        engine: Test ledger.
        model: ORM table to snapshot.

    Returns:
        Ordered row tuples.
    """
    with engine.connect() as connection:
        return connection.execute(
            select(*model.__table__.c).order_by(*model.__table__.primary_key.columns)
        ).all()


def test_schema_roundtrip_parity_grants_and_parent_fk(engine):
    """Match model columns, enforce the composite parent FK and reverse the migration."""
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    table = ActivityLeg.__table__
    assert len(table.c) == 24
    assert set(inspect(engine).get_pk_constraint("activity_leg")["constrained_columns"]) == {
        "provider",
        "activity_id",
    }
    assert inspect(engine).get_indexes("activity_leg")[0]["column_names"] == [
        "provider",
        "parent_activity_id",
    ]
    seed(engine)
    with Session(engine) as session, pytest.raises(IntegrityError):
        session.add(
            ActivityLeg(
                provider=PROVIDER,
                activity_id=101,
                parent_activity_id=999,
                leg_index=0,
                source_response_id=1,
                updated_at=NOW,
            )
        )
        session.commit()
    before = snapshot(engine, Activity)
    downgrade(engine, "0002_activity_training_metrics")
    assert "activity_leg" not in inspect(engine).get_table_names()
    assert snapshot(engine, Activity) == before
    upgrade(engine)
    assert snapshot(engine, Activity) == before
    ddl = offline_sql("oracle")
    assert "GRANT SELECT ON activity_leg TO ATHLETE_LEDGER_READER" in ddl
    assert ddl == (Path(__file__).resolve().parents[3] / "sql/oracle/schema.sql").read_text()
    buffer = StringIO()
    config = migration_config()
    config.output_buffer = buffer
    config.attributes["dialect_name"] = "oracle"
    command.downgrade(config, "0003_activity_legs:0002_activity_training_metrics", sql=True)
    assert buffer.getvalue().index("REVOKE SELECT ON activity_leg") < buffer.getvalue().index(
        "DROP TABLE activity_leg"
    )


def test_mapping_all_columns_and_exclusions():
    """Map the detail-specific aliases and allowlist every normalized column."""
    row = normalize_leg(detail(101, parent=100), 101, 100, 2)
    assert set(row) == set(ActivityLeg.__table__.c.keys()) - {
        "provider",
        "source_response_id",
        "updated_at",
    }
    assert row["activity_id"] == 101 and row["parent_activity_id"] == 100
    assert row["leg_index"] == 2 and row["activity_type"] == "transition_v2"
    assert row["start_time_local"] == datetime(2026, 1, 2, 8, microsecond=123456)
    assert all(row[column] == 123.25 for column in FLOAT_FIELDS)
    assert row["aerobic_training_effect"] == 3.2 and row["anaerobic_training_effect"] == 1.1
    assert row["training_effect_label"] == "synthetic-label"
    assert row["moderate_intensity_min"] == 5 and row["vigorous_intensity_min"] == 2
    assert MARKER not in repr(row)


@pytest.mark.parametrize("value", [None, -1, True, "123", float("inf"), float("nan")])
def test_numeric_value_rules(value):
    """Reject missing, sentinel, boolean, text and nonfinite leg metrics."""
    payload = detail(101, parent=100)
    keys = [
        *FLOAT_FIELDS.values(),
        "trainingEffect",
        "anaerobicTrainingEffect",
        "moderateIntensityMinutes",
        "vigorousIntensityMinutes",
    ]
    payload["summaryDTO"].update(dict.fromkeys(keys, value))
    row = normalize_leg(payload, 101, 100, 0)
    assert all(
        row[key] is None
        for key in [
            *FLOAT_FIELDS,
            "aerobic_training_effect",
            "anaerobic_training_effect",
            "moderate_intensity_min",
            "vigorous_intensity_min",
        ]
    )


@pytest.mark.parametrize("label", ["", "x" * 65, 1, None])
def test_effect_bounds_label_and_zero(label):
    """Keep zero and bounded effects, round minutes, and never truncate labels."""
    payload = detail(101, parent=100)
    payload["summaryDTO"].update(
        {
            "trainingEffect": 5.1,
            "anaerobicTrainingEffect": 0,
            "trainingEffectLabel": label,
            "distance": 0,
        }
    )
    row = normalize_leg(payload, 101, 100, 0)
    assert row["training_effect_label"] is None and row["aerobic_training_effect"] is None
    assert row["anaerobic_training_effect"] == row["distance_m"] == 0


@pytest.mark.parametrize("stamp", ["2026-01-02T08:00:00", "2026-01-02 08:00:00.125", None])
def test_naive_timestamps(stamp):
    """Accept both timestamp separators, fractional seconds and missing values."""
    payload = detail(101, parent=100)
    payload["summaryDTO"]["startTimeLocal"] = stamp
    assert normalize_leg(payload, 101, 100, 0)["start_time_local"] == (
        datetime.fromisoformat(stamp) if stamp else None
    )


@pytest.mark.parametrize("stamp", [MARKER, "2026-01-02T08:00:00Z", "2026-01-02 08:00:00+01:00", 7])
def test_timestamp_errors_are_endpoint_only(stamp):
    """Reject malformed or offset-bearing timestamps without rendering their contents."""
    payload = detail(101, parent=100)
    payload["summaryDTO"]["startTimeLocal"] = stamp
    with pytest.raises(UnexpectedResponseError, match="^get_activity$"):
        normalize_leg(payload, 101, 100, 0)


def test_sync_selection_refresh_raw_provenance_and_empty_parent(engine):
    """Fetch each family once, including zero-leg parents, and refresh idempotently."""
    seed(engine, (100, 200))
    provider = FakeDetails(
        {
            100: detail(100, children=[102, 101]),
            101: detail(101, parent=100),
            102: detail(102, parent=100),
            200: detail(200),
        }
    )
    result = sync_legs(engine, provider, clock=lambda: NOW)
    assert result.parents_checked == 2 and result.requests == 4
    assert result.counts.inserted == 2 and result.parents_failed == 0
    assert provider.calls == [100, 102, 101, 200]
    with Session(engine) as session:
        legs = session.scalars(select(ActivityLeg).order_by(ActivityLeg.leg_index)).all()
        assert [leg.activity_id for leg in legs] == [102, 101]
        for leg in legs:
            raw = session.get(RawResponse, leg.source_response_id)
            assert raw.endpoint == "get_activity" and raw.params == {"activity_id": leg.activity_id}
            assert raw.range_start == raw.range_end == DAY
            assert raw.sdk_version == "synthetic" and raw.fetched_at == NOW and not raw.is_empty
        assert session.scalars(select(SyncState)).all() == []
    raw_before = snapshot(engine, RawResponse)
    leg_before = snapshot(engine, ActivityLeg)
    assert sync_legs(engine, provider).parents_checked == 0
    refreshed = sync_legs(engine, provider, refresh=True, clock=lambda: NOW + timedelta(days=1))
    assert refreshed.counts.unchanged == 2
    assert snapshot(engine, RawResponse) == raw_before
    assert snapshot(engine, ActivityLeg) == leg_before
    provider.payloads[101]["summaryDTO"]["distance"] = 999
    changed = sync_legs(engine, provider, refresh=True)
    assert changed.counts.updated == 1 and changed.counts.unchanged == 1
    assert len(snapshot(engine, Activity)) == 2


@pytest.mark.parametrize("bad", [None, [], {}, {"summaryDTO": []}, ValueError(MARKER)])
def test_parent_failures_roll_back_and_retry_only_failed_family(engine, bad):
    """A bad middle family cannot undo committed families or leave a checked parent raw row."""
    seed(engine, (100, 200, 300))
    provider = FakeDetails(
        {
            100: detail(100, children=[]),
            200: bad,
            300: detail(300, children=[301]),
            301: detail(301, parent=300),
        }
    )
    result = sync_legs(engine, provider)
    assert result.parents_failed == 1 and result.counts.inserted == 1
    provider.payloads[200] = detail(200)
    provider.calls.clear()
    assert sync_legs(engine, provider).parents_failed == 0
    assert provider.calls == [200]


def test_wrong_parent_rolls_back_every_raw_and_leg_in_family(engine):
    """A later invalid leg rolls back the parent detail and earlier valid legs too."""
    seed(engine)
    raw_before = snapshot(engine, RawResponse)
    provider = FakeDetails(
        {
            100: detail(100, children=[101, 102]),
            101: detail(101, parent=100),
            102: detail(102, parent=999),
        }
    )
    result = sync_legs(engine, provider)
    assert result.parents_failed == 1 and result.counts.inserted == 0
    assert snapshot(engine, RawResponse) == raw_before and snapshot(engine, ActivityLeg) == []


def test_leg_cannot_move_between_parents(engine):
    """Reject reparenting even when the returned leg claims to belong to a new parent."""
    seed(engine, (100, 200))
    provider = FakeDetails(
        {100: detail(100, children=[101]), 101: detail(101, parent=100), 200: detail(200)}
    )
    assert sync_legs(engine, provider).parents_failed == 0
    before = snapshot(engine, ActivityLeg)
    provider.payloads[200] = detail(200, children=[101])
    provider.payloads[101] = detail(101, parent=200)
    result = sync_legs(engine, provider, refresh=True)
    assert (
        result.parents_failed == 2
    )  # Both the wrong-parent response and the reparent are rejected.
    assert snapshot(engine, ActivityLeg) == before


def test_rebuild_last_values_final_order_net_idempotence_and_no_raw_writes(engine, monkeypatch):
    """Replay detail histories offline, including an order-only parent refresh."""
    seed(engine)
    provider = FakeDetails(
        {
            100: detail(100, children=[101, 102]),
            101: detail(101, parent=100),
            102: detail(102, parent=100),
        }
    )
    sync_legs(engine, provider)
    provider.payloads[101]["summaryDTO"]["distance"] = 999
    sync_legs(engine, provider, refresh=True)
    provider.payloads[100]["metadataDTO"]["childIds"] = [102, 101]
    sync_legs(engine, provider, refresh=True)
    before = snapshot(engine, ActivityLeg)
    raw_before = snapshot(engine, RawResponse)
    state_before = snapshot(engine, SyncState)
    with Session(engine) as session, session.begin():
        session.query(ActivityLeg).delete()
    result = rebuild(engine, ["legs"])[0]
    assert result.error is None and result.counts.inserted == 2
    # Rebuilt metric/order/provenance fields match sync; timestamps naturally differ.
    assert [tuple(row[:-1]) for row in snapshot(engine, ActivityLeg)] == [
        tuple(row[:-1]) for row in before
    ]
    rebuilt = snapshot(engine, ActivityLeg)
    statements = []
    event.listen(
        engine,
        "before_cursor_execute",
        lambda conn, cursor, statement, params, context, many: statements.append(statement),
    )
    second = rebuild(engine, ["legs"])[0]
    assert second.counts.inserted == second.counts.updated == 0 and second.counts.unchanged == 2
    assert not any(statement.startswith(("UPDATE", "INSERT", "DELETE")) for statement in statements)
    assert snapshot(engine, ActivityLeg) == rebuilt
    assert (
        snapshot(engine, RawResponse) == raw_before and snapshot(engine, SyncState) == state_before
    )
    monkeypatch.setenv("ATHLETE_LEDGER_DB_URL", str(engine.url))
    factory = Mock(side_effect=AssertionError("No provider during rebuild"))
    monkeypatch.setattr("athlete_ledger.sync_cli.PersonalProvider", factory)
    assert run(["sync", "rebuild", "--dataset", "legs"]) == 0
    factory.assert_not_called()


def test_rebuild_corruption_rolls_back_legs_only(engine):
    """A malformed stored detail cannot commit earlier leg changes in the replay."""
    seed(engine)
    provider = FakeDetails(
        {
            100: detail(100, children=[101, 102]),
            101: detail(101, parent=100),
            102: detail(102, parent=100),
        }
    )
    sync_legs(engine, provider)
    with Session(engine) as session, session.begin():
        raw = session.scalar(select(RawResponse).where(RawResponse.id == 4))
        raw.payload = detail(102, parent=999)
        session.query(ActivityLeg).delete()
    result = rebuild(engine, ["legs", "sleep"])
    assert result[0].dataset == "sleep" and result[0].error is None
    assert result[1].error == "get_activity: raw_id=4" and result[1].counts.inserted == 0
    assert snapshot(engine, ActivityLeg) == []


def test_coverage_and_status_ignore_details(engine):
    """Detail raw ranges never expand coverage for any of the four datasets."""
    seed(engine)
    before = ledger_status(engine, DAY).model_dump()
    with Session(engine) as session, session.begin():
        session.add(
            RawResponse(
                provider=PROVIDER,
                endpoint="get_activity",
                params={"activity_id": 100},
                payload=detail(100),
                range_start=date(2025, 1, 1),
                range_end=date(2027, 1, 1),
                content_sha256="a" * 64,
                sdk_version="synthetic",
                fetched_at=NOW,
                is_empty=False,
            )
        )
    assert ledger_status(engine, DAY).model_dump() == before
    coverage = sync_coverage(engine)
    assert coverage["activities"].intervals == ((DAY, DAY),)
    assert all(not coverage[name].intervals for name in DATASETS if name != "activities")


def test_cli_legs_status_privacy_and_dataset_boundaries(engine, monkeypatch, capsys):
    """Print counts only, run automatic legs only for activities and keep status offline."""
    seed(engine)
    provider = FakeDetails({100: detail(100, children=[101]), 101: detail(101, parent=100)})
    monkeypatch.setenv("ATHLETE_LEDGER_DB_URL", str(engine.url))
    factory = Mock(return_value=provider)
    monkeypatch.setattr("athlete_ledger.sync_cli.PersonalProvider", factory)
    assert (
        run(["sync", "backfill", "--since", str(DAY), "--until", str(DAY), "--dataset", "sleep"])
        == 0
    )
    assert provider.calls == []
    assert (
        run(
            [
                "sync",
                "backfill",
                "--since",
                str(DAY),
                "--until",
                str(DAY),
                "--dataset",
                "activities",
            ]
        )
        == 0
    )
    assert provider.calls == [100, 101]
    assert run(["sync", "legs", "--refresh"]) == 0
    factory.reset_mock()
    assert run(["sync", "status"]) == 0
    factory.assert_not_called()
    captured = capsys.readouterr()
    assert "legs: stored=1" in captured.out
    assert "parents_checked=1 detail_requests=2" in captured.out
    assert MARKER not in captured.out + captured.err and "123.25" not in captured.out
    provider.payloads[101] = ValueError(MARKER)
    assert run(["sync", "legs", "--refresh"]) == 1
    captured = capsys.readouterr()
    assert captured.err == "ValueError\n" and MARKER not in captured.out
    for args in (
        ["sync", "run", "--dataset", "legs"],
        ["sync", "backfill", "--since", str(DAY), "--dataset", "legs"],
        ["sync", "legs", "--dataset", "activities"],
    ):
        with pytest.raises(SystemExit):
            run(args)


def test_detail_provider_uses_token_retry_and_safe_errors():
    """Route get_activity through token refresh, transient backoff and SDK-error sanitization."""
    client = Mock()
    client.get_activity.side_effect = [
        HttpError(
            status_code=401, method="GET", url="https://synthetic.invalid", response_text=MARKER
        ),
        requests.Timeout(MARKER),
        detail(100),
    ]
    auth = Mock()
    auth.obtain_token.return_value.access_token = "synthetic-token"
    waits = []
    provider = PersonalProvider(
        SecretStr("synthetic-key"),
        client_factory=lambda token: client,
        authenticator_factory=lambda: auth,
        key_resolver=lambda key: key,
        sleep=waits.append,
    )
    response = provider.activity_detail(100)
    assert response.endpoint == "get_activity" and response.params == {"activity_id": 100}
    assert response.payload == detail(100) and provider.request_count == 3
    assert waits == [2] and auth.obtain_token.call_count == 2
    client.update_bearer_token.assert_called_once_with("synthetic-token")
    assert client.get_activity.call_args.args == (100,)
    client.get_activity.side_effect = HttpError(
        status_code=404, method="GET", url="https://synthetic.invalid", response_text=MARKER
    )
    with pytest.raises(Exception, match="GET get_activity: HTTP 404") as caught:
        provider.activity_detail(100)
    assert MARKER not in str(caught.value)


@pytest.mark.parametrize("children", [[101, 101], [True], [MARKER], "bad"])
def test_invalid_child_identifiers_are_safe(children):
    """Malformed parent lists fail with an endpoint-only diagnostic."""
    with pytest.raises(UnexpectedResponseError, match="^get_activity$"):
        detail_children(detail(100, children=children))


@pytest.mark.parametrize("metadata", [None, {}, {"childIds": None}, {"childIds": []}])
def test_empty_child_lists(metadata):
    """Treat missing or empty child lists as successfully checked zero-leg parents."""
    payload = detail(100)
    payload["metadataDTO"] = metadata
    assert detail_children(payload) == []


def test_rebuild_preserves_legs_absent_from_later_details(engine):
    """A later empty parent does not delete previously stored normalized legs."""
    seed(engine)
    provider = FakeDetails({100: detail(100, children=[101]), 101: detail(101, parent=100)})
    sync_legs(engine, provider)
    provider.payloads[100] = detail(100, children=[])
    sync_legs(engine, provider, refresh=True)
    before = snapshot(engine, ActivityLeg)
    assert len(before) == 1
    result = rebuild(engine, ["legs"])[0]
    assert result.error is None and result.counts.updated == 0
    assert snapshot(engine, ActivityLeg) == before


def test_cli_incremental_legs_and_other_datasets_continue_on_detail_failure(
    engine, monkeypatch, capsys
):
    """Incremental activities run detail sync, whose failures do not stop later datasets."""
    seed(engine)
    provider = FakeDetails({100: ValueError(MARKER)})
    monkeypatch.setenv("ATHLETE_LEDGER_DB_URL", str(engine.url))
    monkeypatch.setattr("athlete_ledger.sync_cli.PersonalProvider", lambda key: provider)
    with Session(engine) as session, session.begin():
        session.add_all(
            [
                SyncState(
                    provider=PROVIDER,
                    dataset=dataset,
                    last_synced_date=DAY,
                    last_status="ok",
                    last_run_at=NOW,
                )
                for dataset in ("activities", "sleep")
            ]
        )

    # Keep the incremental window short without changing production time behavior.
    class FrozenDate(date):
        """Supply the test's local end date."""

        @classmethod
        def today(cls):
            """Return the synthetic incremental end date."""
            return DAY

    monkeypatch.setattr("athlete_ledger.sync_cli.date", FrozenDate)
    assert run(["sync", "run", "--dataset", "activities", "sleep"]) == 1
    output = capsys.readouterr()
    assert provider.calls == [100]
    assert "sleep: chunks=1" in output.out and output.err == "ValueError\n"
    assert MARKER not in output.out + output.err and provider.closed


def test_fresh_rebuild_default_restores_parents_before_legs(engine, monkeypatch, capsys):
    """The default offline rebuild restores the hierarchy from raw responses alone."""
    seed(engine)
    provider = FakeDetails({100: detail(100, children=[101]), 101: detail(101, parent=100)})
    sync_legs(engine, provider)
    with Session(engine) as session, session.begin():
        session.query(ActivityLeg).delete()
        session.query(Activity).delete()
    monkeypatch.setenv("ATHLETE_LEDGER_DB_URL", str(engine.url))
    monkeypatch.setattr(
        "athlete_ledger.sync_cli.PersonalProvider", Mock(side_effect=AssertionError)
    )
    assert run(["sync", "rebuild"]) == 0
    assert len(snapshot(engine, Activity)) == len(snapshot(engine, ActivityLeg)) == 1
    output = capsys.readouterr()
    assert output.err == "" and "legs: raw_read=2" in output.out
