import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "diffusion"))

from lag_detection import cross_index_lag_seconds, detect_first_move, lead_lag_seconds, spot_option_lag_seconds, summarize_lags

SHOCK = datetime(2026, 10, 7, 4, 30, tzinfo=timezone.utc)  # 10:00 IST RBI MPC


def _series(baseline_price, noise_amplitude, jump_price, jump_at_offset_s, n_pre=12, step_s=5, n_post=20):
    """Deterministic alternating +/- noise around baseline_price pre-shock
    and around jump_price once offset >= jump_at_offset_s -- gives a
    non-degenerate baseline stdev so z-score thresholding is meaningful,
    with a known, injected first-move offset to check the detector against."""
    ticks = []
    for i in range(n_pre):
        t = SHOCK - timedelta(seconds=(n_pre - i) * step_s)
        noise = noise_amplitude if i % 2 == 0 else -noise_amplitude
        ticks.append((t.isoformat(), baseline_price + noise))
    for i in range(n_post):
        t = SHOCK + timedelta(seconds=i * step_s)
        offset = i * step_s
        noise = noise_amplitude if i % 2 == 0 else -noise_amplitude
        price = (jump_price if offset >= jump_at_offset_s else baseline_price) + noise
        ticks.append((t.isoformat(), price))
    return ticks


def test_detect_first_move_finds_injected_jump():
    ticks = _series(baseline_price=24000.0, noise_amplitude=5.0, jump_price=24100.0, jump_at_offset_s=15)
    move = detect_first_move(ticks, SHOCK.isoformat(), z_threshold=3.0)
    assert move.timestamp_utc is not None
    detected_offset = (datetime.fromisoformat(move.timestamp_utc) - SHOCK).total_seconds()
    assert 10 <= detected_offset <= 20


def test_detect_first_move_raises_on_insufficient_baseline():
    ticks = [(SHOCK.isoformat(), 24000.0)]
    with pytest.raises(ValueError):
        detect_first_move(ticks, SHOCK.isoformat())


def test_detect_first_move_returns_none_for_sub_noise_move():
    # jump magnitude (3) is well within the baseline's noise band (+/-5,
    # stdev=5), so this should never cross a z_threshold=3.0 bar
    ticks = _series(baseline_price=24000.0, noise_amplitude=5.0, jump_price=24003.0, jump_at_offset_s=15)
    move = detect_first_move(ticks, SHOCK.isoformat(), z_threshold=3.0)
    assert move.timestamp_utc is None


def test_detect_first_move_flat_baseline_falls_back_to_any_nonzero_move():
    ticks = [(SHOCK.isoformat(), 24000.0)] + [
        ((SHOCK - timedelta(seconds=n)).isoformat(), 24000.0) for n in range(5, 65, 5)
    ]
    ticks.append(((SHOCK + timedelta(seconds=10)).isoformat(), 24000.5))
    move = detect_first_move(ticks, SHOCK.isoformat())
    assert move.baseline_stdev == 0.0
    assert move.timestamp_utc == (SHOCK + timedelta(seconds=10)).isoformat()


def test_spot_option_lag_detects_known_injected_lag_spot_leads():
    spot_ticks = _series(baseline_price=24000.0, noise_amplitude=5.0, jump_price=24100.0, jump_at_offset_s=10)
    option_ticks = _series(baseline_price=150.0, noise_amplitude=0.5, jump_price=170.0, jump_at_offset_s=40)
    lag = spot_option_lag_seconds(spot_ticks, option_ticks, SHOCK.isoformat())
    assert lag is not None
    assert 20 <= lag <= 40  # true injected difference is 30s


def test_spot_option_lag_negative_when_option_leads():
    spot_ticks = _series(baseline_price=24000.0, noise_amplitude=5.0, jump_price=24100.0, jump_at_offset_s=40)
    option_ticks = _series(baseline_price=150.0, noise_amplitude=0.5, jump_price=170.0, jump_at_offset_s=10)
    lag = spot_option_lag_seconds(spot_ticks, option_ticks, SHOCK.isoformat())
    assert lag is not None
    assert lag < 0


def test_spot_option_lag_none_when_option_never_crosses_threshold():
    spot_ticks = _series(baseline_price=24000.0, noise_amplitude=5.0, jump_price=24100.0, jump_at_offset_s=10)
    option_ticks = _series(baseline_price=150.0, noise_amplitude=0.5, jump_price=150.3, jump_at_offset_s=10)
    lag = spot_option_lag_seconds(spot_ticks, option_ticks, SHOCK.isoformat())
    assert lag is None


