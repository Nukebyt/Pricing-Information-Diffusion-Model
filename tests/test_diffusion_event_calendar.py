import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "diffusion"))

from event_calendar import EVENTS, after_hours_events, market_hours_events, upcoming_events


def test_all_events_have_timezone_aware_utc_timestamps():
    for e in EVENTS:
        dt = datetime.fromisoformat(e.scheduled_timestamp_utc)
        assert dt.tzinfo is not None
        assert dt.utcoffset().total_seconds() == 0


def test_rbi_mpc_announced_at_1000_ist_which_is_0430_utc():
    rbi_events = [e for e in EVENTS if "RBI MPC" in e.event_name]
    assert len(rbi_events) == 5
    for e in rbi_events:
        dt = datetime.fromisoformat(e.scheduled_timestamp_utc)
        assert (dt.hour, dt.minute) == (4, 30)  # 10:00 IST == 04:30 UTC


def test_union_budget_at_1100_ist_which_is_0530_utc():
    budget = next(e for e in EVENTS if "Union Budget" in e.event_name)
    dt = datetime.fromisoformat(budget.scheduled_timestamp_utc)
    assert (dt.month, dt.day) == (2, 1)
    assert (dt.hour, dt.minute) == (5, 30)  # 11:00 IST == 05:30 UTC


def test_market_hours_events_excludes_cpi():
    names = {e.event_name for e in market_hours_events()}
    assert any("RBI MPC" in n for n in names)
    assert any("Union Budget" in n for n in names)
    assert all("CPI" not in n for n in names)


def test_after_hours_events_is_cpi_only():
    events = after_hours_events()
    assert events
    assert all("CPI" in e.event_name for e in events)
    assert all(e.market_hours_event is False for e in events)


def test_cpi_events_marked_approximate_confidence():
    for e in after_hours_events():
        assert e.timing_confidence == "approximate"


def test_rbi_and_budget_marked_minute_confidence():
    for e in market_hours_events():
        assert e.timing_confidence == "minute"


def test_upcoming_events_filters_past_dates():
    now = datetime(2026, 8, 26, tzinfo=timezone.utc)
    upcoming = upcoming_events(now)
    assert upcoming  # there should be real future events left this year
    for e in upcoming:
        assert datetime.fromisoformat(e.scheduled_timestamp_utc) > now
    # August 2026's MPC has already happened relative to `now`
    assert not any("Aug 2026" in e.event_name for e in upcoming)
    # October's has not
    assert any("Oct 2026" in e.event_name for e in upcoming)


def test_events_sorted_chronologically():
    timestamps = [e.scheduled_timestamp_utc for e in EVENTS]
    assert timestamps == sorted(timestamps)
