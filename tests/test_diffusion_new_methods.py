"""Tests for the additions made 2026-10-05 (BUGS.md DEC-10): returns-based
detection, pre-shock placebo, median settled level, pooling statistics,
futures reference, DB migration, and recorder hardening."""
import asyncio
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import websockets.exceptions

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "data" / "proto"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "diffusion"))

from MarketDataFeed_pb2 import Feed, FeedResponse, Quote, live_feed

import reference_table
import tick_recorder
from adjustment_curves import time_to_90pct_adjustment
from diffusion_db import fetch_ticks, get_connection, insert_ticks
from lag_detection import detect_first_move, pre_shock_false_positive, spot_future_lag_seconds
from pooling import bootstrap_median_ci, pool_results, pooled_summary, render_markdown_report, sign_test_p
from tick_recorder import CaptureStats, _connect_and_record, feed_to_tick_row

SHOCK = datetime(2026, 10, 7, 4, 30, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 7, 4, 30, 5, tzinfo=timezone.utc)


def _t(offset_s):
    return (SHOCK + timedelta(seconds=offset_s)).isoformat()


# --- returns-based detector ---------------------------------------------------

def _drifting_series(jump_at, jump=40.0, drift_per_tick=2.0, n_pre=30, n_post=20):
    """A steady upward drift (2/tick) with +/-1 alternating noise, plus a one-off jump at `jump_at` seconds."""
    ticks = []
    for i in range(-n_pre, n_post):
        offset = i * 5
        level = 24000.0 + drift_per_tick * i + (jump if offset >= jump_at else 0.0)
        ticks.append((_t(offset), level + (1.0 if i % 2 == 0 else -1.0)))
    return ticks


def test_level_detector_fires_on_pure_drift_but_returns_detector_does_not():
    # NO jump at all, just drift: the level z-score sees the price walk away from the pre-shock mean
    # and "detects" a move; the returns detector correctly sees nothing abnormal.
    ticks = _drifting_series(jump_at=10_000)
    assert detect_first_move(ticks, SHOCK.isoformat(), method="level").timestamp_utc is not None
    assert detect_first_move(ticks, SHOCK.isoformat(), method="returns").timestamp_utc is None


def test_returns_detector_finds_a_jump_on_top_of_drift_at_the_right_time():
    ticks = _drifting_series(jump_at=30)
    move = detect_first_move(ticks, SHOCK.isoformat(), method="returns")
    assert (datetime.fromisoformat(move.timestamp_utc) - SHOCK).total_seconds() == 30


def test_returns_detector_needs_enough_pre_shock_ticks():
    ticks = _drifting_series(jump_at=30, n_pre=6)  # needs >= 5 + 5 for the default 5-tick window
    with pytest.raises(ValueError):
        detect_first_move(ticks, SHOCK.isoformat(), method="returns")


def test_returns_detector_confirmation_run():
    ticks = _drifting_series(jump_at=30)
    move = detect_first_move(ticks, SHOCK.isoformat(), method="returns", min_consecutive=3)
    assert (datetime.fromisoformat(move.timestamp_utc) - SHOCK).total_seconds() == 30  # first tick of the run


def test_unknown_method_rejected():
    with pytest.raises(ValueError):
        detect_first_move(_drifting_series(30), SHOCK.isoformat(), method="vibes")


def test_spot_future_lag_future_leads_gives_negative():
    spot = _drifting_series(jump_at=30)
    future = _drifting_series(jump_at=10)
    lag = spot_future_lag_seconds(spot, future, SHOCK.isoformat(), method="returns")
    assert lag == pytest.approx(-20, abs=5)


# --- placebo ------------------------------------------------------------------

