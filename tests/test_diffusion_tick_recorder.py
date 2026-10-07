import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "data" / "proto"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "models"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "diffusion"))

from MarketDataFeed_pb2 import Feed, FeedResponse, Quote

import tick_recorder
from event_calendar import ShockEvent
from tick_recorder import capture_window, feed_to_tick_row, gap_tick_event_name, process_feed_response, record_gap_tick

REFERENCE = {
    "NSE_FO|C1": {"underlying": "NIFTY", "expiry": "2026-10-07", "strike_paise": 2450000, "option_type": "CE"},
}
INDEX_KEYS = {"NSE_INDEX|Nifty 50": "NIFTY"}
NOW = datetime(2026, 10, 7, 4, 30, 5, tzinfo=timezone.utc)

GAP_EVENT = ShockEvent(
    event_name="CPI July 2026 (released)",
    scheduled_timestamp_utc="2026-08-12T10:30:00+00:00",
    timing_confidence="approximate",
    market_hours_event=False,
    affected_underlyings=("NIFTY", "BANKNIFTY"),
    source_note="test fixture",
)


def _option_feed(bid, ask) -> Feed:
    feed = Feed()
    feed.fullFeed.marketFF.marketLevel.bidAskQuote.append(Quote(bidQ=10, bidP=bid, askQ=10, askP=ask))
    return feed


def _index_feed(ltp) -> Feed:
    feed = Feed()
    feed.fullFeed.indexFF.ltpc.ltp = ltp
    return feed


# --- feed_to_tick_row ---

def test_index_feed_produces_spot_row():
    row = feed_to_tick_row("NSE_INDEX|Nifty 50", _index_feed(24334.55), REFERENCE, INDEX_KEYS, "RBI MPC Oct 2026", NOW)
    assert row["kind"] == "spot"
    assert row["underlying"] == "NIFTY"
    assert row["price_paise"] == 2433455
    assert row["timestamp_utc"] == NOW.isoformat()


def test_index_feed_zero_ltp_skipped():
    assert feed_to_tick_row("NSE_INDEX|Nifty 50", _index_feed(0.0), REFERENCE, INDEX_KEYS, "e", NOW) is None


def test_option_feed_produces_option_row_at_mid_price():
    row = feed_to_tick_row("NSE_FO|C1", _option_feed(bid=150.0, ask=152.0), REFERENCE, INDEX_KEYS, "e", NOW)
    assert row["kind"] == "option"
    assert row["strike_paise"] == 2450000
    assert row["option_type"] == "CE"
    assert row["price_paise"] == 15100  # mid of 150.0/152.0 = 151.0


def test_option_feed_one_sided_quote_skipped():
    # Same BUG-5 lesson as ws_stream.py's apply_option_feed, independently
    # re-applied here: never fabricate a price from a missing side.
    assert feed_to_tick_row("NSE_FO|C1", _option_feed(bid=0.0, ask=152.0), REFERENCE, INDEX_KEYS, "e", NOW) is None


def test_unknown_instrument_key_returns_none():
    assert feed_to_tick_row("NSE_FO|UNKNOWN", _option_feed(150.0, 152.0), REFERENCE, INDEX_KEYS, "e", NOW) is None


def test_option_feed_no_depth_returns_none():
    feed = Feed()
    feed.fullFeed.marketFF.oi = 100  # touch marketFF without adding bidAskQuote depth
    assert feed_to_tick_row("NSE_FO|C1", feed, REFERENCE, INDEX_KEYS, "e", NOW) is None


# --- process_feed_response ---

def test_process_feed_response_extracts_multiple_rows():
    response = FeedResponse()
    response.feeds["NSE_FO|C1"].CopyFrom(_option_feed(150.0, 152.0))
    response.feeds["NSE_INDEX|Nifty 50"].CopyFrom(_index_feed(24334.55))
    rows = process_feed_response(response, REFERENCE, INDEX_KEYS, "RBI MPC Oct 2026", NOW)
    assert {r["kind"] for r in rows} == {"option", "spot"}


def test_process_feed_response_skips_unusable_messages():
    response = FeedResponse()
    response.feeds["NSE_FO|C1"].CopyFrom(_option_feed(0.0, 152.0))  # one-sided, dropped
    rows = process_feed_response(response, REFERENCE, INDEX_KEYS, "e", NOW)
    assert rows == []


# --- capture_window ---

def _event(scheduled_iso):
    return ShockEvent(
        event_name="RBI MPC Oct 2026",
        scheduled_timestamp_utc=scheduled_iso,
        timing_confidence="minute",
        market_hours_event=True,
        affected_underlyings=("NIFTY", "BANKNIFTY"),
        source_note="test fixture",
    )


