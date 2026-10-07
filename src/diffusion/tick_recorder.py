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

import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data" / "proto"))

from auth import get_ws_authorized_url  # noqa: E402
from fetch_option_chain import BANKNIFTY, NIFTY, get_option_chain, list_expiries  # noqa: E402
from reference_table import build_futures_reference, build_reference_table, build_subscribe_message  # noqa: E402

from event_calendar import ShockEvent  # noqa: E402

UNDERLYING_KEYS = {NIFTY: "NIFTY", BANKNIFTY: "BANKNIFTY"}

log = logging.getLogger("diffusion")

# A full-mode initial_feed snapshot for ~660 instruments can exceed the
# websockets library's default 1 MiB frame cap, which would close the
# connection with code 1009 right after a successful subscribe.
MAX_MESSAGE_BYTES = 16 * 1024 * 1024
FLUSH_EVERY_TICKS = 200
FLUSH_INTERVAL_SECONDS = 1.0
HEARTBEAT_INTERVAL_SECONDS = 30.0
STALL_ALERT_SECONDS = 60.0
# The feed normally delivers several messages per second in market hours.
# If NOTHING arrives for this long the link is dead (a silent network stall
# leaves the socket "open"): reconnect instead of waiting ~40s for the
# keepalive ping to time out (observed live 2026-10-06, BUGS.md BUG-7).
STALE_CONNECTION_SECONDS = 10.0
PING_INTERVAL_SECONDS = 10.0
PING_TIMEOUT_SECONDS = 10.0
MAX_BACKOFF_SECONDS = 5.0  # a capture window is short and unrepeatable: retry fast


def feed_to_tick_row(
    instrument_key: str,
    feed,
    option_reference: dict[str, dict],
    index_keys: dict[str, str],
    event_name: str,
    received_at_utc: datetime,
    future_reference: dict[str, dict] | None = None,
) -> dict | None:
    """Pure decode step: one protobuf Feed message -> a flat tick-row dict,
    or None if the message carries nothing usable. A one-sided/missing
    option quote is never fabricated into a price (same BUG-5 discipline as
    ws_stream.py's apply_option_feed, independently re-applied here). Kept
    separate from the async streaming loop so it's directly unit-testable
    against synthetic messages."""
    timestamp_utc = received_at_utc.isoformat()

    if future_reference and instrument_key in future_reference:
        # Futures leg (control series): same two-sided-mid rule as options.
        if feed.fullFeed.WhichOneof("FullFeedUnion") != "marketFF":
            return None
        market = feed.fullFeed.marketFF
        if not market.marketLevel.bidAskQuote:
            return None
        top = market.marketLevel.bidAskQuote[0]
        if top.bidP <= 0 or top.askP <= 0:
            return None
        meta = future_reference[instrument_key]
        return {
            "event_name": event_name,
            "instrument_key": instrument_key,
            "kind": "future",
            "underlying": meta["underlying"],
            "strike_paise": None,
            "option_type": None,
            "timestamp_utc": timestamp_utc,
            "price_paise": round((top.bidP + top.askP) / 2.0 * 100),
            "iv": None,
            "expiry": meta.get("expiry"),
            "exchange_ts_ms": market.ltpc.ltt or None,
        }

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
            "expiry": None,
            "exchange_ts_ms": feed.fullFeed.indexFF.ltpc.ltt or None,
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
        # Exchange-computed IV from the full-mode feed; 0 means "not
        # provided". Units are whatever Upstox sends (unverified live) --
        # analysis only uses it as a time series, never in absolute terms.
        "iv": market.iv or None,
        "expiry": meta.get("expiry"),
        "exchange_ts_ms": market.ltpc.ltt or None,
    }


def process_feed_response(
    response,
    option_reference: dict[str, dict],
    index_keys: dict[str, str],
    event_name: str,
    received_at_utc: datetime,
    future_reference: dict[str, dict] | None = None,
) -> list[dict]:
    """One FeedResponse message (which can carry updates for several
    instruments at once) -> the list of tick rows it produced."""
    rows = []
    for instrument_key, feed in response.feeds.items():
        row = feed_to_tick_row(
            instrument_key, feed, option_reference, index_keys, event_name, received_at_utc, future_reference
        )
        if row is not None:
            rows.append(row)
    return rows


def capture_window(event: ShockEvent, pre_seconds: int = 600, post_seconds: int = 1800) -> tuple[datetime, datetime]:
    """[start, end) UTC window to record around a scheduled event."""
    shock = datetime.fromisoformat(event.scheduled_timestamp_utc)
    return shock - timedelta(seconds=pre_seconds), shock + timedelta(seconds=post_seconds)


