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
    out_path = out_dir / f"{event_name.replace(' ', '_')}_diffusion_timeline.png"
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
