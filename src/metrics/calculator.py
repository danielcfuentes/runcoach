"""Compute ACWR, TSB, ATL, CTL, RHR drift, pace decoupling, cadence trend, and more."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.models import Activity, DailyMetric

METERS_PER_MILE = 1609.344
SEC_PER_KM_TO_SEC_PER_MI = 1.609344

# Pace zone boundaries (sec/km)
EASY_PACE_CUTOFF_SEC_PER_KM = 279.6    # 7:30/mi — slower = easy
TEMPO_PACE_CUTOFF_SEC_PER_KM = 250.0   # ~6:42/mi — faster = tempo/hard
TEMPO_THRESHOLD_SEC_PER_KM = 245.5     # 6:35/mi — race-pace trigger threshold


def _to_miles(meters: float) -> float:
    return meters / METERS_PER_MILE


def _utc(d: date) -> datetime:
    return datetime.combine(d, datetime.min.time()).replace(tzinfo=timezone.utc)


def _sec_per_km_to_pace_str(sec_per_km: float) -> str:
    sec_per_mi = sec_per_km * SEC_PER_KM_TO_SEC_PER_MI
    m = int(sec_per_mi // 60)
    s = int(sec_per_mi % 60)
    return f"{m}:{s:02d}/mi"


class WorkloadSnapshot(NamedTuple):
    date: date
    acute_miles: float    # rolling 7-day
    chronic_miles: float  # rolling 28-day
    acwr: float
    atl: float            # 7-day EWM training load
    ctl: float            # 42-day EWM training load
    tsb: float            # CTL - ATL


async def _mileage_by_day(db: AsyncSession, start: date, end: date) -> dict[date, float]:
    start_dt = _utc(start)
    end_dt = _utc(end) + timedelta(days=1)

    result = await db.execute(
        select(
            func.date_trunc("day", Activity.start_time).label("day"),
            func.sum(Activity.distance_meters).label("total_meters"),
        )
        .where(Activity.start_time >= start_dt)
        .where(Activity.start_time < end_dt)
        .where(Activity.activity_type.in_(["Run", "TrailRun", "VirtualRun", "Treadmill"]))
        .group_by("day")
    )
    rows = result.all()
    return {r.day.date(): _to_miles(r.total_meters or 0) for r in rows}


async def compute_workload(db: AsyncSession, target_date: date | None = None) -> WorkloadSnapshot:
    if target_date is None:
        target_date = date.today()

    history_start = target_date - timedelta(days=41)
    daily = await _mileage_by_day(db, history_start, target_date)

    days = [history_start + timedelta(days=i) for i in range((target_date - history_start).days + 1)]
    series = [daily.get(d, 0.0) for d in days]

    acute_window = sum(series[-7:]) if len(series) >= 7 else sum(series)
    chronic_window = sum(series[-28:]) if len(series) >= 28 else sum(series)
    acwr = acute_window / chronic_window if chronic_window > 0 else 0.0

    atl = _ewm(series, tau=7)
    ctl = _ewm(series, tau=42)
    tsb = ctl - atl

    return WorkloadSnapshot(
        date=target_date,
        acute_miles=round(acute_window, 2),
        chronic_miles=round(chronic_window, 2),
        acwr=round(acwr, 3),
        atl=round(atl, 2),
        ctl=round(ctl, 2),
        tsb=round(tsb, 2),
    )


def _ewm(series: list[float], tau: int) -> float:
    """Exponentially weighted mean with time constant tau (days)."""
    if not series:
        return 0.0
    alpha = 2.0 / (tau + 1)
    result = series[0]
    for val in series[1:]:
        result = alpha * val + (1 - alpha) * result
    return result


def _linear_slope(values: list[float]) -> float:
    """Least-squares slope of a sequence."""
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n
    num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    den = sum((i - x_mean) ** 2 for i in range(n))
    return num / den if den != 0 else 0.0


# ---------------------------------------------------------------------------
# RHR
# ---------------------------------------------------------------------------

class RHRStatus(NamedTuple):
    baseline_bpm: float
    recent_avg_bpm: float
    drift_bpm: float
    days_elevated: int
    is_flagged: bool


async def compute_rhr_status(db: AsyncSession, today: date | None = None) -> RHRStatus | None:
    if today is None:
        today = date.today()

    thirty_days_ago = _utc(today - timedelta(days=30))
    two_days_ago = _utc(today - timedelta(days=2))

    result = await db.execute(
        select(DailyMetric.resting_hr)
        .where(DailyMetric.date >= thirty_days_ago)
        .where(DailyMetric.date < two_days_ago)
        .where(DailyMetric.resting_hr.is_not(None))
    )
    baseline_rows = [r[0] for r in result.all()]
    if not baseline_rows:
        return None

    baseline = sum(baseline_rows) / len(baseline_rows)

    result = await db.execute(
        select(DailyMetric.resting_hr, DailyMetric.date)
        .where(DailyMetric.date >= two_days_ago)
        .where(DailyMetric.resting_hr.is_not(None))
        .order_by(DailyMetric.date)
    )
    recent_rows = result.all()
    if not recent_rows:
        return None

    recent_hrr = [r.resting_hr for r in recent_rows]
    recent_avg = sum(recent_hrr) / len(recent_hrr)
    drift = recent_avg - baseline
    days_elevated = sum(1 for r in recent_hrr if r - baseline >= 5)

    return RHRStatus(
        baseline_bpm=round(baseline, 1),
        recent_avg_bpm=round(recent_avg, 1),
        drift_bpm=round(drift, 1),
        days_elevated=days_elevated,
        is_flagged=drift >= 5 and days_elevated >= 2,
    )


# ---------------------------------------------------------------------------
# TSB sustained negative
# ---------------------------------------------------------------------------

async def compute_tsb_sustained_negative_days(db: AsyncSession, today: date | None = None) -> int:
    """Count consecutive days TSB has been negative, working backwards from today."""
    if today is None:
        today = date.today()

    cutoff = _utc(today - timedelta(days=30))

    result = await db.execute(
        select(DailyMetric.date, DailyMetric.tsb)
        .where(DailyMetric.date >= cutoff)
        .where(DailyMetric.tsb.is_not(None))
        .order_by(DailyMetric.date.desc())
    )
    rows = result.all()

    count = 0
    for row in rows:
        if row.tsb < 0:
            count += 1
        else:
            break
    return count


# ---------------------------------------------------------------------------
# Pace decoupling
# ---------------------------------------------------------------------------

class PaceDecouplingStatus(NamedTuple):
    recent_runs: int
    avg_hr_trend_bpm_per_run: float
    is_flagged: bool


async def compute_pace_decoupling(db: AsyncSession, today: date | None = None) -> PaceDecouplingStatus | None:
    """Detect if HR is creeping up on easy runs at similar pace."""
    if today is None:
        today = date.today()

    three_weeks_ago = _utc(today - timedelta(days=21))

    result = await db.execute(
        select(Activity.average_hr, Activity.average_pace_sec_per_km, Activity.start_time)
        .where(Activity.start_time >= three_weeks_ago)
        .where(Activity.average_hr.is_not(None))
        .where(Activity.average_pace_sec_per_km.is_not(None))
        .where(Activity.activity_type.in_(["Run", "TrailRun", "VirtualRun"]))
        .order_by(Activity.start_time)
    )
    runs = result.all()

    easy_runs = [r for r in runs if r.average_pace_sec_per_km > EASY_PACE_CUTOFF_SEC_PER_KM]
    if len(easy_runs) < 3:
        return None

    slope = _linear_slope([r.average_hr for r in easy_runs])

    return PaceDecouplingStatus(
        recent_runs=len(easy_runs),
        avg_hr_trend_bpm_per_run=round(slope, 2),
        is_flagged=slope > 1.5,
    )


# ---------------------------------------------------------------------------
# Cadence trend
# ---------------------------------------------------------------------------

class CadenceTrendStatus(NamedTuple):
    recent_runs: int
    avg_cadence_spm: float
    slope_per_run: float
    is_flagged: bool


async def compute_cadence_trend(db: AsyncSession, today: date | None = None) -> CadenceTrendStatus | None:
    """Detect sustained cadence decrease across recent runs (form breakdown signal)."""
    if today is None:
        today = date.today()

    three_weeks_ago = _utc(today - timedelta(days=21))

    result = await db.execute(
        select(Activity.average_cadence, Activity.start_time)
        .where(Activity.start_time >= three_weeks_ago)
        .where(Activity.average_cadence.is_not(None))
        .where(Activity.average_cadence > 0)
        .where(Activity.activity_type.in_(["Run", "TrailRun", "VirtualRun"]))
        .order_by(Activity.start_time)
    )
    runs = result.all()

    if len(runs) < 4:
        return None

    cadences = [r.average_cadence for r in runs]
    avg = sum(cadences) / len(cadences)
    slope = _linear_slope(cadences)

    return CadenceTrendStatus(
        recent_runs=len(runs),
        avg_cadence_spm=round(avg, 1),
        slope_per_run=round(slope, 2),
        is_flagged=slope < -0.5,  # dropping >0.5 spm per run
    )


# ---------------------------------------------------------------------------
# Consistency gap
# ---------------------------------------------------------------------------

class ConsistencyGapStatus(NamedTuple):
    gap_days: int
    gap_end_date: date
    pre_gap_avg_daily_miles: float
    return_miles: float
    is_flagged: bool


async def compute_consistency_gap(db: AsyncSession, today: date | None = None) -> ConsistencyGapStatus | None:
    """Detect a gap >4 days that ended recently with a return at prior intensity."""
    if today is None:
        today = date.today()

    thirty_days_ago = _utc(today - timedelta(days=30))

    result = await db.execute(
        select(Activity.start_time, Activity.distance_meters)
        .where(Activity.start_time >= thirty_days_ago)
        .where(Activity.activity_type.in_(["Run", "TrailRun", "VirtualRun"]))
        .order_by(Activity.start_time)
    )
    runs = result.all()

    if len(runs) < 2:
        return None

    for i in range(1, len(runs)):
        gap_days = (runs[i].start_time.date() - runs[i - 1].start_time.date()).days
        if gap_days <= 4:
            continue

        gap_end = runs[i].start_time.date()
        if (today - gap_end).days > 7:
            # Only flag gaps that ended in the last 7 days
            continue

        pre_gap_runs = runs[:i]
        return_miles = _to_miles(runs[i].distance_meters or 0)

        # Pre-gap average: total miles / days spanned (not per run)
        pre_span_days = max(1, (runs[i - 1].start_time.date() - runs[0].start_time.date()).days + 1)
        pre_total = sum(_to_miles(r.distance_meters or 0) for r in pre_gap_runs)
        pre_avg_daily = pre_total / pre_span_days

        # Flag if return run covers ≥80% of the pre-gap average daily mileage
        is_flagged = pre_avg_daily > 0 and return_miles >= pre_avg_daily * 0.8

        return ConsistencyGapStatus(
            gap_days=gap_days,
            gap_end_date=gap_end,
            pre_gap_avg_daily_miles=round(pre_avg_daily, 1),
            return_miles=round(return_miles, 1),
            is_flagged=is_flagged,
        )

    return None


# ---------------------------------------------------------------------------
# Volume spike
# ---------------------------------------------------------------------------

async def compute_weekly_volume_spike(
    db: AsyncSession, today: date | None = None
) -> tuple[float, float, str | None]:
    """Returns (this_week_miles, last_week_miles, severity | None)."""
    if today is None:
        today = date.today()

    this_start = today - timedelta(days=6)
    last_start = today - timedelta(days=13)

    daily = await _mileage_by_day(db, last_start, today)

    this_week = sum(daily.get(this_start + timedelta(i), 0) for i in range(7))
    last_week = sum(daily.get(last_start + timedelta(i), 0) for i in range(7))

    if last_week == 0:
        return this_week, last_week, None

    pct_increase = (this_week - last_week) / last_week
    severity = None
    if pct_increase >= 0.30:
        severity = "high_risk"
    elif pct_increase >= 0.20:
        severity = "elevated"

    return round(this_week, 1), round(last_week, 1), severity


# ---------------------------------------------------------------------------
# Calendar week mileage (Mon-Sun, matching how Strava reports weekly totals)
# ---------------------------------------------------------------------------
# Deliberately separate from compute_workload's rolling 7-day acute window and
# compute_weekly_volume_spike's rolling window — those stay rolling-window
# because that's the sports-science standard for ACWR/volume-spike injury-risk
# math, and changing that windowing would change alert sensitivity. This is
# purely a display/comparison stat so Daniel's numbers match what he sees on
# Strava.

class CalendarWeekMileage(NamedTuple):
    week_start: date        # Monday
    this_week_miles: float
    last_week_start: date
    last_week_miles: float


async def compute_calendar_week_mileage(
    db: AsyncSession, today: date | None = None
) -> CalendarWeekMileage:
    if today is None:
        today = date.today()

    this_monday = today - timedelta(days=today.weekday())
    last_monday = this_monday - timedelta(days=7)

    daily = await _mileage_by_day(db, last_monday, today)

    this_week = sum(daily.get(this_monday + timedelta(i), 0.0) for i in range((today - this_monday).days + 1))
    last_week = sum(daily.get(last_monday + timedelta(i), 0.0) for i in range(7))

    return CalendarWeekMileage(
        week_start=this_monday,
        this_week_miles=round(this_week, 2),
        last_week_start=last_monday,
        last_week_miles=round(last_week, 2),
    )


# ---------------------------------------------------------------------------
# Recent paces (for finish time prediction and race-pace trigger)
# ---------------------------------------------------------------------------

class RecentPaces(NamedTuple):
    long_run_pace_sec_per_km: float | None
    tempo_pace_sec_per_km: float | None
    long_run_count: int
    tempo_run_count: int


async def compute_recent_paces(db: AsyncSession, today: date | None = None) -> RecentPaces:
    """Average long-run and tempo paces from the last 28 days."""
    if today is None:
        today = date.today()

    four_weeks_ago = _utc(today - timedelta(days=28))

    result = await db.execute(
        select(Activity.average_pace_sec_per_km, Activity.distance_meters)
        .where(Activity.start_time >= four_weeks_ago)
        .where(Activity.average_pace_sec_per_km.is_not(None))
        .where(Activity.activity_type.in_(["Run", "TrailRun", "VirtualRun"]))
    )
    runs = result.all()

    # Long runs: >14 miles (22.5 km)
    long_runs = [r for r in runs if (r.distance_meters or 0) > 22530]
    # Tempo runs: pace faster than TEMPO_PACE_CUTOFF and distance >5km
    tempo_runs = [r for r in runs
                  if r.average_pace_sec_per_km < TEMPO_PACE_CUTOFF_SEC_PER_KM
                  and (r.distance_meters or 0) > 5000]

    lr_pace = (sum(r.average_pace_sec_per_km for r in long_runs) / len(long_runs)) if long_runs else None
    t_pace = (sum(r.average_pace_sec_per_km for r in tempo_runs) / len(tempo_runs)) if tempo_runs else None

    return RecentPaces(
        long_run_pace_sec_per_km=round(lr_pace, 1) if lr_pace else None,
        tempo_pace_sec_per_km=round(t_pace, 1) if t_pace else None,
        long_run_count=len(long_runs),
        tempo_run_count=len(tempo_runs),
    )


# ---------------------------------------------------------------------------
# Race-pace workout trigger
# ---------------------------------------------------------------------------

async def check_race_pace_trigger(db: AsyncSession, today: date | None = None) -> bool:
    """True if tempo pace ≤6:35/mi (245.5 sec/km) in each of the last 3 weeks."""
    if today is None:
        today = date.today()

    for week_offset in range(3):
        week_end = _utc(today - timedelta(days=week_offset * 7))
        week_start = _utc(today - timedelta(days=week_offset * 7 + 7))

        result = await db.execute(
            select(Activity.average_pace_sec_per_km)
            .where(Activity.start_time >= week_start)
            .where(Activity.start_time < week_end)
            .where(Activity.average_pace_sec_per_km < TEMPO_PACE_CUTOFF_SEC_PER_KM)
            .where(Activity.activity_type.in_(["Run", "TrailRun", "VirtualRun"]))
        )
        tempo_paces = result.scalars().all()

        if not tempo_paces:
            return False
        if sum(tempo_paces) / len(tempo_paces) > TEMPO_THRESHOLD_SEC_PER_KM:
            return False

    return True


# ---------------------------------------------------------------------------
# Persist daily metrics
# ---------------------------------------------------------------------------

async def persist_daily_metrics(db: AsyncSession, today: date | None = None) -> None:
    if today is None:
        today = date.today()

    snap = await compute_workload(db, today)
    daily_miles_map = await _mileage_by_day(db, today, today)
    today_miles = daily_miles_map.get(today, 0.0)

    today_dt = _utc(today)
    result = await db.execute(select(DailyMetric).where(DailyMetric.date == today_dt))
    metric = result.scalar_one_or_none()

    if metric is None:
        metric = DailyMetric(date=today_dt)
        db.add(metric)

    metric.atl = snap.atl
    metric.ctl = snap.ctl
    metric.tsb = snap.tsb
    metric.acwr = snap.acwr
    metric.daily_mileage = today_miles
    metric.weekly_mileage = snap.acute_miles
    metric.four_week_mileage = snap.chronic_miles
    metric.computed_at = datetime.now(timezone.utc)

    await db.flush()
