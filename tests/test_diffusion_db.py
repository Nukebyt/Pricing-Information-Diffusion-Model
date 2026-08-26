import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "diffusion"))

from diffusion_db import fetch_ticks, get_connection, insert_ticks, rows_to_ticks


def _row(event_name="RBI MPC Oct 2026", kind="spot", underlying="NIFTY", ts="2026-10-07T04:30:05+00:00", price=2410000):
    return {
        "event_name": event_name,
        "instrument_key": "NSE_INDEX|Nifty 50" if underlying == "NIFTY" else "NSE_INDEX|Nifty Bank",
        "kind": kind,
        "underlying": underlying,
        "strike_paise": None,
        "option_type": None,
        "timestamp_utc": ts,
        "price_paise": price,
        "iv": None,
    }


def test_insert_and_fetch_round_trip(tmp_path):
    conn = get_connection(tmp_path / "diffusion_ticks.db")
    insert_ticks(conn, [_row()])
    rows = fetch_ticks(conn, "RBI MPC Oct 2026")
    assert len(rows) == 1
    assert rows[0]["price_paise"] == 2410000
    assert rows[0]["kind"] == "spot"


def test_fetch_filters_by_event_name(tmp_path):
    conn = get_connection(tmp_path / "diffusion_ticks.db")
    insert_ticks(conn, [_row(event_name="RBI MPC Oct 2026"), _row(event_name="RBI MPC Dec 2026")])
    rows = fetch_ticks(conn, "RBI MPC Dec 2026")
    assert len(rows) == 1
    assert rows[0]["event_name"] == "RBI MPC Dec 2026"


def test_fetch_filters_by_kind(tmp_path):
    conn = get_connection(tmp_path / "diffusion_ticks.db")
    insert_ticks(conn, [_row(kind="spot"), _row(kind="option", price=15000)])
    rows = fetch_ticks(conn, "RBI MPC Oct 2026", kind="option")
    assert len(rows) == 1
    assert rows[0]["kind"] == "option"


def test_fetch_ordered_by_timestamp(tmp_path):
    conn = get_connection(tmp_path / "diffusion_ticks.db")
    insert_ticks(
        conn,
        [
            _row(ts="2026-10-07T04:30:10+00:00"),
            _row(ts="2026-10-07T04:30:00+00:00"),
            _row(ts="2026-10-07T04:30:05+00:00"),
        ],
    )
    rows = fetch_ticks(conn, "RBI MPC Oct 2026")
    timestamps = [r["timestamp_utc"] for r in rows]
    assert timestamps == sorted(timestamps)


def test_insert_ticks_empty_list_is_noop(tmp_path):
    conn = get_connection(tmp_path / "diffusion_ticks.db")
    insert_ticks(conn, [])
    assert fetch_ticks(conn, "anything") == []


# --- Phase 5: NIFTY-vs-BANKNIFTY needs both indices' spot rows separable
# even though they share event_name and kind="spot" ---

def test_fetch_filters_by_underlying(tmp_path):
    conn = get_connection(tmp_path / "diffusion_ticks.db")
    insert_ticks(conn, [_row(underlying="NIFTY", price=2410000), _row(underlying="BANKNIFTY", price=5120000)])
    rows = fetch_ticks(conn, "RBI MPC Oct 2026", kind="spot", underlying="BANKNIFTY")
    assert len(rows) == 1
    assert rows[0]["underlying"] == "BANKNIFTY"
    assert rows[0]["price_paise"] == 5120000


def test_fetch_underlying_and_kind_filters_combine(tmp_path):
    conn = get_connection(tmp_path / "diffusion_ticks.db")
    insert_ticks(
        conn,
        [
            _row(underlying="NIFTY", kind="spot"),
            _row(underlying="NIFTY", kind="option", price=15000),
            _row(underlying="BANKNIFTY", kind="spot", price=5120000),
        ],
    )
    rows = fetch_ticks(conn, "RBI MPC Oct 2026", kind="spot", underlying="NIFTY")
    assert len(rows) == 1
    assert rows[0]["underlying"] == "NIFTY"
    assert rows[0]["kind"] == "spot"


def test_rows_to_ticks_extracts_timestamp_and_paise_price():
    rows = [_row(ts="2026-10-07T04:30:00+00:00", price=2410000), _row(ts="2026-10-07T04:30:05+00:00", price=2411000)]
    ticks = rows_to_ticks(rows)
    assert ticks == [("2026-10-07T04:30:00+00:00", 2410000.0), ("2026-10-07T04:30:05+00:00", 2411000.0)]


def test_rows_to_ticks_empty():
    assert rows_to_ticks([]) == []
