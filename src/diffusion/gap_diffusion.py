"""Phase 5 stretch: overnight/gap diffusion for after-hours events (CPI,
IIP, GDP).

A genuinely different mechanic from Phase 3's intraday spot-vs-option lag
metric, not a lesser version of it -- there's no continuous tick series
bracketing an after-hours release, since NSE is closed when the release
itself happens. tick_recorder.py's record_gap_tick() instead takes
discrete REST snapshots at named points around the release (conventionally
"pre_close", "post_open", and optionally a later "post_open_settled"
snapshot taken some minutes into the next session); this module measures
how the eventual total move split across those points -- how much
happened invisibly in the closed-market gap versus how much continued to
unfold once trading actually resumed.
"""
from __future__ import annotations

import sys
from pathlib import Path
from statistics import mean, pstdev

sys.path.insert(0, str(Path(__file__).resolve().parent))

from diffusion_db import fetch_ticks  # noqa: E402
from event_calendar import ShockEvent  # noqa: E402
from tick_recorder import gap_tick_event_name  # noqa: E402


def gap_move_breakdown(
    pre_close_price: float,
    post_open_price: float,
    settled_price: float | None = None,
) -> dict:
    """Splits an after-hours event's total observed move into the part
    that happened in the closed-market gap (pre_close -> post_open) and
    the part that continued once trading resumed (post_open -> settled).

    settled_price: a later intraday snapshot (e.g. 15-30 minutes after
    open). If omitted, post_open IS the only post-release point available
    -- by definition the entire observed move happened in the gap, and
    that's reported explicitly (gap_fraction=1.0), not silently assumed by
    the caller.

    Never raises on a flat/zero total move -- an undefined 0/0 fraction is
    reported as None, not fabricated as 0 or 1 (same "don't invent a
    number for an undefined case" discipline as adjustment_curves.py's
    handling of a flat pre-shock/post-shock series)."""
    if settled_price is None:
        settled_price = post_open_price

    gap_move = post_open_price - pre_close_price
    total_move = settled_price - pre_close_price
    followthrough_move = settled_price - post_open_price

    if total_move == 0:
        gap_fraction = None
        followthrough_fraction = None
    else:
        gap_fraction = gap_move / total_move
        followthrough_fraction = followthrough_move / total_move

    return {
        "gap_move": gap_move,
        "followthrough_move": followthrough_move,
        "total_move": total_move,
        "gap_fraction": gap_fraction,
        "followthrough_fraction": followthrough_fraction,
    }


def fetch_gap_price(conn, event: ShockEvent, label: str, underlying: str, kind: str = "spot") -> float | None:
    """Convenience: pull the single snapshot price record_gap_tick() wrote
    for one label ("pre_close"/"post_open"/"post_open_settled") and one
    underlying ("NIFTY"/"BANKNIFTY"). Returns None if that snapshot hasn't
    been captured yet -- a real, expected state until the corresponding
    scheduled job has actually run (there's nothing to stream while NSE
    is shut, and that applies to lookups too: you can't summarize data
    that doesn't exist yet)."""
    rows = fetch_ticks(conn, gap_tick_event_name(event, label), kind=kind, underlying=underlying)
    if not rows:
        return None
    return float(rows[-1]["price_paise"])


def summarize_gap_fractions(gap_fractions: list[float]) -> dict:
    """Descriptive stats only, same small-sample honesty as
    lag_detection.py's summarize_lags() -- CPI releases happen roughly
    monthly, so real events accumulate even more slowly than RBI's six
    meetings a year."""
    n = len(gap_fractions)
    if n == 0:
        return {"n": 0, "mean": None, "median": None, "stdev": None}

    ordered = sorted(gap_fractions)
    mid = n // 2
    median = ordered[mid] if n % 2 else mean(ordered[mid - 1 : mid + 1])

    return {
        "n": n,
        "mean": mean(gap_fractions),
        "median": median,
        "stdev": pstdev(gap_fractions) if n > 1 else 0.0,
    }
