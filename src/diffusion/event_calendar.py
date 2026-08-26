"""Phase 1: scheduled macro-event calendar for the information-diffusion study.

Precise shock timing is the most important design decision in this project
-- every event below carries an explicit timing_confidence rather than
being treated as uniformly precise. Dates and
times verified via WebSearch 2026-08-26 against real news coverage, not
assumed from memory of "how these things usually work" -- see ROADMAP.md S6
for sources and DEC-4/anticipated-pitfalls in BUGS.md for why this matters.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


@dataclass(frozen=True)
class ShockEvent:
    event_name: str
    scheduled_timestamp_utc: str  # ISO 8601, UTC, timezone-aware
    timing_confidence: str  # "second" | "minute" | "approximate"
    market_hours_event: bool
    affected_underlyings: tuple[str, ...]
    source_note: str


def _ist(y: int, m: int, d: int, hh: int, mm: int) -> str:
    return datetime(y, m, d, hh, mm, tzinfo=IST).astimezone(timezone.utc).isoformat()


# --- RBI Monetary Policy Committee: 10:00 IST on day 3 of each 3-day meeting.
# Verified via WebSearch 2026-08-26 (newsonair.gov.in, business-standard.com):
# FY26-27 schedule is Apr 8 / Jun 5 / Aug 5 / Oct 7 / Dec 4, 2026.
_RBI_MPC_2026 = [
    ("RBI MPC Apr 2026", 2026, 4, 8),
    ("RBI MPC Jun 2026", 2026, 6, 5),
    ("RBI MPC Aug 2026", 2026, 8, 5),
    ("RBI MPC Oct 2026", 2026, 10, 7),
    ("RBI MPC Dec 2026", 2026, 12, 4),
]

_RBI_SOURCE_NOTE = (
    "RBI Governor announces the MPC decision at 10:00 IST on day 3 of the "
    "3-day meeting; press conference follows at 12:00 IST. Verified via "
    "WebSearch 2026-08-26 (newsonair.gov.in, business-standard.com) against "
    "the real FY26-27 schedule, not assumed."
)

EVENTS: list[ShockEvent] = [
    ShockEvent(
        event_name=name,
        scheduled_timestamp_utc=_ist(y, m, d, 10, 0),
        timing_confidence="minute",
        market_hours_event=True,
        affected_underlyings=("NIFTY", "BANKNIFTY"),
        source_note=_RBI_SOURCE_NOTE,
    )
    for name, y, m, d in _RBI_MPC_2026
]

EVENTS.append(
    ShockEvent(
        event_name="Union Budget 2026-27",
        scheduled_timestamp_utc=_ist(2026, 2, 1, 11, 0),
        timing_confidence="minute",
        market_hours_event=True,
        affected_underlyings=("NIFTY", "BANKNIFTY"),
        source_note=(
            "FM Sitharaman presented the Union Budget 2026-27 at 11:00 IST "
            "on 2026-02-01 (the first Sunday budget presentation in India's "
            "history). Verified via WebSearch 2026-08-26."
        ),
    )
)

# --- CPI (MoSPI): released 16:00 IST, after NSE's 15:30 IST close -- an
# after-hours/gap event, not intraday. Release time
# itself is confirmed (advanced from 17:30 IST in Nov 2024 specifically to
# land closer to market close); exact per-month 2026 dates follow an
# observed ~12-13-day-after-month-end pattern from confirmed instances
# (Mar data -> Apr 13, Apr data -> May 12, Jun data -> Jul 13) but a full
# 2026 calendar wasn't found in one place -- entered as "approximate" and
# must be re-verified against mospi.gov.in before being trusted for a real
# capture (ROADMAP.md Phase 1, BUGS.md anticipated pitfalls).
_CPI_RELEASES_APPROX = [
    ("CPI July 2026 (released)", 2026, 8, 12),
    ("CPI August 2026 (released)", 2026, 9, 12),
    ("CPI September 2026 (released)", 2026, 10, 13),
]

for _name, _y, _m, _d in _CPI_RELEASES_APPROX:
    EVENTS.append(
        ShockEvent(
            event_name=_name,
            scheduled_timestamp_utc=_ist(_y, _m, _d, 16, 0),
            timing_confidence="approximate",
            market_hours_event=False,
            affected_underlyings=("NIFTY", "BANKNIFTY"),
            source_note=(
                "MoSPI releases CPI at 16:00 IST (confirmed, advanced from "
                "17:30 IST in Nov 2024). This specific release date is "
                "extrapolated from the observed ~12-13-day-after-month-end "
                "pattern, NOT confirmed against a published 2026 calendar -- "
                "re-verify against mospi.gov.in before trusting it for a "
                "real capture."
            ),
        )
    )

EVENTS.sort(key=lambda e: e.scheduled_timestamp_utc)


def upcoming_events(now: datetime | None = None) -> list[ShockEvent]:
    """Events strictly after `now` (defaults to the real current UTC time).
    Used to decide what's actually still capturable -- a past event can't
    be captured live regardless of how well-defined its timestamp is."""
    reference = now if now is not None else datetime.now(timezone.utc)
    return [e for e in EVENTS if datetime.fromisoformat(e.scheduled_timestamp_utc) > reference]


def market_hours_events() -> list[ShockEvent]:
    return [e for e in EVENTS if e.market_hours_event]


def after_hours_events() -> list[ShockEvent]:
    return [e for e in EVENTS if not e.market_hours_event]


if __name__ == "__main__":
    today = date.today()
    print(f"{len(EVENTS)} events in calendar, {len(upcoming_events())} upcoming as of {today.isoformat()}:")
    for e in upcoming_events():
        tag = "market-hours" if e.market_hours_event else "after-hours"
        print(f"  {e.event_name:<32} {e.scheduled_timestamp_utc}  [{e.timing_confidence}, {tag}]")
