import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "diffusion"))

from diffusion_db import get_connection, insert_ticks
from event_calendar import ShockEvent
from gap_diffusion import fetch_gap_price, gap_move_breakdown, summarize_gap_fractions
from tick_recorder import gap_tick_event_name

EVENT = ShockEvent(
    event_name="CPI July 2026 (released)",
    scheduled_timestamp_utc="2026-08-12T10:30:00+00:00",
    timing_confidence="approximate",
    market_hours_event=False,
    affected_underlyings=("NIFTY", "BANKNIFTY"),
    source_note="test fixture",
)


# --- gap_move_breakdown ---

def test_gap_move_breakdown_all_movement_in_the_gap():
    # settled == post_open -> followthrough is zero, the whole move happened overnight
    result = gap_move_breakdown(pre_close_price=24000.0, post_open_price=24120.0, settled_price=24120.0)
    assert result["gap_move"] == 120.0
    assert result["followthrough_move"] == 0.0
    assert result["total_move"] == 120.0
    assert result["gap_fraction"] == pytest.approx(1.0)
    assert result["followthrough_fraction"] == pytest.approx(0.0)


def test_gap_move_breakdown_settled_defaults_to_post_open_when_omitted():
    with_default = gap_move_breakdown(pre_close_price=24000.0, post_open_price=24120.0)
    explicit = gap_move_breakdown(pre_close_price=24000.0, post_open_price=24120.0, settled_price=24120.0)
    assert with_default == explicit
    assert with_default["gap_fraction"] == pytest.approx(1.0)


def test_gap_move_breakdown_movement_continues_after_open():
    # half the eventual move happens in the gap, half continues after open
    result = gap_move_breakdown(pre_close_price=24000.0, post_open_price=24060.0, settled_price=24120.0)
    assert result["gap_fraction"] == pytest.approx(0.5)
    assert result["followthrough_fraction"] == pytest.approx(0.5)


def test_gap_move_breakdown_reversal_after_open_gives_fraction_over_one():
    # opened even further than where it eventually settled, then partially reverted
    result = gap_move_breakdown(pre_close_price=24000.0, post_open_price=24150.0, settled_price=24120.0)
    assert result["gap_fraction"] == pytest.approx(150.0 / 120.0)
    assert result["followthrough_fraction"] == pytest.approx(-30.0 / 120.0)


def test_gap_move_breakdown_zero_total_move_returns_none_fractions_not_fabricated():
    result = gap_move_breakdown(pre_close_price=24000.0, post_open_price=24050.0, settled_price=24000.0)
    assert result["total_move"] == 0.0
    assert result["gap_fraction"] is None
    assert result["followthrough_fraction"] is None


# --- fetch_gap_price / round trip with record_gap_tick()'s tagging convention ---

def _spot_row(event_name, underlying, price_paise):
    return {
        "event_name": event_name,
        "instrument_key": "NSE_INDEX|Nifty 50" if underlying == "NIFTY" else "NSE_INDEX|Nifty Bank",
        "kind": "spot",
        "underlying": underlying,
        "strike_paise": None,
        "option_type": None,
        "timestamp_utc": "2026-08-12T09:30:00+00:00",
        "price_paise": price_paise,
        "iv": None,
    }


def test_fetch_gap_price_round_trips_through_the_same_tagging_convention(tmp_path):
    conn = get_connection(tmp_path / "diffusion_ticks.db")
    tagged_name = gap_tick_event_name(EVENT, "pre_close")
    insert_ticks(conn, [_spot_row(tagged_name, "NIFTY", 2400000)])

    price = fetch_gap_price(conn, EVENT, "pre_close", "NIFTY")
    assert price == 2400000.0


def test_fetch_gap_price_returns_none_when_not_yet_captured(tmp_path):
    conn = get_connection(tmp_path / "diffusion_ticks.db")
    assert fetch_gap_price(conn, EVENT, "post_open", "NIFTY") is None


def test_fetch_gap_price_distinguishes_underlyings(tmp_path):
    conn = get_connection(tmp_path / "diffusion_ticks.db")
    tagged_name = gap_tick_event_name(EVENT, "pre_close")
    insert_ticks(conn, [_spot_row(tagged_name, "NIFTY", 2400000), _spot_row(tagged_name, "BANKNIFTY", 5100000)])

    assert fetch_gap_price(conn, EVENT, "pre_close", "NIFTY") == 2400000.0
    assert fetch_gap_price(conn, EVENT, "pre_close", "BANKNIFTY") == 5100000.0


def test_full_gap_workflow_three_snapshots_to_breakdown(tmp_path):
    conn = get_connection(tmp_path / "diffusion_ticks.db")
    insert_ticks(
        conn,
        [
            _spot_row(gap_tick_event_name(EVENT, "pre_close"), "NIFTY", 2400000),
            _spot_row(gap_tick_event_name(EVENT, "post_open"), "NIFTY", 2406000),
            _spot_row(gap_tick_event_name(EVENT, "post_open_settled"), "NIFTY", 2412000),
        ],
    )
    pre_close = fetch_gap_price(conn, EVENT, "pre_close", "NIFTY")
    post_open = fetch_gap_price(conn, EVENT, "post_open", "NIFTY")
    settled = fetch_gap_price(conn, EVENT, "post_open_settled", "NIFTY")

    result = gap_move_breakdown(pre_close, post_open, settled)
    assert result["gap_fraction"] == pytest.approx(0.5)


# --- summarize_gap_fractions ---

def test_summarize_gap_fractions_basic_stats():
    stats = summarize_gap_fractions([0.5, 0.7, 0.9])
    assert stats["n"] == 3
    assert stats["mean"] == pytest.approx(0.7)
    assert stats["median"] == pytest.approx(0.7)


def test_summarize_gap_fractions_empty():
    assert summarize_gap_fractions([]) == {"n": 0, "mean": None, "median": None, "stdev": None}