def test_capture_window_default_offsets():
    shock = datetime(2026, 10, 7, 4, 30, tzinfo=timezone.utc)
    start, end = capture_window(_event(shock.isoformat()))
    assert start == shock - timedelta(seconds=600)
    assert end == shock + timedelta(seconds=1800)


def test_capture_window_custom_offsets():
    shock = datetime(2026, 10, 7, 4, 30, tzinfo=timezone.utc)
    start, end = capture_window(_event(shock.isoformat()), pre_seconds=60, post_seconds=120)
    assert start == shock - timedelta(seconds=60)
    assert end == shock + timedelta(seconds=120)


# --- gap_tick_event_name / record_gap_tick (Phase 5: after-hours events) ---

def test_gap_tick_event_name_tags_with_label():
    assert gap_tick_event_name(GAP_EVENT, "pre_close") == "CPI July 2026 (released) [pre_close]"


def _fake_chain(spot, strike, bid, ask, iv=12.5, instrument_key="NSE_FO|CALLKEY"):
    return [
        {
            "strike_price": strike,
            "underlying_spot_price": spot,
            "call_options": {
                "instrument_key": instrument_key,
                "market_data": {"bid_price": bid, "ask_price": ask},
                "option_greeks": {"iv": iv},
            },
        }
    ]


def test_record_gap_tick_writes_spot_and_option_rows_for_both_indices(monkeypatch):
    def fake_chain(key, expiry):
        return _fake_chain(spot=24000.0 if key == tick_recorder.NIFTY else 51000.0, strike=24000.0 if key == tick_recorder.NIFTY else 51000.0, bid=150.0, ask=152.0)

    monkeypatch.setattr(tick_recorder, "list_expiries", lambda key: ["2026-08-14"])
    monkeypatch.setattr(tick_recorder, "get_option_chain", fake_chain)

    inserted = []
    rows = record_gap_tick(GAP_EVENT, "pre_close", conn=None, insert_ticks_fn=lambda conn, rows: inserted.extend(rows))

    assert rows == inserted
    assert {r["underlying"] for r in rows} == {"NIFTY", "BANKNIFTY"}
    assert {r["kind"] for r in rows} == {"spot", "option"}
    assert all(r["event_name"] == "CPI July 2026 (released) [pre_close]" for r in rows)

    nifty_spot = next(r for r in rows if r["underlying"] == "NIFTY" and r["kind"] == "spot")
    assert nifty_spot["price_paise"] == 2400000

    nifty_option = next(r for r in rows if r["underlying"] == "NIFTY" and r["kind"] == "option")
    assert nifty_option["price_paise"] == 15100  # mid of 150.0/152.0
    assert nifty_option["option_type"] == "CE"


def test_record_gap_tick_skips_option_leg_without_two_sided_quote(monkeypatch):
    # Same BUG-5-style discipline as feed_to_tick_row: never fabricate a
    # price from a missing bid or ask.
    monkeypatch.setattr(tick_recorder, "list_expiries", lambda key: ["2026-08-14"])
    monkeypatch.setattr(tick_recorder, "get_option_chain", lambda key, expiry: _fake_chain(spot=24000.0, strike=24000.0, bid=None, ask=None))

    rows = record_gap_tick(GAP_EVENT, "post_open", conn=None, insert_ticks_fn=lambda conn, rows: None)
    assert all(r["kind"] != "option" for r in rows)
    assert any(r["kind"] == "spot" for r in rows)


def test_record_gap_tick_skips_underlying_with_no_expiries(monkeypatch):
    monkeypatch.setattr(tick_recorder, "list_expiries", lambda key: [] if key == tick_recorder.BANKNIFTY else ["2026-08-14"])
    monkeypatch.setattr(tick_recorder, "get_option_chain", lambda key, expiry: _fake_chain(spot=24000.0, strike=24000.0, bid=150.0, ask=152.0))

    rows = record_gap_tick(GAP_EVENT, "pre_close", conn=None, insert_ticks_fn=lambda conn, rows: None)
    assert all(r["underlying"] == "NIFTY" for r in rows)


# --- streaming loop (fake websocket; BUG-4 / BUG-5 regressions) ---

import asyncio

import pytest
import websockets.exceptions
from MarketDataFeed_pb2 import initial_feed, live_feed, market_info

from tick_recorder import CaptureStats, _connect_and_record, seconds_until

FAR_FUTURE = datetime.now(timezone.utc) + timedelta(hours=1)


