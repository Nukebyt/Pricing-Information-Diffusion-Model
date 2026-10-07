"""Phase 3/4 glue: captured DB rows -> one event's headline numbers.

The measurement primitives (lag_detection, adjustment_curves, gap_diffusion)
each take clean single-series tick lists. A real capture isn't that shape:
the recorder logs ~660 option legs, two futures and two indices in one
table, so the first job here is picking WHICH series to compare -- feeding
every option row to spot_option_lag_seconds() would blend different
strikes/expiries into one nonsense "price" series. The option leg compared
against spot is the nearest-expiry, closest-to-the-money contract (per
option type) that actually has enough pre-shock ticks to build a baseline
from; an ATM +/- 2-strike basket is analysed alongside it so one thin
strike can't carry the headline.

Every lag is reported under four detector variants -- level/returns x
1-tick/3-tick confirmation -- and one is designated PRIMARY *before any real
data was seen* (BUGS.md DEC-10): the return-based detector with 3-tick
confirmation. The other three are sensitivity checks, not alternatives to
pick from after the fact.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import median, pstdev

sys.path.insert(0, str(Path(__file__).resolve().parent))

from adjustment_curves import time_to_90pct_adjustment  # noqa: E402
from diffusion_db import fetch_ticks, rows_to_ticks  # noqa: E402
from event_calendar import ShockEvent  # noqa: E402
from gap_diffusion import fetch_gap_price, gap_move_breakdown  # noqa: E402
from lag_detection import detect_first_move, lead_lag_detail, pre_shock_false_positive  # noqa: E402
from paths import safe_filename  # noqa: E402

DEFAULT_RESULTS_DIR = Path(__file__).resolve().parents[2] / "data" / "results"
MIN_BASELINE_POINTS = 5
BASKET_WIDTH = 2  # ATM +/- 2 strikes
SETTLED_LEVEL_WINDOW_SECONDS = 60.0
PLACEBO_PREFIX = "PLACEBO "

# (method, min_consecutive) -- the four reported detector variants.
VARIANTS = [("level", 1), ("level", 3), ("returns", 1), ("returns", 3)]
PRIMARY = "returns_c3"  # pre-registered, BUGS.md DEC-10


def _vkey(method: str, k: int) -> str:
    return f"{method}_c{k}"


def _offset(ts: str | None, shock: datetime) -> float | None:
    return None if ts is None else (datetime.fromisoformat(ts) - shock).total_seconds()


MIN_OUTAGE_SECONDS = 10.0


def outage_threshold(ticks: list[tuple[str, float]]) -> float:
    """A stretch with no ticks counts as a data OUTAGE (dropped connection)
    rather than a quiet market when it exceeds 10s AND 20x the series' own
    median inter-tick time -- so a thin strike that legitimately ticks every
    few seconds isn't flagged, but a 10-minute network hole in a
    1-tick-per-second index series is."""
    stamps = sorted(datetime.fromisoformat(t).timestamp() for t, _ in ticks)
    if len(stamps) < 3:
        return MIN_OUTAGE_SECONDS
    gaps = sorted(b - a for a, b in zip(stamps, stamps[1:]))
    return max(MIN_OUTAGE_SECONDS, 20.0 * gaps[len(gaps) // 2])


def find_outages(ticks: list[tuple[str, float]], threshold: float | None = None) -> list[dict]:
    threshold = outage_threshold(ticks) if threshold is None else threshold
    ordered = sorted(ticks, key=lambda tp: tp[0])
    return [
        {"start_utc": a, "seconds": (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()}
        for (a, _), (b, _) in zip(ordered, ordered[1:])
        if (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() > threshold
    ]


def _split_counts(ticks: list[tuple[str, float]], shock: datetime) -> dict:
    pre = [t for t, _ in ticks if datetime.fromisoformat(t) < shock]
    post = [t for t, _ in ticks if datetime.fromisoformat(t) >= shock]
    max_gap = None
    if len(post) > 1:
        stamps = sorted(datetime.fromisoformat(t) for t in post)
        max_gap = max((b - a).total_seconds() for a, b in zip(stamps, stamps[1:]))
    return {"n_pre": len(pre), "n_post": len(post), "max_post_gap_s": max_gap}


def _eligible_contracts(rows: list[dict], shock: datetime) -> list[dict]:
    """Per-instrument tick counts; only contracts with enough pre-shock
    ticks for a baseline and at least one post-shock tick are eligible --
    an untraded ATM strike falls back to its nearest liquid neighbour
    instead of failing the whole event."""
    by_instrument: dict[str, dict] = {}
    for r in rows:
        info = by_instrument.setdefault(
            r["instrument_key"],
            {
                "instrument_key": r["instrument_key"],
                "strike_paise": r["strike_paise"],
                "expiry": r.get("expiry"),
                "option_type": r["option_type"],
                "n_pre": 0,
                "n_post": 0,
            },
        )
        if datetime.fromisoformat(r["timestamp_utc"]) < shock:
            info["n_pre"] += 1
        else:
            info["n_post"] += 1
    return [i for i in by_instrument.values() if i["n_pre"] >= MIN_BASELINE_POINTS and i["n_post"] >= 1]


def pick_atm_legs(rows: list[dict], spot_paise: float, shock: datetime) -> dict[str, dict]:
    """rows: option rows (all strikes/expiries) for ONE underlying. Returns
    {"CE": leg, "PE": leg} where leg = {instrument_key, strike_paise, expiry, ...}.
    Preference: nearest expiry first, then closest strike to spot_paise."""
    eligible = _eligible_contracts(rows, shock)
    legs: dict[str, dict] = {}
    for option_type in ("CE", "PE"):
        candidates = [i for i in eligible if i["option_type"] == option_type]
        if candidates:
            legs[option_type] = min(candidates, key=lambda i: (i["expiry"] or "9999", abs(i["strike_paise"] - spot_paise)))
    return legs


def pick_atm_basket(rows: list[dict], spot_paise: float, shock: datetime, width: int = BASKET_WIDTH) -> dict[str, list[dict]]:
    """Per option type: the (2*width+1) eligible strikes closest to spot in
    the nearest eligible expiry."""
    eligible = _eligible_contracts(rows, shock)
    basket: dict[str, list[dict]] = {}
    for option_type in ("CE", "PE"):
        candidates = [i for i in eligible if i["option_type"] == option_type]
        if not candidates:
            continue
        expiry = min((i["expiry"] or "9999") for i in candidates)
        same_expiry = [i for i in candidates if (i["expiry"] or "9999") == expiry]
        same_expiry.sort(key=lambda i: abs(i["strike_paise"] - spot_paise))
        basket[option_type] = same_expiry[: 2 * width + 1]
    return basket


def _exchange_ticks(rows: list[dict]) -> list[tuple[str, float]]:
    """Ticks stamped with the EXCHANGE's last-trade time instead of local
    arrival time, one per distinct exchange timestamp (a repeated ltt means
    a quote update with no new trade, which carries no new exchange-time
    information). Experimental: validated against live data only once a
    real capture exists."""
    out, previous = [], None
    for r in rows:
        ts = r.get("exchange_ts_ms")
        if not ts or ts == previous:
            continue
        previous = ts
        out.append((datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat(), float(r["price_paise"])))
    return out


def feed_delay_stats(rows: list[dict]) -> dict | None:
    """Local arrival time minus exchange timestamp, in seconds -- how stale
    the feed is on arrival and how much it jitters. None if the rows carry
    no exchange timestamps."""
    delays = sorted(
        (datetime.fromisoformat(r["timestamp_utc"]).timestamp() - r["exchange_ts_ms"] / 1000)
        for r in rows
        if r.get("exchange_ts_ms")
    )
    if not delays:
        return None
    return {
        "n": len(delays),
        "median_s": median(delays),
        "p95_s": delays[min(len(delays) - 1, int(0.95 * len(delays)))],
        "min_s": delays[0],
        "max_s": delays[-1],
    }


def shock_detectability(ticks: list[tuple[str, float]], shock: datetime, unit_scale: float = 100.0) -> dict | None:
    """DESCRIPTIVE numbers on whether the event moved this series by more
    than its own ordinary noise -- deliberately no pass/fail threshold (a
    threshold chosen after seeing data would be a forking path):

      largest_5s_move_pre   biggest absolute price change over any 5-second
                            span in the whole pre-shock window
      largest_5s_move_post60  the same, within the first 60s after the shock
      net_move_at_{60,300,end}_s  price change vs the last pre-shock tick
      pre_noise_sd_last_120s  stdev of the price level in the last 2 min before

    If post60 is no bigger than pre, a second-scale lead-lag of this series
    is not measuring a reaction to the event.

    unit_scale divides every price-unit output (ticks are stored in paise, so
    100 gives index points / rupees of premium; pass 1.0 for non-price series
    such as IV)."""
    pts = sorted(((datetime.fromisoformat(t) - shock).total_seconds(), p) for t, p in ticks)
    pre = [(x, p) for x, p in pts if x < 0]
    post = [(x, p) for x, p in pts if x >= 0]
    if len(pre) < 5 or not post:
        return None

    def max_move(seg, span=5.0):
        best, j = 0.0, 0
        for i, (x, p) in enumerate(seg):
            j = max(j, i)
            while j + 1 < len(seg) and seg[j + 1][0] - x <= span:
                j += 1
            best = max(best, abs(seg[j][1] - p))
        return best

    def level_at(offset):
        prev = None
        for x, p in post:
            if x <= offset:
                prev = p
            else:
                break
        return prev

    base = pre[-1][1]
    last_pre = [p for x, p in pre if x >= -120]
    out = {
        "largest_5s_move_pre": max_move(pre) / unit_scale,
        "largest_5s_move_post60": max_move([(x, p) for x, p in post if x <= 60]) / unit_scale,
        "pre_noise_sd_last_120s": pstdev(last_pre) / unit_scale if len(last_pre) > 1 else None,
        "net_move_at_end": (post[-1][1] - base) / unit_scale,
    }
    for label, offset in (("60", 60.0), ("300", 300.0)):
        level = level_at(offset)
        out[f"net_move_at_{label}s"] = None if level is None else (level - base) / unit_scale
    return out


def _analyze_series(ticks: list[tuple[str, float]], shock_ts: str, shock: datetime, z_threshold: float, unit_scale: float = 100.0) -> dict:
    """Per-series first-move offsets under every variant, settle speed, and
    the within-event pre-shock placebo. Errors become data, not crashes."""
    out: dict = {**_split_counts(ticks, shock)}
    pre_prices = [p for t, p in ticks if datetime.fromisoformat(t) < shock]
    if pre_prices:
        out["baseline_mean"] = sum(pre_prices) / len(pre_prices)

    threshold = outage_threshold(ticks)
    out["outages"] = find_outages(ticks, threshold)
    offsets, errors, placebo, censored = {}, {}, {}, {}
    for method, k in VARIANTS:
        key = _vkey(method, k)
        try:
            move = detect_first_move(ticks, shock_ts, z_threshold, min_consecutive=k, method=method, max_gap_seconds=threshold)
            offsets[key] = _offset(move.timestamp_utc, shock)
            censored[key] = move.censored
        except ValueError as exc:
            offsets[key] = None
            errors[key] = str(exc)
        placebo[key] = pre_shock_false_positive(ticks, shock_ts, z_threshold=z_threshold, min_consecutive=k, method=method)
    out["first_move_offset_s"] = offsets
    out["first_move_offset_s"]["primary"] = offsets[PRIMARY]
    out["detectability"] = shock_detectability(ticks, shock, unit_scale)
    out["censored_by_gap"] = censored
    out["placebo_pre_shock"] = placebo
    if errors:
        out["errors"] = errors
        if "level_c1" in errors:  # the original, minimum-data error contract
            out["error"] = errors["level_c1"]
    if "baseline_mean" in out and ticks:
        out["settle_90pct_s"] = time_to_90pct_adjustment(
            ticks, shock_ts, out["baseline_mean"], settled_level_window_seconds=SETTLED_LEVEL_WINDOW_SECONDS
        )
    return out


def _lag(a, b, shock_ts, z_threshold) -> dict:
    """Lead-lag of series a vs b under every variant. Positive => `a` moved
    first. A variant whose lag is missing because a first move only
    appeared after a data outage is listed in `censored` (as opposed to
    simply "never moved")."""
    result: dict = {}
    max_gap = max(outage_threshold(a), outage_threshold(b))
    censored: list[str] = []
    for method, k in VARIANTS:
        key = _vkey(method, k)
        try:
            detail = lead_lag_detail(a, b, shock_ts, z_threshold, min_consecutive=k, method=method, max_gap_seconds=max_gap)
            result[key] = detail["lag_s"]
            if detail["censored_a"] or detail["censored_b"]:
                censored.append(key)
        except ValueError as exc:
            result[key] = None
            result.setdefault("errors", {})[key] = str(exc)
    result["primary"] = result[PRIMARY]
    result["censored"] = censored
    return result


def analyze_market_hours_event(conn, event: ShockEvent, z_threshold: float = 3.0) -> dict:
    """Headline numbers for one captured RBI/Budget-style (or placebo) event.
    Positive lag => spot (or NIFTY) moved first; negative => the option /
    future (or BANKNIFTY) did."""
    shock_ts = event.scheduled_timestamp_utc
    shock = datetime.fromisoformat(shock_ts)
    is_placebo = event.event_name.startswith(PLACEBO_PREFIX)
    result: dict = {
        "event_name": event.event_name,
        "shock_utc": shock_ts,
        "is_placebo": is_placebo,
        "z_threshold": z_threshold,
        "primary_variant": PRIMARY,
        "underlyings": {},
        "feed_delay": {},
        "warnings": [],
    }

    spot_series: dict[str, list[tuple[str, float]]] = {}
    for underlying in event.affected_underlyings:
        spot_rows = fetch_ticks(conn, event.event_name, kind="spot", underlying=underlying)
        spot_ticks = rows_to_ticks(spot_rows)
        spot_series[underlying] = spot_ticks
        block: dict = {"spot": _analyze_series(spot_ticks, shock_ts, shock, z_threshold), "options": {}}
        result["underlyings"][underlying] = block
        delay = feed_delay_stats(spot_rows)
        if delay:
            result["feed_delay"][f"{underlying} spot"] = delay
        for outage in block["spot"].get("outages", []):
            start = datetime.fromisoformat(outage["start_utc"])
            end_offset = (start - shock).total_seconds() + outage["seconds"]
            at_shock = (start - shock).total_seconds() <= 60 and end_offset >= -60
            result["warnings"].append(
                f"{underlying} spot: DATA OUTAGE of {outage['seconds']:.0f}s starting {outage['start_utc']} "
                f"({(start - shock).total_seconds():+.0f}s from shock)"
                + (" -- AT THE SHOCK: first moves inside it are censored, not timed" if at_shock else "")
            )

        if not spot_ticks:
            result["warnings"].append(f"{underlying}: no spot ticks captured at all")
            continue

        # --- futures control leg
        fut_rows = fetch_ticks(conn, event.event_name, kind="future", underlying=underlying)
        if fut_rows:
            fut_ticks = rows_to_ticks(fut_rows)
            future = _analyze_series(fut_ticks, shock_ts, shock, z_threshold)
            future["spot_minus_future_lag_s"] = _lag(spot_ticks, fut_ticks, shock_ts, z_threshold)
            block["future"] = future
            delay = feed_delay_stats(fut_rows)
            if delay:
                result["feed_delay"][f"{underlying} future"] = delay
        else:
            result["warnings"].append(f"{underlying}: no futures ticks (control leg unavailable)")

        pre_spot = [p for t, p in spot_ticks if datetime.fromisoformat(t) < shock]
        if not pre_spot:
            result["warnings"].append(f"{underlying}: no pre-shock spot ticks, cannot choose an ATM option")
            continue

        option_rows = fetch_ticks(conn, event.event_name, kind="option", underlying=underlying)
        legs = pick_atm_legs(option_rows, pre_spot[-1], shock)
        if not legs:
            result["warnings"].append(f"{underlying}: no option contract had a usable baseline + post-shock ticks")

        rows_by_key: dict[str, list[dict]] = {}
        for r in option_rows:
            rows_by_key.setdefault(r["instrument_key"], []).append(r)

        for option_type, leg in legs.items():
            leg_rows = rows_by_key[leg["instrument_key"]]
            leg_ticks = rows_to_ticks(leg_rows)
            analysis = _analyze_series(leg_ticks, shock_ts, shock, z_threshold)
            analysis.update(instrument_key=leg["instrument_key"], strike=leg["strike_paise"] / 100, expiry=leg["expiry"])
            analysis["spot_minus_option_lag_s"] = _lag(spot_ticks, leg_ticks, shock_ts, z_threshold)

            # exchange-time variant (experimental) + feed staleness
            spot_x, leg_x = _exchange_ticks(spot_rows), _exchange_ticks(leg_rows)
            if len(spot_x) >= MIN_BASELINE_POINTS and len(leg_x) >= MIN_BASELINE_POINTS:
                analysis["spot_minus_option_lag_exchange_time_s"] = _lag(spot_x, leg_x, shock_ts, z_threshold)
            delay = feed_delay_stats(leg_rows)
            if delay:
                result["feed_delay"][f"{underlying} {option_type} ATM"] = delay

            # implied-vol series for the same contract, where the feed carried it
            iv_ticks = [(r["timestamp_utc"], float(r["iv"])) for r in leg_rows if r.get("iv")]
            if len(iv_ticks) >= MIN_BASELINE_POINTS:
                iv = _analyze_series(iv_ticks, shock_ts, shock, z_threshold, unit_scale=1.0)
                iv["spot_minus_iv_lag_s"] = _lag(spot_ticks, iv_ticks, shock_ts, z_threshold)
                analysis["iv"] = iv
            block["options"][option_type] = analysis

        # --- ATM +/- 2 strike basket: is the headline one thin strike?
        basket = pick_atm_basket(option_rows, pre_spot[-1], shock)
        block["basket"] = {}
        for option_type, contracts in basket.items():
            lags = []
            for c in contracts:
                c_ticks = rows_to_ticks(rows_by_key[c["instrument_key"]])
                lag = _lag(spot_ticks, c_ticks, shock_ts, z_threshold)
                lags.append({"strike": c["strike_paise"] / 100, "expiry": c["expiry"], "lag_s": lag})
            detected = [x["lag_s"]["primary"] for x in lags if x["lag_s"]["primary"] is not None]
            block["basket"][option_type] = {
                "legs": lags,
                "n_legs": len(lags),
                "n_detected": len(detected),
                "median_primary_lag_s": median(detected) if detected else None,
            }

    if "NIFTY" in spot_series and "BANKNIFTY" in spot_series:
        result["cross_index_lag_s"] = _lag(spot_series["NIFTY"], spot_series["BANKNIFTY"], shock_ts, z_threshold)

    for underlying, block in result["underlyings"].items():
        spot_n = block["spot"]["n_post"] + block["spot"]["n_pre"]
        if spot_n and block["spot"]["n_post"] == 0:
            result["warnings"].append(f"{underlying}: spot has ticks but none after the shock -- capture ended early?")

    result["false_positive_check"] = _false_positive_summary(result, is_placebo)
    result["placebo_summary"] = result["false_positive_check"]["by_variant"].get(PRIMARY, {"evaluated": 0, "fired": 0})
    return result


def _iter_series(result: dict):
    for block in result["underlyings"].values():
        if not block:
            continue
        yield block["spot"]
        if "future" in block:
            yield block["future"]
        for opt in block["options"].values():
            yield opt


def _false_positive_summary(result: dict, is_placebo: bool) -> dict:
    """How often the detector "finds a first move" where no news landed.
    On a PLACEBO event (a no-news control window recorded the same way as a
    real one) that's every detection after the pseudo-shock; on a real
    event it's the within-event pseudo-shock 5 minutes before the real one.
    Either way, a real-event lag is only interpretable against this rate."""
    by_variant: dict[str, dict] = {}
    for method, k in VARIANTS:
        key = _vkey(method, k)
        evaluated = fired = 0
        for series in _iter_series(result):
            if is_placebo:
                if ("errors" in series and key in series["errors"]) or series["censored_by_gap"].get(key):
                    continue  # unevaluable / the "detection" would only be a data-outage artifact
                evaluated += 1
                fired += series["first_move_offset_s"][key] is not None
            else:
                p = series["placebo_pre_shock"][key]
                if p["evaluated"]:
                    evaluated += 1
                    fired += bool(p["fired"])
        by_variant[key] = {"evaluated": evaluated, "fired": fired}
    return {"kind": "control_window" if is_placebo else "pre_shock_pseudo", "by_variant": by_variant}


def analyze_after_hours_event(conn, event: ShockEvent) -> dict:
    """Overnight-gap breakdown from record_gap_tick()'s named snapshots."""
    result: dict = {"event_name": event.event_name, "shock_utc": event.scheduled_timestamp_utc, "underlyings": {}, "warnings": []}
    for underlying in event.affected_underlyings:
        pre_close = fetch_gap_price(conn, event, "pre_close", underlying)
        post_open = fetch_gap_price(conn, event, "post_open", underlying)
        settled = fetch_gap_price(conn, event, "post_open_settled", underlying)
        if pre_close is None or post_open is None:
            missing = [n for n, v in (("pre_close", pre_close), ("post_open", post_open)) if v is None]
            result["warnings"].append(f"{underlying}: missing snapshot(s) {missing}; gap breakdown not computable")
            result["underlyings"][underlying] = None
            continue
        breakdown = gap_move_breakdown(pre_close, post_open, settled)
        breakdown["has_settled_snapshot"] = settled is not None
        result["underlyings"][underlying] = breakdown
    return result