def test_pre_shock_placebo_quiet_series_does_not_fire_and_excludes_post_shock_move():
    # real jump lands AFTER the shock; the placebo window must not see it
    ticks = []
    for i in range(-80, 20):
        ticks.append((_t(i * 10), 24000.0 + (3.0 if i % 2 == 0 else -3.0) + (500.0 if i >= 0 else 0.0)))
    out = pre_shock_false_positive(ticks, SHOCK.isoformat(), pseudo_offset_seconds=300, method="level")
    assert out["evaluated"] is True and out["fired"] is False


def test_pre_shock_placebo_fires_on_a_genuine_pre_shock_jump():
    ticks = []
    for i in range(-80, 0):
        jump = 500.0 if i * 10 >= -200 else 0.0  # a jump 200s before the real shock, i.e. inside the pseudo window
        ticks.append((_t(i * 10), 24000.0 + (3.0 if i % 2 == 0 else -3.0) + jump))
    out = pre_shock_false_positive(ticks, SHOCK.isoformat(), pseudo_offset_seconds=300, method="level")
    assert out["fired"] is True


def test_pre_shock_placebo_unevaluable_when_baseline_too_short():
    ticks = [(_t(-i), 100.0) for i in range(1, 9)]  # all within 8s of the shock
    assert pre_shock_false_positive(ticks, SHOCK.isoformat())["evaluated"] is False


# --- median settled level ------------------------------------------------------

def test_median_settled_level_ignores_one_noisy_last_tick():
    # series moves 100 -> 200 by +10s and holds; the very last tick is a one-off 400 outlier
    ticks = [(_t(-5), 100.0)] + [(_t(s), 200.0) for s in range(10, 300, 10)] + [(_t(300), 400.0)]
    pre_level = 100.0
    last_tick = time_to_90pct_adjustment(ticks, SHOCK.isoformat(), pre_level, settle_seconds_after=300)
    median_level = time_to_90pct_adjustment(ticks, SHOCK.isoformat(), pre_level, settle_seconds_after=300, settled_level_window_seconds=60)
    assert median_level == 10  # settled at 200 -> 90% reached at the first 200 tick
    # with the last tick as the 'settled' level (400), 90% of the move is only ever reached by the
    # outlier itself, so the answer degenerates to the outlier's own timestamp
    assert last_tick == 300


# --- pooling ------------------------------------------------------------------

def test_sign_test_exact_values():
    assert sign_test_p([1, 1, 1, 1, 1, 1]) == pytest.approx(2 * 0.5**6)
    assert sign_test_p([1, -1, 1, -1]) == 1.0
    assert sign_test_p([0, 0]) is None
    assert sign_test_p([5, 5, 5, 5, 5, -1]) == pytest.approx(2 * (1 + 6) / 64)


def test_bootstrap_ci_is_seeded_and_brackets_the_median():
    values = [3.0, 5.0, 7.0, 9.0, 11.0, 13.0]
    assert bootstrap_median_ci(values) == bootstrap_median_ci(values)
    lo, hi = bootstrap_median_ci(values)
    assert lo <= 8.0 <= hi


def test_pooled_summary_withholds_inference_below_min_n():
    s = pooled_summary([5.0, 8.0, 6.0])
    assert s["n"] == 3 and s["median"] == 6.0
    assert s["median_ci95"] is None and s["sign_test_p"] is None and "descriptive only" in s["note"]
    s6 = pooled_summary([5.0, 8.0, 6.0, 7.0, 9.0, 4.0])
    assert s6["median_ci95"] is not None and s6["sign_test_p"] is not None


def _fake_result(name, lag, placebo=False):
    return {
        "event_name": name, "is_placebo": placebo, "placebo_summary": {"evaluated": 4, "fired": 1},
        "underlyings": {"NIFTY": {"spot": {}, "options": {"CE": {"spot_minus_option_lag_s": {"primary": lag}}}}},
        "cross_index_lag_s": {"primary": None},
    }


def test_pool_results_skips_placebos_and_undefined_lags():
    pooled = pool_results([_fake_result("A", 10.0), _fake_result("B", 20.0), _fake_result("PLACEBO x", 999.0, placebo=True)])
    assert pooled["NIFTY spot vs ATM CE"]["n"] == 2
    assert "NIFTY vs BANKNIFTY" not in pooled  # primary was None in every event


