"""Unit tests for injury risk flag logic."""
import pytest
from datetime import date
from src.metrics.injury_risk import InjuryFlag, RiskAssessment
from src.metrics.calculator import WorkloadSnapshot


def make_snap(acwr: float, tsb: float = 0.0, atl: float = 5.0, ctl: float = 5.0) -> WorkloadSnapshot:
    return WorkloadSnapshot(
        date=date.today(),
        acute_miles=50.0,
        chronic_miles=50.0,
        acwr=acwr,
        atl=atl,
        ctl=ctl,
        tsb=tsb,
    )


def test_no_flags_clear():
    assessment = RiskAssessment(flags=[], overall_severity="clear", workload=make_snap(1.1))
    assert not assessment.should_send_immediate_alert()
    assert assessment.high_risk_count == 0
    assert assessment.elevated_count == 0


def test_single_elevated_no_immediate_alert():
    flag = InjuryFlag(name="acwr_elevated", severity="elevated", description="test")
    assessment = RiskAssessment(flags=[flag], overall_severity="elevated", workload=make_snap(1.35))
    assert not assessment.should_send_immediate_alert()


def test_high_risk_triggers_alert():
    flag = InjuryFlag(name="acwr_high_risk", severity="high_risk", description="test")
    assessment = RiskAssessment(flags=[flag], overall_severity="high_risk", workload=make_snap(1.6))
    assert assessment.should_send_immediate_alert()


def test_two_elevated_triggers_alert():
    flags = [
        InjuryFlag(name="acwr_elevated", severity="elevated", description="test"),
        InjuryFlag(name="tsb_sustained_negative", severity="elevated", description="test"),
    ]
    assessment = RiskAssessment(flags=flags, overall_severity="high_risk", workload=make_snap(1.35))
    assert assessment.should_send_immediate_alert()
    assert assessment.elevated_count == 2


def test_cadence_drop_flag():
    flag = InjuryFlag(name="cadence_drop", severity="elevated", description="test", data={"slope_per_run": -0.8})
    assessment = RiskAssessment(flags=[flag], overall_severity="elevated", workload=make_snap(1.0))
    assert assessment.elevated_count == 1
    assert not assessment.should_send_immediate_alert()


def test_consistency_gap_flag():
    flag = InjuryFlag(
        name="consistency_gap", severity="elevated", description="test",
        data={"gap_days": 6, "return_miles": 8.0, "pre_gap_avg_daily_miles": 7.0}
    )
    assessment = RiskAssessment(flags=[flag], overall_severity="elevated", workload=make_snap(1.0))
    assert assessment.elevated_count == 1


def test_three_elevated_is_high_risk():
    flags = [
        InjuryFlag("acwr_elevated", "elevated", "test"),
        InjuryFlag("cadence_drop", "elevated", "test"),
        InjuryFlag("rhr_drift", "elevated", "test"),
    ]
    assessment = RiskAssessment(flags=flags, overall_severity="high_risk", workload=make_snap(1.3))
    assert assessment.should_send_immediate_alert()
    assert assessment.elevated_count == 3
    assert assessment.high_risk_count == 0


def test_tsb_sustained_flag_name():
    flag = InjuryFlag(
        name="tsb_sustained_negative", severity="elevated", description="TSB negative 12 days",
        data={"consecutive_negative_days": 12}
    )
    assert flag.name == "tsb_sustained_negative"
    assert flag.data["consecutive_negative_days"] == 12