class FakeWS:
    """Replays encoded messages through recv(); when exhausted either drops
    the connection (ConnectionClosed, like a real network drop) or blocks."""

    def __init__(self, messages, drop_at_end=True):
        self.messages = list(messages)
        self.sent = []
        self.drop_at_end = drop_at_end

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        if self.messages:
            return self.messages.pop(0)
        if self.drop_at_end:
            raise websockets.exceptions.ConnectionClosedError(None, None)
        await asyncio.sleep(3600)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _encode(feed_type, feeds):
    response = FeedResponse(type=feed_type)
    for key, feed in feeds.items():
        response.feeds[key].CopyFrom(feed)
    return response.SerializeToString()


def _run(coro):
    return asyncio.run(coro)


def _record(ws, stop_at=FAR_FUTURE, **kwargs):
    inserted = []
    stats = CaptureStats()
    coro = _connect_and_record(
        REFERENCE, INDEX_KEYS, "e", stop_at, conn=None,
        insert_ticks_fn=lambda conn, rows: inserted.extend(rows),
        stats=stats, connect_fn=lambda url: ws, url_fn=lambda: "wss://fake", **kwargs,
    )
    return coro, inserted, stats


def test_buffered_ticks_flushed_when_connection_drops():
    # BUG-4: ticks sitting in the buffer (< flush_every) used to be lost
    # when the connection raised.
    ws = FakeWS([_encode(live_feed, {"NSE_INDEX|Nifty 50": _index_feed(24334.55)})])
    coro, inserted, stats = _record(ws, flush_every=1000, flush_interval_seconds=3600)
    with pytest.raises(websockets.exceptions.ConnectionClosed):
        _run(coro)
    assert [r["kind"] for r in inserted] == ["spot"]
    assert stats.ticks == 1 and stats.messages_by_type == {"live_feed": 1}


def test_subscribe_message_sent_with_valid_mode():
    import json

    ws = FakeWS([], drop_at_end=True)
    coro, _, _ = _record(ws)
    with pytest.raises(websockets.exceptions.ConnectionClosed):
        _run(coro)
    sent = json.loads(ws.sent[0].decode("utf-8"))
    assert sent["data"]["mode"] == "full"
    assert set(sent["data"]["instrumentKeys"]) == {"NSE_FO|C1", "NSE_INDEX|Nifty 50"}


def test_initial_feed_dropped_on_reconnect_but_kept_on_first_connect():
    # BUG-5: a reconnect's initial_feed snapshot is stale state stamped with
    # the reconnect time -- it must not become a fake post-shock tick.
    snapshot = _encode(initial_feed, {"NSE_INDEX|Nifty 50": _index_feed(24000.0)})
    live = _encode(live_feed, {"NSE_INDEX|Nifty 50": _index_feed(24010.0)})

    coro, inserted, _ = _record(FakeWS([snapshot, live]), skip_initial_feed=True)
    with pytest.raises(websockets.exceptions.ConnectionClosed):
        _run(coro)
    assert [r["price_paise"] for r in inserted] == [2401000]

    coro, inserted, _ = _record(FakeWS([snapshot, live]), skip_initial_feed=False)
    with pytest.raises(websockets.exceptions.ConnectionClosed):
        _run(coro)
    assert [r["price_paise"] for r in inserted] == [2400000, 2401000]


def test_market_info_messages_counted_but_produce_no_rows():
    ws = FakeWS([_encode(market_info, {})])
    coro, inserted, stats = _record(ws)
    with pytest.raises(websockets.exceptions.ConnectionClosed):
        _run(coro)
    assert inserted == []
    assert stats.messages_by_type == {"market_info": 1} and stats.ticks == 0


def test_stops_at_window_end_even_when_feed_is_silent():
    # recv() blocks forever; the loop must still exit when stop_at passes.
    ws = FakeWS([], drop_at_end=False)
    coro, _, _ = _record(ws, stop_at=datetime.now(timezone.utc) + timedelta(seconds=0.3), flush_interval_seconds=0.1)
    _run(asyncio.wait_for(coro, timeout=5))  # would raise TimeoutError if it never stopped


def test_exchange_timestamp_and_expiry_recorded():
    feed = _option_feed(150.0, 152.0)
    feed.fullFeed.marketFF.ltpc.ltt = 1791345600000
    row = feed_to_tick_row("NSE_FO|C1", feed, REFERENCE, INDEX_KEYS, "e", NOW)
    assert row["exchange_ts_ms"] == 1791345600000
    assert row["expiry"] == "2026-10-07"


def test_seconds_until():
    now = datetime(2026, 10, 7, 4, 0, tzinfo=timezone.utc)
    assert seconds_until(now + timedelta(seconds=90), now) == 90
    assert seconds_until(now - timedelta(seconds=5), now) == -5
