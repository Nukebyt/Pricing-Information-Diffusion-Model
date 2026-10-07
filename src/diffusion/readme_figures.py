"""Builds the figures and summary numbers shown in the README from a
captured event in the tick database (opened read-only).

    python src/diffusion/readme_figures.py                      # RBI MPC Oct 2026 -> docs/images, docs/results
    python src/diffusion/readme_figures.py --event "RBI MPC Oct 2026" --db data/diffusion_ticks.db

The tick database itself is not in the repository (hundreds of MB); this
script plus the summary JSON in docs/results/ is how the published
numbers and charts were produced.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import pstdev

sys.path.insert(0, str(Path(__file__).resolve().parent))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from diffusion_db import DEFAULT_DB_PATH  # noqa: E402
from event_calendar import get_event  # noqa: E402
from paths import safe_filename  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
IST = timezone(timedelta(hours=5, minutes=30))
BLUE, ORANGE, GREEN, GREY = "#1f77b4", "#ff7f0e", "#2ca02c", "#9aa0a6"


def load(conn, event_name: str, where: str, shock_ts: float) -> list[tuple[float, float]]:
    """[(seconds from shock, price)] for one series, oldest first."""
    rows = conn.execute(
        f"SELECT timestamp_utc, price_paise FROM diffusion_ticks WHERE event_name = ? AND {where} ORDER BY timestamp_utc, id",
        (event_name,),
    ).fetchall()
    return [(datetime.fromisoformat(t).timestamp() - shock_ts, p / 100.0) for t, p in rows]


def price_at(series: list[tuple[float, float]], offset: float) -> float | None:
    last = None
    for x, p in series:
        if x <= offset:
            last = p
        else:
            break
    return last


def noise_sd(series: list[tuple[float, float]]) -> float:
    recent = [p for x, p in series if -120 <= x < 0]
    return pstdev(recent) if len(recent) > 1 else 0.0


def fig_overview(nifty, bank, out: Path) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(10, 6.4), sharex=True)
    for ax, series, label, colour in ((axes[0], nifty, "NIFTY 50", BLUE), (axes[1], bank, "BANK NIFTY", ORANGE)):
        base = price_at(series, -0.001)
        sd = noise_sd(series)
        ax.fill_between([series[0][0], series[-1][0]], -3 * sd, 3 * sd, color=GREY, alpha=0.25, label="normal pre-announcement wiggle")
        ax.plot([x for x, _ in series], [p - base for _, p in series], color=colour, linewidth=1.0, label=label)
        ax.axvline(0, color="black", linestyle="--", linewidth=1, label="RBI announcement (10:00 IST)")
        ax.set_ylabel("Points moved\nsince 10:00")
        ax.legend(loc="upper left", fontsize=8)
        ax.grid(alpha=0.2)
    axes[1].set_xlabel("Seconds after the announcement (1,800 s = 30 minutes)")
    fig.suptitle("How the market digested the RBI decision, minute by minute", fontsize=12)
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)


def fig_both_indices_pct(nifty, bank, out: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 4.2))
    for series, label, colour in ((nifty, "NIFTY 50", BLUE), (bank, "BANK NIFTY", ORANGE)):
        base = price_at(series, -0.001)
        ax.plot([x / 60 for x, _ in series], [(p / base - 1) * 100 for _, p in series], color=colour, linewidth=1.1, label=label)
    ax.axvline(0, color="black", linestyle="--", linewidth=1)
    ax.axhline(0, color=GREY, linewidth=0.6)
    ax.set_xlabel("Minutes after the announcement")
    ax.set_ylabel("Change since 10:00 (%)")
    ax.set_title("Both indices rallied together; banks moved about twice as much")
    ax.legend()
    ax.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)


def fig_first_minutes(nifty, future, out: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 4.2))
    sd = noise_sd(nifty)
    lo, hi = -60, 300
    ax.fill_between([lo, hi], -3 * sd, 3 * sd, color=GREY, alpha=0.25, label="normal pre-announcement wiggle")
    for series, label, colour in ((nifty, "NIFTY 50 index", BLUE), (future, "NIFTY futures", GREEN)):
        base = price_at(series, -0.001)
        pts = [(x, p - base) for x, p in series if lo <= x <= hi]
        ax.plot([x for x, _ in pts], [p for _, p in pts], color=colour, linewidth=1.1, label=label)
    ax.axvline(0, color="black", linestyle="--", linewidth=1, label="announcement")
    ax.set_xlim(lo, hi)
    ax.set_xlabel("Seconds after the announcement")
    ax.set_ylabel("Points moved since 10:00")
    ax.set_title("The first five minutes, up close: the index and its futures moved in step")
    ax.legend(loc="lower left", fontsize=8)
    ax.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)


def fig_capture_activity(conn, event_name: str, shock_ts: float, out: Path) -> dict:
    """Ticks recorded per 10 seconds, stacked by instrument type."""
    bin_s = 10
    counts: dict[str, dict[int, int]] = {"option": {}, "future": {}, "spot": {}}
    for kind, ts in conn.execute("SELECT kind, timestamp_utc FROM diffusion_ticks WHERE event_name = ?", (event_name,)):
        b = int((datetime.fromisoformat(ts).timestamp() - shock_ts) // bin_s)
        counts[kind][b] = counts[kind].get(b, 0) + 1
    bins = sorted({b for c in counts.values() for b in c})
    fig, ax = plt.subplots(figsize=(10, 3.8))
    bottom = [0] * len(bins)
    labels = {"option": "Option quotes", "future": "Futures quotes", "spot": "Index prices"}
    for kind, colour in (("option", BLUE), ("future", GREEN), ("spot", ORANGE)):
        ys = [counts[kind].get(b, 0) / bin_s for b in bins]
        ax.bar([b * bin_s / 60 for b in bins], ys, width=bin_s / 60, bottom=bottom, color=colour, label=labels[kind])
        bottom = [a + b for a, b in zip(bottom, ys)]
    ax.axvline(0, color="black", linestyle="--", linewidth=1)
    ax.set_xlabel("Minutes after the announcement")
    ax.set_ylabel("Ticks recorded per second")
    ax.set_title("A steady, uninterrupted recording: about 380 price updates per second")
    ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return {k: sum(v.values()) for k, v in counts.items()}


def summarize(conn, event_name: str, shock_ts: float, series: dict[str, list[tuple[float, float]]], totals: dict) -> dict:
    checkpoints = {"1 min": 60, "5 min": 300, "10 min": 600, "20 min": 1200, "30 min": 1795}
    summary: dict = {"event": event_name, "ticks_recorded": totals, "ticks_total": sum(totals.values()), "moves_points": {}, "moves_percent": {}}
    for label, s in series.items():
        base = price_at(s, -0.001)
        summary["moves_points"][label] = {k: round(price_at(s, v) - base, 1) for k, v in checkpoints.items()}
        summary["moves_percent"][label] = {k: round((price_at(s, v) / base - 1) * 100, 3) for k, v in checkpoints.items()}
        summary.setdefault("price_before_announcement", {})[label] = round(base, 2)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", default="RBI MPC Oct 2026")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--images", default=str(REPO / "docs" / "images"))
    parser.add_argument("--results", default=str(REPO / "docs" / "results"))
    args = parser.parse_args()

    images, results = Path(args.images), Path(args.results)
    images.mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)

    event = get_event(args.event)
    shock_ts = datetime.fromisoformat(event.scheduled_timestamp_utc).timestamp()
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=30)

    nifty = load(conn, event.event_name, "kind='spot' AND underlying='NIFTY'", shock_ts)
    bank = load(conn, event.event_name, "kind='spot' AND underlying='BANKNIFTY'", shock_ts)
    nifty_fut = load(conn, event.event_name, "kind='future' AND underlying='NIFTY'", shock_ts)

    fig_overview(nifty, bank, images / "01_market_overview.png")
    fig_both_indices_pct(nifty, bank, images / "02_both_indices.png")
    fig_first_minutes(nifty, nifty_fut, images / "03_first_five_minutes.png")
    totals = fig_capture_activity(conn, event.event_name, shock_ts, images / "04_recording_activity.png")

    summary = summarize(conn, event.event_name, shock_ts, {"NIFTY 50": nifty, "NIFTY futures": nifty_fut, "BANK NIFTY": bank}, totals)
    summary["window_ist"] = "09:50-10:30"
    (results / f"{safe_filename(event.event_name)}_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
