"""Injury risk classifier — rule-based, tunable thresholds."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.data.models import InjuryAlert
from src.metrics.calculator import (
    WorkloadSnapshot,
    compute_cadence_trend,
    compute_consistency_gap,
    compute_pace_decoupling,
    compute_rhr_status,
    compute_tsb_sustained_negative_days,
    compute_weekly_volume_spike,
    compute_workload,
)


@dataclass
class InjuryFlag:
    name: str
    severity: str  # "elevated" | "high_risk"
    description: str
    data: dict = field(default_factory=dict)


@dataclass
class RiskAssessment:
    flags: list[InjuryFlag]
    overall_severity: str  # "clear" | "elevated" | "high_risk"
    workload: WorkloadSnapshot

    @property
    def high_risk_count(self) -> int:
        return sum(1 for f in self.flags if f.severity == "high_risk")

    @property
    def elevated_count(self) -> int:
        return sum(1 for f in self.flags if f.severity == "elevated")

    def should_send_immediate_alert(self) -> bool:
        return self.high_risk_count >= 1 or self.elevated_count >= 2


async def assess_risk(db: AsyncSession, today: date | None = None) -> RiskAssessment:
    if today is None:
        today = date.today()

    flags: list[InjuryFlag] = []
    workload = await compute_workload(db, today)
    acwr = workload.acwr

    # --- ACWR ---
    if acwr >= settings.acwr_high_risk:
        flags.append(InjuryFlag(
            name="acwr_high_risk",
            severity="high_risk",
            description=(
                f"ACWR is {acwr:.2f} (threshold: {settings.acwr_high_risk}). "
                f"7-day load ({workload.acute_miles:.1f}mi) is too high relative to "
                f"4-week baseline ({workload.chronic_miles:.1f}mi)."
            ),
            data={"acwr": acwr, "acute_miles": workload.acute_miles, "chronic_miles": workload.chronic_miles},
        ))
    elif acwr >= settings.acwr_elevated:
        flags.append(InjuryFlag(
            name="acwr_elevated",
            severity="elevated",
            description=(
                f"ACWR is {acwr:.2f} — approaching elevated zone (threshold: {settings.acwr_elevated}). "
                f"7-day: {workload.acute_miles:.1f}mi, 4-week avg: {workload.chronic_miles:.1f}mi."
            ),
            data={"acwr": acwr, "acute_miles": workload.acute_miles, "chronic_miles": workload.chronic_miles},
        ))

    # --- TSB sustained negative (PRD: negative >10 days triggers deload) ---
    tsb_neg_days = await compute_tsb_sustained_negative_days(db, today)
    if tsb_neg_days >= settings.tsb_negative_days:
        flags.append(InjuryFlag(
            name="tsb_sustained_negative",
            severity="elevated",
            description=(
                f"TSB has been negative for {tsb_neg_days} consecutive days "
                f"(current: {workload.tsb:.1f}). "
                "Accumulated fatigue is outpacing fitness — a deload week is overdue."
            ),
            data={"tsb": workload.tsb, "consecutive_negative_days": tsb_neg_days,
                  "atl": workload.atl, "ctl": workload.ctl},
        ))

    # --- RHR drift ---
    rhr = await compute_rhr_status(db, today)
    if rhr and rhr.is_flagged:
        flags.append(InjuryFlag(
            name="rhr_drift",
            severity="elevated",
            description=(
                f"Resting HR has been {rhr.drift_bpm:.0f} bpm above your 30-day baseline "
                f"({rhr.baseline_bpm:.0f} bpm) for {rhr.days_elevated} days. "
                "Possible overtraining, illness, or under-recovery."
            ),
            data={"baseline_bpm": rhr.baseline_bpm, "recent_avg_bpm": rhr.recent_avg_bpm,
                  "drift_bpm": rhr.drift_bpm, "days_elevated": rhr.days_elevated},
        ))

    # --- Pace-at-effort decoupling ---
    decoupling = await compute_pace_decoupling(db, today)
    if decoupling and decoupling.is_flagged:
        flags.append(InjuryFlag(
            name="pace_decoupling",
            severity="elevated",
            description=(
                f"HR on easy runs trending up +{decoupling.avg_hr_trend_bpm_per_run:.1f} bpm/run "
                f"over the last {decoupling.recent_runs} runs — "
                "aerobic efficiency declining, possible accumulated fatigue."
            ),
            data={"slope_bpm_per_run": decoupling.avg_hr_trend_bpm_per_run,
                  "runs_analyzed": decoupling.recent_runs},
        ))

    # --- Cadence drop (form breakdown / fatigue signal) ---
    cadence = await compute_cadence_trend(db, today)
    if cadence and cadence.is_flagged:
        flags.append(InjuryFlag(
            name="cadence_drop",
            severity="elevated",
            description=(
                f"Average cadence declining {abs(cadence.slope_per_run):.1f} spm/run "
                f"over the last {cadence.recent_runs} runs "
                f"(current avg: {cadence.avg_cadence_spm:.0f} spm). "
                "Sustained cadence loss often signals form breakdown from fatigue."
            ),
            data={"avg_cadence_spm": cadence.avg_cadence_spm,
                  "slope_per_run": cadence.slope_per_run,
                  "runs_analyzed": cadence.recent_runs},
        ))

    # --- Consistency gap (return after layoff at prior intensity) ---
    gap = await compute_consistency_gap(db, today)
    if gap and gap.is_flagged:
        flags.append(InjuryFlag(
            name="consistency_gap",
            severity="elevated",
            description=(
                f"{gap.gap_days}-day gap ended {gap.gap_end_date.strftime('%b %d')}. "
                f"First run back was {gap.return_miles:.1f}mi vs. "
                f"pre-gap avg of {gap.pre_gap_avg_daily_miles:.1f}mi/day — "
                "returning too hard after a layoff significantly raises injury risk."
            ),
            data={"gap_days": gap.gap_days, "gap_end_date": str(gap.gap_end_date),
                  "return_miles": gap.return_miles,
                  "pre_gap_avg_daily_miles": gap.pre_gap_avg_daily_miles},
        ))

    # --- Weekly volume spike ---
    this_week, last_week, spike_severity = await compute_weekly_volume_spike(db, today)
    if spike_severity:
        pct = ((this_week - last_week) / last_week * 100) if last_week > 0 else 0
        flags.append(InjuryFlag(
            name="volume_spike",
            severity=spike_severity,
            description=(
                f"Weekly mileage jumped {pct:.0f}% ({last_week:.1f}mi → {this_week:.1f}mi). "
                + ("High-risk zone (>30%)." if spike_severity == "high_risk"
                   else "Caution zone (>20%).")
            ),
            data={"this_week": this_week, "last_week": last_week, "pct_increase": round(pct, 1)},
        ))

    # --- Derive overall severity ---
    n_elevated = sum(1 for f in flags if f.severity == "elevated")
    n_high = sum(1 for f in flags if f.severity == "high_risk")
    if n_high >= 1 or n_elevated >= 2:
        overall = "high_risk"
    elif n_elevated == 1:
        overall = "elevated"
    else:
        overall = "clear"

    return RiskAssessment(flags=flags, overall_severity=overall, workload=workload)


async def save_alert(db: AsyncSession, assessment: RiskAssessment, message: str) -> InjuryAlert:
    alert = InjuryAlert(
        severity=assessment.overall_severity,
        flags=[{"name": f.name, "severity": f.severity, "description": f.description, "data": f.data}
               for f in assessment.flags],
        message=message,
    )
    db.add(alert)
    await db.flush()
    return alert
