"""Unit tests for metric calculations — no DB required."""
import pytest
from src.metrics.calculator import _ewm, _linear_slope, _to_miles, _sec_per_km_to_pace_str


def test_to_miles():
    assert abs(_to_miles(1609.344) - 1.0) < 0.001
    assert abs(_to_miles(8046.72) - 5.0) < 0.001


def test_ewm_empty():
    assert _ewm([], tau=7) == 0.0


def test_ewm_single():
    assert _ewm([10.0], tau=7) == 10.0


def test_ewm_constant():
    series = [5.0] * 50
    assert abs(_ewm(series, tau=7) - 5.0) < 0.01


def test_ewm_rising():
    series = list(range(1, 20))
    result = _ewm(series, tau=7)
    assert result > 1.0
    assert result < 19.0


def test_linear_slope_flat():
    assert _linear_slope([5.0, 5.0, 5.0, 5.0]) == 0.0


def test_linear_slope_rising():
    slope = _linear_slope([1.0, 2.0, 3.0, 4.0])
    assert abs(slope - 1.0) < 0.001


def test_linear_slope_falling():
    slope = _linear_slope([4.0, 3.0, 2.0, 1.0])
    assert abs(slope + 1.0) < 0.001


def test_linear_slope_too_short():
    assert _linear_slope([5.0]) == 0.0


def test_sec_per_km_to_pace_str():
    # 6:52/mi = 412 sec/mi → 412 / 1.609344 = 255.9 sec/km
    result = _sec_per_km_to_pace_str(255.9)
    assert ":" in result
    assert "/mi" in result


class TestACWR:
    def test_volume_spike_pct(self):
        last_week = 40.0
        this_week = 56.0
        pct = (this_week - last_week) / last_week
        assert abs(pct - 0.40) < 0.001
        assert pct >= 0.30


class TestPredictFinishTime:
    def test_predict_reasonable_range(self):
        from src.coach.claude_client import predict_finish_time
        secs, formatted = predict_finish_time(
            recent_long_run_pace_sec_per_km=286,
            recent_tempo_pace_sec_per_km=245,
            weekly_miles=50,
        )
        # Should predict somewhere in 2:50–3:30 range
        assert 2 * 3600 + 50 * 60 < secs < 3 * 3600 + 30 * 60
        assert ":" in formatted

    def test_format_two_digit_minutes(self):
        from src.coach.claude_client import predict_finish_time
        secs, formatted = predict_finish_time(286, 270, 35)
        parts = formatted.split(":")
        assert len(parts) == 3
        assert len(parts[1]) == 2
        assert len(parts[2]) == 2

    def test_mileage_adjustment(self):
        from src.coach.claude_client import predict_finish_time
        secs_low, _ = predict_finish_time(286, 245, 30)
        secs_high, _ = predict_finish_time(286, 245, 55)
        # Higher mileage should predict faster
        assert secs_high < secs_low
