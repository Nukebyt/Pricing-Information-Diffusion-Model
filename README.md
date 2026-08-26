# Information Diffusion Model

**How fast does new information get priced into a market, and who moves first — the underlying, or its own options?**

A market-microstructure research project measuring price discovery speed in NIFTY/BANKNIFTY index options around real, scheduled Indian macro events (RBI rate decisions, the Union Budget, CPI releases). It's the dynamic companion to my [NSE Options Arbitrage Detection Engine](https://github.com/Nukebyt/NSE-Options-Arbitrage-Detection-Engine): that project asks *"are these prices internally consistent right now"* (a static, structural question); this one asks *"how long does it take for a new piece of information to be fully reflected in price, and does the underlying or the options market get there first"* (a dynamic, event-driven question). Together they cover two distinct, classically-tested angles on market efficiency.

**Status at a glance:** the full measurement pipeline — event calendar, live tick recorder, three independent lead-lag/diffusion angles, and their statistics — is built, unit-tested against synthetic data with *known, injected ground truth* (not just "it runs without error"), and has been partially validated against a real live Upstox feed during actual NSE trading hours. It has **not yet captured a real scheduled event end-to-end** — that's an honest, calendar-imposed constraint explained in [What's Done / What's Left](#whats-done--whats-left) below, not a gap in the engineering.

---

## Table of Contents

