# Bug Report Log — Information Diffusion Model

Companion to [ROADMAP.md](ROADMAP.md). Same rule as the companion [NSE Options Arbitrage Detection Engine](https://github.com/Nukebyt/NSE-Options-Arbitrage-Detection-Engine) project's own log: **log a bug the day you fix it, not at the end of the project** — you won't remember the real root cause or the wrong turns three weeks later, and this file is your best "tell me about a bug" material. Numbering restarts at DEC-1/BUG-1 for this project — see that project's own `BUGS.md` for its (much longer) log; the two aren't merged since they're different projects with different histories.

Every real entry should let you answer, from memory, in front of an interviewer: what broke, how you noticed, what the root cause actually was, and what you changed.

---

## How to file an entry

```markdown
### [BUG-N] Short title

- **Phase:** (0–6)
- **Date found:** YYYY-MM-DD
- **Symptom:** What you observed
- **Root cause:** What was actually wrong, not just where it surfaced
- **Fix:** What you changed
- **How you caught it:** unit test / manual inspection / a check disagreeing with intuition / a crash
- **Interview angle:** One sentence — why this is worth telling an interviewer
```

Severity/impact tags, optional: `#data-integrity #logic #concurrency #performance #api-integration #verification #correctness`

---

## Decision Log

### [DEC-1] Rebuilt around NSE options instead of trying to salvage the original pre-pivot plan

- **Phase:** 0
- **Date:** 2026-08-26
- **Context:** This project's original planning file (`information_diffusion_model.md`) was written entirely around a different prediction-market venue + GDELT, before the sibling mispricing project hit that venue's hard India-access restriction and pivoted to NSE options via Upstox (sibling project's own bug/decision log). By the time this project's own planning started, the sibling project's pivot was already complete and its India-accessible data layer already built and tested.
- **Alternatives considered:** (1) try to adapt the old GDELT + prediction-market plan to a different venue accessible from India — rejected, no clean equivalent exists and it would duplicate the sibling project's already-solved access problem with a different vendor; (2) keep the old plan's *shape* (a poller-based Phase 2) but just swap the original venue for Upstox — rejected once actually reading `ws_stream.py` showed a dedicated poller wasn't needed at all (see DEC-2 below); (3) rebuild the whole plan around NSE options, reusing the sibling project's data layer directly.
- **Decision:** (3). The research question (how fast does information diffuse, who leads) is domain-agnostic; only the market and data plumbing needed to change, and NSE options already had working, tested infrastructure to build on top of.
- **Interview angle:** a second, independent instance of the same "hit a real external constraint, adapt deliberately rather than force the original plan" story as the sibling project's own DEC-1 — worth being able to say precisely that this wasn't just "copy what worked before," it required actually re-deriving what the new domain made possible (DEC-2) rather than assuming the old plan's structure still applied.

#pivot #decision

### [DEC-2] Reusing `ws_stream.py`'s existing dual spot+option feed instead of building a new poller

- **Phase:** 0/2
- **Date:** 2026-08-26
- **Context:** The old pre-pivot plan's Phase 2 explicitly assumed a brand-new, dedicated high-frequency poller was needed ("your regular background polling... is too coarse for lag measurement, this needs its own high-frequency capture job") — written before the sibling project had any real-time layer at all.
- **What actually happened:** reading `ws_stream.py` directly (not recalling it from memory, per this project's own stated discipline) showed its `LiveBook` class already tracks both spot index ticks (`apply_index_feed`) and option ticks (`apply_option_feed`) off a single already-working WebSocket connection, and `build_reference_table()` already subscribes to both. The high-frequency, event-driven capture layer the old plan assumed didn't exist yet, already existed.
- **Decision:** `tick_recorder.py` taps the same connection/reference-table pattern (reusing `build_reference_table()` and `get_ws_authorized_url()` directly) and adds a *recording* responsibility — log every tick with an arrival timestamp — instead of building a parallel high-frequency REST poller from scratch.
- **Interview angle:** a clean, concrete example of checking a plan's assumptions against the *current* state of the code before building on top of them, rather than trusting what an older planning document said was true at the time it was written — the constraint the old plan described was real when it was written and stopped being true once the sibling project's WS layer was built.

#decision #verification

### [DEC-3] Separate `diffusion_ticks.db` instead of a new table in the sibling project's `snapshots.db`

- **Phase:** 0
- **Date:** 2026-08-26
- **Context:** Both databases ultimately store option/spot price observations, which made "just add a table to the existing DB" a real, tempting option to avoid a second SQLite file.
- **Decision:** kept them separate. `snapshots.db` is a continuous, roughly-uniform ~60s sample of the full chain; `diffusion_ticks.db` is a bursty, sub-second-to-second stream limited to narrow event windows, tagged by `event_name`. The risk being designed around is a future query silently blending two cadences that mean structurally different things (e.g. "average time between consecutive rows for instrument X" would be meaningless computed across both).
- **Interview angle:** a deliberate schema-separation decision made *before* a confusion bug could happen, not a fix applied after one — worth contrasting with the sibling project's own BUG-5/BUG-7, which were real bugs caught after the fact; this is the same underlying lesson (don't let two different kinds of rows look interchangeable) applied preemptively during design instead of reactively during debugging.

#decision

### [DEC-4] Chose "spot vs. options lead-lag" as the headline angle over cross-index or cross-strike diffusion

- **Phase:** 0
- **Date:** 2026-08-26
- **Context:** Three real candidate headline questions were on the table before any Phase 1+ code was written: (a) does NIFTY spot lead or lag its own options after a shock; (b) does NIFTY or BANKNIFTY absorb a shock first (cross-index); (c) does a shock propagate from ATM strikes out to the wings, or the reverse, across the IV smile (cross-strike). This was a real decision point, asked of and answered by the project owner directly rather than assumed.
- **Decision:** (a). It maps onto a genuinely classic, named market-microstructure research question ("do options lead the underlying"), which makes it easier to frame precisely in an interview than a bespoke cross-index or cross-strike comparison would be, and it connects naturally to the companion project's own IV-curve work as a future extension once options themselves are the object of study, not the underlying.
- **Not abandoned, deferred:** both (b) and (c) remain real Phase 5 stretch goals (ROADMAP.md §Phase 5) — (b) especially, since the tick recorder already captures both indices with zero additional data-collection work, only a small analysis-layer addition.
- **Interview angle:** shows a real prioritization decision among several legitimate options, with an explicit, statable reason (interview framing + code reuse) rather than picking the first idea that came to mind — worth walking through if asked "why this angle and not the others."

#decision #scope

### [DEC-5] Kept ticks in raw price_paise units through the lag-detection pipeline, deliberately skipped a rupee-conversion step

- **Phase:** 3/5
- **Date:** 2026-08-26
- **Context:** Building `rows_to_ticks()` (converts `diffusion_db.fetch_ticks()` rows into the `(timestamp, price)` shape `lag_detection.py` expects) for the Phase 5 NIFTY-vs-BANKNIFTY cross-index work, the obvious-looking next step was to convert `price_paise` back to rupees before handing ticks to `detect_first_move()`, matching how a human would read the number.
- **Decision:** didn't. `detect_first_move()`'s baseline mean/stdev are computed independently per series, so the z-score threshold-crossing test is scale-invariant within a series — multiplying every price in a series by a constant (paise vs. rupees) doesn't change which tick crosses the z-threshold or when. A conversion step would add code and a unit-tracking burden for zero behavioral benefit, and would directly reintroduce the *shape* of the sibling project's BUG-9 (a 100x rupee/percentage scale mismatch that silently produced a plausible-looking wrong number) for no reason.
- **How verified, not just asserted:** re-ran the existing `lag_detection` synthetic tests with prices scaled by 100x (paise-shaped magnitudes, e.g. 2,400,000 instead of 24,000.0) alongside the original rupee-shaped versions — identical detected offsets and lag values in both, confirming the scale-invariance claim rather than taking it on faith.
- **Interview angle:** a good example of catching a plausible-but-unnecessary "fix" *before* writing it, by actually reasoning about whether the downstream math cares about the input's units — rather than converting reflexively because "rupees looks more normal to read," which is exactly the instinct that would have added a real unit-mismatch bug surface for no functional gain.

#decision #verification #correctness

### [DEC-6] Didn't clamp gap_move_breakdown()'s fractions to [0, 1]

- **Phase:** 5
- **Date:** 2026-08-26
- **Context:** `gap_move_breakdown()` (the overnight-gap mechanic for after-hours events) reports what fraction of an after-hours event's eventual price move happened in the closed-market gap versus after trading resumed. The natural mental model — "some fraction happened overnight, the rest happened after open" — makes `[0, 1]` look like the obviously correct range, and clamping the output to that range was a real option considered while writing the function.
- **Decision:** didn't clamp. A real price can open *past* where it eventually settles and then partially revert during the session (an overreaction that fades) — that case needs `gap_fraction > 1` (more than 100% of the eventual move happened in the gap, then partially unwound) and a *negative* `followthrough_fraction` to be represented honestly. Clamping to `[0, 1]` would silently misreport a genuine overreaction-and-fade pattern as if the price had moved monotonically toward its settled level, which is exactly the kind of real, interesting market behavior this stretch angle exists to be able to see.
- **Verified with a test, not just reasoned about:** `test_gap_move_breakdown_reversal_after_open_gives_fraction_over_one` constructs exactly this case (pre_close=24000, post_open=24150, settled=24120) and asserts `gap_fraction == 150/120` (1.25) and `followthrough_fraction == -30/120` (-0.25), rather than only testing the tidy monotonic case.
- **Interview angle:** a good example of not reaching for the "obviously correct" bound (clamping to a plausible-looking range) without first asking whether the real underlying phenomenon can actually violate it — same instinct as not assuming every "violation" your own code finds is a bug in the code rather than a genuine signal (sibling project's DEC-4/DEC-5 pattern, applied here to a design choice made *before* shipping rather than a bug caught after).

#decision #correctness

### [DEC-7] Gave this project its own copy of the Upstox data layer, extracted rather than copied whole

- **Phase:** 0/2
- **Date:** 2026-08-26
- **Context:** A different working session, on the companion mispricing project, discovered this project's `src/diffusion/` module and its 7 test files had been created directly inside that *other* project's shared `src/`/`tests/` trees rather than in this project's own tree — surfaced when that project's own `pytest tests/` run silently went from 85 to 151 tests, which got investigated rather than reported as-is. That session relocated the files but deliberately left `tick_recorder.py`'s broken cross-project import unfixed, naming three real options (own copy / shared package / relative path to the other repo) as an architecture decision for this project's own owner to make, not to guess at.
- **Decision:** option 1 — this project's own copy of the data layer, since both projects are explicitly headed toward separate GitHub repos (ROADMAP.md S7, already written before this decision came up) and a relative path or shared package would either not survive the split or add real setup overhead for a two-person-scale portfolio project.
- **Refinement made while executing, not just following the letter of the plan:** `tick_recorder.py` never actually used more than two functions out of the sibling project's `ws_stream.py` — `build_reference_table()` and `build_subscribe_message()`, neither of which touches that module's `consistency.py` arbitrage-check imports. Copying `ws_stream.py` whole (the literal reading of "own copy") would have quietly made this project depend on code it never calls, for a domain (structural no-arbitrage checks) that has nothing to do with information diffusion. Extracted just those two functions into a new `src/data/reference_table.py` instead. Same reasoning applied to `auth.py`: copied only `get_auth_headers()`/`get_ws_authorized_url()`, not the sibling project's separate, in-progress OAuth2 experiment (its own DEC-7-numbered entry, unrelated to this one) that this project has no use for.
- **Verified, not assumed:** ran both projects' test suites separately, from each project's own root, after the change — this project 66/66 (41 previously-passing plus the 25 that had been failing to import), sibling project 96/96, confirming the relocation didn't leak anything either direction.
- **Interview angle:** a clean example of a "vendor a copy" decision done with actual judgment about *what* to copy, not a reflexive whole-file copy-paste — recognizing that a stated architecture option ("give this project its own auth.py + compiled proto") still leaves a real design choice underneath it about scope, and that dragging in an unrelated dependency (arbitrage-check imports, an unrelated OAuth2 experiment) just because it happened to live in the same source file would be a worse outcome than the broken import it was fixing.

#decision #tooling

### [DEC-8] `min_consecutive` confirmation on first-move detection, reported alongside the plain rule

- **Phase:** 3
- **Date:** 2026-10-05
- **Context:** the detector flags the first tick whose z-score against the pre-shock baseline reaches 3. Option mid-prices sit on a 0.05 tick grid and one-tick bid/ask bounces are common, so a single noisy tick right after the shock can be reported as "the option moved first" — which would flip the sign of the headline lag.
- **Decision:** added `min_consecutive` (default 1, so existing behavior and tests are unchanged). Analysis reports `confirm_1` (plain rule) and `confirm_3` (3 consecutive breaching ticks, reporting the *first* tick of the run) side by side. If they disagree materially, that event's lag is noise-sensitive and should be described that way, not as a clean number.
- **Not done, deliberately:** returns-based / MAD-based detection, or changing the default threshold. Changing the method after seeing real data is a forking-paths problem — any such change should be decided before the first real event is analysed and applied to every event (see ROADMAP "Improvements").

#decision #correctness

### [DEC-9] Analysis picks one ATM option leg per type instead of passing all option rows to the lag function

- **Phase:** 3
- **Date:** 2026-10-05
- **Context:** the recorder stores ~660 option legs across strikes and two expiries in one table; `fetch_ticks(kind="option")` returns them all interleaved. Feeding that to `spot_option_lag_seconds()` would compute a baseline mean/stdev across *different contracts* — a meaningless series that still yields a plausible-looking number.
- **Decision:** `analyze_event.pick_atm_legs()` selects, per option type, the nearest-expiry contract closest to the last pre-shock spot that has >=5 pre-shock ticks and >=1 post-shock tick (an untraded ATM strike falls back to its nearest liquid neighbour). The chosen strike/expiry are recorded in the result so the choice is auditable. `expiry` and the exchange's own last-trade timestamp (`exchange_ts_ms`) became DB columns so this choice is possible at all.
- **Interview angle:** a "data shape doesn't match the function's input contract" catch before it silently produced a number.

#decision #correctness

### [DEC-10] Pre-registered analysis plan: primary detector chosen BEFORE any real event was seen

- **Phase:** 3
- **Date:** 2026-10-05 (two days before the first real capture)
- **Context:** the first-move detector has real degrees of freedom — z-threshold, level vs. return statistic, single-tick vs. confirmed breach, which option strike, which time base. With one event per month, picking whichever combination produces a clean number *after* seeing the data would be a forking-paths problem that no amount of later honesty fixes.
- **Decision (fixed now, applied to every event):**
  - **Primary** = `returns_c3`: z-score (threshold 3.0) of the trailing 5-tick *return* against the pre-shock return distribution, requiring 3 consecutive breaching ticks. Chosen because a level z-score is biased by pre-shock drift/random-walk (demonstrated on the synthetic demo: on BANKNIFTY futures the level detector reports the wrong sign, +3s vs the injected -3s, while the returns detector recovers it), and confirmation guards against single-tick bid/ask bounces.
  - **Sensitivity (always reported, never substituted):** `level_c1`, `level_c3`, `returns_c1`. If the primary and the sensitivities disagree on sign, that event is reported as "method-sensitive", not resolved by picking one.
  - **Primary option leg:** nearest-expiry ATM call and put; the ATM ±2 strike basket is reported alongside to show the result isn't one thin strike.
  - **Time base:** local arrival time for the primary; exchange-time lag and feed-delay stats reported as a check, labelled experimental until validated on a live capture.
  - **False-positive floor:** every real event reports a within-event pseudo-shock placebo (5 min before the shock); separate no-news control windows (`capture --placebo-at`) measure the same detector on ordinary days. A lag is only interpretable against that rate.
  - **Pooling:** descriptive only until n >= 6 events; then percentile-bootstrap CI for the median and an exact sign test, still labelled small-sample.
- **Also added in the same pass:** a futures control leg (does the spot index lag its own future?), IV series, median-of-last-60s settled level, a stall alarm and undecodable-frame tolerance in the recorder, and a clearly-labelled synthetic demo (`diffusion_cli.py demo`) that exercises the whole stack against injected lags.
- **Not changed after seeing data:** nothing — there is no real data yet. Any future change to this plan must be logged as a new DEC entry and re-applied to all prior events.
- **Interview angle:** "I wrote down the analysis plan and the primary metric before the first event, kept the alternatives as sensitivity checks, and built a placebo to measure my detector's false-positive rate" is a much stronger answer to "how do you know it isn't noise?" than a clean number would be.

#decision #methodology #correctness

---

## Anticipated pitfalls by phase (pre-seeded — not real bugs yet)

Same discipline as the sibling project: **not logged bugs** until one actually happens. Convert an item to a dated Log entry the moment it bites; delete an item once confirmed avoided by design.

### Phase 1 — Event Calendar
- **Timezone arithmetic errors converting IST to UTC:** IST is a fixed UTC+5:30 offset with no DST, which makes it simple, but "simple" is exactly the kind of thing worth a dedicated unit test rather than trusting by inspection — a silent 30-minute or 5-hour error here corrupts every downstream baseline/threshold calculation without ever raising an exception.
- **Treating an "approximate" CPI date as if it were as reliable as a confirmed RBI date:** the calendar deliberately carries a `timing_confidence` field for exactly this reason — a future capture run (or a future reader of this project) needs to actually branch on it, not just record it and ignore it.

### Phase 2 — Tick Capture
- **Reusing the sibling project's BUG-6 lesson (WS-authorize endpoint version/field-casing drift) without re-verifying it's still true:** Upstox's own docs can drift again between when the sibling project verified this and when this project's recorder actually runs live — re-check, don't assume the old finding is permanently valid.
- **A capture window that starts recording but never stops** (a bug in `capture_window()`'s boundary math, or a `run_capture()` loop that doesn't correctly detect `stop_at_utc` has passed) — would silently keep an expensive live connection open indefinitely past the intended window, or worse, blend one event's ticks into the next event's capture file if two events are close together.

### Phase 3 — Lag & Diffusion Measurement
- **Off-by-one or boundary-condition errors at exactly the shock timestamp itself:** is a tick timestamped exactly at the shock instant "pre" or "post"? `detect_first_move()`'s partition uses `timestamp_utc < shock_timestamp_utc` for the baseline (strictly before) — worth double-checking this boundary choice is applied consistently everywhere the shock timestamp is used, not just in one function.
- **z_threshold tuned to make synthetic tests pass rather than chosen for a real, statable reason:** the default (3.0) needs to be defensible as a real methodological choice, not backed into by adjusting it until unit tests happened to go green.

### Phase 4 — Visualization
- **Normalizing by the wrong pre-shock level** (e.g. accidentally using the option's pre-level to normalize the spot series or vice versa) — would silently produce a visually plausible but meaningless chart, the kind of bug that's easy to miss because the output still *looks* like a reasonable chart.

### Phase 5 — Stretch
- **Conflating the overnight-gap metric with the intraday lag metric in reporting** — genuinely different mechanics, worth a specific check that any future summary table clearly labels which metric a CPI-release row actually is.

---

## Log

### [BUG-1] `db.py` name collision with the sibling project's `src/data/db.py` broke the full-suite test run

- **Phase:** 0
- **Date found:** 2026-08-26
- **Symptom:** Every individual `test_diffusion_*.py` file passed in isolation, but running the *whole* repo's test suite together (`pytest tests/`) failed collection with `ImportError: cannot import name 'fetch_ticks' from 'db' (.../src/data/db.py)` — `test_diffusion_db.py`'s `from db import ...` was resolving to the sibling project's `db.py`, not this project's own.
- **Root cause:** the exact same mechanism as the sibling project's own BUG-4, independently rediscovered rather than avoided by remembering it: this repo uses flat `sys.path.insert()` per test file, no package structure, so Python's `sys.modules` import cache is keyed by bare module name only. Both `src/data/db.py` (sibling) and `src/diffusion/db.py` (this project) were named `db.py`; whichever test file's `sys.path.insert` ran first during collection determined which one got cached under the name `db` for the rest of the session, regardless of which directory another test file had actually put first on its own path.
- **Fix:** renamed this project's file to `src/diffusion/diffusion_db.py` (same disambiguation pattern the sibling project used for its own archived legacy modules), updated its one importer (`tests/test_diffusion_db.py`) and every doc reference to the old path.
- **How you caught it:** ran the *combined* suite (`pytest tests/`), not just this project's own new test files in isolation — each new file had passed individually, which would have looked like "done" without ever exercising the actual collision, exactly the gap the sibling project's own BUG-4 entry already named as the lesson ("check whether a new file shares a basename with anything else importable in the project, not just whether the specific file being added looks self-contained").
- **Interview angle:** a genuine repeat of a documented failure mode from the *same repo's* own bug log, caught the same way the first time was caught (running the full suite, not trusting a file-by-file green check) — a good, honest answer to "did you actually apply the lessons from your own past bugs, or just write them down" (yes, the process that catches it generalized even though the specific collision wasn't consciously guarded against while writing the new file).

#tooling #verification

### [BUG-2] Test fixture for build_reference_table() had both underlyings clobber the same instrument key

- **Phase:** 0 (test coverage added alongside DEC-7's data-layer relocation)
- **Date found:** 2026-08-26
- **Symptom:** Writing `test_diffusion_reference_table.py` (new coverage for the extracted `build_reference_table()`, DEC-7), the first version of `test_build_reference_table_shapes_option_reference_and_index_keys` failed: asserted `option_reference["NSE_FO|C1"]["underlying"] == "NIFTY"`, got `"BANKNIFTY"` instead.
- **Root cause:** the test's own fake `get_option_contracts()` returned the identical fake contract list (`instrument_key="NSE_FO|C1"`) regardless of which underlying it was called for. `build_reference_table()` loops `TRACKED` (NIFTY then BANKNIFTY) and writes into one shared `option_reference` dict keyed by `instrument_key` — with both underlyings' mocked calls returning the same key, BANKNIFTY's pass (processed second) silently overwrote NIFTY's row. Not a bug in `reference_table.py` itself — real Upstox instrument keys are unique per contract, so this collision can't happen against real data — but a real bug in the fixture that would have made the test pass or fail somewhat arbitrarily depending on dict iteration order if the assertion had been written more loosely.
- **Fix:** made the fake `get_option_contracts` return distinct instrument keys per underlying (`key == NIFTY` branch vs. a BANKNIFTY-only key), matching how real Upstox data actually behaves.
- **How you caught it:** the test failed on its first run, exactly the mechanism this discipline exists for — running a new test against real logic surfaced a wrong fixture assumption immediately, rather than a passing-by-coincidence test silently hiding it.
- **Interview angle:** a small, honest example of "testing the test" — a synthetic fixture can have its own bugs, and a test that happens to pass isn't proof the fixture correctly models the real system's behavior (here, uniqueness of instrument keys across underlyings) until something forces the assumption to be checked, which a failing assertion did here.

#tooling #verification

### [BUG-3] Live WS smoke test: connection/auth/subscribe all confirmed, real live_feed ticks still unconfirmed

- **Phase:** 0/2
- **Date found:** 2026-08-26, ~15:10-15:23 IST (inside real NSE trading hours, market closes 15:30 IST)
- **Symptom:** Ran a bounded live smoke test against this project's own (post-DEC-7) data layer, three attempts totaling ~150s of combined listening time during real market hours, not a synthetic/mocked run:
  - `build_reference_table()` (REST: `list_expiries` + `get_option_contracts`): **confirmed working live** — 660 real option legs + 2 indices (`NIFTY`, `BANKNIFTY`) built in ~1s.
  - `get_ws_authorized_url()`: **confirmed working live** — returned a real, working `wss://` URL. New, previously-undocumented fact worth recording: the actual WS host is `wsfeeder-api.upstox.com` (path `/market-data-feeder/v3/upstox-developer-api/feeds`) — not previously written down anywhere in either project's docs, since the sibling project's own WS work never got a real successful connection confirmed live before now (their ROADMAP.md Phase 4 note: "no tick was received... consistent with NSE being closed").
  - WS `connect()` + `build_subscribe_message()` send: **confirmed working live** — connects cleanly, no rejection.
  - **Not confirmed:** actual tick data. Across all three attempts, exactly one message arrived per connection, each with `FeedResponse.type == market_info` (enum value 2 — `initial_feed`=0 and `live_feed`=1, the ones that would carry real quotes, per `MarketDataFeed.proto`'s own `Type` enum), with an empty `feeds` map. Zero `live_feed` messages, zero real spot or option ticks, in any of the three attempts (bounded windows: ~90s, ~40s, ~20s, run back-to-back in the last ~13 minutes before close).
- **Root cause (updated 2026-10-05): the subscribe `mode` string was invalid.** Upstox's v3 docs list exactly four valid modes — `ltpc`, `option_greeks`, `full`, `full_d30` — and the code was sending `"full_d5"`. That string is the *protobuf enum name* (`RequestMode.full_d5`, which the server echoes back on decoded messages), not a legal value for the JSON subscribe request; the server accepts it without error and simply never registers a subscription. That fits every symptom: clean connect, clean subscribe, a lone `market_info` housekeeping message per connection, and not even an `initial_feed` snapshot (which a real subscription triggers immediately). It also explains why thin end-of-day trading never explained "not even one index LTP tick".
  - Original (superseded) hypotheses, 2026-08-26: (1) genuinely thin update activity in the last ~10-15 minutes before close; (2) the subscribe message's format is wrong in a way that gets silently accepted. (2) was right, and it was the *mode value* — not the JSON shape or the binary framing, both of which match the docs.
  - **Status: RESOLVED, confirmed live 2026-10-06 ~10:07 IST.** `diffusion_cli.py preflight` during NORMAL_OPEN (45s, 738 option legs + 2 futures + 2 indices, mode `full`) received `market_info` x1, `initial_feed` x1, `live_feed` x172 and decoded 8,193 ticks (7,862 option / 277 spot / 54 future), 0 decode errors, 0 stalls. The 2026-08-26 failure (zero live_feed messages) is therefore confirmed to have been the invalid `full_d5` mode.
  - **Fix:** the default mode is now `"full"`, and `build_subscribe_message()` raises `ValueError` for any mode outside the four valid ones (regression test `test_build_subscribe_message_rejects_full_d5`), so this exact failure can't be silent again.
- **What this is NOT:** the sibling project's earlier, more benign finding ("no tick because NSE was closed at 11pm") does not apply here — this run happened while NSE was genuinely open, which makes the zero-live-feed result a real, more concerning open question rather than an expected non-result.
- **Verification step:** run `python src/diffusion/diffusion_cli.py preflight` during NORMAL_OPEN. It requires real `live_feed`/`initial_feed` messages that decode into both spot AND option ticks, and prints `PREFLIGHT FAILED` with the specific symptom otherwise. If it still fails with the corrected mode, `--instruments N` isolates whether the 662-instrument request size is the problem.
- **How you caught it:** didn't stop at "no error was thrown" — built a diagnostic version that logs `FeedResponse.type` and `currentTs` explicitly per message (checked the `.proto` schema for what fields existed rather than guessing), which is what turned an ambiguous "zero rows decoded" result into a precise, actionable one (confirmed protocol-level communication is happening, `market_info` housekeeping messages ARE arriving, but subscription-confirmed live ticks are not).
- **Interview angle:** a real, in-progress debugging story with an honestly-unresolved root cause — "here's exactly what I confirmed, here's exactly what I didn't, here's the two remaining hypotheses and how I'd distinguish them" is a stronger, more credible answer than a tidier-sounding but incomplete "it works now" would be, and matches this project's own stated discipline of not overclaiming a result the data doesn't actually support yet.

#api-integration #verification #resolved

### [BUG-4] Recorder lost its buffered ticks whenever the connection dropped

- **Phase:** 2
- **Date found:** 2026-10-05 (code review ahead of the first real capture)
- **Symptom:** none yet — found by reading, before it could bite on the one real shot at an event.
- **Root cause:** `_connect_and_record()` buffered up to 19 ticks and only flushed them to SQLite at 20 ticks or after a clean loop exit. A dropped connection raises out of the `async for`, skipping the final flush — so every drop silently discarded the most recent buffered ticks, and the moments around a drop are exactly when a capture is already stressed. The reconnect handler also only caught `ConnectionClosed`/`OSError`, so a handshake failure (`InvalidStatus`) would have ended the whole capture.
- **Fix:** flush in a `finally` block; also flush on a 1-second timer rather than only a tick count, so a crash loses at most ~1s; the reconnect handler now catches `WebSocketException`.
- **How you caught it:** reading the loop with "what if it raises here" in mind; regression test `test_buffered_ticks_flushed_when_connection_drops` uses a fake websocket that drops mid-stream.
- **Interview angle:** a data-loss-on-the-failure-path bug in a pipeline whose whole value is one unrepeatable capture, found by failure-path review rather than by the real event exposing it.

#data-integrity #concurrency

### [BUG-5] A reconnect would have injected a fake post-shock price move

- **Phase:** 2/3
- **Date found:** 2026-10-05
- **Symptom:** none yet — reasoned out from the feed protocol.
- **Root cause:** every successful subscribe makes Upstox send an `initial_feed` message — a snapshot of each instrument's last-known state. On a reconnect mid-window, the recorder stamped that snapshot with the *reconnect arrival time* and stored it like a live tick. Instruments that had not traded would appear to "tick" at the reconnect moment at a stale price, and instruments that moved while disconnected would appear to jump all at once — exactly the signature `detect_first_move()` looks for, unrelated to the shock.
- **Fix:** reconnects (`attempt > 1`) drop `initial_feed` responses; the first connection keeps its snapshot as a baseline point. Regressions: `test_initial_feed_dropped_on_reconnect_but_kept_on_first_connect`, plus an end-to-end test against a local websocket server that drops the connection mid-stream (`test_diffusion_local_ws_integration.py`).
- **Interview angle:** reasoning about what the *protocol* does on reconnect, not just whether the code runs — a measurement artifact that would have produced a plausible but wrong lag.

#correctness #data-integrity

### [BUG-6] CPI calendar dates were extrapolated wrong, and future releases were labeled "(released)"

- **Phase:** 1
- **Date found:** 2026-10-05
- **Symptom:** the calendar had Sep-2026 CPI on 2026-10-13 and Aug-2026 CPI on 2026-09-12; event names carried a "(released)" suffix even for the not-yet-happened October release; the README said the next CPI was "expected mid-September" while it was already October.
- **Root cause:** dates were extrapolated from "~12-13 days after month end" (the roadmap's own caveat said to re-verify against MoSPI). MoSPI's published Advance Release Calendar 2026-27 puts CPI on the **12th** of each month, moved to the next working day when that is a weekend/holiday. Aug-2026 data was released Mon 2026-09-14 (the 12th was a Saturday), and Sep-2026 data is due Mon **2026-10-12**, not the 13th.
- **Fix:** calendar rebuilt from MoSPI's calendar; names no longer claim "(released)"; `timing_confidence="minute"` where the date is MoSPI-published or confirmed by an actual release, `"approximate"` only for 2026-12-14 (Dec 12 is a Saturday and the shift is unconfirmed). Tests assert each date against the 12th-or-next-working-day rule and that no event lands on a weekend.
- **Interview angle:** the project's own pitfall list warned about exactly this; the honest outcome is that the warning was right and the extrapolated date was off by a day — which, for a gap capture triggered by the clock, is the difference between capturing the release and missing it.

#data-integrity #verification

### [BUG-7] A network outage mid-window would have been reported as a market "first move"

- **Phase:** 2/3
- **Date found:** 2026-10-06, live, during the first full-length rehearsal (a no-news control capture at 10:30 IST)
- **Symptom:** the capture lost its connection twice (a keepalive-ping timeout at ~10:20, then at ~10:32 a total loss of DNS/internet that lasted ~10 minutes). The database showed a 576-second hole in NIFTY spot starting ~100s after the control "shock", plus a 29s stall at the start. The recorder itself recovered correctly every time (reconnect, stale snapshot dropped, no data loss before the drop).
- **Root cause (two parts):**
  1. *Analysis:* after a hole, the first tick carries all the price change accumulated inside it. Both detectors would flag a "first move" at the reconnect timestamp — a pure data-outage artifact, indistinguishable in the output from a real reaction. With the real event capture possibly hit the same way, this could have produced a confident-looking wrong lag.
  2. *Recorder:* a silent link death (socket open, nothing arriving) was only noticed when the websocket keepalive timed out (~27s observed), and reconnect backoff reached 30s, so each incident cost more data than necessary.
- **Fix:** (1) `detect_first_move(max_gap_seconds=...)` reports a breach that first appears right after an outage as **censored** (timestamp `None`, `censored=True`) — "it moved somewhere inside the gap" — rather than timing it at the reconnect; analysis derives the outage threshold per series (>10s and >20x its own median inter-tick time), lists outages as warnings (loudly if one overlaps the shock), marks affected lags `[CENSORED by data outage]`, and excludes censored series from false-positive counts. (2) The recorder now treats 10s of total silence as a dead link and reconnects, pings every 10s, and caps reconnect backoff at 5s.
- **Also learned from the same run:** the plain one-tick detectors fire often on quiet windows (`returns_c1` fired on 4 of 5 series, `level_c1` on 2 of 4) while the pre-registered 3-tick-confirmed primary fired on 1 of 2 evaluable series — direct evidence for DEC-10's choice of a confirmed primary and for always reporting a false-positive floor. And for options/futures the exchange timestamp is the last *trade* time, so "feed delay" there is trade age, not network latency; only the index timestamp measures feed jitter.
- **How you caught it:** by running a full-length control capture a day early and then querying the database for gaps, instead of trusting "the capture finished". Regression tests: `test_move_that_first_appears_after_an_outage_is_censored_not_timed`, `test_silent_dead_link_triggers_reconnect_error_quickly`, `test_analysis_flags_outage_at_shock_and_excludes_censored_from_placebo_counts`.
- **Interview angle:** rehearsal found a failure mode that no synthetic test had — real networks drop — and the fix is a statistical one (censoring), not just a retry loop.

#data-integrity #correctness #verification

### [BUG-8] A colon in an event name silently turned the capture log and charts into hidden NTFS streams

- **Phase:** 4
- **Date found:** 2026-10-06 (first control-window capture)
- **Symptom:** after the control capture, `data/logs/` held a visible **0-byte** file `capture_PLACEBO_2026-10-06_10` and `data/diffusion_charts/` a 0-byte `PLACEBO_2026-10-06_10`; the log content and two of three charts were nowhere to be found, and no error was raised.
- **Root cause:** the placebo event name is `PLACEBO 2026-10-06 10:30 IST`. Filenames were built with `name.replace(' ', '_')` only, leaving `:`. On Windows/NTFS a colon is not rejected: the text after it becomes an *alternate data stream* of a file named for the text before it. (The JSON and one chart had happened to strip colons already, which is why some outputs survived and others didn't — an inconsistent-sanitization bug.)
- **Fix:** one shared `paths.safe_filename()` (letters, digits, `.`, `_`, `-`; everything else collapses to `_`) used by the log, results and every chart; tests assert no colon can reach a filename. The stray 0-byte files were deleted and the analysis re-run (the database was never affected).
- **How you caught it:** noticing a 0-byte file with a truncated name in the directory listing, instead of assuming "no error means the files were written".
- **Interview angle:** a platform-specific failure that raises nothing and drops data quietly; found by checking the outputs exist, not the exit code.

#correctness #tooling

*(Further entries land here as they're actually found.)*