def test_lead_lag_seconds_is_the_shared_primitive_behind_spot_option_lag():
    # Same series/offsets as test_spot_option_lag_detects_known_injected_lag_spot_leads --
    # spot_option_lag_seconds() should be a thin wrapper producing an
    # identical result to calling the general primitive directly.
    series_a = _series(baseline_price=24000.0, noise_amplitude=5.0, jump_price=24100.0, jump_at_offset_s=10)
    series_b = _series(baseline_price=150.0, noise_amplitude=0.5, jump_price=170.0, jump_at_offset_s=40)
    assert lead_lag_seconds(series_a, series_b, SHOCK.isoformat()) == spot_option_lag_seconds(
        series_a, series_b, SHOCK.isoformat()
    )


def test_cross_index_lag_positive_when_nifty_leads_banknifty():
    nifty_ticks = _series(baseline_price=24000.0, noise_amplitude=5.0, jump_price=24100.0, jump_at_offset_s=10)
    banknifty_ticks = _series(baseline_price=51000.0, noise_amplitude=10.0, jump_price=51300.0, jump_at_offset_s=35)
    lag = cross_index_lag_seconds(nifty_ticks, banknifty_ticks, SHOCK.isoformat())
    assert lag is not None
    assert 15 <= lag <= 35  # true injected difference is 25s


def test_cross_index_lag_negative_when_banknifty_leads_nifty():
    nifty_ticks = _series(baseline_price=24000.0, noise_amplitude=5.0, jump_price=24100.0, jump_at_offset_s=35)
    banknifty_ticks = _series(baseline_price=51000.0, noise_amplitude=10.0, jump_price=51300.0, jump_at_offset_s=10)
    lag = cross_index_lag_seconds(nifty_ticks, banknifty_ticks, SHOCK.isoformat())
    assert lag is not None
    assert lag < 0


def test_cross_index_lag_none_when_banknifty_never_crosses_threshold():
    nifty_ticks = _series(baseline_price=24000.0, noise_amplitude=5.0, jump_price=24100.0, jump_at_offset_s=10)
    banknifty_ticks = _series(baseline_price=51000.0, noise_amplitude=10.0, jump_price=51005.0, jump_at_offset_s=10)
    assert cross_index_lag_seconds(nifty_ticks, banknifty_ticks, SHOCK.isoformat()) is None


def test_summarize_lags_basic_stats():
    stats = summarize_lags([10.0, 20.0, 30.0])
    assert stats == {"n": 3, "mean": 20.0, "median": 20.0, "stdev": stats["stdev"]}
    assert stats["stdev"] > 0


def test_summarize_lags_empty():
    stats = summarize_lags([])
    assert stats == {"n": 0, "mean": None, "median": None, "stdev": None}


def test_summarize_lags_single_value_zero_stdev():
    stats = summarize_lags([15.0])
    assert stats["n"] == 1
    assert stats["mean"] == 15.0
    assert stats["stdev"] == 0.0


# --- min_consecutive confirmation ---

def _flat_noisy_series_with_spike_then_real_move():
    """Baseline +/-5 around 100; a single-tick spike at +5s that immediately
    reverts, then a real, sustained jump starting at +30s."""
    ticks = []
    for i in range(12):
        t = SHOCK - timedelta(seconds=(12 - i) * 5)
        ticks.append((t.isoformat(), 100.0 + (5.0 if i % 2 == 0 else -5.0)))
    for i in range(12):
        offset = i * 5
        if offset == 5:
            price = 130.0  # lone spike
        elif offset >= 30:
            price = 130.0  # real, sustained move
        else:
            price = 100.0 + (5.0 if i % 2 == 0 else -5.0)
        ticks.append(((SHOCK + timedelta(seconds=offset)).isoformat(), price))
    return ticks


def test_min_consecutive_1_flags_the_lone_spike():
    move = detect_first_move(_flat_noisy_series_with_spike_then_real_move(), SHOCK.isoformat(), min_consecutive=1)
    assert (datetime.fromisoformat(move.timestamp_utc) - SHOCK).total_seconds() == 5


def test_min_consecutive_3_skips_spike_and_reports_start_of_the_real_run():
    move = detect_first_move(_flat_noisy_series_with_spike_then_real_move(), SHOCK.isoformat(), min_consecutive=3)
    # reported timestamp is the FIRST tick of the sustained run, not the 3rd
    assert (datetime.fromisoformat(move.timestamp_utc) - SHOCK).total_seconds() == 30


def test_min_consecutive_must_be_positive():
    with pytest.raises(ValueError):
        detect_first_move(_flat_noisy_series_with_spike_then_real_move(), SHOCK.isoformat(), min_consecutive=0)


def test_lead_lag_threads_min_consecutive_through():
    spot = _series(24000.0, 5.0, 24100.0, jump_at_offset_s=10)
    option = _series(150.0, 0.5, 160.0, jump_at_offset_s=40)
    assert spot_option_lag_seconds(spot, option, SHOCK.isoformat(), min_consecutive=2) == pytest.approx(30, abs=5)