def test_markdown_report_mentions_placebo_and_caveats():
    md = render_markdown_report([_fake_result("RBI MPC Oct 2026", 12.0), _fake_result("PLACEBO 2026-10-06 10:00 IST", 0.0, placebo=True)])
    assert "RBI MPC Oct 2026" in md and "+12.0" in md
    assert "Placebo" in md and "1" in md and "Caveats" in md
    assert "descriptive only" in md


# --- futures reference ----------------------------------------------------------

def _master():
    def fut(key, underlying, expiry_ms):
        return {"segment": "NSE_FO", "instrument_type": "FUT", "instrument_key": key, "underlying_symbol": underlying, "expiry": expiry_ms}

    return [
        fut("N_OLD", "NIFTY", 1_000),                      # already expired
        fut("N_NEAR", "NIFTY", 2_000_000_000_000),
        fut("N_FAR", "NIFTY", 3_000_000_000_000),
        fut("B_NEAR", "BANKNIFTY", 2_000_000_000_000),
        fut("OTHER", "FINNIFTY", 2_000_000_000_000),      # not tracked
        {"segment": "NSE_FO", "instrument_type": "CE", "instrument_key": "OPT", "underlying_symbol": "NIFTY", "expiry": 2_000_000_000_000},
    ]


def test_build_futures_reference_picks_nearest_unexpired_per_underlying():
    ref = reference_table.build_futures_reference(now_ms=1_000_000, fetch_fn=_master)
    assert set(ref) == {"N_NEAR", "B_NEAR"}
    assert ref["N_NEAR"]["underlying"] == "NIFTY" and ref["N_NEAR"]["expiry"].startswith("2033")


def test_build_futures_reference_never_raises_on_download_failure():
    def boom():
        raise OSError("network down")

    assert reference_table.build_futures_reference(fetch_fn=boom) == {}


# --- DB migration ----------------------------------------------------------------

