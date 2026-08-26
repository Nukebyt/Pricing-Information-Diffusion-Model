"""Phase 3: first-move / lead-lag detection between spot and options after a
shock, plus the honest small-sample cross-event summary.

Method: for a single tick series, build a baseline mean/stdev from ticks
STRICTLY before the shock, then find the first post-shock tick whose
z-score against that baseline crosses a threshold. Applied independently
to a spot series and an option series for the same event, the lag is
simply the difference between their first-move timestamps -- positive
means spot moved first (options lagged), negative means the option moved
first.

The baseline must never include a post-shock point -- doing so folds the
very move being measured into "what's normal" and biases the detector
toward under-detecting it, the same look-ahead-bias discipline as in
backtesting more generally, applied here to a baseline-statistics step
instead of a backtest feature.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import mean, pstdev

Tick = tuple[str, float]  # (timestamp_utc ISO string, price)


@dataclass(frozen=True)
class FirstMove:
    timestamp_utc: str | None  # None if no threshold-crossing found post-shock
    baseline_mean: float
    baseline_stdev: float


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def detect_first_move(
    ticks: list[Tick],
    shock_timestamp_utc: str,
    z_threshold: float = 3.0,
    min_baseline_points: int = 5,
) -> FirstMove:
    """ticks: [(timestamp_utc, price), ...], any order, mixed pre/post-shock.

    Raises ValueError if there aren't enough strictly-pre-shock points to
    build a trustworthy baseline -- a silent, near-empty baseline would make
    z-scores meaningless rather than merely imprecise.
    """
    shock_dt = _parse(shock_timestamp_utc)
    pre = sorted(((t, p) for t, p in ticks if _parse(t) < shock_dt), key=lambda tp: tp[0])
    post = sorted(((t, p) for t, p in ticks if _parse(t) >= shock_dt), key=lambda tp: tp[0])

    if len(pre) < min_baseline_points:
        raise ValueError(f"need at least {min_baseline_points} strictly pre-shock points for a baseline, got {len(pre)}")

    baseline_prices = [p for _, p in pre]
    baseline_mean = mean(baseline_prices)
    baseline_stdev = pstdev(baseline_prices)

    if baseline_stdev == 0:
        # A perfectly flat baseline (e.g. synthetic test data, or a genuinely
        # untraded strike) breaks a z-score outright -- fall back to "any
        # nonzero move counts" rather than dividing by zero or silently
        # reporting "never moves."
        for t, p in post:
            if p != baseline_mean:
                return FirstMove(t, baseline_mean, baseline_stdev)
        return FirstMove(None, baseline_mean, baseline_stdev)

    for t, p in post:
        z = abs(p - baseline_mean) / baseline_stdev
        if z >= z_threshold:
            return FirstMove(t, baseline_mean, baseline_stdev)
    return FirstMove(None, baseline_mean, baseline_stdev)


def lead_lag_seconds(
    series_a: list[Tick],
    series_b: list[Tick],
    shock_timestamp_utc: str,
    z_threshold: float = 3.0,
) -> float | None:
    """General two-series lead-lag primitive. Positive => series_a moved
    first (series_b lagged by this many seconds). Negative => series_b
    moved first. None if either series never crossed the threshold within
    its captured window.

    spot_option_lag_seconds() and cross_index_lag_seconds() below are thin,
    semantically-named wrappers around this -- the detection math doesn't
    care what the two series represent (ROADMAP.md Phase 5, BUGS.md DEC-4:
    the cross-index angle reuses this unchanged, it's a pure analysis-layer
    extension, not a new detection method)."""
    move_a = detect_first_move(series_a, shock_timestamp_utc, z_threshold)
    move_b = detect_first_move(series_b, shock_timestamp_utc, z_threshold)
    if move_a.timestamp_utc is None or move_b.timestamp_utc is None:
        return None
    return (_parse(move_b.timestamp_utc) - _parse(move_a.timestamp_utc)).total_seconds()


def spot_option_lag_seconds(
    spot_ticks: list[Tick],
    option_ticks: list[Tick],
    shock_timestamp_utc: str,
    z_threshold: float = 3.0,
) -> float | None:
    """Positive => spot moved first (options lagged by this many seconds).
    Negative => the option moved first. The project's headline metric
    (ROADMAP.md S1)."""
    return lead_lag_seconds(spot_ticks, option_ticks, shock_timestamp_utc, z_threshold)


def cross_index_lag_seconds(
    nifty_ticks: list[Tick],
    banknifty_ticks: list[Tick],
    shock_timestamp_utc: str,
    z_threshold: float = 3.0,
) -> float | None:
    """Positive => NIFTY moved first (BANKNIFTY lagged by this many
    seconds). Negative => BANKNIFTY moved first. Phase 5 stretch angle
    (ROADMAP.md): the tick recorder already captures both indices'
    spot ticks off the same connection (reference_table.py's
    build_reference_table subscribes to both), so this needed no new data
    collection -- only this analysis-layer wrapper (BUGS.md DEC-4)."""
    return lead_lag_seconds(nifty_ticks, banknifty_ticks, shock_timestamp_utc, z_threshold)


def summarize_lags(lags_seconds: list[float]) -> dict:
    """Descriptive stats only -- deliberately not a hypothesis test. This
    project's entire dataset grows by one point per real scheduled macro
    event, so a formal significance claim on n=3-5 would be more
    misleading than none at all."""
    n = len(lags_seconds)
    if n == 0:
        return {"n": 0, "mean": None, "median": None, "stdev": None}

    ordered = sorted(lags_seconds)
    mid = n // 2
    median = ordered[mid] if n % 2 else mean(ordered[mid - 1 : mid + 1])

    return {
        "n": n,
        "mean": mean(lags_seconds),
        "median": median,
        "stdev": pstdev(lags_seconds) if n > 1 else 0.0,
    }
