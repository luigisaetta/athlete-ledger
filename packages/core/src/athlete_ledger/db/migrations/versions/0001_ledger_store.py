"""Initial ledger schema, generated from the Phase 1a models.

This snapshot remains independent of future model changes.
"""

import sqlalchemy as sa
from alembic import op

from athlete_ledger.db.types import JSONDocument, UTCDateTime

revision = "0001_ledger_store"
down_revision = None
branch_labels = None
depends_on = None

TABLE_NAMES = (
    "activity",
    "daily_hrv",
    "daily_sleep",
    "daily_stress",
    "raw_response",
    "sync_state",
    "alembic_version",
)


def _reader_privileges(action: str) -> None:
    """Grant/revoke the read-only role on ledger and revision tables."""
    if op.get_context().dialect.name == "oracle":
        for table in TABLE_NAMES:
            if action == "grant":
                op.execute(f"GRANT SELECT ON {table} TO ATHLETE_LEDGER_READER")
            else:
                op.execute(f"REVOKE SELECT ON {table} FROM ATHLETE_LEDGER_READER")


def upgrade() -> None:
    """Create the initial store and reader grants."""
    op.create_table(
        "raw_response",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("endpoint", sa.String(length=256), nullable=False),
        sa.Column("params", JSONDocument(), nullable=False),
        sa.Column("range_start", sa.Date(), nullable=True),
        sa.Column("range_end", sa.Date(), nullable=True),
        sa.Column("fetched_at", UTCDateTime(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("sdk_version", sa.String(length=64), nullable=False),
        sa.Column(
            "is_empty", sa.Boolean(create_constraint=True, name="is_empty_bool"), nullable=False
        ),
        sa.Column("payload", JSONDocument(), nullable=True),
        sa.CheckConstraint("params IS JSON", name=op.f("ck_raw_response_params_json")).ddl_if(
            dialect="oracle"
        ),
        sa.CheckConstraint("payload IS JSON", name=op.f("ck_raw_response_payload_json")).ddl_if(
            dialect="oracle"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_raw_response")),
    )
    op.create_index(
        "ix_raw_response_lookup",
        "raw_response",
        ["provider", "endpoint", "range_start"],
        unique=False,
    )
    op.create_table(
        "sync_state",
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("dataset", sa.String(length=64), nullable=False),
        sa.Column("last_synced_date", sa.Date(), nullable=True),
        sa.Column("last_run_at", UTCDateTime(), nullable=True),
        sa.Column("last_status", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("provider", "dataset", name=op.f("pk_sync_state")),
    )
    op.create_table(
        "activity",
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("activity_id", sa.BigInteger(), nullable=False),
        sa.Column("start_time_local", sa.DateTime(), nullable=True),
        sa.Column("activity_name", sa.String(length=512), nullable=True),
        sa.Column("activity_type", sa.String(length=128), nullable=True),
        sa.Column("distance_m", sa.Float(), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("avg_hr_bpm", sa.Float(), nullable=True),
        sa.Column("calories_kcal", sa.Float(), nullable=True),
        sa.Column(
            "file_status",
            sa.String(length=32),
            server_default=sa.text("'not_downloaded'"),
            nullable=False,
        ),
        sa.Column("source_response_id", sa.Integer(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_response_id"], ["raw_response.id"], name=op.f("fk_activity_source_response_id")
        ),
        sa.PrimaryKeyConstraint("provider", "activity_id", name=op.f("pk_activity")),
    )
    op.create_table(
        "daily_hrv",
        sa.Column("last_night_avg_ms", sa.Float(), nullable=True),
        sa.Column("weekly_avg_ms", sa.Float(), nullable=True),
        sa.Column("last_night_5min_high_ms", sa.Float(), nullable=True),
        sa.Column("baseline_balanced_low_ms", sa.Float(), nullable=True),
        sa.Column("baseline_balanced_upper_ms", sa.Float(), nullable=True),
        sa.Column("hrv_status", sa.String(length=64), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("calendar_date", sa.Date(), nullable=False),
        sa.Column("source_response_id", sa.Integer(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_response_id"],
            ["raw_response.id"],
            name=op.f("fk_daily_hrv_source_response_id"),
        ),
        sa.PrimaryKeyConstraint("provider", "calendar_date", name=op.f("pk_daily_hrv")),
    )
    op.create_table(
        "daily_sleep",
        sa.Column("sleep_time_s", sa.Integer(), nullable=True),
        sa.Column("deep_sleep_s", sa.Integer(), nullable=True),
        sa.Column("light_sleep_s", sa.Integer(), nullable=True),
        sa.Column("rem_sleep_s", sa.Integer(), nullable=True),
        sa.Column("awake_s", sa.Integer(), nullable=True),
        sa.Column("nap_time_s", sa.Integer(), nullable=True),
        sa.Column("sleep_score", sa.Float(), nullable=True),
        sa.Column("avg_hr_bpm", sa.Float(), nullable=True),
        sa.Column("avg_respiration_brpm", sa.Float(), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("calendar_date", sa.Date(), nullable=False),
        sa.Column("source_response_id", sa.Integer(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_response_id"],
            ["raw_response.id"],
            name=op.f("fk_daily_sleep_source_response_id"),
        ),
        sa.PrimaryKeyConstraint("provider", "calendar_date", name=op.f("pk_daily_sleep")),
    )
    op.create_table(
        "daily_stress",
        sa.Column("overall_stress_level", sa.Integer(), nullable=True),
        sa.Column("rest_s", sa.Integer(), nullable=True),
        sa.Column("low_stress_s", sa.Integer(), nullable=True),
        sa.Column("medium_stress_s", sa.Integer(), nullable=True),
        sa.Column("high_stress_s", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("calendar_date", sa.Date(), nullable=False),
        sa.Column("source_response_id", sa.Integer(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "overall_stress_level BETWEEN 0 AND 100", name=op.f("ck_daily_stress_stress_range")
        ),
        sa.ForeignKeyConstraint(
            ["source_response_id"],
            ["raw_response.id"],
            name=op.f("fk_daily_stress_source_response_id"),
        ),
        sa.PrimaryKeyConstraint("provider", "calendar_date", name=op.f("pk_daily_stress")),
    )
    _reader_privileges("grant")


def downgrade() -> None:
    """Remove reader grants and the initial store."""
    _reader_privileges("revoke")
    op.drop_table("daily_stress")
    op.drop_table("daily_sleep")
    op.drop_table("daily_hrv")
    op.drop_table("activity")
    op.drop_table("sync_state")
    op.drop_index("ix_raw_response_lookup", table_name="raw_response")
    op.drop_table("raw_response")
