"""Phase 4: diffusion-timeline visualization -- two normalized price paths
around a shock, shock time marked. Same matplotlib-to-PNG pattern as
../backtest/analyze_violations.py's chart output.

plot_diffusion_timeline() is a general two-series primitive (same pattern
as lag_detection.py's lead_lag_seconds, ROADMAP.md Phase 5): it doesn't
care what series_a/series_b represent. plot_spot_option_diffusion_timeline()
and plot_cross_index_diffusion_timeline() are thin, semantically-named
wrappers over it -- the headline spot-vs-option chart and the Phase 5
NIFTY-vs-BANKNIFTY chart share the same plotting code, not two copies of it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from adjustment_curves import normalized_price_path  # noqa: E402
from paths import safe_filename  # noqa: E402

DEFAULT_CHART_DIR = Path(__file__).resolve().parents[2] / "data" / "diffusion_charts"


def plot_diffusion_timeline(
    event_name: str,
    series_a_ticks: list[tuple[str, float]],
    series_b_ticks: list[tuple[str, float]],
    shock_timestamp_utc: str,
    series_a_pre_level: float,
    series_b_pre_level: float,
    series_a_label: str = "Series A (normalized)",
    series_b_label: str = "Series B (normalized)",
    out_dir: Path = DEFAULT_CHART_DIR,
) -> Path:
    series_a_path = normalized_price_path(series_a_ticks, shock_timestamp_utc, series_a_pre_level)
    series_b_path = normalized_price_path(series_b_ticks, shock_timestamp_utc, series_b_pre_level)

    fig, ax = plt.subplots(figsize=(10, 5))
    if series_a_path:
        xs, ys = zip(*series_a_path)
        ax.plot(xs, ys, label=series_a_label, color="tab:blue")
    if series_b_path:
        xs, ys = zip(*series_b_path)
        ax.plot(xs, ys, label=series_b_label, color="tab:orange")
    ax.axvline(0, color="black", linestyle="--", linewidth=1, label="shock")
    ax.set_xlabel("Seconds from shock")
    ax.set_ylabel("Price / pre-shock level")
    ax.set_title(f"Diffusion timeline: {event_name}")
    ax.legend()
    fig.tight_layout()

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{safe_filename(event_name)}_diffusion_timeline.png"
    fig.savefig(out_path)
    plt.close(fig)
    return out_path


def plot_spot_option_diffusion_timeline(
    event_name: str,
    spot_ticks: list[tuple[str, float]],
    option_ticks: list[tuple[str, float]],
    shock_timestamp_utc: str,
    spot_pre_level: float,
    option_pre_level: float,
    out_dir: Path = DEFAULT_CHART_DIR,
) -> Path:
    """The project's headline chart (ROADMAP.md S1)."""
    return plot_diffusion_timeline(
        event_name,
        spot_ticks,
        option_ticks,
        shock_timestamp_utc,
        spot_pre_level,
        option_pre_level,
        series_a_label="NIFTY spot (normalized)",
        series_b_label="Near-ATM option (normalized)",
        out_dir=out_dir,
    )


def plot_cross_index_diffusion_timeline(
    event_name: str,
    nifty_ticks: list[tuple[str, float]],
    banknifty_ticks: list[tuple[str, float]],
    shock_timestamp_utc: str,
    nifty_pre_level: float,
    banknifty_pre_level: float,
    out_dir: Path = DEFAULT_CHART_DIR,
) -> Path:
    """Phase 5 stretch chart -- which index absorbed the shock first."""
    return plot_diffusion_timeline(
        event_name,
        nifty_ticks,
        banknifty_ticks,
        shock_timestamp_utc,
        nifty_pre_level,
        banknifty_pre_level,
        series_a_label="NIFTY spot (normalized)",
        series_b_label="BANKNIFTY spot (normalized)",
        out_dir=out_dir,
    )


