"""Phase 3: first-move / lead-lag detection between spot and options after a
shock, plus the honest small-sample cross-event summary.

Method: for a single tick series, build a baseline from ticks STRICTLY
before the shock, then find the first post-shock tick that is abnormal
against that baseline. Applied independently to two series for the same
event, the lag is simply the difference between their first-move
timestamps -- positive means series A moved first, negative means B did.

Two detectors share that frame (BUGS.md DEC-10):

  method="level"    z-score of the raw PRICE LEVEL against the pre-shock
                    level mean/stdev. Simple, but a drifting pre-shock
                    baseline inflates the stdev and delays detection, and a
                    0.05 option-tick grid makes a flat baseline brittle.
  method="returns"  z-score of the trailing `return_window`-tick RETURN
                    (p[i] - p[i-w]) against the distribution of the same
                    statistic over the pre-shock ticks. Insensitive to the
                    price level and to slow drift; fires on the tick where
                    a genuinely abnormal jump lands.

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

METHODS = ("level", "returns")
DEFAULT_RETURN_WINDOW = 5


@dataclass(frozen=True)
class FirstMove:
    timestamp_utc: str | None  # None if no threshold-crossing found post-shock
    baseline_mean: float
    baseline_stdev: float
    # True when the first breach only appeared right after a data gap
    # (> max_gap_seconds with no ticks): the move happened SOMEWHERE in the
    # gap, so no honest timestamp exists and none is reported.
    censored: bool = False


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _first_confirmed(breaches: list[bool], timestamps: list[str], min_consecutive: int) -> str | None:
    """Timestamp of the FIRST tick of the first run of `min_consecutive`
    consecutive breaches, or None."""
    run = 0
    for i, breached in enumerate(breaches):
        run = run + 1 if breached else 0
        if run >= min_consecutive:
            return timestamps[i - min_consecutive + 1]
    return None


def _first_confirmed_index(breaches: list[bool], min_consecutive: int) -> int | None:
    run = 0
    for i, breached in enumerate(breaches):
        run = run + 1 if breached else 0
        if run >= min_consecutive:
            return i - min_consecutive + 1
    return None


def _gap_before(ordered: list[Tick], index: int, lookback: int, max_gap_seconds: float | None) -> bool:
    """Is there an outage between any consecutive pair of ticks in
    ordered[index - lookback .. index]?"""
    if max_gap_seconds is None:
        return False
    lo = max(1, index - lookback)
    for k in range(lo, index + 1):
        if (_parse(ordered[k][0]) - _parse(ordered[k - 1][0])).total_seconds() > max_gap_seconds:
            return True
    return False


def detect_first_move(
    ticks: list[Tick],
    shock_timestamp_utc: str,
    z_threshold: float = 3.0,
    min_baseline_points: int = 5,
    min_consecutive: int = 1,
    method: str = "level",
    return_window: int = DEFAULT_RETURN_WINDOW,
    max_gap_seconds: float | None = None,
) -> FirstMove:
    """ticks: [(timestamp_utc, price), ...], any order, mixed pre/post-shock.

    min_consecutive: how many consecutive post-shock ticks must all breach
    the threshold before the move counts (the REPORTED timestamp is still
    the first tick of that run). 1 reproduces the plain first-crossing
    rule; >1 filters out a single bid/ask-bounce or stale-quote tick that
    briefly pokes past 3 sigma -- an option mid on a 0.05 tick grid is
    exactly where that happens. Reporting both gives a robustness check.

    method: "level" or "returns" (see module docstring). For "returns" the
    FirstMove's baseline_mean/stdev describe the pre-shock return
    distribution; callers wanting a pre-shock price LEVEL (normalization,
    settle-speed) should use the series' own pre-shock mean instead.

    max_gap_seconds: if set, a stretch longer than this with no ticks is a
    data outage (dropped connection, not a quiet market). The first tick
    after an outage carries all the price change accumulated inside it, so
    a breach starting there says "it moved during the gap", not "it moved
    now" -- that case is reported as censored (timestamp None,
    censored=True) instead of being timed at the reconnect.

    Raises ValueError if there aren't enough strictly-pre-shock points to
    build a trustworthy baseline -- a silent, near-empty baseline would make
    z-scores meaningless rather than merely imprecise.
    """
    if min_consecutive < 1:
        raise ValueError("min_consecutive must be >= 1")
    if method not in METHODS:
        raise ValueError(f"unknown method {method!r}; expected one of {METHODS}")
    shock_dt = _parse(shock_timestamp_utc)
    ordered = sorted(ticks, key=lambda tp: tp[0])
    pre = [(t, p) for t, p in ordered if _parse(t) < shock_dt]
    post = [(t, p) for t, p in ordered if _parse(t) >= shock_dt]

    if method == "level":
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
            breaches = [p != baseline_mean for _, p in post]
        else:
            breaches = [abs(p - baseline_mean) / baseline_stdev >= z_threshold for _, p in post]
        start = _first_confirmed_index(breaches, min_consecutive)
        if start is None:
            return FirstMove(None, baseline_mean, baseline_stdev)
        if _gap_before(ordered, len(pre) + start, 0, max_gap_seconds):
            return FirstMove(None, baseline_mean, baseline_stdev, censored=True)
        return FirstMove(post[start][0], baseline_mean, baseline_stdev)

    # method == "returns": need enough pre-shock ticks to form >= min_baseline_points w-tick returns
    w = return_window
    if len(pre) < min_baseline_points + w:
        raise ValueError(
            f"need at least {min_baseline_points + w} strictly pre-shock points for a {w}-tick-return baseline, got {len(pre)}"
        )
    pre_prices = [p for _, p in pre]
    baseline_returns = [pre_prices[j] - pre_prices[j - w] for j in range(w, len(pre_prices))]
    ret_mean = mean(baseline_returns)
    ret_stdev = pstdev(baseline_returns)

    all_prices = [p for _, p in ordered]
    first_post_index = len(pre)
    breaches, stamps = [], []
    for i in range(first_post_index, len(ordered)):
        r = all_prices[i] - all_prices[i - w]
        if ret_stdev == 0:
            breaches.append(r != ret_mean)
        else:
            breaches.append(abs(r - ret_mean) / ret_stdev >= z_threshold)
        stamps.append(ordered[i][0])
    start = _first_confirmed_index(breaches, min_consecutive)
    if start is None:
        return FirstMove(None, ret_mean, ret_stdev)
    # the w-tick return at the breaching tick spans the previous w ticks
    if _gap_before(ordered, first_post_index + start, w, max_gap_seconds):
        return FirstMove(None, ret_mean, ret_stdev, censored=True)
    return FirstMove(stamps[start], ret_mean, ret_stdev)


def pre_shock_false_positive(
    ticks: list[Tick],
    shock_timestamp_utc: str,
    pseudo_offset_seconds: float = 300.0,
    z_threshold: float = 3.0,
    min_consecutive: int = 1,
    method: str = "level",
) -> dict:
    """Within-event placebo: pretend the shock happened `pseudo_offset_seconds`
    BEFORE the real one, use only the ticks before that pseudo-shock as the
    baseline, and run the identical detector over the (pseudo-shock, real
    shock) span -- a stretch with no scheduled news. If the detector "finds
    a first move" here, that is a false positive at this threshold on this
    series, and the same noise floor sits underneath any real-shock result.

    Returns {"evaluated": bool, "fired": bool|None, "offset_s": float|None,
    "reason": str|None}; ticks at/after the real shock are excluded so the
    real move can't leak in."""
    real_shock = _parse(shock_timestamp_utc)
    pseudo_shock = real_shock.timestamp() - pseudo_offset_seconds
    pseudo_iso = datetime.fromtimestamp(pseudo_shock, tz=real_shock.tzinfo).isoformat()
    pre_only = [(t, p) for t, p in ticks if _parse(t) < real_shock]
    try:
        move = detect_first_move(pre_only, pseudo_iso, z_threshold, min_consecutive=min_consecutive, method=method)
    except ValueError as exc:
        return {"evaluated": False, "fired": None, "offset_s": None, "reason": str(exc)}
    offset = None if move.timestamp_utc is None else (_parse(move.timestamp_utc) - _parse(pseudo_iso)).total_seconds()
    return {"evaluated": True, "fired": move.timestamp_utc is not None, "offset_s": offset, "reason": None}


