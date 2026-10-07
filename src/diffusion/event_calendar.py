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
# after-hours/gap event, not intraday (advanced from 17:30 IST in Nov 2024).
# Dates below come from MoSPI's own Advance Release Calendar 2026-27
# (mospi.gov.in, "ADVANCE RELEASE CALENDAR 2026-27 FINAL 05.02.2026"), which
# schedules CPI on the 12th of every month; when the 12th is a weekend/
# holiday the release moves to the next working day -- Aug 2026 data was
# released Mon 2026-09-14 (the 12th was a Saturday), confirmed against
# MoSPI's own press release. Verified 2026-10-05.
#   name, y, m, d, timing_confidence
# "minute" = the calendar date is MoSPI-published (or confirmed by the
# actual release); "approximate" = the 12th falls on a weekend and the
# next-working-day shift hasn't been confirmed by a release yet.
_CPI_RELEASES = [
    ("CPI July 2026", 2026, 8, 12, "minute"),
    ("CPI August 2026", 2026, 9, 14, "minute"),
    ("CPI September 2026", 2026, 10, 12, "minute"),
    ("CPI October 2026", 2026, 11, 12, "minute"),
    ("CPI November 2026", 2026, 12, 14, "approximate"),  # Dec 12 is a Saturday
]

for _name, _y, _m, _d, _confidence in _CPI_RELEASES:
    EVENTS.append(
        ShockEvent(
            event_name=_name,
            scheduled_timestamp_utc=_ist(_y, _m, _d, 16, 0),
            timing_confidence=_confidence,
            market_hours_event=False,
            affected_underlyings=("NIFTY", "BANKNIFTY"),
            source_note=(
                "MoSPI releases CPI at 16:00 IST, after NSE's 15:30 close. "
                "Date from MoSPI's Advance Release Calendar 2026-27 (CPI on the "
                "12th, shifted to the next working day when that is a "
                "weekend/holiday); 'approximate' where the shift is "
                "unconfirmed -- re-check mospi.gov.in before a real capture."
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


PLACEBO_PREFIX = "PLACEBO "


def placebo_event(date_str: str, time_str: str) -> ShockEvent:
    """A no-news CONTROL window: same machinery and window as a real event
    but pinned to an ordinary day/time (IST, e.g. "2026-10-06", "10:00").
    Running the identical detector on it measures the false-positive rate --
    how often "a first move" is found when nothing happened -- which is
    the noise floor any real-event lag has to be read against."""
    year, month, day = (int(x) for x in date_str.split("-"))
    hour, minute = (int(x) for x in time_str.split(":"))
    return ShockEvent(
        event_name=f"{PLACEBO_PREFIX}{date_str} {time_str} IST",
        scheduled_timestamp_utc=_ist(year, month, day, hour, minute),
        timing_confidence="minute",
        market_hours_event=True,
        affected_underlyings=("NIFTY", "BANKNIFTY"),
        source_note="Placebo/control window: no scheduled news. Detections here are false positives.",
    )


def get_event(event_name: str) -> ShockEvent:
    """Look an event up by exact name (placebo names are parsed back into
    events); raises KeyError listing valid names."""
    if event_name.startswith(PLACEBO_PREFIX):
        try:
            date_str, time_str, _tz = event_name[len(PLACEBO_PREFIX):].split(" ")
            return placebo_event(date_str, time_str)
        except ValueError as exc:
            raise KeyError(f"malformed placebo event name {event_name!r}") from exc
    for e in EVENTS:
        if e.event_name == event_name:
            return e
    raise KeyError(f"unknown event {event_name!r}; known events: {[e.event_name for e in EVENTS]}")


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