- [The Research Question](#the-research-question)
- [Why This Is a Genuinely Different Project, Not a Reskin](#why-this-is-a-genuinely-different-project-not-a-reskin)
- [Method](#method)
- [Architecture](#architecture)
- [Engineering Discipline: Test Before Trusting](#engineering-discipline-test-before-trusting)
- [What's Done / What's Left](#whats-done--whats-left)
- [Real Numbers So Far](#real-numbers-so-far)
- [Repo Structure](#repo-structure)
- [Running It Yourself](#running-it-yourself)
- [Limitations, Stated Plainly](#limitations-stated-plainly)
- [Companion Project](#companion-project)

---

## The Research Question

**Price discovery** is the process by which new information becomes reflected in an asset's price. A large body of market-microstructure research asks a specific, well-known version of this question: when new information hits, does an asset's *options* market ever move before its *underlying* does — evidence that informed traders sometimes prefer the leverage options offer over trading the underlying directly — or does the underlying (more liquid, cheaper to trade) always get there first, with options catching up mechanically?

This project answers that question empirically for NIFTY, using events with **unambiguous, known shock timestamps**: RBI Monetary Policy Committee decisions (announced 10:00 IST, verified against real news coverage of the FY26-27 schedule, not assumed), the Union Budget (11:00 IST, February 1 by convention), and CPI releases (16:00 IST, after market close — a different, "overnight gap" mechanic, explained below). For each event it measures, in seconds:

1. **Does spot or the near-ATM option move first**, and by how much?
2. **How long does each series take to settle** into its new level (time-to-90%-adjustment)?
3. Two supporting angles, reusing the same detection machinery: **does NIFTY or BANKNIFTY absorb a shock first** (a second, independent lead-lag question), and for after-hours releases, **how much of the eventual move happens invisibly overnight** versus continuing once trading resumes the next day?

## Why This Is a Genuinely Different Project, Not a Reskin

It would be easy to mistake this for "the same options data, plotted differently." It isn't — the two projects have different data shapes, different statistical methods, and answer different questions:

| | Arbitrage Detection Engine | Information Diffusion Model |
|---|---|---|
| **Question** | Are these prices internally consistent *right now*? | How fast does new information get priced in, and who moves first? |
| **Method** | No-arbitrage replication arguments (put-call parity, vertical spreads, convexity) | Event-study methodology: baseline + threshold-crossing first-move detection |
| **Data shape** | Continuous ~60s polling of the full option chain | Bursty, sub-second WebSocket capture in narrow windows bracketing real scheduled events |
| **Headline output** | A detected mispricing, sized in ₹, net of transaction costs | A lead-lag measurement, sized in seconds |
| **Sample size concern** | Grows every poll cycle (dozens/day) | Grows by *one data point per real scheduled macro event* (~6 RBI meetings + 1 Budget + 12 CPI releases a year) — a much sharper small-sample constraint, treated accordingly |

## Method

**1. Define the shock precisely.** A lag measurement is only as good as the timestamp it's measured against — an event whose "shock time" is off by a minute silently corrupts the baseline the whole detection method depends on. Every event in the calendar carries an explicit `timing_confidence` (`"minute"` for RBI/Budget, verified against real coverage of the actual schedule; `"approximate"` for CPI dates extrapolated from an observed release pattern, flagged as such rather than asserted with false precision) and is classified `market_hours_event: bool`, since CPI/IIP releases land *after* NSE closes and need a fundamentally different capture mechanic (see below).

**2. Capture ticks around the event.** For a market-hours event (RBI, Budget), a WebSocket recorder connects a fixed window before the shock and stays connected through a window after, logging every spot and option tick with its arrival timestamp. For an after-hours event (CPI), there's nothing to stream while the market is shut — instead, three discrete REST snapshots are taken: the last price before close, the first price after the next session opens, and a later snapshot once the session has had a few minutes to digest the news.

**3. Detect the first real move.** For a tick series bracketing a shock, compute the mean and standard deviation from ticks *strictly before* the shock (the baseline) — including even one post-shock tick in the baseline would bias the detector toward missing the very move it's trying to find. Scan forward and flag the first tick whose deviation from that baseline crosses a z-score threshold (default 3σ). Applied independently to a spot series and an option series, the lag is simply the difference between their two first-move timestamps.

**4. Measure how long it takes to settle.** A separate metric — time from shock to the first tick that has closed ≥90% of the eventual gap between the pre-shock level and the settled post-shock level — captures adjustment *speed*, not just *order*.

**5. Report honestly.** Every cross-event summary (mean/median/stdev of a handful of lags) is explicitly framed as descriptive statistics, never a hypothesis test — with a realistic sample of 3-6 real events a year, a formal significance claim would be more misleading than none at all.

## Architecture

![Architecture: event_calendar.py drives tick_recorder.py's capture window (WebSocket path for market-hours events, REST gap-snapshot path for after-hours events), which writes to diffusion_db.py; lag_detection.py, adjustment_curves.py, and gap_diffusion.py read it back for analysis and feed charts.py](docs/architecture.svg)

Three lead-lag angles share one detection primitive (`lead_lag_seconds()`) rather than three copies of the same math — `spot_option_lag_seconds()` (headline), `cross_index_lag_seconds()` (NIFTY vs. BANKNIFTY), and `gap_move_breakdown()` (overnight/after-hours) are thin, semantically-named wrappers over the same tested core. The charting layer follows the identical pattern. `diffusion_db.py`'s SQLite file is deliberately separate from the companion project's continuous-poll `snapshots.db` — different capture cadence, different meaning per row.

## Engineering Discipline: Test Before Trusting

Every statistical/detection function is validated against **synthetic data with a known, injected ground truth** before being trusted — not just checked for "runs without error." Concretely: `spot_option_lag_seconds()` is tested by constructing two synthetic tick series with a deliberately different, known jump offset (e.g. spot moves at +10s, the option at +40s) and confirming the function recovers a value close to the true 30-second injected difference — in both directions, and in a null case where the threshold is never crossed. The same discipline applies to first-move detection (including the degenerate flat-baseline edge case), the settle-speed metric, and the overnight-gap breakdown (including a deliberate test of a real *overreaction-and-reversal* case, since the gap fraction is intentionally **not** clamped to `[0, 1]` — clamping would silently misreport a genuine overshoot-then-fade pattern as a smooth, monotonic move).

This discipline caught real things, not hypothetical ones — logged transparently rather than hidden:
- A **module-name collision** between this project's database module and the sibling project's own `db.py`, invisible when each project's tests ran alone, only surfaced by running both test suites together.
- A **scale-invariance decision for cross-index lag detection**, verified empirically (re-ran the detection math at both rupee- and paise-scaled magnitudes, confirmed identical results) rather than just asserted from the math.
- A **test-fixture bug** (not a code bug) caught while adding coverage for the WebSocket reference-table builder — two synthetic underlyings colliding on the same fake instrument key, exactly the kind of thing a passing-by-coincidence test can hide until it's specifically checked.
- A **live debugging session**, documented mid-flight rather than only once resolved: a smoke test during real NSE trading hours confirmed the REST layer, WebSocket auth, connection, and subscribe all work end-to-end against the live Upstox API — but real tick data hasn't been confirmed yet (see below). The diagnostic process (inspecting the protobuf `FeedResponse.type` field to distinguish a housekeeping message from a real tick) and the two live, undistinguished hypotheses for the gap are written up precisely, not glossed over.

Full decision-by-decision and bug-by-bug log: [`BUGS.md`](BUGS.md).

## What's Done / What's Left

| Phase | Status | Detail |
|---|---|---|
| **0 — Setup** | 🟢 Done | Project scaffolded with its own copy of the Upstox data layer (auth, REST client, WS reference-table builder, compiled protobuf schema) — extracted, not blindly copied, so it depends on nothing it doesn't actually use. |
| **1 — Event calendar** | 🟢 Done | Real RBI MPC / Union Budget / CPI dates, WebSearch-verified, each with an honest timing-confidence label. |
| **2 — Tick capture** | 🟡 Code done, live capture in progress | WebSocket recorder + REST gap-snapshot capture both built and synthetic-tested. Live smoke-tested during real trading hours: connection/auth/subscribe confirmed; real tick delivery still being debugged (see below). |
| **3 — Lag & diffusion measurement** | 🟢 Done | First-move detection, lead-lag, settle-speed — all synthetic-validated against known injected ground truth. |
| **4 — Visualization** | 🟢 Done | Diffusion-timeline chart generation, tested; no real event to plot yet. |
| **5 — Stretch angles** | 🟡 2 of 3 done | NIFTY-vs-BANKNIFTY cross-index lead-lag ✅, overnight-gap diffusion for CPI ✅, GDELT unscheduled-event catching ⬜ (not started). |
| **6 — Packaging** | 🟡 In progress | This README; a real headline chart and finding are still pending real data. |

**Why there's no headline finding yet, and why that's not a red flag:** unlike the arbitrage detector (which can backfill by polling continuously), this project's core data — high-frequency ticks bracketing a *real* scheduled macro shock — cannot be manufactured or backfilled. It requires the recorder actually running during an actual RBI decision, Budget, or CPI release. The next real, capturable market-hours event is the **RBI MPC on 2026-10-07 at 10:00 IST**; the next CPI release is expected mid-September. Everything that *can* be built and proven correct without waiting for one of those has been — the entire measurement and statistics pipeline is complete and tested against ground truth, which is the harder, more failure-prone part of a project like this to get right. The live smoke test above is a first, real step past "code that should work" toward "code confirmed against the real exchange."

## Real Numbers So Far

Not the headline result yet — that needs a captured event — but real, live-confirmed facts about the system itself, gathered 2026-08-26 during actual NSE trading hours:

- **660** real NIFTY + BANKNIFTY option legs and 2 index instruments built into a live reference table in ~1 second via REST.
- A real, working WebSocket connection to Upstox's feed (`wss://wsfeeder-api.upstox.com/...`), subscribed successfully with no rejection.
- **73/73** of this project's own tests passing; **96/96** on the companion arbitrage project — both suites confirmed independent and uncontaminated after this project's code was relocated into its own self-contained tree.

## Repo Structure

```
Pricing-Information-Diffusion-Model/
├── README.md                # this file
├── ROADMAP.md                # phase-by-phase build plan (the detailed version of the table above)
├── BUGS.md                   # decision log + bug log, dated, filed the day each happened
├── docs/
│   └── architecture.svg     # the diagram above
├── requirements.txt
├── .env.example
├── src/
│   ├── data/                 # this project's own trimmed Upstox client (auth, REST, WS reference table, proto)
│   └── diffusion/            # event calendar, tick recorder, lag detection, adjustment curves, charts, gap diffusion
├── tests/                    # 73 tests, synthetic ground-truth style throughout
└── data/                     # diffusion_ticks.db + generated charts (gitignored / committed selectively)
```

## Running It Yourself

```bash
cd information_diffusion_model
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add your own Upstox Analytics access token

pytest tests/          # 73 tests, all synthetic — no live credentials needed
```

Live capture (`tick_recorder.run_capture()` / `record_gap_tick()`) needs a real Upstox Analytics access token and, for the market-hours path, an active NSE trading session — see `ROADMAP.md` §8 for the current capture calendar.

## Limitations, Stated Plainly

- **Small, real sample size ceiling.** Unlike a continuously-polled dataset, this project's real data grows by roughly one point per scheduled macro event — a structural constraint, not a shortcut taken. Every cross-event statistic is reported as descriptive, never as a significance claim.
- **No real captured event yet.** The entire pipeline is validated against synthetic ground truth and a partial live connectivity test, not yet a real end-to-end capture. This README will be updated with the actual headline finding the moment one exists.
- **Local arrival-timestamp noise.** Ticks are timestamped on local receipt, not by an exchange-side sequence number — adds millisecond-scale noise on top of whatever real lag exists, small relative to a lag reported in seconds but worth naming.
- **Single feed, single broker.** No cross-venue confirmation; results (once they exist) describe Upstox's view of NSE, not a cross-exchange consensus.

## Companion Project

**[NSE Options Arbitrage Detection Engine](https://github.com/Nukebyt/NSE-Options-Arbitrage-Detection-Engine)** — the structural, static-consistency half of this two-project portfolio: no-arbitrage checks (put-call parity, vertical spreads, butterfly convexity) on the same NIFTY/BANKNIFTY option chain, backtested net of real transaction costs, with a real-time WebSocket detection layer. Originally targeted a US prediction-market platform before a hard India-access restriction forced a pivot to NSE options — a genuine engineering-adaptation story documented in that project's own `BUGS.md`.

---

*Author: Priyanuj Boruah (Nukebyt). Built as a portfolio project targeting quant/market-microstructure-adjacent roles. See `BUGS.md` for the full, dated engineering log behind every decision above.*
