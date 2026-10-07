"""Command-line entrypoint -- the thing you actually run on event day.

    python src/diffusion/diffusion_cli.py events
    python src/diffusion/diffusion_cli.py preflight              # run during a trading session, the day before
    python src/diffusion/diffusion_cli.py capture                # next market-hours event; waits for its window
    python src/diffusion/diffusion_cli.py capture --event "RBI MPC Oct 2026"
    python src/diffusion/diffusion_cli.py gap --event "CPI September 2026" --label pre_close
    python src/diffusion/diffusion_cli.py analyze --event "RBI MPC Oct 2026"
    python src/diffusion/diffusion_cli.py capture --placebo-at "2026-10-06 10:00"   # no-news control window
    python src/diffusion/diffusion_cli.py report                 # pooled summary across captured events
    python src/diffusion/diffusion_cli.py demo                   # SYNTHETIC end-to-end demo, clearly labelled

See RUNBOOK.md for the full event-day timeline.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze_event import DEFAULT_RESULTS_DIR, analyze_event, format_report, render_charts, write_result  # noqa: E402
from diffusion_db import DEFAULT_DB_PATH, get_connection, insert_ticks, list_event_names  # noqa: E402
from event_calendar import EVENTS, PLACEBO_PREFIX, ShockEvent, get_event, placebo_event, upcoming_events  # noqa: E402
from paths import safe_filename  # noqa: E402
from pooling import load_results, render_markdown_report  # noqa: E402
from synthetic import build_demo_db  # noqa: E402
from tick_recorder import (  # noqa: E402
    CaptureStats,
    _connect_and_record,
    capture_window,
    record_gap_tick,
    run_capture,
    seconds_until,
)

log = logging.getLogger("diffusion")
LOG_DIR = DEFAULT_DB_PATH.parent / "logs"


def _setup_logging(name: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_DIR / f"{name}.log", encoding="utf-8")]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers, force=True)


def _resolve_event(name: str | None, market_hours: bool | None = None) -> ShockEvent:
    if name:
        return get_event(name)
    candidates = [e for e in upcoming_events() if market_hours is None or e.market_hours_event == market_hours]
    if not candidates:
        raise SystemExit("no upcoming event in the calendar; pass --event explicitly")
    return candidates[0]


def cmd_events(_args) -> int:
    now = datetime.now(timezone.utc)
    for e in EVENTS:
        when = datetime.fromisoformat(e.scheduled_timestamp_utc)
        tag = "market-hours" if e.market_hours_event else "after-hours "
        status = "UPCOMING" if when > now else "past    "
        print(f"{status} {e.event_name:<24} {e.scheduled_timestamp_utc}  [{e.timing_confidence:<11} {tag}]")
    return 0


def cmd_preflight(args) -> int:
    """End-to-end live check, mirroring exactly what capture will do: token,
    market status, reference table, authorize, connect, subscribe, then
    listen and require REAL live_feed messages decoding into spot AND option
    ticks. Exists because BUG-3 (a silently-invalid subscribe mode) showed
    that "connected and subscribed without error" proves nothing."""
    _setup_logging("preflight")
    from auth import get_auth_headers
    from fetch_option_chain import get_market_status
    from reference_table import build_futures_reference, build_reference_table

    ok = True
    try:
        get_auth_headers()
        print("[ok ] UPSTOX_ACCESS_TOKEN present")
    except RuntimeError as exc:
        print(f"[FAIL] {exc}")
        return 1

    status = get_market_status("NSE")
    print(f"[info] NSE market status: {status}")
    market_open = status == "NORMAL_OPEN"
    if not market_open:
        print("[warn] market is not NORMAL_OPEN -- zero live ticks below would be EXPECTED, so this run cannot prove the feed works")

    option_reference, index_keys = build_reference_table()
    print(f"[ok ] reference table: {len(option_reference)} option legs, {len(index_keys)} indices")
    future_reference = build_futures_reference()
    if future_reference:
        print(f"[ok ] futures reference: {sorted(v['underlying'] + ' ' + v['expiry'] for v in future_reference.values())}")
    else:
        print("[warn] futures reference unavailable -- the futures control leg would be missing from a capture")
    if args.instruments:
        # smaller subscription for isolating a problem: indices + first N option legs
        option_reference = dict(list(option_reference.items())[: args.instruments])
        print(f"[info] limited to {len(option_reference)} option legs (--instruments)")

    stats = CaptureStats()
    stop_at = datetime.now(timezone.utc) + timedelta(seconds=args.seconds)
    print(f"[info] listening for {args.seconds}s ...")
    try:
        asyncio.run(
            _connect_and_record(
                option_reference, index_keys, "preflight", stop_at, None, lambda conn, rows: None, stats=stats,
                future_reference=future_reference,
            )
        )
    except Exception as exc:  # noqa: BLE001 -- a preflight reports, it doesn't crash
        print(f"[FAIL] connection error: {exc!r}")
        return 1

    print(f"[info] {stats.summary()}")
    live = stats.messages_by_type.get("live_feed", 0) + stats.messages_by_type.get("initial_feed", 0)
    if stats.connects == 0:
        print("[FAIL] never connected")
        ok = False
    elif live == 0:
        print("[FAIL] connected + subscribed but ZERO initial/live feed messages -- subscription is not registering (BUG-3 symptom)")
        ok = False
    elif stats.ticks_by_kind.get("spot", 0) == 0:
        print("[FAIL] feed messages arrived but no NIFTY/BANKNIFTY spot ticks decoded")
        ok = False
    elif stats.ticks_by_kind.get("option", 0) == 0:
        print("[FAIL] spot ticks decoded but no option ticks (option decode / two-sided-quote filter?)")
        ok = False
    else:
        print("[ok ] spot AND option ticks decoded from the live feed")
        if future_reference and stats.ticks_by_kind.get("future", 0) == 0:
            print("[warn] no futures ticks decoded -- futures control leg would be empty (not fatal)")
        elif future_reference:
            print("[ok ] futures ticks decoded")
    if ok and not market_open:
        print("[warn] passed, but market was not open; re-run during NORMAL_OPEN before trusting this")
    if not ok and not market_open and stats.connects > 0:
        # connected + subscribed fine, but with NSE closed zero ticks is the expected outcome
        print("PREFLIGHT INCONCLUSIVE (market closed: connection works, tick delivery can't be judged) -- re-run during NORMAL_OPEN")
        return 3
    print("PREFLIGHT " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


def cmd_capture(args) -> int:
    if args.placebo_at:
        date_str, time_str = args.placebo_at.split(" ")
        event = placebo_event(date_str, time_str)
    else:
        event = _resolve_event(args.event, market_hours=True)
    if not event.market_hours_event:
        raise SystemExit(f"{event.event_name} is an after-hours event; use the `gap` command instead")
    _setup_logging(f"capture_{safe_filename(event.event_name)}")
    start, stop = capture_window(event, args.pre, args.post)
    log.info("event: %s (shock %s, confidence=%s)", event.event_name, event.scheduled_timestamp_utc, event.timing_confidence)
    if seconds_until(stop) <= 0:
        raise SystemExit("this event's capture window has already ended")

    conn = get_connection(Path(args.db))
    stats = asyncio.run(run_capture(event, conn, insert_ticks, args.pre, args.post, wait_for_start=not args.no_wait))
    if stats.ticks == 0:
        log.error("CAPTURE RECEIVED ZERO TICKS -- do not trust this event; see BUGS.md BUG-3")
        return 2

    if not args.no_analyze:
        result = analyze_event(conn, event)
        print(format_report(result))
        print("saved:", write_result(result))
        for path in render_charts(conn, event, result):
            print("chart:", path)
    return 0


def cmd_gap(args) -> int:
    event = _resolve_event(args.event, market_hours=False)
    _setup_logging("gap")
    conn = get_connection(Path(args.db))
    rows = record_gap_tick(event, args.label, conn, insert_ticks)
    for r in rows:
        print(f"{r['underlying']:<10} {r['kind']:<6} {r['price_paise'] / 100:>12.2f}")
    if not rows:
        print("no rows recorded (empty option chain?) -- is the market closed?")
        return 1
    return 0


def cmd_analyze(args) -> int:
    conn = get_connection(Path(args.db))
    if args.event:
        events = [get_event(args.event)]
    else:
        captured = set(list_event_names(conn))
        events = [e for e in EVENTS if e.event_name in captured or any(n.startswith(e.event_name + " [") for n in captured)]
        events += [get_event(n) for n in sorted(captured) if n.startswith(PLACEBO_PREFIX)]
    if not events:
        print("nothing captured yet")
        return 1
    for event in events:
        result = analyze_event(conn, event, args.z)
        print(format_report(result))
        print("saved:", write_result(result))
        if event.market_hours_event and not args.no_charts:
            for path in render_charts(conn, event, result):
                print("chart:", path)
        print()
    return 0


def cmd_report(args) -> int:
    results = load_results(DEFAULT_RESULTS_DIR)
    if not results:
        print(f"no result files in {DEFAULT_RESULTS_DIR} -- run `analyze` first")
        return 1
    markdown = render_markdown_report(results)
    out = DEFAULT_RESULTS_DIR / "SUMMARY.md"
    out.write_text(markdown, encoding="utf-8")
    print(markdown)
    print("saved:", out)
    return 0


def cmd_demo(args) -> int:
    """End-to-end run on a SYNTHETIC capture (synthetic.py) with known
    injected lags. Everything lands under data/demo/, never in the real
    database/results, so it can't contaminate `report`."""
    demo_dir = DEFAULT_DB_PATH.parent / "demo"
    demo_dir.mkdir(parents=True, exist_ok=True)
    db_path = demo_dir / "demo_ticks.db"
    if db_path.exists():
        db_path.unlink()
    event = build_demo_db(db_path)
    conn = get_connection(db_path)
    print("*** SYNTHETIC DATA -- generated by synthetic.py, NOT a market capture ***")
    print("Injected truth: future +1s, NIFTY spot +3s, BANKNIFTY spot +4s, options +6s after the shock.")
    print("Expected: spot-vs-option ~ +3, spot-vs-future ~ -2, NIFTY-vs-BANKNIFTY ~ +1.")
    print()
    result = analyze_event(conn, event)
    print(format_report(result))
    print("saved:", write_result(result, demo_dir / "results"))
    for path in render_charts(conn, event, result, demo_dir / "charts"):
        print("chart:", path)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("events", help="list the calendar").set_defaults(func=cmd_events)

    p = sub.add_parser("preflight", help="live end-to-end feed check (run during NORMAL_OPEN)")
    p.add_argument("--seconds", type=int, default=45)
    p.add_argument("--instruments", type=int, default=0, help="limit to N option legs (to isolate a problem)")
    p.set_defaults(func=cmd_preflight)

    p = sub.add_parser("capture", help="record a market-hours event (waits for its window)")
    p.add_argument("--event")
    p.add_argument("--placebo-at", metavar='"YYYY-MM-DD HH:MM"', help="record a no-news CONTROL window at this IST time instead of a real event")
    p.add_argument("--pre", type=int, default=600, help="seconds recorded before the shock")
    p.add_argument("--post", type=int, default=1800, help="seconds recorded after the shock")
    p.add_argument("--db", default=str(DEFAULT_DB_PATH))
    p.add_argument("--no-wait", action="store_true", help="start recording immediately instead of at window open")
    p.add_argument("--no-analyze", action="store_true")
    p.set_defaults(func=cmd_capture)

    p = sub.add_parser("gap", help="one REST snapshot for an after-hours (CPI) event")
    p.add_argument("--event")
    p.add_argument("--label", required=True, choices=["pre_close", "post_open", "post_open_settled"])
    p.add_argument("--db", default=str(DEFAULT_DB_PATH))
    p.set_defaults(func=cmd_gap)

    p = sub.add_parser("analyze", help="compute headline numbers for captured events")
    p.add_argument("--event")
    p.add_argument("--z", type=float, default=3.0)
    p.add_argument("--no-charts", action="store_true")
    p.add_argument("--db", default=str(DEFAULT_DB_PATH))
    p.set_defaults(func=cmd_analyze)

    sub.add_parser("report", help="pooled summary across captured events -> data/results/SUMMARY.md").set_defaults(func=cmd_report)
    sub.add_parser("demo", help="end-to-end run on SYNTHETIC data with known injected lags").set_defaults(func=cmd_demo)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
