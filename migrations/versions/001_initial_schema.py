"""Initial schema with TimescaleDB hypertable for daily_metrics.

Revision ID: 001
Revises:
Create Date: 2026-07-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "activities",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("strava_id", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("external_id", sa.String(100), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("activity_type", sa.String(50), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("elapsed_seconds", sa.Integer(), nullable=True),
        sa.Column("moving_seconds", sa.Integer(), nullable=True),
        sa.Column("distance_meters", sa.Float(), nullable=True),
        sa.Column("elevation_gain_meters", sa.Float(), nullable=True),
        sa.Column("average_pace_sec_per_km", sa.Float(), nullable=True),
        sa.Column("average_hr", sa.Float(), nullable=True),
        sa.Column("max_hr", sa.Float(), nullable=True),
        sa.Column("average_cadence", sa.Float(), nullable=True),
        sa.Column("suffer_score", sa.Integer(), nullable=True),
        sa.Column("training_load", sa.Float(), nullable=True),
        sa.Column("perceived_effort", sa.Integer(), nullable=True),
        sa.Column("raw_data", JSONB(), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "external_id", name="uq_activity_source_external"),
        sa.UniqueConstraint("strava_id"),
    )
    op.create_index("ix_activities_start_time", "activities", ["start_time"])
    op.create_index("ix_activities_start_time_type", "activities", ["start_time", "activity_type"])

    op.create_table(
        "daily_metrics",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resting_hr", sa.Float(), nullable=True),
        sa.Column("hrv_ms", sa.Float(), nullable=True),
        sa.Column("sleep_hours", sa.Float(), nullable=True),
        sa.Column("atl", sa.Float(), nullable=True),
        sa.Column("ctl", sa.Float(), nullable=True),
        sa.Column("tsb", sa.Float(), nullable=True),
        sa.Column("acwr", sa.Float(), nullable=True),
        sa.Column("daily_mileage", sa.Float(), nullable=True, server_default="0.0"),
        sa.Column("weekly_mileage", sa.Float(), nullable=True),
        sa.Column("four_week_mileage", sa.Float(), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        # Composite PK (id, date): TimescaleDB requires the partitioning column
        # to be part of every unique/primary key on a hypertable.
        sa.PrimaryKeyConstraint("id", "date"),
        sa.UniqueConstraint("date"),
    )
    op.create_index("ix_daily_metrics_date", "daily_metrics", ["date"])

    op.create_table(
        "injury_alerts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("flags", JSONB(), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("sent_via_telegram", sa.Boolean(), nullable=True, server_default="false"),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_injury_alerts_created_at", "injury_alerts", ["created_at"])

    op.create_table(
        "coach_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("message_type", sa.String(50), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("week_start", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_coach_messages_created_at", "coach_messages", ["created_at"])

    op.create_table(
        "weekly_plans",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("week_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("weeks_to_race", sa.Integer(), nullable=True),
        sa.Column("training_phase", sa.String(50), nullable=True),
        sa.Column("target_mileage", sa.Float(), nullable=True),
        sa.Column("acwr_at_generation", sa.Float(), nullable=True),
        sa.Column("tsb_at_generation", sa.Float(), nullable=True),
        sa.Column("predicted_finish_seconds", sa.Integer(), nullable=True),
        sa.Column("plan_json", JSONB(), nullable=True),
        sa.Column("narrative", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("week_start"),
    )

    op.create_table(
        "strava_tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("access_token", sa.String(255), nullable=True),
        sa.Column("refresh_token", sa.String(255), nullable=True),
        sa.Column("expires_at", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # TimescaleDB hypertable for time-series queries on daily_metrics.
    # Gracefully skips if TimescaleDB extension is not installed (plain Postgres works too).
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE")
    op.execute(
        "SELECT create_hypertable('daily_metrics', 'date', if_not_exists => TRUE, "
        "migrate_data => TRUE)"
    )


def downgrade() -> None:
    op.drop_table("strava_tokens")
    op.drop_table("weekly_plans")
    op.drop_table("coach_messages")
    op.drop_table("injury_alerts")
    op.drop_table("daily_metrics")
    op.drop_table("activities")
