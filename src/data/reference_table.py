"""Reference-table + subscribe-message helpers for Upstox's WebSocket feed.

Connection/reference-table plumbing only, deliberately kept separate from
any no-arbitrage/consistency-check logic -- this project has no use for
arbitrage detection, only for getting a WebSocket connection subscribed to
a live NIFTY/BANKNIFTY reference table (spot index ticks and option ticks
off one connection). See BUGS.md for the design history.
"""
from __future__ import annotations

import json
import uuid
from datetime import date

from fetch_option_chain import BANKNIFTY, NIFTY, get_option_contracts, list_expiries

TRACKED = {NIFTY: {"label": "NIFTY", "n_expiries": 2}, BANKNIFTY: {"label": "BANKNIFTY", "n_expiries": 1}}


def build_reference_table() -> tuple[dict[str, dict], dict[str, str]]:
    """Returns (option_reference, index_instrument_keys).
    option_reference: instrument_key -> {underlying, expiry, strike_paise, option_type}
    index_instrument_keys: underlying_key -> label (for tracking spot price)
    """
    option_reference: dict[str, dict] = {}
    index_keys: dict[str, str] = {}
    today = date.today().isoformat()

    for instrument_key, cfg in TRACKED.items():
        index_keys[instrument_key] = cfg["label"]
        expiries = [e for e in list_expiries(instrument_key) if e > today][: cfg["n_expiries"]]
        for expiry in expiries:
            for c in get_option_contracts(instrument_key, expiry):
                option_reference[c["instrument_key"]] = {
                    "underlying": cfg["label"],
                    "expiry": expiry,
                    "strike_paise": round(c["strike_price"] * 100),
                    "option_type": c["instrument_type"],
                }
    return option_reference, index_keys


INSTRUMENT_MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
FUTURE_UNDERLYINGS = {"NIFTY": "NIFTY", "BANKNIFTY": "BANKNIFTY"}


def _download_instrument_master() -> list[dict]:
    import gzip
    import urllib.request

    request = urllib.request.Request(INSTRUMENT_MASTER_URL, headers={"User-Agent": "Mozilla/5.0"})
    return json.loads(gzip.decompress(urllib.request.urlopen(request, timeout=60).read()))


def build_futures_reference(now_ms: int | None = None, fetch_fn=None) -> dict[str, dict]:
    """instrument_key -> {underlying, expiry (ISO date)} for the nearest
    not-yet-expired NIFTY and BANKNIFTY futures, from Upstox's public
    instrument master (no auth needed; format verified 2026-10-05, e.g.
    {'instrument_key': 'NSE_FO|48704', 'trading_symbol': 'NIFTY FUT 27 OCT 26',
    'instrument_type': 'FUT', 'underlying_symbol': 'NIFTY', 'expiry': <ms>}).

    NEVER raises: the futures leg is a control series (does the spot index
    lag its own future?), not the headline measurement, so a failed
    download must not abort an event capture -- it logs and returns {}."""
    import logging
    import time
    from datetime import datetime, timedelta, timezone

    ist = timezone(timedelta(hours=5, minutes=30))
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    try:
        instruments = (fetch_fn or _download_instrument_master)()
    except Exception as exc:  # noqa: BLE001 -- see docstring
        logging.getLogger("diffusion").warning("futures reference unavailable (%r); continuing without the futures leg", exc)
        return {}

    nearest: dict[str, dict] = {}
    for inst in instruments:
        if inst.get("segment") != "NSE_FO" or inst.get("instrument_type") != "FUT":
            continue
        underlying = FUTURE_UNDERLYINGS.get(inst.get("underlying_symbol"))
        expiry_ms = inst.get("expiry")
        if underlying is None or not expiry_ms or expiry_ms <= now_ms:
            continue
        if underlying not in nearest or expiry_ms < nearest[underlying]["expiry_ms"]:
            nearest[underlying] = {"instrument_key": inst["instrument_key"], "expiry_ms": expiry_ms}

    return {
        v["instrument_key"]: {
            "underlying": u,
            "expiry": datetime.fromtimestamp(v["expiry_ms"] / 1000, tz=ist).date().isoformat(),
        }
        for u, v in nearest.items()
    }


# Valid v3 subscribe modes, per Upstox's own docs (verified 2026-10-05):
# "full_d5" is the protobuf *enum* name (RequestMode.full_d5) echoed back on
# decoded messages -- it is NOT a valid string for the subscribe request.
# The 5-level-depth mode is requested as "full". full_d30 needs Upstox Plus.
VALID_SUBSCRIBE_MODES = ("ltpc", "option_greeks", "full", "full_d30")


def build_subscribe_message(instrument_keys: list[str], mode: str = "full") -> bytes:
    """UTF-8-encoded JSON, sent as a binary WebSocket frame (Upstox's docs:
    "the request message should be sent in binary format, not as a text
    message"). Raises on an unknown mode instead of sending it: an invalid
    mode string is accepted silently by the server and simply never
    registers a subscription -- that was the root cause of BUGS.md BUG-3
    (mode="full_d5" -> connected cleanly, zero live_feed messages)."""
    if mode not in VALID_SUBSCRIBE_MODES:
        raise ValueError(f"invalid subscribe mode {mode!r}; valid modes: {VALID_SUBSCRIBE_MODES}")
    message = {"guid": str(uuid.uuid4()), "method": "sub", "data": {"mode": mode, "instrumentKeys": instrument_keys}}
    return json.dumps(message).encode("utf-8")