@dataclass
class CaptureStats:
    """Running diagnostics for a capture -- the thing that would have turned
    BUG-3 (silently zero ticks) from a post-mortem into a live alarm."""

    connects: int = 0
    messages: int = 0
    messages_by_type: dict = field(default_factory=dict)
    ticks: int = 0
    ticks_by_kind: dict = field(default_factory=dict)
    last_tick_at_utc: datetime | None = None
    decode_errors: int = 0
    stalls: int = 0

    def summary(self) -> str:
        return (
            f"connects={self.connects} messages={self.messages} by_type={self.messages_by_type} "
            f"ticks={self.ticks} by_kind={self.ticks_by_kind} last_tick={self.last_tick_at_utc} "
            f"decode_errors={self.decode_errors} stalls={self.stalls}"
        )


_TYPE_NAMES = {0: "initial_feed", 1: "live_feed", 2: "market_info"}


def seconds_until(target_utc: datetime, now: datetime | None = None) -> float:
    reference = now if now is not None else datetime.now(timezone.utc)
    return (target_utc - reference).total_seconds()


async def _connect_and_record(
    option_reference: dict,
    index_keys: dict,
    event_name: str,
    stop_at_utc: datetime,
    conn,
    insert_ticks_fn,
    stats: CaptureStats | None = None,
    skip_initial_feed: bool = False,
    mode: str = "full",
    connect_fn=None,
    url_fn=None,
    flush_every: int = FLUSH_EVERY_TICKS,
    flush_interval_seconds: float = FLUSH_INTERVAL_SECONDS,
    future_reference: dict | None = None,
    stall_alert_seconds: float = STALL_ALERT_SECONDS,
    stale_connection_seconds: float = STALE_CONNECTION_SECONDS,
) -> None:
    """One connection attempt: record ticks until stop_at_utc is reached or
    the connection drops (raises on drop -- reconnect policy lives in
    run_capture(), same separation of concerns as ws_stream.py's
    _connect_and_stream/run split).

    Buffered ticks are flushed in a finally block, so a dropped connection
    never loses the last few seconds of data -- and the last few seconds
    before a drop are exactly the ones most likely to matter (BUGS.md BUG-4).

    skip_initial_feed: on a RECONNECT the server re-sends an `initial_feed`
    snapshot of every instrument's last-known state. Stamped with the
    reconnect time it would look like a fresh post-shock price move and
    corrupt first-move detection, so reconnects drop it (BUGS.md BUG-5).

    connect_fn/url_fn are injection points for tests."""
    import asyncio

    import websockets

    from google.protobuf.message import DecodeError
    from MarketDataFeed_pb2 import FeedResponse, initial_feed

    stats = stats if stats is not None else CaptureStats()
    connect_fn = connect_fn or (
        lambda url: websockets.connect(
            url, max_size=MAX_MESSAGE_BYTES, ping_interval=PING_INTERVAL_SECONDS, ping_timeout=PING_TIMEOUT_SECONDS
        )
    )
    url = (url_fn or get_ws_authorized_url)()  # fetched fresh every attempt -- the embedded code is single-use
    future_reference = future_reference or {}
    all_keys = list(option_reference.keys()) + list(future_reference.keys()) + list(index_keys.keys())
    buffer: list[dict] = []
    last_flush = time.monotonic()
    last_heartbeat = time.monotonic()
    last_data = time.monotonic()  # last time a tick was decoded (or the connection started)
    last_message = time.monotonic()  # last time ANY frame arrived
    stall_alerted = False

    try:
        async with connect_fn(url) as ws:
            stats.connects += 1
            await ws.send(build_subscribe_message(all_keys, mode=mode))

            while True:
                remaining = seconds_until(stop_at_utc)
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, flush_interval_seconds))
                except asyncio.TimeoutError:
                    raw = None  # quiet feed: loop around to re-check the stop time and flush

                if isinstance(raw, bytes):
                    last_message = time.monotonic()
                    response = FeedResponse()
                    received_at = datetime.now(timezone.utc)
                    try:
                        response.ParseFromString(raw)
                    except DecodeError:
                        # one malformed frame must not take down an event capture
                        stats.decode_errors += 1
                        log.warning("dropped an undecodable frame (%d bytes)", len(raw))
                        continue
                    stats.messages += 1
                    type_name = _TYPE_NAMES.get(response.type, str(response.type))
                    stats.messages_by_type[type_name] = stats.messages_by_type.get(type_name, 0) + 1

                    if not (skip_initial_feed and response.type == initial_feed):
                        rows = process_feed_response(
                            response, option_reference, index_keys, event_name, received_at, future_reference
                        )
                        for row in rows:
                            stats.ticks_by_kind[row["kind"]] = stats.ticks_by_kind.get(row["kind"], 0) + 1
                        if rows:
                            stats.ticks += len(rows)
                            stats.last_tick_at_utc = received_at
                            last_data = time.monotonic()
                            stall_alerted = False
                        buffer.extend(rows)

                now_mono = time.monotonic()
                if now_mono - last_message >= stale_connection_seconds:
                    raise ConnectionError(f"no frames for {stale_connection_seconds:.0f}s -- link presumed dead, reconnecting")
                if now_mono - last_data >= stall_alert_seconds and not stall_alerted:
                    # Silent zero-tick capture was the BUG-3 failure mode; make it loud.
                    stats.stalls += 1
                    stall_alerted = True
                    log.error("STALL: no ticks decoded for %.0fs (%s)", stall_alert_seconds, stats.summary())
                if buffer and (len(buffer) >= flush_every or now_mono - last_flush >= flush_interval_seconds):
                    insert_ticks_fn(conn, buffer)
                    buffer = []
                    last_flush = now_mono
                if now_mono - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                    log.info("capture heartbeat: %s", stats.summary())
                    last_heartbeat = now_mono
    finally:
        if buffer:
            insert_ticks_fn(conn, buffer)


