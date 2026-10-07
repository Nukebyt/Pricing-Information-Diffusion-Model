import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "data" / "proto"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "diffusion"))

from analyze_event import (
    PRIMARY,
    analyze_after_hours_event,
    analyze_market_hours_event,
    feed_delay_stats,
    format_report,
    pick_atm_basket,
    pick_atm_legs,
    render_charts,
    write_result,
)
from diffusion_db import fetch_ticks, get_connection, insert_ticks
from event_calendar import ShockEvent
from tick_recorder import gap_tick_event_name

SHOCK = datetime(2026, 10, 7, 4, 30, tzinfo=timezone.utc)
EVENT = ShockEvent(
    event_name="RBI MPC Oct 2026",
    scheduled_timestamp_utc=SHOCK.isoformat(),
    timing_confidence="minute",
    market_hours_event=True,
    affected_underlyings=("NIFTY", "BANKNIFTY"),
    source_note="test fixture",
)


def _rows(event_name, key, kind, underlying, base, jump_to, jump_at_s, noise, strike=None, option_type=None,
          expiry=None, iv=None, n_pre=12, step=5):
    """n_pre pre-shock ticks (alternating +/-noise) and 24 post-shock ticks, `step` s apart.
    iv=(base_iv, jump_iv) adds an IV series that jumps at the same moment as the price.
    Exchange timestamps are arrival minus 0.4s."""
    rows = []
    for i in range(-n_pre, 24):
        t = SHOCK + timedelta(seconds=i * step)
        offset = i * step
        jumped = offset >= jump_at_s
        price = (jump_to if jumped else base) + (noise if i % 2 == 0 else -noise)
        row = {
            "event_name": event_name, "instrument_key": key, "kind": kind, "underlying": underlying,
            "strike_paise": strike, "option_type": option_type, "timestamp_utc": t.isoformat(),
            "price_paise": int(price), "iv": None, "expiry": expiry,
            "exchange_ts_ms": int((t.timestamp() - 0.4) * 1000),
        }
        if iv:
            row["iv"] = (iv[1] if jumped else iv[0]) + (0.1 if i % 2 == 0 else -0.1)
        rows.append(row)
    return rows


@pytest.fixture
def conn(tmp_path):
    c = get_connection(tmp_path / "t.db")
    name = EVENT.event_name
    rows = []
    rows += _rows(name, "NIFTY_IDX", "spot", "NIFTY", 2_400_000, 2_410_000, 10, 500)
    rows += _rows(name, "BANK_IDX", "spot", "BANKNIFTY", 5_100_000, 5_120_000, 25, 1000)
    # futures lead spot: they jump at +5s, spot at +10s -> spot-minus-future = -5s
    rows += _rows(name, "NIFTY_FUT", "future", "NIFTY", 2_401_000, 2_411_000, 5, 500, expiry="2026-10-27")
    rows += _rows(name, "BANK_FUT", "future", "BANKNIFTY", 5_101_000, 5_121_000, 20, 1000, expiry="2026-10-27")
    # the ATM call (nearest expiry, strike 24000) reacts at +40s -> spot led by ~30s; IV moves with it
    rows += _rows(name, "NIFTY_24000_CE_E1", "option", "NIFTY", 15_000, 18_000, 40, 50, 2_400_000, "CE", "2026-10-13", iv=(12.0, 15.0))
    # decoys: a far strike (same expiry) and the same ATM strike in a later expiry that reacts instantly
    rows += _rows(name, "NIFTY_26000_CE_E1", "option", "NIFTY", 500, 900, 0, 5, 2_600_000, "CE", "2026-10-13")
    rows += _rows(name, "NIFTY_24000_CE_E2", "option", "NIFTY", 30_000, 36_000, 0, 50, 2_400_000, "CE", "2026-10-20")
    rows += _rows(name, "NIFTY_24000_PE_E1", "option", "NIFTY", 14_000, 11_000, 40, 50, 2_400_000, "PE", "2026-10-13")
    # BANKNIFTY options so the fixture is complete
    rows += _rows(name, "BANK_51000_CE_E1", "option", "BANKNIFTY", 20_000, 24_000, 40, 50, 5_100_000, "CE", "2026-10-27")
    rows += _rows(name, "BANK_51000_PE_E1", "option", "BANKNIFTY", 19_000, 15_000, 40, 50, 5_100_000, "PE", "2026-10-27")
    insert_ticks(c, rows)
    return c


def test_pick_atm_legs_prefers_nearest_expiry_then_closest_strike(conn):
    rows = fetch_ticks(conn, EVENT.event_name, kind="option", underlying="NIFTY")
    legs = pick_atm_legs(rows, 2_400_500, SHOCK)
    assert legs["CE"]["instrument_key"] == "NIFTY_24000_CE_E1"
    assert legs["PE"]["instrument_key"] == "NIFTY_24000_PE_E1"


