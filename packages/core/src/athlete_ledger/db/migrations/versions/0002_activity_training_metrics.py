"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Add nullable activity training metrics without modifying existing records.

    The existing table-level SELECT grant to ATHLETE_LEDGER_READER already covers
    new columns, so no grant changes are needed. This snapshot does not import
    live models; batch operations also support SQLite downgrade table recreation.
"""  # noqa: D205, D212

import sqlalchemy as sa
from alembic import op

revision = "0002_activity_training_metrics"
down_revision = "0001_ledger_store"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add exactly nineteen nullable metric columns without defaults, indexes or constraints."""
    with op.batch_alter_table("activity") as batch:
        batch.add_column(sa.Column("aerobic_training_effect", sa.Float(), nullable=True))
        batch.add_column(sa.Column("anaerobic_training_effect", sa.Float(), nullable=True))
        batch.add_column(sa.Column("training_effect_label", sa.String(64), nullable=True))
        batch.add_column(sa.Column("training_load", sa.Float(), nullable=True))
        batch.add_column(sa.Column("training_stress_score", sa.Float(), nullable=True))
        batch.add_column(sa.Column("intensity_factor", sa.Float(), nullable=True))
        batch.add_column(sa.Column("normalized_power_w", sa.Float(), nullable=True))
        batch.add_column(sa.Column("elevation_gain_m", sa.Float(), nullable=True))
        batch.add_column(sa.Column("elevation_loss_m", sa.Float(), nullable=True))
        batch.add_column(sa.Column("max_hr_bpm", sa.Float(), nullable=True))
        batch.add_column(sa.Column("avg_speed_mps", sa.Float(), nullable=True))
        batch.add_column(sa.Column("moving_duration_s", sa.Float(), nullable=True))
        batch.add_column(sa.Column("moderate_intensity_min", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("vigorous_intensity_min", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("hr_zone_1_s", sa.Float(), nullable=True))
        batch.add_column(sa.Column("hr_zone_2_s", sa.Float(), nullable=True))
        batch.add_column(sa.Column("hr_zone_3_s", sa.Float(), nullable=True))
        batch.add_column(sa.Column("hr_zone_4_s", sa.Float(), nullable=True))
        batch.add_column(sa.Column("hr_zone_5_s", sa.Float(), nullable=True))


def downgrade() -> None:
    """Drop only the new metrics, preserving pre-existing columns and rows through batch mode."""
    with op.batch_alter_table("activity") as batch:
        batch.drop_column("hr_zone_5_s")
        batch.drop_column("hr_zone_4_s")
        batch.drop_column("hr_zone_3_s")
        batch.drop_column("hr_zone_2_s")
        batch.drop_column("hr_zone_1_s")
        batch.drop_column("vigorous_intensity_min")
        batch.drop_column("moderate_intensity_min")
        batch.drop_column("moving_duration_s")
        batch.drop_column("avg_speed_mps")
        batch.drop_column("max_hr_bpm")
        batch.drop_column("elevation_loss_m")
        batch.drop_column("elevation_gain_m")
        batch.drop_column("normalized_power_w")
        batch.drop_column("intensity_factor")
        batch.drop_column("training_stress_score")
        batch.drop_column("training_load")
        batch.drop_column("training_effect_label")
        batch.drop_column("anaerobic_training_effect")
        batch.drop_column("aerobic_training_effect")