async def run_capture(
    event: ShockEvent,
    conn,
    insert_ticks_fn,
    pre_seconds: int = 600,
    post_seconds: int = 1800,
    max_reconnects: int | None = None,
    wait_for_start: bool = True,
    stats: CaptureStats | None = None,
    option_reference: dict | None = None,
    index_keys: dict | None = None,
    future_reference: dict | None = None,
) -> CaptureStats:
    """Top-level entrypoint for a market-hours event: builds the reference
    table once, sleeps until the capture window opens (wait_for_start --
    a recorder started at 09:00 shouldn't capture an hour of irrelevant
    pre-event ticks), then records through the window, reconnecting on a
    drop (bounded backoff, simpler than ws_stream.run()'s open-ended loop
    since a capture window has a known end time). Returns the CaptureStats
    so a caller can sanity-check the capture actually received ticks."""
    import asyncio

    import websockets.exceptions

    stats = stats if stats is not None else CaptureStats()
    if option_reference is None or index_keys is None:
        option_reference, index_keys = build_reference_table()
        if future_reference is None:
            future_reference = build_futures_reference()  # {} on failure -- never aborts a capture
    future_reference = future_reference or {}
    start_at_utc, stop_at_utc = capture_window(event, pre_seconds, post_seconds)
    log.info(
        "reference table: %d option legs, %d futures, %d indices; window %s -> %s",
        len(option_reference), len(future_reference), len(index_keys), start_at_utc.isoformat(), stop_at_utc.isoformat(),
    )

    if wait_for_start:
        while (wait := seconds_until(start_at_utc)) > 0:
            log.info("waiting %.0fs for capture window to open", wait)
            await asyncio.sleep(min(wait, 30.0))

    backoff_seconds = 1.0
    max_backoff_seconds = MAX_BACKOFF_SECONDS
    attempt = 0

    while datetime.now(timezone.utc) < stop_at_utc and (max_reconnects is None or attempt < max_reconnects):
        attempt += 1
        try:
            await _connect_and_record(
                option_reference, index_keys, event.event_name, stop_at_utc, conn, insert_ticks_fn,
                stats=stats, skip_initial_feed=attempt > 1, future_reference=future_reference,
            )
            backoff_seconds = 1.0
        except Exception as exc:  # noqa: BLE001 -- the window is unrepeatable; keep trying until it closes
            # Expected: WebSocketException (ConnectionClosed AND handshake
            # failures like InvalidStatus) and OSError (the REST authorize
            # call failing -- requests' exceptions subclass it). Anything
            # else is a bug, but is still better retried (and logged with
            # a traceback) than allowed to end the capture mid-event.
            expected = isinstance(exc, (websockets.exceptions.WebSocketException, OSError))
            log.log(
                logging.WARNING if expected else logging.ERROR,
                "capture connection attempt %d failed: %r -- retrying in %.0fs", attempt, exc, backoff_seconds,
                exc_info=not expected,
            )
            await asyncio.sleep(backoff_seconds)
            backoff_seconds = min(backoff_seconds * 2, max_backoff_seconds)

    log.info("capture finished: %s", stats.summary())
    return stats


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
        expiry = expiries[0]
        chain = get_option_chain(underlying_key, expiry)
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
                "expiry": None,
                "exchange_ts_ms": None,
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
                    "expiry": expiry,
                    "exchange_ts_ms": None,
                }
            )

    insert_ticks_fn(conn, rows)
    return rows
