"""run_capture() against a real (local) websockets server speaking the
Upstox wire protocol: binary subscribe frame in, protobuf FeedResponse
frames out, with a mid-stream connection drop. Exercises the real
websockets client stack (max_size, recv cancellation, close handling) --
the closest thing to the live feed that can run without credentials."""
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "data" / "proto"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "diffusion"))

import websockets
from MarketDataFeed_pb2 import Feed, FeedResponse, Quote, initial_feed, live_feed, market_info

import tick_recorder
from diffusion_db import fetch_ticks, get_connection, insert_ticks
from event_calendar import ShockEvent

REFERENCE = {"NSE_FO|C1": {"underlying": "NIFTY", "expiry": "2026-10-13", "strike_paise": 2400000, "option_type": "CE"}}
INDEX_KEYS = {"NSE_INDEX|Nifty 50": "NIFTY"}


def _frame(msg_type, index_ltp=None, bid=None, ask=None):
    response = FeedResponse(type=msg_type)
    if index_ltp is not None:
        feed = Feed()
        feed.fullFeed.indexFF.ltpc.ltp = index_ltp
        response.feeds["NSE_INDEX|Nifty 50"].CopyFrom(feed)
    if bid is not None:
        feed = Feed()
        feed.fullFeed.marketFF.marketLevel.bidAskQuote.append(Quote(bidQ=1, bidP=bid, askQ=1, askP=ask))
        response.feeds["NSE_FO|C1"].CopyFrom(feed)
    return response.SerializeToString()


def test_run_capture_reconnects_drops_stale_snapshot_and_keeps_all_live_ticks(tmp_path, monkeypatch):
    subscriptions = []

    async def server_handler(ws):
        subscribe = json.loads((await ws.recv()).decode("utf-8"))  # must arrive as a binary frame
        subscriptions.append(subscribe)
        n = len(subscriptions)
        await ws.send(_frame(market_info))
        await ws.send(_frame(initial_feed, index_ltp=24000.0 + n, bid=150.0, ask=152.0))  # stale snapshot
        await ws.send(_frame(live_feed, index_ltp=24010.0 + n, bid=151.0, ask=153.0))
        await ws.send(_frame(live_feed, index_ltp=24020.0 + n))
        if n == 1:
            await ws.close(code=1011)  # server-side drop -> client must reconnect
        else:
            await ws.wait_closed()

    async def scenario():
        async with websockets.serve(server_handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            monkeypatch.setattr(tick_recorder, "get_ws_authorized_url", lambda: f"ws://127.0.0.1:{port}")
            shock = datetime.now(timezone.utc) + timedelta(seconds=1)
            event = ShockEvent("RBI MPC Oct 2026", shock.isoformat(), "minute", True, ("NIFTY",), "fixture")
            conn = get_connection(tmp_path / "ws.db")
            stats = await tick_recorder.run_capture(
                event, conn, insert_ticks, pre_seconds=1, post_seconds=3, wait_for_start=False,
                option_reference=REFERENCE, index_keys=INDEX_KEYS,
            )
            return conn, stats

    conn, stats = asyncio.run(asyncio.wait_for(scenario(), timeout=30))

    assert len(subscriptions) >= 2, "client should have reconnected after the server dropped it"
    assert all(s["data"]["mode"] == "full" and s["method"] == "sub" for s in subscriptions)
    assert set(subscriptions[0]["data"]["instrumentKeys"]) == {"NSE_FO|C1", "NSE_INDEX|Nifty 50"}

    spot_prices = [r["price_paise"] for r in fetch_ticks(conn, "RBI MPC Oct 2026", kind="spot")]
    # connection 1 keeps its initial snapshot (24001.0) + 2 live; connection 2 DROPS its snapshot (24002.0)
    assert 2400100 in spot_prices
    assert 2400200 not in spot_prices
    assert {2401100, 2402100, 2401200, 2402200} <= set(spot_prices)
    assert stats.connects >= 2 and stats.messages_by_type["live_feed"] >= 4
    assert stats.ticks_by_kind["option"] >= 2