def plot_adjustment_progress(
    event_name: str,
    series: list[tuple[str, list[tuple[str, float]], float]],
    shock_timestamp_utc: str,
    markers: dict[str, float | None] | None = None,
    window_seconds: tuple[float, float] = (-10.0, 60.0),
    settle_window_seconds: tuple[float, float] = (20.0, 60.0),
    out_dir: Path = DEFAULT_CHART_DIR,
) -> Path:
    """The chart that makes a lead-lag legible: every series rescaled to
    ADJUSTMENT PROGRESS -- 0 at its own pre-shock level, 1 at its own
    short-horizon settled level (median over settle_window_seconds after
    the shock) -- and zoomed on the first minute. Raw normalized prices
    can't do this: a 0.2% index move and a 10% option move on one axis
    leave the index looking flat. markers: label -> detected first-move
    offset in seconds, drawn as vertical lines in the series' colour.

    series: [(label, ticks, pre_level), ...] (pre_level is the fallback when
    there are no ticks in the 30s before the shock). A series whose settled level
    equals its pre level (no net move) or that has no ticks in the settle
    window is skipped rather than drawn as a meaningless flat line."""
    from datetime import datetime
    from statistics import median

    shock = datetime.fromisoformat(shock_timestamp_utc)
    colours = ["tab:blue", "tab:orange", "tab:green", "tab:red"]
    fig, ax = plt.subplots(figsize=(10, 5))
    drawn = 0
    for i, (label, ticks, pre_level) in enumerate(series):
        points = sorted(((datetime.fromisoformat(t) - shock).total_seconds(), p) for t, p in ticks)
        settle = [p for x, p in points if settle_window_seconds[0] <= x <= settle_window_seconds[1]]
        if not settle:
            continue
        # anchor 0 at the level just BEFORE the shock (median of the last 30s),
        # not the whole-window mean: a drifting series would otherwise start
        # the chart away from 0 and fake an early "move".
        just_before = [p for x, p in points if -30.0 <= x < 0.0]
        if just_before:
            pre_level = median(just_before)
        settled = median(settle)
        if settled == pre_level:
            continue
        xs = [x for x, _ in points if window_seconds[0] <= x <= window_seconds[1]]
        ys = [(p - pre_level) / (settled - pre_level) for x, p in points if window_seconds[0] <= x <= window_seconds[1]]
        if not xs:
            continue
        colour = colours[i % len(colours)]
        ax.step(xs, ys, where="post", label=label, color=colour, linewidth=1.4)
        marker = (markers or {}).get(label)
        if marker is not None:
            ax.axvline(marker, color=colour, linestyle=":", linewidth=1)
        drawn += 1
    ax.axvline(0, color="black", linestyle="--", linewidth=1, label="shock")
    ax.axhline(0, color="grey", linewidth=0.6)
    ax.axhline(1, color="grey", linewidth=0.6)
    ax.axhline(0.9, color="grey", linestyle=":", linewidth=0.8)
    ax.set_xlim(*window_seconds)
    ax.set_xlabel("Seconds from shock")
    ax.set_ylabel("Adjustment progress (0 = pre-shock level, 1 = settled level)")
    ax.set_title(f"Adjustment progress: {event_name}")
    if drawn:
        ax.legend()
    fig.tight_layout()

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{safe_filename(event_name)}_adjustment_progress.png"
    fig.savefig(out_path)
    plt.close(fig)
    return out_path


def plot_event_overview(
    event_name: str,
    series: list[tuple[str, list[tuple[str, float]]]],
    shock_timestamp_utc: str,
    unit_scale: float = 100.0,
    out_dir: Path = DEFAULT_CHART_DIR,
) -> Path:
    """The honest picture: each index's move from its last pre-shock price,
    in POINTS, over the WHOLE capture window, with the pre-shock noise band
    (+/- 3 sd of the level over the last 2 minutes before the shock)
    shaded. It answers "did anything happen at the shock, and on what
    timescale?" before any lead-lag is read -- if the line never leaves the
    band at the shock, or only drifts out minutes later, a second-scale
    lead-lag has nothing to measure. No rescaling to a 'settled level'
    (that is what makes a noise-sized drift look like a staircase in
    plot_adjustment_progress).

    series: [(label, ticks), ...] with ticks in paise (unit_scale=100)."""
    from datetime import datetime
    from statistics import pstdev

    shock = datetime.fromisoformat(shock_timestamp_utc)
    fig, axes = plt.subplots(len(series), 1, figsize=(10, 3.2 * len(series)), sharex=True, squeeze=False)
    for ax, (label, ticks) in zip(axes[:, 0], series):
        points = sorted(((datetime.fromisoformat(t) - shock).total_seconds(), p / unit_scale) for t, p in ticks)
        pre = [(x, p) for x, p in points if x < 0]
        if len(pre) < 5:
            ax.set_title(f"{label}: not enough pre-shock ticks")
            continue
        base = pre[-1][1]
        recent = [p for x, p in pre if x >= -120]
        sd = pstdev(recent) if len(recent) > 1 else 0.0
        ax.fill_between([points[0][0], points[-1][0]], -3 * sd, 3 * sd, color="grey", alpha=0.2, label=f"+/-3 sd pre-shock noise ({3 * sd:.1f} pts)")
        ax.plot([x for x, _ in points], [p - base for _, p in points], linewidth=0.9, label=label)
        ax.axvline(0, color="black", linestyle="--", linewidth=1, label="shock")
        ax.set_ylabel("Move from last pre-shock price (points)")
        ax.legend(loc="upper left", fontsize=8)
    axes[-1, 0].set_xlabel("Seconds from shock")
    fig.suptitle(f"Event overview: {event_name}")
    fig.tight_layout()

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{safe_filename(event_name)}_overview.png"
    fig.savefig(out_path)
    plt.close(fig)
    return out_path