def lead_lag_detail(
    series_a: list[Tick],
    series_b: list[Tick],
    shock_timestamp_utc: str,
    z_threshold: float = 3.0,
    min_consecutive: int = 1,
    method: str = "level",
    max_gap_seconds: float | None = None,
) -> dict:
    """Like lead_lag_seconds() but also says WHY a lag is missing:
    {"lag_s": float|None, "censored_a": bool, "censored_b": bool}. A series
    whose first move only showed up after a data outage is censored, which
    is different from "never moved"."""
    move_a = detect_first_move(series_a, shock_timestamp_utc, z_threshold, min_consecutive=min_consecutive, method=method, max_gap_seconds=max_gap_seconds)
    move_b = detect_first_move(series_b, shock_timestamp_utc, z_threshold, min_consecutive=min_consecutive, method=method, max_gap_seconds=max_gap_seconds)
    lag = None
    if move_a.timestamp_utc is not None and move_b.timestamp_utc is not None:
        lag = (_parse(move_b.timestamp_utc) - _parse(move_a.timestamp_utc)).total_seconds()
    return {"lag_s": lag, "censored_a": move_a.censored, "censored_b": move_b.censored}


def lead_lag_seconds(
    series_a: list[Tick],
    series_b: list[Tick],
    shock_timestamp_utc: str,
    z_threshold: float = 3.0,
    min_consecutive: int = 1,
    method: str = "level",
    max_gap_seconds: float | None = None,
) -> float | None:
    """General two-series lead-lag primitive. Positive => series_a moved
    first (series_b lagged by this many seconds). Negative => series_b
    moved first. None if either series never crossed the threshold within
    its captured window (or only after a data outage, see
    detect_first_move's max_gap_seconds).

    spot_option_lag_seconds() and cross_index_lag_seconds() below are thin,
    semantically-named wrappers around this -- the detection math doesn't
    care what the two series represent (ROADMAP.md Phase 5, BUGS.md DEC-4:
    the cross-index angle reuses this unchanged, it's a pure analysis-layer
    extension, not a new detection method)."""
    return lead_lag_detail(series_a, series_b, shock_timestamp_utc, z_threshold, min_consecutive, method, max_gap_seconds)["lag_s"]