def analyze_event(conn, event: ShockEvent, z_threshold: float = 3.0) -> dict:
    if event.market_hours_event:
        return analyze_market_hours_event(conn, event, z_threshold)
    return analyze_after_hours_event(conn, event)


def render_charts(conn, event: ShockEvent, result: dict, out_dir: Path | None = None) -> list[Path]:
    """Diffusion-timeline PNGs for a market-hours result: NIFTY spot vs. its
    chosen ATM call, and NIFTY vs. BANKNIFTY. Uses each series' own
    pre-shock mean (from `result`) as the normalization level."""
    from charts import (
        DEFAULT_CHART_DIR,
        plot_adjustment_progress,
        plot_cross_index_diffusion_timeline,
        plot_event_overview,
        plot_spot_option_diffusion_timeline,
    )

    out_dir = out_dir or DEFAULT_CHART_DIR
    shock_ts = event.scheduled_timestamp_utc
    paths: list[Path] = []
    nifty = result["underlyings"].get("NIFTY")
    if not nifty or "baseline_mean" not in nifty["spot"]:
        return paths

    nifty_ticks = rows_to_ticks(fetch_ticks(conn, event.event_name, kind="spot", underlying="NIFTY"))
    ce = nifty["options"].get("CE")

    # headline: adjustment progress, zoomed on the first minute, first moves marked
    progress_series = [("NIFTY spot", nifty_ticks, nifty["spot"]["baseline_mean"])]
    markers = {"NIFTY spot": nifty["spot"]["first_move_offset_s"].get("primary")}
    fut = nifty.get("future")
    if fut and "baseline_mean" in fut:
        fut_ticks = rows_to_ticks(fetch_ticks(conn, event.event_name, kind="future", underlying="NIFTY"))
        progress_series.append(("NIFTY future", fut_ticks, fut["baseline_mean"]))
        markers["NIFTY future"] = fut["first_move_offset_s"].get("primary")
    if ce and "baseline_mean" in ce:
        ce_progress_ticks = rows_to_ticks(fetch_ticks(conn, event.event_name, instrument_key=ce["instrument_key"]))
        progress_series.append((f"ATM {ce['strike']:.0f} CE", ce_progress_ticks, ce["baseline_mean"]))
        markers[f"ATM {ce['strike']:.0f} CE"] = ce["first_move_offset_s"].get("primary")
    paths.append(plot_adjustment_progress(event.event_name, progress_series, shock_ts, markers, out_dir=out_dir))
    overview = [("NIFTY spot", nifty_ticks)]
    if result["underlyings"].get("BANKNIFTY") and "baseline_mean" in result["underlyings"]["BANKNIFTY"]["spot"]:
        overview.append(("BANKNIFTY spot", rows_to_ticks(fetch_ticks(conn, event.event_name, kind="spot", underlying="BANKNIFTY"))))
    paths.insert(0, plot_event_overview(event.event_name, overview, shock_ts, out_dir=out_dir))

    if ce and "baseline_mean" in ce:
        ce_ticks = rows_to_ticks(fetch_ticks(conn, event.event_name, instrument_key=ce["instrument_key"]))
        paths.append(
            plot_spot_option_diffusion_timeline(
                event.event_name, nifty_ticks, ce_ticks, shock_ts,
                nifty["spot"]["baseline_mean"], ce["baseline_mean"], out_dir=out_dir,
            )
        )
    bank = result["underlyings"].get("BANKNIFTY")
    if bank and "baseline_mean" in bank["spot"]:
        bank_ticks = rows_to_ticks(fetch_ticks(conn, event.event_name, kind="spot", underlying="BANKNIFTY"))
        paths.append(
            plot_cross_index_diffusion_timeline(
                f"{event.event_name} cross-index", nifty_ticks, bank_ticks, shock_ts,
                nifty["spot"]["baseline_mean"], bank["spot"]["baseline_mean"], out_dir=out_dir,
            )
        )
    return paths


