"""
Author: L. Saetta
Date Last Modified: 2026-11-17
License: MIT
Description: Store multisport legs and grant ATHLETE_LEDGER_READER access.
"""  # noqa: D205, D212

import sqlalchemy as sa
from alembic import op

from athlete_ledger.db.types import UTCDateTime

revision = "0003_activity_legs"
down_revision = "0002_activity_training_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the legs table, parent index and Oracle reader grant."""
    op.create_table(
        "activity_leg",
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("activity_id", sa.BigInteger(), nullable=False),
        sa.Column("parent_activity_id", sa.BigInteger(), nullable=False),
        sa.Column("leg_index", sa.Integer(), nullable=False),
        sa.Column("start_time_local", sa.DateTime(timezone=False), nullable=True),
        sa.Column("activity_type", sa.String(128), nullable=True),
        sa.Column("distance_m", sa.Float(), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("moving_duration_s", sa.Float(), nullable=True),
        sa.Column("avg_hr_bpm", sa.Float(), nullable=True),
        sa.Column("max_hr_bpm", sa.Float(), nullable=True),
        sa.Column("calories_kcal", sa.Float(), nullable=True),
        sa.Column("avg_speed_mps", sa.Float(), nullable=True),
        sa.Column("elevation_gain_m", sa.Float(), nullable=True),
        sa.Column("elevation_loss_m", sa.Float(), nullable=True),
        sa.Column("aerobic_training_effect", sa.Float(), nullable=True),
        sa.Column("anaerobic_training_effect", sa.Float(), nullable=True),
        sa.Column("training_load", sa.Float(), nullable=True),
        sa.Column("normalized_power_w", sa.Float(), nullable=True),
        sa.Column("training_effect_label", sa.String(64), nullable=True),
        sa.Column("moderate_intensity_min", sa.Integer(), nullable=True),
        sa.Column("vigorous_intensity_min", sa.Integer(), nullable=True),
        sa.Column("source_response_id", sa.Integer(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("provider", "activity_id", name=op.f("pk_activity_leg")),
        sa.ForeignKeyConstraint(
            ["provider", "parent_activity_id"],
            ["activity.provider", "activity.activity_id"],
            name=op.f("fk_activity_leg_provider"),
        ),
        sa.ForeignKeyConstraint(
            ["source_response_id"],
            ["raw_response.id"],
            name=op.f("fk_activity_leg_source_response_id"),
        ),
    )
    op.create_index("ix_activity_leg_parent", "activity_leg", ["provider", "parent_activity_id"])
    if op.get_context().dialect.name == "oracle":
        op.execute("GRANT SELECT ON activity_leg TO ATHLETE_LEDGER_READER")


def downgrade() -> None:
    """Revoke the Oracle reader grant and remove only the legs table."""
    if op.get_context().dialect.name == "oracle":
        op.execute("REVOKE SELECT ON activity_leg FROM ATHLETE_LEDGER_READER")
    op.drop_index("ix_activity_leg_parent", table_name="activity_leg")
    op.drop_table("activity_leg")
