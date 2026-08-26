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
- **Root cause: not yet determined, stated honestly rather than guessed at.** Two real, undistinguished possibilities: (1) genuinely thin update activity in the last ~10-15 minutes before close for a `full_d5` depth-mode subscription specifically (plausible but doesn't explain why not even one index LTP tick arrived, since spot index LTP updates are typically frequent even in quiet markets); (2) the subscribe message's format is actually wrong in a way that gets silently accepted (no error) but never actually registers a real subscription — `reference_table.py`'s own docstring already flagged this exact risk as unverified ("best-effort, not independently confirmed against a real successful subscribe"), inherited unchanged from the sibling project's identical caveat.
- **What this is NOT:** the sibling project's earlier, more benign finding ("no tick because NSE was closed at 11pm") does not apply here — this run happened while NSE was genuinely open, which makes the zero-live-feed result a real, more concerning open question rather than an expected non-result.
- **Next diagnostic step, not done yet (ran out of market-hours time this session):** re-run with a full-session window (subscribe near market open, not in the last 15 minutes before close) to rule out possibility (1); if still zero `live_feed` messages, try a smaller subscribed instrument count (e.g. 2 indices only, not 662 instruments) and/or a different `mode` value to isolate whether the 662-instrument `full_d5` request itself is the problem.
- **How you caught it:** didn't stop at "no error was thrown" — built a diagnostic version that logs `FeedResponse.type` and `currentTs` explicitly per message (checked the `.proto` schema for what fields existed rather than guessing), which is what turned an ambiguous "zero rows decoded" result into a precise, actionable one (confirmed protocol-level communication is happening, `market_info` housekeeping messages ARE arriving, but subscription-confirmed live ticks are not).
- **Interview angle:** a real, in-progress debugging story with an honestly-unresolved root cause — "here's exactly what I confirmed, here's exactly what I didn't, here's the two remaining hypotheses and how I'd distinguish them" is a stronger, more credible answer than a tidier-sounding but incomplete "it works now" would be, and matches this project's own stated discipline of not overclaiming a result the data doesn't actually support yet.

#api-integration #verification #unresolved

*(Further entries land here as they're actually found.)*
