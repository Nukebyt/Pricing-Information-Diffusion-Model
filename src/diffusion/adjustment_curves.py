"""Phase 3: adjustment-curve metrics -- how long a series takes to settle
into its new post-shock level, and a normalized price path for the
diffusion-timeline chart (Phase 4)."""
from __future__ import annotations

from datetime import datetime

Tick = tuple[str, float]  # (timestamp_utc ISO string, price)


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def time_to_90pct_adjustment(
    ticks: list[Tick],
    shock_timestamp_utc: str,
    pre_level: float,
    settle_seconds_after: int = 1800,
) -> float | None:
    """pre_level: the price level just before the shock (e.g.
    detect_first_move()'s baseline_mean). The "new level" is taken as the
    last observed price within settle_seconds_after of the shock -- a
    pragmatic proxy for "settled," not a provable claim the market is done
    moving.

    Returns seconds from shock to the first post-shock tick that has closed
    at least 90% of the gap between pre_level and the new level, or None if
    there's no post-shock data to work with.
    """
    shock_dt = _parse(shock_timestamp_utc)
    post = sorted(((t, p) for t, p in ticks if _parse(t) >= shock_dt), key=lambda tp: tp[0])
    if not post:
        return None

    window_end = shock_dt.timestamp() + settle_seconds_after
    windowed = [(t, p) for t, p in post if _parse(t).timestamp() <= window_end]
    if not windowed:
        windowed = post  # nothing landed inside the settle window -- use whatever was captured

    new_level = windowed[-1][1]
    total_move = new_level - pre_level
    if total_move == 0:
        return 0.0  # already at the "new" level -- trivially adjusted

    for t, p in windowed:
        progress = (p - pre_level) / total_move
        if progress >= 0.9:
            return (_parse(t) - shock_dt).total_seconds()
    return None


def normalized_price_path(
    ticks: list[Tick],
    shock_timestamp_utc: str,
    pre_level: float,
) -> list[tuple[float, float]]:
    """Returns [(seconds_from_shock, price / pre_level), ...] sorted by
    time -- puts spot (tens of thousands of rupees) and an option premium
    (tens to hundreds) on the same visual scale for the diffusion-timeline
    chart."""
    if pre_level == 0:
        raise ValueError("pre_level must be nonzero to normalize")
    shock_dt = _parse(shock_timestamp_utc)
    return sorted(((_parse(t) - shock_dt).total_seconds(), p / pre_level) for t, p in ticks)
