from datetime import datetime
from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Float, Index, Integer,
    String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Activity(Base):
    __tablename__ = "activities"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    strava_id = Column(BigInteger, unique=True)  # null for Garmin-only activities
    source = Column(String(20), nullable=False)  # "strava" | "garmin"
    external_id = Column(String(100), nullable=False)
    name = Column(String(255))
    activity_type = Column(String(50))  # Run, TrailRun, VirtualRun, etc.
    start_time = Column(DateTime(timezone=True), nullable=False, index=True)
    elapsed_seconds = Column(Integer)
    moving_seconds = Column(Integer)
    distance_meters = Column(Float)
    elevation_gain_meters = Column(Float)
    average_pace_sec_per_km = Column(Float)
    average_hr = Column(Float)
    max_hr = Column(Float)
    average_cadence = Column(Float)
    average_watts = Column(Float)  # running power, when a power meter is present
    suffer_score = Column(Integer)
    training_load = Column(Float)
    perceived_effort = Column(Integer)  # 1-10, user-supplied
    raw_data = Column(JSONB)
    synced_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_activity_source_external"),
        Index("ix_activities_start_time_type", "start_time", "activity_type"),
    )


class DailyMetric(Base):
    """One row per calendar day — RHR, HRV, sleep, computed training load."""
    __tablename__ = "daily_metrics"

    # Composite PK (id, date): TimescaleDB requires the partitioning column
    # to be part of every unique/primary key on a hypertable.
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(DateTime(timezone=True), primary_key=True, nullable=False, unique=True, index=True)
    resting_hr = Column(Float)
    hrv_ms = Column(Float)
    sleep_hours = Column(Float)
    atl = Column(Float)
    ctl = Column(Float)
    tsb = Column(Float)
    acwr = Column(Float)
    daily_mileage = Column(Float, default=0.0)
    weekly_mileage = Column(Float)
    four_week_mileage = Column(Float)
    computed_at = Column(DateTime(timezone=True), server_default=func.now())


class InjuryAlert(Base):
    __tablename__ = "injury_alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    severity = Column(String(20), nullable=False)  # "elevated" | "high_risk"
    flags = Column(JSONB, nullable=False)
    message = Column(Text)
    sent_via_telegram = Column(Boolean, default=False)
    acknowledged_at = Column(DateTime(timezone=True))


class CoachMessage(Base):
    """Log of every coach↔athlete message for context continuity."""
    __tablename__ = "coach_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    role = Column(String(20), nullable=False)  # "coach" | "athlete"
    message_type = Column(String(50))  # "weekly_plan" | "daily_checkin" | "alert" | "chat" | "recalibration"
    content = Column(Text, nullable=False)
    telegram_message_id = Column(BigInteger)
    week_start = Column(DateTime(timezone=True))


class WeeklyPlan(Base):
    __tablename__ = "weekly_plans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    week_start = Column(DateTime(timezone=True), nullable=False, unique=True)
    generated_at = Column(DateTime(timezone=True), server_default=func.now())
    weeks_to_race = Column(Integer)
    training_phase = Column(String(50))
    target_mileage = Column(Float)
    acwr_at_generation = Column(Float)
    tsb_at_generation = Column(Float)
    predicted_finish_seconds = Column(Integer)
    plan_json = Column(JSONB)
    narrative = Column(Text)
    sent_at = Column(DateTime(timezone=True))


class StravaToken(Base):
    __tablename__ = "strava_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    access_token = Column(String(255))
    refresh_token = Column(String(255))
    expires_at = Column(Integer)  # unix timestamp
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
