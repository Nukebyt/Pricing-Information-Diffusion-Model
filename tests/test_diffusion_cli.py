import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "data" / "proto"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "diffusion"))

import diffusion_cli
from diffusion_db import get_connection, insert_ticks
from tick_recorder import CaptureStats


@pytest.fixture(autouse=True)
def isolate_outputs(tmp_path, monkeypatch):
    # never write logs/results/charts into the real data/ directory from a test
    monkeypatch.setattr(diffusion_cli, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(diffusion_cli, "write_result", lambda result: tmp_path / "result.json")
    monkeypatch.setattr(diffusion_cli, "render_charts", lambda conn, event, result: [])


def test_events_lists_the_october_mpc(capsys):
    assert diffusion_cli.main(["events"]) == 0
    out = capsys.readouterr().out
    assert "RBI MPC Oct 2026" in out and "CPI September 2026" in out


def test_analyze_with_nothing_captured_returns_1(tmp_path, capsys):
    assert diffusion_cli.main(["analyze", "--db", str(tmp_path / "empty.db")]) == 1
    assert "nothing captured" in capsys.readouterr().out


def test_analyze_finds_captured_after_hours_event_by_label_prefix(tmp_path, capsys):
    db = tmp_path / "gap.db"
    conn = get_connection(db)
    base = {"kind": "spot", "underlying": "NIFTY", "instrument_key": "n", "strike_paise": None,
            "option_type": None, "timestamp_utc": "2026-10-13T03:50:00+00:00", "iv": None}
    insert_ticks(conn, [
        {**base, "event_name": "CPI September 2026 [pre_close]", "price_paise": 2_400_000},
        {**base, "event_name": "CPI September 2026 [post_open]", "price_paise": 2_410_000},
    ])
    assert diffusion_cli.main(["analyze", "--db", str(db)]) == 0
    assert "CPI September 2026" in capsys.readouterr().out


def test_capture_exits_nonzero_when_zero_ticks_received(tmp_path, monkeypatch):
    # The BUG-3 alarm: a "successful" capture with no ticks must not look like success.
    async def fake_run_capture(event, conn, insert_fn, pre, post, wait_for_start=True):
        return CaptureStats(connects=1, messages=3, messages_by_type={"market_info": 3})

    monkeypatch.setattr(diffusion_cli, "run_capture", fake_run_capture)
    monkeypatch.setattr(diffusion_cli, "seconds_until", lambda target: 100.0)  # pretend the window is still open
    assert diffusion_cli.main(["capture", "--event", "RBI MPC Oct 2026", "--db", str(tmp_path / "c.db")]) == 2


def test_capture_rejects_after_hours_event(tmp_path):
    with pytest.raises(SystemExit):
        diffusion_cli.main(["capture", "--event", "CPI September 2026", "--db", str(tmp_path / "c.db")])


def test_unknown_event_name_raises_keyerror(tmp_path):
    with pytest.raises(KeyError):
        diffusion_cli.main(["analyze", "--event", "RBI MPC Jan 1999", "--db", str(tmp_path / "x.db")])


def test_capture_placebo_at_builds_a_placebo_event(tmp_path, monkeypatch):
    seen = {}

    async def fake_run_capture(event, conn, insert_fn, pre, post, wait_for_start=True):
        seen["event"] = event
        return CaptureStats(connects=1, messages=1, ticks=0)

    monkeypatch.setattr(diffusion_cli, "run_capture", fake_run_capture)
    monkeypatch.setattr(diffusion_cli, "seconds_until", lambda target: 100.0)
    diffusion_cli.main(["capture", "--placebo-at", "2026-10-06 10:00", "--db", str(tmp_path / "p.db")])
    assert seen["event"].event_name == "PLACEBO 2026-10-06 10:00 IST"
    assert seen["event"].scheduled_timestamp_utc == "2026-10-06T04:30:00+00:00"


def test_analyze_picks_up_captured_placebo_windows(tmp_path, capsys):
    db = tmp_path / "pl.db"
    conn = get_connection(db)
    insert_ticks(conn, [{
        "event_name": "PLACEBO 2026-10-06 10:00 IST", "instrument_key": "n", "kind": "spot", "underlying": "NIFTY",
        "strike_paise": None, "option_type": None, "timestamp_utc": "2026-10-06T04:29:00+00:00", "price_paise": 100, "iv": None,
    }])
    assert diffusion_cli.main(["analyze", "--db", str(db), "--no-charts"]) == 0
    assert "PLACEBO" in capsys.readouterr().out


def test_demo_runs_end_to_end_and_recovers_the_injected_lags(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(diffusion_cli, "DEFAULT_DB_PATH", tmp_path / "real.db")  # demo writes beside this, under demo/
    monkeypatch.setattr(diffusion_cli, "write_result", lambda result, out_dir=None: tmp_path / "r.json")
    monkeypatch.setattr(diffusion_cli, "render_charts", lambda conn, event, result, out_dir=None: [])
    assert diffusion_cli.main(["demo"]) == 0
    out = capsys.readouterr().out
    assert "SYNTHETIC DATA" in out
    assert "spot-vs-option PRIMARY=+3.0s" in out  # injected: spot +3s, option +6s
    assert "spot-vs-future PRIMARY=-2.0s" in out  # injected: future +1s, spot +3s
    assert (tmp_path / "demo" / "demo_ticks.db").exists()


def test_report_requires_results_then_writes_summary(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(diffusion_cli, "DEFAULT_RESULTS_DIR", tmp_path / "results")
    assert diffusion_cli.main(["report"]) == 1
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "RBI.json").write_text(
        '{"event_name": "RBI MPC Oct 2026", "is_placebo": false, "underlyings": {"NIFTY": {"spot": {}, "options": '
        '{"CE": {"spot_minus_option_lag_s": {"primary": 4.0}}}}}}', encoding="utf-8")
    assert diffusion_cli.main(["report"]) == 0
    assert (tmp_path / "results" / "SUMMARY.md").exists()
    assert "+4.0" in capsys.readouterr().out
