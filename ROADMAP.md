# Information Diffusion Model — Roadmap

**Author:** Priyanuj Boruah (Nukebyt)
**Purpose:** Companion portfolio project to the [NSE Options Arbitrage Detection Engine](https://github.com/Nukebyt/NSE-Options-Arbitrage-Detection-Engine). Where that project asks "are these prices internally consistent right now," this one asks "how fast does new information get priced in, and who moves first" — market microstructure's price-discovery/latency side rather than its static-consistency side.

**Companion doc:** [BUGS.md](BUGS.md) — bug/decision log, filed as things are actually found while building, same discipline as the sibling project's own log.

**Current Status:** 🟡 Phases 0–4 (code) built and unit-tested against synthetic data with known injected ground truth, plus two of Phase 5's three stretch angles: NIFTY-vs-BANKNIFTY cross-index lead-lag, and overnight/gap diffusion for after-hours (CPI) events — both built by generalizing/extending existing modules rather than duplicating them (see Phase 5 below). GDELT-based unscheduled-event catching remains unbuilt. Phase 1's event calendar is populated with real, WebSearch-verified dates. This project now stands on its own — its own repo, its own trimmed copy of the Upstox data layer (§0) — rather than living inside the sibling project's shared tree. Live tick capture (the actual data this project needs to answer any of its headline questions) is calendar-gated — see §0. This project's own test suite: 73/73.

---

## 0. Why this looks like the sibling project, but isn't a rebuild

This project's original planning draft targeted a different prediction-market venue + GDELT, written before the sibling mispricing project hit that venue's hard India-access restriction and pivoted to NSE options via Upstox. That plan doesn't apply anymore — not because the research question changed, but because the *market* did. This roadmap rebuilds the plan around NSE options from scratch, reusing the sibling project's already-built, already-tested data-layer design as a reference point, then giving this project its **own trimmed copy** of the equivalent pieces so it can stand alone as its own repo:

- **`src/data/auth.py`** — the static Analytics-token Bearer auth. Trimmed to only what this project actually calls (`get_auth_headers`, `get_ws_authorized_url`) — not the sibling project's separate OAuth2 experiment, which this project has no use for.
- **`src/data/fetch_option_chain.py`** — REST option chain, contracts, market status, historical candles.
- **`src/data/reference_table.py`** — `build_reference_table()`/`build_subscribe_message()`: connection/reference-table plumbing only, deliberately **not** bundled with the sibling project's no-arbitrage check logic — this project never needed that, only the plumbing that gets a WebSocket connection subscribed to a live NIFTY/BANKNIFTY reference table (both spot index ticks *and* option ticks off a single connection). The old pre-pivot plan assumed a from-scratch high-frequency poller was Phase 2's first job; it isn't — this project only ever needed a *recorder* that taps an already-working feed shape and logs every tick with an arrival timestamp.

**What's genuinely new, not reused:** the whole measurement layer (`src/diffusion/`) — event calendar, tick recorder, first-move/lag detection, adjustment curves, diffusion-timeline charts, gap diffusion — since none of that existed anywhere before this project; it's the entire point of it.

**The honest constraint:** you cannot backfill tick-level history. Real capture data requires the recorder actually running during a real RBI MPC decision, Union Budget, or CPI release. Everything buildable and testable without live market data — event calendar, tick decode/recording logic, lag/adjustment-curve math, chart generation — is built and synthetic-tested now (§3, Phases 0–4 marked 🟢 "code done"); the live capture itself runs opportunistically as real scheduled events occur.

---

## 1. Project Thesis (say this in interviews)

> "I built a companion project to my NSE options mispricing detector that measures a different question: not whether prices are internally consistent, but how fast new information gets priced in, and who moves first. I define precise 'shock' timestamps from India's actual scheduled macro calendar — RBI MPC decisions, the Union Budget, CPI releases — and record high-frequency ticks for NIFTY spot and its near-ATM options bracketing each event, reusing the WebSocket infrastructure design from my arbitrage detector. Then I measure, in seconds, whether spot or the option market reacts first — a direct test of the 'do options lead the underlying' market-microstructure question — and how long each takes to settle into its new level. The two projects intentionally cover two different quant skillsets: one is structural/static market efficiency, the other is dynamic price-discovery speed."

Hooks: **price discovery, lead-lag analysis, event studies, informed order flow, latency measurement.** Keep the two projects' framings distinct in an interview — conflating them muddies both stories.

---

## 2. Scope Decision

- **Location:** its own standalone repo, with its own trimmed copy of the Upstox data layer (§0) — not a cross-repo import into the sibling project.
- **Headline question:** does NIFTY spot move first after a scheduled macro shock, with options (price, later IV) catching up with a lag — or does informed options order flow sometimes move first? Chosen over two other candidate angles (NIFTY-vs-BANKNIFTY cross-index lead-lag; cross-strike IV-smile diffusion) as the one to build first; both remain real Phase 5 stretch goals, not abandoned ideas.
- **Event source:** a curated, WebSearch-verified calendar of India's own scheduled macro releases (RBI MPC, Union Budget, CPI/IIP/GDP), not GDELT. GDELT remains a real Phase 5 stretch option for catching *unscheduled* shocks (a surprise RBI action, a surprise geopolitical event) once the scheduled-event pipeline has real data — preferring scheduled events with known timestamps over GDELT's coarser update cadence for the *core* result.
- **Not doing:** live execution on the signal (measurement project, not a strategy); cross-venue diffusion (single-broker Upstox feed only).

---

## 3. Phased Roadmap

### **Phase 0 — Setup** 🟢 code done
- [x] Confirm the reuse design: a WebSocket reference table can carry both index and option ticks off one connection.
- [x] Scaffold `src/diffusion/` and `src/data/` (flat `sys.path.insert` convention, no package/`__init__.py`).
- [x] Decide on a separate SQLite file (`data/diffusion_ticks.db`) rather than mixing with a continuous-polling snapshot table — different capture cadence (event-triggered, sub-second-to-second bursts vs. continuous polling), and mixing them risks a future reader assuming every row means the same thing.
- [ ] **Not yet fully done — genuine blocker:** confirm live, during NSE trading hours, that the tick recorder actually receives and decodes real index + option ticks end-to-end (see Phase 2 — partial progress made, root cause of the remaining gap still open).

**Exit criteria:** `src/diffusion/` and `src/data/` scaffolded, dependency design confirmed against real code, DB approach decided.

---

### **Phase 1 — Define shock events precisely** 🟢 code done
Precise shock definition is the single most important design decision in this project — a lag measurement is only as good as the timestamp it's measured against.

- [x] **RBI MPC decisions** — verified via WebSearch (2026-08-26): six meetings/year, each a 3-day session, decision announced by the Governor at **10:00 IST** on day 3, press conference at 12:00 IST. FY26-27 dates: Apr 8, Jun 5, Aug 5, Oct 7, Dec 4, 2026 (Aug 5 already passed relative to today's date). `timing_confidence="minute"` — the 10:00 IST convention is well-established but the recorder should still gate on the exchange's own live market-status endpoint rather than trusting the clock alone, since a meeting occasionally runs long.
- [x] **Union Budget** — presented in Parliament at **11:00 IST**, historically Feb 1 by convention. FY26-27 budget: 2026-02-01 (a Sunday — the first time in India's history a budget was presented on one), already passed relative to today. `timing_confidence="minute"`.
- [x] **CPI (MoSPI)** — release time confirmed **16:00 IST** (advanced from 17:30 IST in Nov 2024 specifically to align with market close). This is **after** NSE's 15:30 IST close — an after-hours/gap event, not an intraday one; flagged `market_hours_event=False`. Exact per-month release dates follow an observed ~12–13-days-after-month-end pattern (confirmed instances: Mar data → Apr 13, Apr data → May 12, Jun data → Jul 13) but a full 2026 calendar wasn't found in one place — future months are entered as `timing_confidence="approximate"` and should be re-verified against `mospi.gov.in`'s own release calendar before being trusted for real capture, not assumed from the pattern alone.
- [x] Each event carries: `event_name, scheduled_timestamp_utc, timing_confidence, market_hours_event, affected_underlyings, source_note` — `src/diffusion/event_calendar.py`.
- [ ] GDELT as a secondary validation/unscheduled-event source — deferred to Phase 5, not needed until the scheduled pipeline has real data.

**Exit criteria:** ✅ met — a clean, sourced table of events with honest per-event timing confidence, distinguishing market-hours from after-hours events.

**Deliverable artifact:** [`src/diffusion/event_calendar.py`](src/diffusion/event_calendar.py) + [`tests/test_diffusion_event_calendar.py`](tests/test_diffusion_event_calendar.py).

---

### **Phase 2 — High-frequency tick capture** 🟡 code done, live capture not yet fully confirmed
- [x] `feed_to_tick_row()` — pure decode step (protobuf `Feed` message → a flat tick-row dict, or `None`), directly unit-tested against synthetic messages. A one-sided/missing quote is never fabricated into a price.
- [x] `capture_window()` — pure function computing the `[pre_seconds before, post_seconds after]` UTC window around a scheduled event.
- [x] `_connect_and_record()` / `run_capture()` — the live streaming loop: fresh authorized WS URL per attempt, reconnect with the same event's window still active, recording every tick to `data/diffusion_ticks.db` instead of running arbitrage checks.
- [x] `record_gap_tick()` — for after-hours events (CPI/IIP/GDP): a single REST snapshot (spot + near-ATM option) tagged to a named point (`pre_close` / `post_open` / `post_open_settled`), meant to be triggered by a scheduled job around the release, not run continuously — there is nothing to stream while NSE is shut, and naively polling into dead air just produces frozen, duplicate reads.
- [ ] **Live confirmation — partial, real progress made 2026-08-26** (BUGS.md BUG-3): ran a bounded live smoke test in the last ~13 minutes of a real trading session. **Confirmed live:** REST reference-table build (660 real legs), the WS-authorize REST call (real host recorded for the first time: `wss://wsfeeder-api.upstox.com/market-data-feeder/v3/upstox-developer-api/feeds`), WS connect, and subscribe send with no rejection. **Not yet confirmed:** actual `live_feed`-typed tick messages — three attempts all received only a single `market_info`-typed message per connection, zero real ticks. Root cause genuinely undetermined (near-close thin activity vs. a subscribe-format problem) — see BUG-3 for the two live hypotheses and the concrete next diagnostic step (re-run with a full-session window from market open, not the last 15 minutes before close). Still need a real event capture (**RBI MPC, 2026-10-07, 10:00 IST**) to fully close this out, but ideally re-run a plain connectivity check earlier in a trading day first, to isolate BUG-3 before spending the one real shot at a scheduled event on an unresolved subscribe-format question.

**Exit criteria:** Code and synthetic tests done; live capture against a real event is the one remaining step.

**Deliverable artifact:** [`src/diffusion/tick_recorder.py`](src/diffusion/tick_recorder.py) + [`src/diffusion/diffusion_db.py`](src/diffusion/diffusion_db.py) + [`tests/test_diffusion_tick_recorder.py`](tests/test_diffusion_tick_recorder.py).

---

### **Phase 3 — Lag & diffusion measurement** 🟢 code done, synthetic-validated
Core analysis phase — the actual answer to the headline question, once real events accumulate.

- [x] `detect_first_move()` — rolling pre-shock baseline (mean + population stdev from strictly pre-shock ticks only — using any post-shock tick in the baseline would bias the detector toward *not* finding the very move being measured, a look-ahead-bias trap in miniature) + z-score threshold crossing. Handles the degenerate flat-baseline case (stdev=0, e.g. synthetic test data) explicitly rather than dividing by zero.
- [x] `spot_option_lag_seconds()` — the headline metric: positive means spot led (options lagged by N seconds), negative means the option led. **Unit-tested against a synthetic tick series with a known injected lag**, not just "runs without error" — a detection routine that merely runs is not evidence it's correct; a synthetic ground-truth case is.
- [x] `summarize_lags()` — mean/median/stdev across events, deliberately framed as small-sample descriptive stats, not a confident point estimate — a handful of scheduled macro events per year is a genuinely small sample.
- [x] `time_to_90pct_adjustment()` — settle-speed metric per series (spot vs. option): time to close ≥90% of the eventual gap between pre- and post-shock levels.
- [x] `normalized_price_path()` — price / pre-shock level, so spot (tens of thousands) and an option premium (tens to hundreds) sit on the same visual scale for Phase 4's chart.
- [ ] Cross-event consistency (does the same lead-lag direction hold across event types?) and a liquidity correlation (does OI/volume predict reaction speed?) — both need several real events accumulated first; the aggregation function (`summarize_lags`) is ready, the real numbers aren't yet.

**Exit criteria:** Detection and measurement logic built and validated against synthetic data with known ground truth; the real cross-event finding is blocked on Phase 2's live data.

**Deliverable artifact:** [`src/diffusion/lag_detection.py`](src/diffusion/lag_detection.py) + [`src/diffusion/adjustment_curves.py`](src/diffusion/adjustment_curves.py) + [`tests/test_diffusion_lag_detection.py`](tests/test_diffusion_lag_detection.py) + [`tests/test_diffusion_adjustment_curves.py`](tests/test_diffusion_adjustment_curves.py).

---

### **Phase 4 — Visualization** 🟡 chart function done, no real events to plot yet
- [x] `plot_diffusion_timeline()` — normalized spot vs. option price path, shock time marked at x=0, matplotlib → PNG.
- [ ] Lag-distribution chart across events (a simple histogram/strip plot of `spot_option_lag_seconds` per event) — trivial to add once ≥3–4 real events exist; not worth building against zero or one data point.
- [ ] A small dashboard (Streamlit) — deferred; console output + saved PNGs cover the same ground until there's enough real data to make a dashboard worth the screen space.

**Exit criteria:** Chart-generation code built and exercised against synthetic data; the real headline chart (one clean diffusion timeline for a real RBI MPC or Budget event) is the Phase 6 packaging target once live data exists.

**Deliverable artifact:** [`src/diffusion/charts.py`](src/diffusion/charts.py).

---

### **Phase 5 — Stretch** 🟡 2 of 3 angles built, code done
- [x] **NIFTY vs. BANKNIFTY cross-index lead-lag** — the second candidate headline angle from the original scope decision. Reused the tick recorder unchanged (both indices are already tracked — no new data collection needed) and generalized the detection/charting layer instead of duplicating it: `lag_detection.py`'s `detect_first_move()` now sits behind a shared `lead_lag_seconds()` primitive, with `spot_option_lag_seconds()` (headline) and `cross_index_lag_seconds()` (this angle) as thin, semantically-named wrappers over it — same pattern in `charts.py` (`plot_diffusion_timeline()` generic, `plot_spot_option_diffusion_timeline()`/`plot_cross_index_diffusion_timeline()` wrappers). `diffusion_db.py`'s `fetch_ticks()` gained an `underlying` filter (both indices' spot rows share `event_name`/`kind="spot"`, differentiated only by that field) plus `rows_to_ticks()` to bridge DB rows into the `Tick` shape the detection layer expects. Synthetic-tested the same way as the headline metric — known injected lag, both directions, and a null case. Still blocked on the same live-capture constraint as Phase 2/3: real numbers need a real captured event.
- [x] **Overnight/gap diffusion for after-hours events** — `gap_diffusion.py`'s `gap_move_breakdown()` splits an after-hours event's eventual move into "happened in the closed-market gap" vs. "continued once trading resumed," given three named REST snapshots (`record_gap_tick()`'s `pre_close`/`post_open`/`post_open_settled`). Deliberately doesn't clamp the resulting fraction to `[0, 1]` — a real price can open past its eventual settled level and partially revert, which needs a fraction >1 (gap overshot) or negative (post-open move reversed) to be represented honestly rather than silently clipped into a misleading number (BUGS.md DEC-6). Synthetic-tested including the zero-total-move case (reported as `None`, not fabricated). Same calendar-gated blocker as everything else here — the real numbers need a real captured CPI release (next approximate one: ~2026-09-12).
- [ ] **GDELT as an unscheduled-shock catcher** — a global event database, usable here to catch surprise events (an unscheduled RBI intervention, a surprise geopolitical shock) once the scheduled pipeline is validated, and to test a real hypothesis: surprise events should diffuse slower/noisier than anticipated ones, since no one was positioned in advance.
- [ ] Cross-project synthesis: a short section explicitly connecting the two projects — "one finds structural pricing errors, the other measures how fast information gets priced in — together they cover static and dynamic market efficiency."

---

### **Phase 6 — Packaging** 🟡 in progress
- [x] Public `README.md`: thesis, method, architecture diagram, honest status.
- [ ] One real diffusion-timeline chart from an actual captured event — pending real data.
- [x] Cross-link to the companion project, framed as two halves of one portfolio.

---

## 4. Key Concepts Reference

- **First-move detection:** baseline mean/stdev from strictly pre-shock ticks, flag the first post-shock tick whose z-score exceeds a threshold.
- **Lead-lag:** `spot_option_lag_seconds = option_first_move_time − spot_first_move_time`. Positive ⇒ spot led; negative ⇒ the option led.
- **Adjustment speed:** time from shock to the first tick that has closed ≥90% of the gap between the pre-shock level and the (pragmatically defined) settled post-shock level.
- **Why options might lead or lag spot at all:** informed-trading/leverage channel (options offer more leveraged exposure per rupee, attracting informed flow first) vs. liquidity/transaction-cost channel (spot/futures are usually more liquid and cheaper to trade, so uninformed hedging flow shows up there first).

---

## 5. Repo Structure

```
Pricing-Information-Diffusion-Model/
├── README.md
├── ROADMAP.md                # this file
├── BUGS.md                   # decision log + bug log, dated, filed the day each happened
├── docs/
│   └── architecture.svg
├── requirements.txt
├── .env.example
├── src/
│   ├── data/                 # trimmed Upstox client (auth, REST, WS reference table, proto)
│   │   ├── auth.py
│   │   ├── fetch_option_chain.py
│   │   ├── reference_table.py
│   │   └── proto/MarketDataFeed.proto, MarketDataFeed_pb2.py
│   └── diffusion/
│       ├── event_calendar.py       # Phase 1: scheduled shock events
│       ├── diffusion_db.py         # separate data/diffusion_ticks.db
│       ├── tick_recorder.py        # Phase 2: WS tick recording + REST gap snapshots
│       ├── lag_detection.py        # Phase 3: first-move + lead-lag (+ cross-index wrapper)
│       ├── adjustment_curves.py    # Phase 3: settle-speed + normalized paths
│       ├── charts.py               # Phase 4: diffusion-timeline plot (generic + named wrappers)
│       └── gap_diffusion.py        # Phase 5: overnight-gap breakdown for after-hours (CPI) events
├── tests/                     # 73 tests
└── data/                       # diffusion_ticks.db + generated charts (gitignored / committed selectively)
```

---

## 6. Reference Sources

- RBI MPC calendar and 10:00 IST announcement convention: verified via WebSearch 2026-08-26 (rbi.org.in is the primary source; re-verify meeting-by-meeting before trusting a specific future date).
- Union Budget presentation time/date convention: verified via WebSearch 2026-08-26 (Ministry of Finance / Doordarshan/Sansad TV coverage of the 2026-02-01 budget).
- MoSPI CPI release timing (16:00 IST, advanced from 17:30 IST Nov 2024): verified via WebSearch 2026-08-26; full per-month 2026 calendar not found in one place — re-check `mospi.gov.in`'s own release calendar before trusting an approximate future date.
- Upstox developer docs: `upstox.com/developer/api-documentation/open-api/`.

---

## 7. How this repo came to be

This project was originally built inside the same repo as the companion arbitrage-detection project, reusing its data layer directly. Once the diffusion-specific code (`src/diffusion/`) was substantial and clearly its own thing, it moved into a self-contained tree with its own trimmed copy of the Upstox data layer (§0) — no cross-repo relative imports, nothing that only works with both projects checked out side by side — and was published here as its own repo. Nothing in `src/diffusion/`'s logic changed in that process; only where its data-layer dependencies physically live did.

---

## 8. Notes for Future You / The AI Assistant

- Real Phase 2/3 progress is **calendar-gated** — you cannot accelerate this with a backfill script. The next real capturable market-hours event is the **2026-10-07 RBI MPC** (10:00 IST decision); mark your own calendar, not just this file's.
- If a capture window fails (missed connection, API downtime during the actual event), don't force-fit bad data — skip that event and note it in BUGS.md. A clean 5-event dataset beats a noisy 10-event one.
- When discussing in interviews, lead with the *finding*, not the pipeline: "spot led the options market by a median of N seconds across M real RBI decisions" is a much stronger opening line than "I built a system that records ticks."
