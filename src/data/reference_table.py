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


def build_subscribe_message(instrument_keys: list[str], mode: str = "full_d5") -> bytes:
    """UTF-8-encoded JSON, sent as a binary WebSocket frame -- the doc
    page's prose says the request must be sent in binary format but only
    ever shows a JSON-shaped payload; best-effort read. Live-tested
    2026-08-26: connects and sends without rejection, but real tick
    delivery isn't confirmed yet -- see BUGS.md BUG-3 for the open
    question (thin near-close activity vs. a genuine format issue)."""
    message = {"guid": str(uuid.uuid4()), "method": "sub", "data": {"mode": mode, "instrumentKeys": instrument_keys}}
    return json.dumps(message).encode("utf-8")
