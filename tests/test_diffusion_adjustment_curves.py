import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "diffusion"))

from adjustment_curves import normalized_price_path, time_to_90pct_adjustment

SHOCK = datetime(2026, 10, 7, 4, 30, tzinfo=timezone.utc)


def _ramp_series(pre_level, new_level, ramp_seconds, n_post=20, step_s=5):
    ticks = [
        ((SHOCK - timedelta(seconds=30)).isoformat(), pre_level),
        ((SHOCK - timedelta(seconds=10)).isoformat(), pre_level),
    ]
    for i in range(n_post):
        t = SHOCK + timedelta(seconds=i * step_s)
        offset = i * step_s
        progress = min(offset / ramp_seconds, 1.0) if ramp_seconds > 0 else 1.0
        price = pre_level + progress * (new_level - pre_level)
        ticks.append((t.isoformat(), price))
    return ticks


def test_time_to_90pct_adjustment_matches_linear_ramp():
    ticks = _ramp_series(pre_level=24000.0, new_level=24100.0, ramp_seconds=100)
    t90 = time_to_90pct_adjustment(ticks, SHOCK.isoformat(), pre_level=24000.0, settle_seconds_after=1800)
    assert t90 is not None
    assert 85 <= t90 <= 100  # linear ramp to 100% at t=100s -> 90% crossed at t=90s (step=5s tolerance)


def test_time_to_90pct_adjustment_zero_when_already_flat():
    ticks = [
        ((SHOCK - timedelta(seconds=10)).isoformat(), 24000.0),
        (SHOCK.isoformat(), 24000.0),
        ((SHOCK + timedelta(seconds=10)).isoformat(), 24000.0),
    ]
    t90 = time_to_90pct_adjustment(ticks, SHOCK.isoformat(), pre_level=24000.0)
    assert t90 == 0.0


def test_time_to_90pct_adjustment_none_without_post_shock_data():
    ticks = [((SHOCK - timedelta(seconds=10)).isoformat(), 24000.0)]
    t90 = time_to_90pct_adjustment(ticks, SHOCK.isoformat(), pre_level=24000.0)
    assert t90 is None


def test_normalized_price_path_scales_and_shifts_by_shock_time():
    ticks = [(SHOCK.isoformat(), 24000.0), ((SHOCK + timedelta(seconds=5)).isoformat(), 24120.0)]
    path = normalized_price_path(ticks, SHOCK.isoformat(), pre_level=24000.0)
    assert path == [(0.0, 1.0), (5.0, pytest.approx(1.005))]


def test_normalized_price_path_sorted_regardless_of_input_order():
    ticks = [
        ((SHOCK + timedelta(seconds=10)).isoformat(), 24200.0),
        (SHOCK.isoformat(), 24000.0),
        ((SHOCK + timedelta(seconds=5)).isoformat(), 24100.0),
    ]
    path = normalized_price_path(ticks, SHOCK.isoformat(), pre_level=24000.0)
    offsets = [o for o, _ in path]
    assert offsets == sorted(offsets)


def test_normalized_price_path_rejects_zero_pre_level():
    with pytest.raises(ValueError):
        normalized_price_path([(SHOCK.isoformat(), 100.0)], SHOCK.isoformat(), pre_level=0)