def test_old_schema_database_is_migrated_and_accepts_futures(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(
        """
        CREATE TABLE diffusion_ticks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_name TEXT NOT NULL, instrument_key TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('spot', 'option')), underlying TEXT NOT NULL, strike_paise INTEGER,
            option_type TEXT CHECK (option_type IN ('CE', 'PE') OR option_type IS NULL),
            timestamp_utc TEXT NOT NULL, price_paise INTEGER NOT NULL, iv REAL
        );
        INSERT INTO diffusion_ticks (event_name, instrument_key, kind, underlying, timestamp_utc, price_paise)
        VALUES ('old event', 'k', 'spot', 'NIFTY', '2026-08-26T00:00:00+00:00', 100);
        """
    )
    old.commit()
    old.close()

    conn = get_connection(path)
    assert [r["price_paise"] for r in fetch_ticks(conn, "old event")] == [100]  # data survived the rebuild
    insert_ticks(conn, [{
        "event_name": "e", "instrument_key": "f", "kind": "future", "underlying": "NIFTY", "strike_paise": None,
        "option_type": None, "timestamp_utc": "2026-10-07T04:30:00+00:00", "price_paise": 5, "iv": None, "expiry": "2026-10-27",
    }])
    assert fetch_ticks(conn, "e", kind="future")[0]["expiry"] == "2026-10-27"
    assert get_connection(path) is not None  # re-opening an already-migrated DB is a no-op


# --- recorder: futures, IV, decode errors, stall alert -----------------------------

FUT_REF = {"NSE_FO|FUT1": {"underlying": "NIFTY", "expiry": "2026-10-27"}}
OPT_REF = {"NSE_FO|C1": {"underlying": "NIFTY", "expiry": "2026-10-13", "strike_paise": 2400000, "option_type": "CE"}}
IDX = {"NSE_INDEX|Nifty 50": "NIFTY"}


def _market_feed(bid, ask, iv=0.0, ltt=0):
    feed = Feed()
    feed.fullFeed.marketFF.marketLevel.bidAskQuote.append(Quote(bidQ=1, bidP=bid, askQ=1, askP=ask))
    feed.fullFeed.marketFF.iv = iv
    feed.fullFeed.marketFF.ltpc.ltt = ltt
    return feed


def test_future_decoded_as_future_kind_with_mid_price_and_expiry():
    row = feed_to_tick_row("NSE_FO|FUT1", _market_feed(24000.0, 24001.0, ltt=123), OPT_REF, IDX, "e", NOW, FUT_REF)
    assert row["kind"] == "future" and row["underlying"] == "NIFTY" and row["expiry"] == "2026-10-27"
    assert row["price_paise"] == 2400050 and row["exchange_ts_ms"] == 123 and row["option_type"] is None


def test_future_one_sided_quote_skipped():
    assert feed_to_tick_row("NSE_FO|FUT1", _market_feed(0.0, 24001.0), OPT_REF, IDX, "e", NOW, FUT_REF) is None


def test_option_iv_recorded_and_zero_iv_means_missing():
    assert feed_to_tick_row("NSE_FO|C1", _market_feed(150.0, 152.0, iv=13.7), OPT_REF, IDX, "e", NOW)["iv"] == 13.7
    assert feed_to_tick_row("NSE_FO|C1", _market_feed(150.0, 152.0, iv=0.0), OPT_REF, IDX, "e", NOW)["iv"] is None


class _FakeWS:
    def __init__(self, messages, block_after=False):
        self.messages, self.sent, self.block_after = list(messages), [], block_after

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        if self.messages:
            return self.messages.pop(0)
        if self.block_after:
            await asyncio.sleep(3600)
        raise websockets.exceptions.ConnectionClosedError(None, None)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def test_undecodable_frame_is_counted_and_skipped_not_fatal():
    good = FeedResponse(type=live_feed)
    good.feeds["NSE_INDEX|Nifty 50"].CopyFrom(_index(24000.0))
    ws = _FakeWS([b"\xff\xff\xff not protobuf", good.SerializeToString()])
    inserted, stats = [], CaptureStats()
    coro = _connect_and_record(
        OPT_REF, IDX, "e", datetime.now(timezone.utc) + timedelta(hours=1), None, lambda c, r: inserted.extend(r),
        stats=stats, connect_fn=lambda url: ws, url_fn=lambda: "x",
    )
    with pytest.raises(websockets.exceptions.ConnectionClosed):
        asyncio.run(coro)
    assert stats.decode_errors == 1 and [r["kind"] for r in inserted] == ["spot"]


def _index(ltp):
    feed = Feed()
    feed.fullFeed.indexFF.ltpc.ltp = ltp
    return feed


def test_futures_keys_included_in_subscription_and_stall_alert_fires(caplog):
    import json
    import logging

    caplog.set_level(logging.ERROR, logger="diffusion")
    ws = _FakeWS([], block_after=True)
    stats = CaptureStats()
    stop = datetime.now(timezone.utc) + timedelta(seconds=0.6)
    coro = _connect_and_record(
        OPT_REF, IDX, "e", stop, None, lambda c, r: None, stats=stats, connect_fn=lambda url: ws, url_fn=lambda: "x",
        future_reference=FUT_REF, stall_alert_seconds=0.2, flush_interval_seconds=0.1,
    )
    asyncio.run(asyncio.wait_for(coro, timeout=5))
    keys = json.loads(ws.sent[0].decode("utf-8"))["data"]["instrumentKeys"]
    assert "NSE_FO|FUT1" in keys and "NSE_FO|C1" in keys and "NSE_INDEX|Nifty 50" in keys
    assert stats.stalls == 1  # alerts once per stall, not once per loop iteration
    assert any("STALL" in r.message for r in caplog.records)


# --- dead-link detection + gap censoring (BUG-7, found live 2026-10-06) -------------

def test_silent_dead_link_triggers_reconnect_error_quickly():
    ws = _FakeWS([], block_after=True)  # connected, then total silence
    coro = _connect_and_record(
        OPT_REF, IDX, "e", datetime.now(timezone.utc) + timedelta(hours=1), None, lambda c, r: None,
        connect_fn=lambda url: ws, url_fn=lambda: "x", stale_connection_seconds=0.3, flush_interval_seconds=0.1,
    )
    with pytest.raises(ConnectionError):
        asyncio.run(asyncio.wait_for(coro, timeout=5))


def _series_with_gap(gap_after_offset=10, gap_seconds=600, jump=100.0):
    """Quiet noisy series; the connection drops at gap_after_offset and comes back gap_seconds later at a
    higher price (the move happened INSIDE the gap)."""
    ticks = []
    for i in range(-40, 0):
        ticks.append((_t(i * 2), 24000.0 + (1.0 if i % 2 == 0 else -1.0)))
    for i in range(0, gap_after_offset // 2):
        ticks.append((_t(i * 2), 24000.0 + (1.0 if i % 2 == 0 else -1.0)))
    resume = gap_after_offset + gap_seconds
    for i in range(30):
        ticks.append((_t(resume + i * 2), 24000.0 + jump + (1.0 if i % 2 == 0 else -1.0)))
    return ticks


@pytest.mark.parametrize("method", ["level", "returns"])
def test_move_that_first_appears_after_an_outage_is_censored_not_timed(method):
    ticks = _series_with_gap()
    # without gap awareness the detector "finds" the move at the reconnect time
    naive = detect_first_move(ticks, SHOCK.isoformat(), method=method)
    assert naive.timestamp_utc is not None and not naive.censored
    aware = detect_first_move(ticks, SHOCK.isoformat(), method=method, max_gap_seconds=10.0)
    assert aware.timestamp_utc is None and aware.censored is True


def test_move_before_an_outage_is_still_timed():
    ticks = []
    for i in range(-40, 0):
        ticks.append((_t(i * 2), 24000.0 + (1.0 if i % 2 == 0 else -1.0)))
    for i in range(0, 10):  # real jump at +4s, well before the outage
        ticks.append((_t(i * 2), 24000.0 + (100.0 if i >= 2 else 0.0) + (1.0 if i % 2 == 0 else -1.0)))
    ticks.append((_t(20 + 600), 24100.0))  # then a 10-minute hole
    move = detect_first_move(ticks, SHOCK.isoformat(), method="level", max_gap_seconds=10.0)
    assert move.censored is False and (datetime.fromisoformat(move.timestamp_utc) - SHOCK).total_seconds() == 4


def test_analysis_flags_outage_at_shock_and_excludes_censored_from_placebo_counts(tmp_path):
    from analyze_event import analyze_market_hours_event
    from event_calendar import ShockEvent

    event = ShockEvent("PLACEBO 2026-10-06 10:30 IST", SHOCK.isoformat(), "minute", True, ("NIFTY",), "control")
    conn = get_connection(tmp_path / "gap.db")
    rows = [
        {"event_name": event.event_name, "instrument_key": "NIFTY_IDX", "kind": "spot", "underlying": "NIFTY",
         "strike_paise": None, "option_type": None, "timestamp_utc": t, "price_paise": int(p * 100), "iv": None}
        for t, p in _series_with_gap()
    ]
    insert_ticks(conn, rows)
    result = analyze_market_hours_event(conn, event)
    assert any("DATA OUTAGE" in w and "AT THE SHOCK" in w for w in result["warnings"])
    assert result["underlyings"]["NIFTY"]["spot"]["censored_by_gap"]["returns_c3"] is True
    assert result["placebo_summary"] == {"evaluated": 0, "fired": 0}  # an outage artifact is not a false positive
