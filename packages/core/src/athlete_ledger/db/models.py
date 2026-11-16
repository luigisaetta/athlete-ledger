"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Provider-scoped ledger schema; every metric is nullable and names carry units.
"""  # noqa: D205, D212

from datetime import UTC, date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    String,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from athlete_ledger.db.types import JSONDocument, JSONValue, UTCDateTime


def utc_now() -> datetime:
    """Create a current UTC timestamp for fetch, update and synchronization metadata.

    Returns:
        A timezone-aware datetime with UTC tzinfo.
    """
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Shared metadata with deterministic, unquoted constraint names."""

    metadata = MetaData(
        naming_convention={
            "pk": "pk_%(table_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s",
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
        }
    )


class RawResponse(Base):
    """Append-only provenance; repeated content hashes are intentionally allowed."""

    __tablename__ = "raw_response"
    __table_args__ = (
        Index("ix_raw_response_lookup", "provider", "endpoint", "range_start"),
        CheckConstraint("params IS JSON", name="params_json").ddl_if(dialect="oracle"),
        CheckConstraint("payload IS JSON", name="payload_json").ddl_if(dialect="oracle"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64))
    endpoint: Mapped[str] = mapped_column(String(256))
    params: Mapped[JSONValue] = mapped_column(JSONDocument(), default=dict)
    range_start: Mapped[date | None]
    range_end: Mapped[date | None]
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    content_sha256: Mapped[str] = mapped_column(String(64))
    sdk_version: Mapped[str] = mapped_column(String(64))
    is_empty: Mapped[bool] = mapped_column(Boolean(create_constraint=True, name="is_empty_bool"))
    payload: Mapped[JSONValue | None] = mapped_column(JSONDocument())


class SourceRecord:
    """Provenance and UTC modification time for normalized records."""

    source_response_id: Mapped[int] = mapped_column(ForeignKey("raw_response.id"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now)


class Activity(SourceRecord, Base):
    """An activity summary; start_time_local is provider-local naive wall time."""

    __tablename__ = "activity"

    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    activity_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    start_time_local: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    activity_name: Mapped[str | None] = mapped_column(String(512))
    activity_type: Mapped[str | None] = mapped_column(String(128))
    distance_m: Mapped[float | None]
    duration_s: Mapped[float | None]
    avg_hr_bpm: Mapped[float | None]
    calories_kcal: Mapped[float | None]
    file_status: Mapped[str] = mapped_column(
        String(32), default="not_downloaded", server_default=text("'not_downloaded'")
    )


class DailyRecord(SourceRecord):
    """A provider-scoped calendar date, never inferred from result position."""

    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    calendar_date: Mapped[date] = mapped_column(primary_key=True)


class DailySleep(DailyRecord, Base):
    """Daily sleep summary in seconds, beats/minute and breaths/minute."""

    __tablename__ = "daily_sleep"

    sleep_time_s: Mapped[int | None]
    deep_sleep_s: Mapped[int | None]
    light_sleep_s: Mapped[int | None]
    rem_sleep_s: Mapped[int | None]
    awake_s: Mapped[int | None]
    nap_time_s: Mapped[int | None]
    sleep_score: Mapped[float | None]
    avg_hr_bpm: Mapped[float | None]
    avg_respiration_brpm: Mapped[float | None]


class DailyHRV(DailyRecord, Base):
    """Daily heart-rate variability summary in milliseconds."""

    __tablename__ = "daily_hrv"

    last_night_avg_ms: Mapped[float | None]
    weekly_avg_ms: Mapped[float | None]
    last_night_5min_high_ms: Mapped[float | None]
    baseline_balanced_low_ms: Mapped[float | None]
    baseline_balanced_upper_ms: Mapped[float | None]
    hrv_status: Mapped[str | None] = mapped_column(String(64))


class DailyStress(DailyRecord, Base):
    """Daily stress score and durations in seconds."""

    __tablename__ = "daily_stress"
    __table_args__ = (
        CheckConstraint("overall_stress_level BETWEEN 0 AND 100", name="stress_range"),
    )

    overall_stress_level: Mapped[int | None]
    rest_s: Mapped[int | None]
    low_stress_s: Mapped[int | None]
    medium_stress_s: Mapped[int | None]
    high_stress_s: Mapped[int | None]


class SyncState(Base):
    """Progress per provider and dataset; synchronization is implemented later."""

    __tablename__ = "sync_state"

    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_synced_date: Mapped[date | None]
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_status: Mapped[str | None] = mapped_column(String(32))
