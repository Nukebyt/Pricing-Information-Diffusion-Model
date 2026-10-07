"""Cross-event pooling: small-sample statistics + the markdown summary.

Everything here is deliberately conservative. With one real scheduled event
per month or so, the honest framing is descriptive until n is large enough
for an interval to mean something -- below MIN_N_FOR_INFERENCE no interval
or p-value is produced at all (an n=3 bootstrap CI is a number, not
evidence). Above it, a percentile-bootstrap CI for the median and an exact
two-sided sign test (is the lag's sign consistent across events?) are
reported, still labelled small-sample.
"""
from __future__ import annotations

import json
import random
from math import comb
from pathlib import Path
from statistics import mean, median, pstdev

MIN_N_FOR_INFERENCE = 6


def bootstrap_median_ci(values: list[float], n_boot: int = 10_000, alpha: float = 0.05, seed: int = 0) -> tuple[float, float]:
    """Percentile bootstrap CI for the median. Seeded: the same data must
    always print the same interval."""
    if not values:
        raise ValueError("need at least one value")
    rng = random.Random(seed)
    n = len(values)
    medians = sorted(median(rng.choices(values, k=n)) for _ in range(n_boot))
    lo = medians[int((alpha / 2) * n_boot)]
    hi = medians[min(n_boot - 1, int((1 - alpha / 2) * n_boot))]
    return lo, hi


def sign_test_p(values: list[float]) -> float | None:
    """Exact two-sided binomial sign test of H0: P(value > 0) = 0.5.
    Zeros are dropped (standard). None if nothing is left."""
    nonzero = [v for v in values if v != 0]
    n = len(nonzero)
    if n == 0:
        return None
    k = sum(1 for v in nonzero if v > 0)
    tail = sum(comb(n, i) for i in range(0, min(k, n - k) + 1)) / 2**n
    return min(1.0, 2 * tail)


def pooled_summary(values: list[float]) -> dict:
    n = len(values)
    out: dict = {"n": n, "mean": None, "median": None, "stdev": None, "n_positive": 0, "n_negative": 0,
                 "median_ci95": None, "sign_test_p": None, "note": None}
    if n == 0:
        out["note"] = "no events"
        return out
    out.update(
        mean=mean(values), median=median(values), stdev=pstdev(values) if n > 1 else 0.0,
        n_positive=sum(1 for v in values if v > 0), n_negative=sum(1 for v in values if v < 0),
    )
    if n < MIN_N_FOR_INFERENCE:
        out["note"] = f"descriptive only: n={n} < {MIN_N_FOR_INFERENCE}, no interval or test reported"
    else:
        out["median_ci95"] = bootstrap_median_ci(values)
        out["sign_test_p"] = sign_test_p(values)
        out["note"] = "small-sample: bootstrap CI and exact sign test, not a claim of generality"
    return out


# Series each event contributes one primary lag for, as (label, extractor).
def _lag_paths(result: dict) -> dict[str, float | None]:
    lags: dict[str, float | None] = {}
    for underlying, block in result.get("underlyings", {}).items():
        if not block or "options" not in block:
            continue
        for option_type, opt in block["options"].items():
            lags[f"{underlying} spot vs ATM {option_type}"] = opt["spot_minus_option_lag_s"].get("primary")
        fut = block.get("future")
        if fut and "spot_minus_future_lag_s" in fut:
            lags[f"{underlying} spot vs future"] = fut["spot_minus_future_lag_s"].get("primary")
    if "cross_index_lag_s" in result:
        lags["NIFTY vs BANKNIFTY"] = result["cross_index_lag_s"].get("primary")
    return lags


def _is_market_hours_result(result: dict) -> bool:
    """After-hours (gap) results carry gap breakdowns, not per-series blocks."""
    return any(block and "options" in block for block in result.get("underlyings", {}).values())


def pool_results(results: list[dict]) -> dict[str, dict]:
    """results: analyze_market_hours_event() dicts for REAL (non-placebo)
    events. One pooled_summary per comparison, over events where the primary
    lag was defined."""
    per_label: dict[str, list[float]] = {}
    for result in results:
        if result.get("is_placebo"):
            continue
        for label, lag in _lag_paths(result).items():
            if lag is not None:
                per_label.setdefault(label, []).append(lag)
    return {label: pooled_summary(values) for label, values in per_label.items()}


def render_markdown_report(results: list[dict]) -> str:
    real = [r for r in results if not r.get("is_placebo") and _is_market_hours_result(r)]
    placebos = [r for r in results if r.get("is_placebo")]
    lines = ["# Results summary", "",
             "Positive lag = the first-named series moved first (e.g. spot led the option by N seconds). "
             "Primary method: 3-tick-confirmed return-based detector (pre-registered, BUGS.md DEC-10).", ""]
    lines += [f"Real events pooled: **{len(real)}**" + (" -- " + ", ".join(r["event_name"] for r in real) if real else ""), ""]

    if real:
        lines += ["## Per-event primary lags (seconds)", "", "| Event | Comparison | Primary lag |", "|---|---|---|"]
        for r in real:
            for label, lag in _lag_paths(r).items():
                lines.append(f"| {r['event_name']} | {label} | {'n/a (no detection)' if lag is None else f'{lag:+.1f}'} |")
        lines += ["", "## Pooled across events", "", "| Comparison | n | median | mean | stdev | +/- | 95% CI (median) | sign-test p | note |", "|---|---|---|---|---|---|---|---|---|"]
        for label, s in pool_results(real).items():
            ci = "-" if s["median_ci95"] is None else f"[{s['median_ci95'][0]:+.1f}, {s['median_ci95'][1]:+.1f}]"
            p = "-" if s["sign_test_p"] is None else f"{s['sign_test_p']:.3f}"
            lines.append(f"| {label} | {s['n']} | {s['median']:+.1f} | {s['mean']:+.1f} | {s['stdev']:.1f} | {s['n_positive']}/{s['n_negative']} | {ci} | {p} | {s['note']} |")
        lines.append("")

    if placebos:
        lines += ["## Placebo (no-news control windows)", "",
                  "Same detector, same window, a day/time with no scheduled event. Any detection here is a false positive; "
                  "a real-event lag is only interpretable relative to this noise floor.", "",
                  "| Placebo | Series evaluated | Fired (false positives) |", "|---|---|---|"]
        for r in placebos:
            ps = r.get("placebo_summary", {})
            lines.append(f"| {r['event_name']} | {ps.get('evaluated', 0)} | {ps.get('fired', 0)} |")
        lines.append("")

    lines += ["## Caveats", "",
              "- Each event is one observation; pooled statistics are descriptive/small-sample, never a claim of generality.",
              "- Index ticks arrive ~1/s; option and futures quotes update on every change. See feed-delay diagnostics in each event's JSON.",
              "- Single broker feed (Upstox) of NSE."]
    return "\n".join(lines) + "\n"


def load_results(results_dir: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(results_dir.glob("*.json")) if p.name != "summary.json"]