def write_result(result: dict, out_dir: Path = DEFAULT_RESULTS_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{safe_filename(result['event_name'])}.json"
    path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return path


def _fmt(v) -> str:
    return "n/a" if v is None else f"{v:+.1f}"


def _secs(v) -> str:
    return "n/a" if v is None else f"{v:+.1f}s"


def _lag_line(lag: dict) -> str:
    others = " ".join(f"{k}={_fmt(lag.get(k))}" for k in ("level_c1", "level_c3", "returns_c1"))
    note = f"  [CENSORED by data outage: {', '.join(lag['censored'])}]" if lag.get("censored") else ""
    return f"PRIMARY={_secs(lag.get('primary'))}  (sensitivity: {others}){note}"


def format_report(result: dict) -> str:
    lines = [f"=== {result['event_name']}  (shock {result['shock_utc']}) ==="]
    if result.get("is_placebo"):
        lines.append("PLACEBO / control window: any detection below is a FALSE POSITIVE, not a market reaction.")
    lines.append("lag > 0: spot (or NIFTY) moved first; lag < 0: the option / future (or BANKNIFTY) did.")
    for underlying, block in result["underlyings"].items():
        if block is None:
            lines.append(f"[{underlying}] no gap breakdown")
            continue
        if "spot" not in block:  # after-hours breakdown
            gf = block["gap_fraction"]
            lines.append(
                f"[{underlying}] gap_move={block['gap_move'] / 100:+.2f} followthrough={block['followthrough_move'] / 100:+.2f} "
                f"total={block['total_move'] / 100:+.2f} gap_fraction={'n/a' if gf is None else f'{gf:.2f}'}"
            )
            continue
        spot = block["spot"]
        lines.append(
            f"[{underlying}] spot ticks pre/post={spot['n_pre']}/{spot['n_post']} "
            f"first_move={_secs(spot['first_move_offset_s'].get('primary'))} settle90={_secs(spot.get('settle_90pct_s'))}"
            + (f"  ERROR: {spot['error']}" if "error" in spot else "")
        )
        d = spot.get("detectability")
        if d:
            lines.append(
                f"    shock detectability (descriptive): largest 5s move pre-shock {d['largest_5s_move_pre']:.1f} vs first 60s post "
                f"{d['largest_5s_move_post60']:.1f} (index points); net move at +60s/+300s/end: "
                f"{_fmt(d['net_move_at_60s'])}/{_fmt(d['net_move_at_300s'])}/{_fmt(d['net_move_at_end'])} vs pre-shock noise sd {_fmt(d['pre_noise_sd_last_120s'])}"
            )
        if "future" in block:
            f = block["future"]
            lines.append(f"    future: ticks pre/post={f['n_pre']}/{f['n_post']} spot-vs-future {_lag_line(f['spot_minus_future_lag_s'])}")
        for option_type, opt in block["options"].items():
            lines.append(
                f"    {option_type} {opt['strike']:.0f} exp {opt['expiry']}: ticks pre/post={opt['n_pre']}/{opt['n_post']} "
                f"settle90={_secs(opt.get('settle_90pct_s'))} | spot-vs-option {_lag_line(opt['spot_minus_option_lag_s'])}"
            )
            if "spot_minus_option_lag_exchange_time_s" in opt:
                lines.append(f"        exchange-time (experimental): {_lag_line(opt['spot_minus_option_lag_exchange_time_s'])}")
            if "iv" in opt:
                lines.append(f"        IV series: first_move={_secs(opt['iv']['first_move_offset_s'].get('primary'))} spot-vs-IV {_lag_line(opt['iv']['spot_minus_iv_lag_s'])}")
        for option_type, b in block.get("basket", {}).items():
            lines.append(f"    basket {option_type} (ATM+/-{BASKET_WIDTH}): {b['n_detected']}/{b['n_legs']} legs detected, median primary lag={_secs(b['median_primary_lag_s'])}")
    if "cross_index_lag_s" in result:
        lines.append(f"[NIFTY vs BANKNIFTY] {_lag_line(result['cross_index_lag_s'])}")
    fp = result.get("false_positive_check")
    if fp:
        p = fp["by_variant"][PRIMARY]
        lines.append(f"false-positive check ({fp['kind']}), primary variant: {p['fired']}/{p['evaluated']} series fired with no news")
    if result.get("feed_delay"):
        for name, d in result["feed_delay"].items():
            # only the index timestamp is a clean latency/jitter measure; for options and
            # futures the exchange stamp is the LAST TRADE time, so this is trade age on arrival
            label = "feed delay" if name.endswith("spot") else "last-trade age (not network delay)"
            lines.append(f"{label} {name}: median {d['median_s']:.2f}s p95 {d['p95_s']:.2f}s (n={d['n']})")
    for w in result["warnings"]:
        lines.append(f"WARNING: {w}")
    return "\n".join(lines)
