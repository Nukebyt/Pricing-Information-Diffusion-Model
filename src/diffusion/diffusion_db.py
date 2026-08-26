"""SQLite schema for high-frequency tick capture around scheduled shock events.

Deliberately a separate database file rather than mixed into a continuous
polling table -- this captures at WS tick cadence within narrow event
windows, not on a fixed interval; mixing the two cadences in one table
would let a future query silently blend two structurally different kinds
of rows (BUGS.md DEC-3).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "diffusion_ticks.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS diffusion_ticks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_name      TEXT NOT NULL,
    instrument_key  TEXT NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('spot', 'option')),
    underlying      TEXT NOT NULL,
    strike_paise    INTEGER,
    option_type     TEXT CHECK (option_type IN ('CE', 'PE') OR option_type IS NULL),
    timestamp_utc   TEXT NOT NULL,
    price_paise     INTEGER NOT NULL,
    iv              REAL
);

CREATE INDEX IF NOT EXISTS idx_diffusion_event_time
    ON diffusion_ticks (event_name, timestamp_utc);

CREATE INDEX IF NOT EXISTS idx_diffusion_event_instrument_time
    ON diffusion_ticks (event_name, instrument_key, timestamp_utc);
"""


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def insert_ticks(conn: sqlite3.Connection, rows: list[dict]) -> None:
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO diffusion_ticks (
            event_name, instrument_key, kind, underlying, strike_paise, option_type,
            timestamp_utc, price_paise, iv
        ) VALUES (
            :event_name, :instrument_key, :kind, :underlying, :strike_paise, :option_type,
            :timestamp_utc, :price_paise, :iv
        )
        """,
        rows,
    )
    conn.commit()


def fetch_ticks(
    conn: sqlite3.Connection,
    event_name: str,
    kind: str | None = None,
    underlying: str | None = None,
) -> list[dict]:
    """underlying lets a caller pull just "NIFTY" or just "BANKNIFTY" spot
    ticks for the same event -- both indices' spot rows share event_name
    and kind="spot", differentiated only by this field (needed for the
    Phase 5 cross-index angle, ROADMAP.md, BUGS.md DEC-4)."""
    query = (
        "SELECT event_name, instrument_key, kind, underlying, strike_paise, option_type, "
        "timestamp_utc, price_paise, iv FROM diffusion_ticks WHERE event_name = ?"
    )
    params: list = [event_name]
    if kind is not None:
        query += " AND kind = ?"
        params.append(kind)
    if underlying is not None:
        query += " AND underlying = ?"
        params.append(underlying)
    query += " ORDER BY timestamp_utc"

    cur = conn.execute(query, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def rows_to_ticks(rows: list[dict]) -> list[tuple[str, float]]:
    """Converts fetch_ticks() rows into the (timestamp_utc, price) Tick
    shape lag_detection.py/adjustment_curves.py expect. Deliberately kept
    in raw price_paise units, not converted to rupees -- detect_first_move's
    z-score baseline is computed independently per series, so the
    threshold-crossing math is scale-invariant and a conversion buys
    nothing except a chance to reintroduce a unit-mismatch bug like the
    sibling project's BUG-9 (a 100x rupee/percentage scale error). See
    BUGS.md DEC-5."""
    return [(r["timestamp_utc"], float(r["price_paise"])) for r in rows]