def test_pick_atm_legs_falls_back_when_atm_strike_is_illiquid(conn):
    def thin(offset_s):
        return {
            "event_name": EVENT.event_name, "instrument_key": "THIN", "kind": "option", "underlying": "NIFTY",
            "strike_paise": 2_400_500, "option_type": "CE", "timestamp_utc": (SHOCK + timedelta(seconds=offset_s)).isoformat(),
            "price_paise": 100, "iv": None, "expiry": "2026-10-13",
        }

    # a NEARER-money contract, but only 2 pre-shock ticks -> ineligible
    insert_ticks(conn, [thin(-10), thin(-5), thin(1)])
    rows = fetch_ticks(conn, EVENT.event_name, kind="option", underlying="NIFTY")
    assert pick_atm_legs(rows, 2_400_500, SHOCK)["CE"]["instrument_key"] == "NIFTY_24000_CE_E1"


def test_pick_atm_basket_takes_closest_strikes_in_nearest_expiry(conn):
    rows = fetch_ticks(conn, EVENT.event_name, kind="option", underlying="NIFTY")
    basket = pick_atm_basket(rows, 2_400_500, SHOCK, width=2)
    # nearest expiry is 2026-10-13: only the 24000 and 26000 CE exist there; the 10-20 expiry is excluded
    assert [c["instrument_key"] for c in basket["CE"]] == ["NIFTY_24000_CE_E1", "NIFTY_26000_CE_E1"]
    assert all(c["expiry"] == "2026-10-13" for c in basket["CE"])


def test_market_hours_analysis_recovers_injected_lags(conn):
    result = analyze_market_hours_event(conn, EVENT)
    nifty = result["underlyings"]["NIFTY"]
    assert nifty["spot"]["first_move_offset_s"]["level_c1"] == pytest.approx(10, abs=5)
    ce = nifty["options"]["CE"]
    assert ce["instrument_key"] == "NIFTY_24000_CE_E1"
    lag = ce["spot_minus_option_lag_s"]
    for variant in ("level_c1", "level_c3", "returns_c1", "returns_c3", "primary"):
        assert lag[variant] == pytest.approx(30, abs=5), variant
    # a put falls on a rally; the abs z-score still detects it at the same moment
    assert nifty["options"]["PE"]["spot_minus_option_lag_s"]["primary"] == pytest.approx(30, abs=5)
    # NIFTY jumped at +10s, BANKNIFTY at +25s -> NIFTY led by ~15s
    assert result["cross_index_lag_s"]["primary"] == pytest.approx(15, abs=5)
    assert result["primary_variant"] == PRIMARY == "returns_c3"
    assert result["warnings"] == []


def test_future_leads_spot_is_reported_as_negative_spot_minus_future(conn):
    result = analyze_market_hours_event(conn, EVENT)
    lag = result["underlyings"]["NIFTY"]["future"]["spot_minus_future_lag_s"]
    assert lag["primary"] == pytest.approx(-5, abs=3)
    assert result["underlyings"]["BANKNIFTY"]["future"]["spot_minus_future_lag_s"]["primary"] == pytest.approx(-5, abs=3)


def test_missing_futures_is_a_warning_not_a_failure(tmp_path):
    c = get_connection(tmp_path / "nofut.db")
    insert_ticks(c, _rows(EVENT.event_name, "NIFTY_IDX", "spot", "NIFTY", 2_400_000, 2_410_000, 10, 500))
    result = analyze_market_hours_event(c, EVENT)
    assert any("no futures ticks" in w for w in result["warnings"])
    assert "future" not in result["underlyings"]["NIFTY"]


def test_iv_series_analysed_and_lagged_against_spot(conn):
    iv = analyze_market_hours_event(conn, EVENT)["underlyings"]["NIFTY"]["options"]["CE"]["iv"]
    assert iv["first_move_offset_s"]["primary"] == pytest.approx(40, abs=5)
    assert iv["spot_minus_iv_lag_s"]["primary"] == pytest.approx(30, abs=5)


def test_basket_reports_median_over_detected_legs(conn):
    basket = analyze_market_hours_event(conn, EVENT)["underlyings"]["NIFTY"]["basket"]["CE"]
    assert basket["n_legs"] == 2 and basket["n_detected"] == 2
    # 24000 CE reacts at +40 (lag 30); the 26000 decoy reacts at +0 (lag -10) -> median of the two
    assert basket["median_primary_lag_s"] == pytest.approx(10, abs=8)


def test_exchange_time_lag_and_feed_delay_present(conn):
    result = analyze_market_hours_event(conn, EVENT)
    ce = result["underlyings"]["NIFTY"]["options"]["CE"]
    assert ce["spot_minus_option_lag_exchange_time_s"]["primary"] == pytest.approx(30, abs=5)
    assert result["feed_delay"]["NIFTY spot"]["median_s"] == pytest.approx(0.4, abs=0.01)


def test_feed_delay_stats_none_without_exchange_timestamps():
    assert feed_delay_stats([{"timestamp_utc": SHOCK.isoformat(), "exchange_ts_ms": None}]) is None


