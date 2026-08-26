import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "diffusion"))

from charts import plot_cross_index_diffusion_timeline, plot_diffusion_timeline, plot_spot_option_diffusion_timeline

SHOCK = datetime(2026, 10, 7, 4, 30, tzinfo=timezone.utc)


def test_plot_spot_option_diffusion_timeline_writes_a_png(tmp_path):
    spot_ticks = [
        (SHOCK.isoformat(), 24000.0),
        ((SHOCK + timedelta(seconds=10)).isoformat(), 24100.0),
    ]
    option_ticks = [
        (SHOCK.isoformat(), 150.0),
        ((SHOCK + timedelta(seconds=40)).isoformat(), 170.0),
    ]
    out_path = plot_spot_option_diffusion_timeline(
        event_name="RBI MPC Oct 2026",
        spot_ticks=spot_ticks,
        option_ticks=option_ticks,
        shock_timestamp_utc=SHOCK.isoformat(),
        spot_pre_level=24000.0,
        option_pre_level=150.0,
        out_dir=tmp_path,
    )
    assert out_path.exists()
    assert out_path.suffix == ".png"
    assert out_path.stat().st_size > 0


def test_plot_cross_index_diffusion_timeline_writes_a_png(tmp_path):
    nifty_ticks = [
        (SHOCK.isoformat(), 24000.0),
        ((SHOCK + timedelta(seconds=10)).isoformat(), 24100.0),
    ]
    banknifty_ticks = [
        (SHOCK.isoformat(), 51000.0),
        ((SHOCK + timedelta(seconds=25)).isoformat(), 51200.0),
    ]
    out_path = plot_cross_index_diffusion_timeline(
        event_name="RBI MPC Oct 2026",
        nifty_ticks=nifty_ticks,
        banknifty_ticks=banknifty_ticks,
        shock_timestamp_utc=SHOCK.isoformat(),
        nifty_pre_level=24000.0,
        banknifty_pre_level=51000.0,
        out_dir=tmp_path,
    )
    assert out_path.exists()
    assert out_path.stat().st_size > 0


def test_plot_diffusion_timeline_generic_primitive_uses_custom_labels(tmp_path):
    series_a = [(SHOCK.isoformat(), 100.0), ((SHOCK + timedelta(seconds=5)).isoformat(), 110.0)]
    series_b = [(SHOCK.isoformat(), 200.0), ((SHOCK + timedelta(seconds=5)).isoformat(), 205.0)]
    out_path = plot_diffusion_timeline(
        event_name="generic test",
        series_a_ticks=series_a,
        series_b_ticks=series_b,
        shock_timestamp_utc=SHOCK.isoformat(),
        series_a_pre_level=100.0,
        series_b_pre_level=200.0,
        series_a_label="A",
        series_b_label="B",
        out_dir=tmp_path,
    )
    assert out_path.exists()