def spot_option_lag_seconds(
    spot_ticks: list[Tick],
    option_ticks: list[Tick],
    shock_timestamp_utc: str,
    z_threshold: float = 3.0,
    min_consecutive: int = 1,
    method: str = "level",
) -> float | None:
    """Positive => spot moved first (options lagged by this many seconds).
    Negative => the option moved first. The project's headline metric
    (ROADMAP.md S1)."""
    return lead_lag_seconds(spot_ticks, option_ticks, shock_timestamp_utc, z_threshold, min_consecutive, method)


def spot_future_lag_seconds(
    spot_ticks: list[Tick],
    future_ticks: list[Tick],
    shock_timestamp_utc: str,
    z_threshold: float = 3.0,
    min_consecutive: int = 1,
    method: str = "level",
) -> float | None:
    """Positive => the spot index moved first (the future lagged); negative
    => the future moved first. Futures typically lead a computed spot
    index, so this is the control for reading a spot-vs-option lag as
    "options are slow" when part of it is "the spot index is slow"
    (ROADMAP.md Improvements #6)."""
    return lead_lag_seconds(spot_ticks, future_ticks, shock_timestamp_utc, z_threshold, min_consecutive, method)


def cross_index_lag_seconds(
    nifty_ticks: list[Tick],
    banknifty_ticks: list[Tick],
    shock_timestamp_utc: str,
    z_threshold: float = 3.0,
    min_consecutive: int = 1,
    method: str = "level",
) -> float | None:
    """Positive => NIFTY moved first (BANKNIFTY lagged by this many
    seconds). Negative => BANKNIFTY moved first. Phase 5 stretch angle
    (ROADMAP.md): the tick recorder already captures both indices'
    spot ticks off the same connection (reference_table.py's
    build_reference_table subscribes to both), so this needed no new data
    collection -- only this analysis-layer wrapper (BUGS.md DEC-4)."""
    return lead_lag_seconds(nifty_ticks, banknifty_ticks, shock_timestamp_utc, z_threshold, min_consecutive, method)


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