def test_pre_shock_false_positive_check_is_reported_for_real_events(tmp_path):
    # 60 pre-shock ticks at 10s: pseudo-shock at T-300s has a >=10-tick baseline before it
    c = get_connection(tmp_path / "pre.db")
    insert_ticks(c, _rows(EVENT.event_name, "NIFTY_IDX", "spot", "NIFTY", 2_400_000, 2_410_000, 10, 500, n_pre=60, step=10))
    fp = analyze_market_hours_event(c, EVENT)["false_positive_check"]
    assert fp["kind"] == "pre_shock_pseudo"
    assert fp["by_variant"][PRIMARY] == {"evaluated": 1, "fired": 0}  # quiet pre-shock noise, nothing fires


def test_placebo_event_counts_detections_as_false_positives(tmp_path):
    placebo = ShockEvent("PLACEBO 2026-10-06 10:00 IST", SHOCK.isoformat(), "minute", True, ("NIFTY",), "control")
    c = get_connection(tmp_path / "placebo.db")
    # a quiet series (jump_to == base): nothing should fire
    insert_ticks(c, _rows(placebo.event_name, "NIFTY_IDX", "spot", "NIFTY", 2_400_000, 2_400_000, 10, 500))
    result = analyze_market_hours_event(c, placebo)
    assert result["is_placebo"] and result["false_positive_check"]["kind"] == "control_window"
    assert result["placebo_summary"] == {"evaluated": 1, "fired": 0}
    assert "FALSE POSITIVE" in format_report(result)

    # a series that jumps would register as a false positive in a control window
    c2 = get_connection(tmp_path / "placebo2.db")
    insert_ticks(c2, _rows(placebo.event_name, "NIFTY_IDX", "spot", "NIFTY", 2_400_000, 2_410_000, 10, 500))
    assert analyze_market_hours_event(c2, placebo)["placebo_summary"] == {"evaluated": 1, "fired": 1}


def test_empty_capture_yields_warnings_not_a_crash(tmp_path):
    result = analyze_market_hours_event(get_connection(tmp_path / "empty.db"), EVENT)
    assert any("no spot ticks" in w for w in result["warnings"])
    assert "WARNING" in format_report(result)


def test_insufficient_baseline_reported_as_error_not_exception(tmp_path):
    c = get_connection(tmp_path / "short.db")
    rows = _rows(EVENT.event_name, "NIFTY_IDX", "spot", "NIFTY", 2_400_000, 2_410_000, 10, 500)
    insert_ticks(c, [r for r in rows if datetime.fromisoformat(r["timestamp_utc"]) >= SHOCK - timedelta(seconds=10)])
    result = analyze_market_hours_event(c, EVENT)
    assert "error" in result["underlyings"]["NIFTY"]["spot"]


def test_write_result_and_report_roundtrip(conn, tmp_path):
    result = analyze_market_hours_event(conn, EVENT)
    path = write_result(result, tmp_path)
    assert json.loads(path.read_text(encoding="utf-8"))["event_name"] == EVENT.event_name
    report = format_report(result)
    assert "NIFTY vs BANKNIFTY" in report and "PRIMARY=" in report and "basket CE" in report


def test_render_charts_writes_pngs(conn, tmp_path):
    result = analyze_market_hours_event(conn, EVENT)
    paths = render_charts(conn, EVENT, result, tmp_path)
    assert len(paths) == 4 and all(p.exists() for p in paths)
    assert any(p.name.endswith("adjustment_progress.png") for p in paths)


def test_after_hours_analysis_uses_gap_snapshots(tmp_path):
    cpi = ShockEvent("CPI September 2026", "2026-10-12T10:30:00+00:00", "minute", False, ("NIFTY", "BANKNIFTY"), "fixture")
    c = get_connection(tmp_path / "gap.db")

    def snap(label, nifty, bank):
        name = gap_tick_event_name(cpi, label)
        base = {"kind": "spot", "strike_paise": None, "option_type": None, "timestamp_utc": "2026-10-13T03:50:00+00:00", "iv": None}
        return [
            {**base, "event_name": name, "instrument_key": "n", "underlying": "NIFTY", "price_paise": nifty},
            {**base, "event_name": name, "instrument_key": "b", "underlying": "BANKNIFTY", "price_paise": bank},
        ]

    insert_ticks(c, snap("pre_close", 2_400_000, 5_100_000) + snap("post_open", 2_410_000, 5_100_000))
    result = analyze_after_hours_event(c, cpi)
    assert result["underlyings"]["NIFTY"]["gap_fraction"] == 1.0  # no settled snapshot -> all in the gap
    assert result["underlyings"]["BANKNIFTY"]["gap_fraction"] is None  # flat -> undefined, not fabricated
    assert "WARNING" not in format_report(result)


def test_after_hours_missing_snapshot_is_a_warning(tmp_path):
    cpi = ShockEvent("CPI September 2026", "2026-10-12T10:30:00+00:00", "minute", False, ("NIFTY",), "fixture")
    result = analyze_after_hours_event(get_connection(tmp_path / "none.db"), cpi)
    assert result["underlyings"]["NIFTY"] is None and result["warnings"]
