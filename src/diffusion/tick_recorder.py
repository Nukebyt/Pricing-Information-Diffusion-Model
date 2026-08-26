"""Phase 2: high-frequency tick recorder for information-diffusion event
windows.

Uses this project's own copy of the Upstox data layer (src/data/auth.py,
fetch_option_chain.py, reference_table.py, proto/) -- connection plumbing
and protobuf decode shape only, recording every tick with an arrival
timestamp. See ROADMAP.md Phase 2 and BUGS.md.

For an after-hours event (market_hours_event=False, e.g. a CPI release)
there is nothing to stream during the release itself since NSE is closed --
record_gap_tick() instead takes a single REST snapshot, meant to be
triggered at each named point (once near the prior close, once after the
next session opens, optionally once more a few minutes into that session)
by a scheduled job, not run continuously -- naively polling into dead air
just produces frozen, duplicate reads.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data" / "proto"))

from auth import get_ws_authorized_url  # noqa: E402
from fetch_option_chain import BANKNIFTY, NIFTY, get_option_chain, list_expiries  # noqa: E402
from reference_table import build_reference_table, build_subscribe_message  # noqa: E402

from event_calendar import ShockEvent  # noqa: E402

UNDERLYING_KEYS = {NIFTY: "NIFTY", BANKNIFTY: "BANKNIFTY"}


def feed_to_tick_row(
    instrument_key: str,
    feed,
    option_reference: dict[str, dict],
    index_keys: dict[str, str],
    event_name: str,
    received_at_utc: datetime,
) -> dict | None:
    """Pure decode step: one protobuf Feed message -> a flat tick-row dict,
    or None if the message carries nothing usable. A one-sided/missing
    option quote is never fabricated into a price (same BUG-5 discipline as
    ws_stream.py's apply_option_feed, independently re-applied here). Kept
    separate from the async streaming loop so it's directly unit-testable
    against synthetic messages."""
    timestamp_utc = received_at_utc.isoformat()

    if instrument_key in index_keys:
        if feed.fullFeed.WhichOneof("FullFeedUnion") != "indexFF":
            return None
        ltp = feed.fullFeed.indexFF.ltpc.ltp
        if ltp <= 0:
            return None
        return {
            "event_name": event_name,
            "instrument_key": instrument_key,
            "kind": "spot",
            "underlying": index_keys[instrument_key],
            "strike_paise": None,
            "option_type": None,
            "timestamp_utc": timestamp_utc,
            "price_paise": round(ltp * 100),
            "iv": None,
        }

    meta = option_reference.get(instrument_key)
    if meta is None:
        return None
    if feed.fullFeed.WhichOneof("FullFeedUnion") != "marketFF":
        return None
    market = feed.fullFeed.marketFF
    if not market.marketLevel.bidAskQuote:
        return None
    top = market.marketLevel.bidAskQuote[0]
    if top.bidP <= 0 or top.askP <= 0:
        return None

    mid = (top.bidP + top.askP) / 2.0
    return {
        "event_name": event_name,
        "instrument_key": instrument_key,
        "kind": "option",
        "underlying": meta["underlying"],
        "strike_paise": meta["strike_paise"],
        "option_type": meta["option_type"],
        "timestamp_utc": timestamp_utc,
        "price_paise": round(mid * 100),
        "iv": None,
    }


def process_feed_response(
    response,
    option_reference: dict[str, dict],
    index_keys: dict[str, str],
    event_name: str,
    received_at_utc: datetime,
) -> list[dict]:
    """One FeedResponse message (which can carry updates for several
    instruments at once) -> the list of tick rows it produced."""
    rows = []
    for instrument_key, feed in response.feeds.items():
        row = feed_to_tick_row(instrument_key, feed, option_reference, index_keys, event_name, received_at_utc)
        if row is not None:
            rows.append(row)
    return rows


def capture_window(event: ShockEvent, pre_seconds: int = 600, post_seconds: int = 1800) -> tuple[datetime, datetime]:
    """[start, end) UTC window to record around a scheduled event."""
    shock = datetime.fromisoformat(event.scheduled_timestamp_utc)
    return shock - timedelta(seconds=pre_seconds), shock + timedelta(seconds=post_seconds)


async def _connect_and_record(
    option_reference: dict,
    index_keys: dict,
    event_name: str,
    stop_at_utc: datetime,
    conn,
    insert_ticks_fn,
    flush_every: int = 20,
) -> None:
    """One connection attempt: record ticks until stop_at_utc is reached or
    the connection drops (raises on drop -- reconnect policy lives in
    run_capture(), same separation of concerns as ws_stream.py's
    _connect_and_stream/run split)."""
    import websockets

    from MarketDataFeed_pb2 import FeedResponse

    url = get_ws_authorized_url()  # fetched fresh every attempt -- the embedded code is single-use
    all_keys = list(option_reference.keys()) + list(index_keys.keys())
    buffer: list[dict] = []

    async with websockets.connect(url) as ws:
        await ws.send(build_subscribe_message(all_keys))

        async for raw in ws:
            if datetime.now(timezone.utc) >= stop_at_utc:
                break
            if isinstance(raw, str):
                continue

            response = FeedResponse()
            response.ParseFromString(raw)
            received_at = datetime.now(timezone.utc)

            buffer.extend(process_feed_response(response, option_reference, index_keys, event_name, received_at))

            if len(buffer) >= flush_every:
                insert_ticks_fn(conn, buffer)
                buffer = []

    if buffer:
        insert_ticks_fn(conn, buffer)


async def run_capture(
    event: ShockEvent,
    conn,
    insert_ticks_fn,
    pre_seconds: int = 600,
    post_seconds: int = 1800,
    max_reconnects: int | None = None,
) -> None:
    """Top-level entrypoint for a market-hours event: builds the reference
    table once, then records through the capture window, reconnecting on a
    drop (bounded backoff, simpler than ws_stream.run()'s open-ended loop
    since a capture window has a known end time)."""
    import asyncio

    import websockets.exceptions

    option_reference, index_keys = build_reference_table()
    _, stop_at_utc = capture_window(event, pre_seconds, post_seconds)

    backoff_seconds = 1.0
    max_backoff_seconds = 30.0
    attempt = 0

    while datetime.now(timezone.utc) < stop_at_utc and (max_reconnects is None or attempt < max_reconnects):
        attempt += 1
        try:
            await _connect_and_record(
                option_reference, index_keys, event.event_name, stop_at_utc, conn, insert_ticks_fn
            )
            backoff_seconds = 1.0
        except (websockets.exceptions.ConnectionClosed, OSError):
            await asyncio.sleep(backoff_seconds)
            backoff_seconds = min(backoff_seconds * 2, max_backoff_seconds)


def gap_tick_event_name(event: ShockEvent, label: str) -> str:
    """The event_name convention record_gap_tick() rows are tagged under --
    pulled out as its own function so gap_diffusion.py can look the same
    rows back up without duplicating (and risking drifting from) this
    string format."""
    return f"{event.event_name} [{label}]"


def record_gap_tick(event: ShockEvent, label: str, conn, insert_ticks_fn) -> list[dict]:
    """label: e.g. "pre_close", "post_open", or a later "post_open_settled"
    -- a single REST snapshot of NIFTY/BANKNIFTY spot + their nearest-expiry
    ATM call, tagged to this event and label. Meant to be invoked at each
    named point around an after-hours release by a scheduled job, not run
    continuously -- there's nothing to stream while NSE is shut.
    gap_diffusion.py's gap_move_breakdown() consumes exactly these three
    labels to split the eventual move into "happened in the gap" vs.
    "continued once trading resumed"."""
    received_at = datetime.now(timezone.utc).isoformat()
    tagged_event_name = gap_tick_event_name(event, label)
    rows: list[dict] = []

    for underlying_key, underlying_label in UNDERLYING_KEYS.items():
        expiries = list_expiries(underlying_key)
        if not expiries:
            continue
        chain = get_option_chain(underlying_key, expiries[0])
        if not chain:
            continue

        spot = chain[0]["underlying_spot_price"]
        rows.append(
            {
                "event_name": tagged_event_name,
                "instrument_key": underlying_key,
                "kind": "spot",
                "underlying": underlying_label,
                "strike_paise": None,
                "option_type": None,
                "timestamp_utc": received_at,
                "price_paise": round(spot * 100),
                "iv": None,
            }
        )

        atm_row = min(chain, key=lambda row: abs(row["strike_price"] - spot))
        call = atm_row.get("call_options") or {}
        market_data = call.get("market_data") or {}
        bid, ask = market_data.get("bid_price"), market_data.get("ask_price")
        if bid and ask:
            rows.append(
                {
                    "event_name": tagged_event_name,
                    "instrument_key": call.get("instrument_key", ""),
                    "kind": "option",
                    "underlying": underlying_label,
                    "strike_paise": round(atm_row["strike_price"] * 100),
                    "option_type": "CE",
                    "timestamp_utc": received_at,
                    "price_paise": round((bid + ask) / 2.0 * 100),
                    "iv": (call.get("option_greeks") or {}).get("iv"),
                }
            )

    insert_ticks_fn(conn, rows)
    return rows
