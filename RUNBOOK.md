# Event-Day Runbook

The data this project needs can only be captured live, once per event. This is the checklist for not wasting that shot. All commands run from the repo root.

## Schedule (interview Fri 2026-10-09)

| When (IST) | What | Why |
|---|---|---|
| **Tonight / Tue 10-06 before 09:15** | Get the Upstox token into `.env` (below) | nothing live works without it |
| **Tue 10-06, 09:30** | `preflight` | closes BUG-3; the only live proof the feed + decode work |
| **Tue 10-06, start by 09:40** | `capture --placebo-at "2026-10-06 10:00"` | full-scale rehearsal AND your first no-news control window |
| **Tue 10-06, after 11:00** | `analyze`, read warnings | the 10:30 control was spoiled by a ~10 min network outage (BUG-7) |
| **Tue 10-06, start by 13:40** | repeat the control: `capture --placebo-at "2026-10-06 14:00"` | a clean no-news window, and a live test of the new fast-reconnect code |
| **Wed 10-07, start by 09:40** | `capture --event "RBI MPC Oct 2026"` | **the event** (records 09:50-10:30) |
| **Wed 10-07, evening** | `analyze`, `report`, back up `data/diffusion_ticks.db` | the data is unrepeatable |
| **Thu 10-08, start by 09:40** | optional: `capture --placebo-at "2026-10-08 10:00"` | second control, a non-expiry weekday |
| **Thu 10-08** | rehearse with INTERVIEW_PREP.md; update README with the real numbers | |
| **Fri 10-09** | interview | |
| Mon 10-12 | CPI (16:00) gap snapshots - **after** the interview | say so honestly |

If `preflight` fails on Tuesday, open an issue with its full output (or fix the cause) that same day -- the fix has to land before Wednesday 09:40.

## Token (do this first)

This machine has no `UPSTOX_ACCESS_TOKEN` (not in the environment, no `.env` anywhere). Two routes:

1. **Reuse the old one (try this first).** On the system where you built the project, open its `.env` and copy the `UPSTOX_ACCESS_TOKEN=...` line. An Analytics token is valid for **1 year** from generation, so it is probably still good. Paste the line into a new file `.env` in this repo root (never commit it; `.gitignore` already excludes it).
2. **Generate a new one.** Log in at https://account.upstox.com/developer/apps -> **Analytics** tab -> Generate Token -> confirm -> copy. Upstox allows **only one active Analytics token per account**, so generating a new one replaces the old one: put the new value in the `.env` of **both** this project and the NSE-Options-Arbitrage-Detection-Engine project, or the latter breaks.

The token is read-only (no trading) and covers market data + WebSocket with no IP whitelisting. Never paste it in chat.

Verify with no market needed: `python src/diffusion/diffusion_cli.py preflight` prints `[ok ] UPSTOX_ACCESS_TOKEN present` and fetches the reference table over REST even after hours (it then ends `PREFLIGHT INCONCLUSIVE (market closed ...)`, exit code 3, which is expected -- only a run during NORMAL_OPEN can pass).

## One-time setup

```bash
pip install -r requirements.txt
python -m pytest tests/ -q    # expect 147 passed
python src/diffusion/diffusion_cli.py demo   # SYNTHETIC end-to-end run, proves the analysis stack works on this machine
```

## Preflight (during NORMAL_OPEN, 09:20-15:20 IST)

```bash
python src/diffusion/diffusion_cli.py preflight
```

Must end with `PREFLIGHT PASSED` and `[ok ] spot AND option ticks decoded`. A pass outside market hours proves nothing (it says so). If it fails:

| Output | Meaning | Next step |
|---|---|---|
| token missing / HTTP 401 | token not set or expired | see Token above |
| connected, ZERO feed messages | subscription not registering (the BUG-3 symptom) | try `--instruments 20`; capture the full output and investigate (see BUGS.md BUG-3) |
| feed messages but no spot ticks | decode path | send the output |
| spot but no option ticks | option decode / two-sided-quote filter | send the output |
| no futures ticks (warning) | futures control leg empty | not fatal; capture proceeds without it |
| connection error / 1009 | frame size / network | retry; `MAX_MESSAGE_BYTES` in tick_recorder.py |

## Machine prep

**Network is the biggest risk (learned live on 2026-10-06).** During the rehearsal this machine lost internet for ~10 minutes (DNS failures) and had a silent stall earlier. The recorder reconnects on its own, but the data in the hole is gone and any move inside it can only be reported as *censored*. Before Wed 09:40:

- Use **wired Ethernet** if you can; otherwise sit next to the router. Close VPNs and anything bandwidth-heavy.
- Have your **phone hotspot ready**. If the log shows `retrying` lines with DNS/`getaddrinfo` errors, switch networks immediately (the recorder reconnects by itself within ~5s of connectivity returning).
- Check Windows doesn't switch Wi-Fi networks or sleep the adapter (Device Manager -> network adapter -> Power Management -> untick "allow the computer to turn off this device").

- Plugged in, stable network, **sleep disabled**: `powercfg /change standby-timeout-ac 0` (a laptop that sleeps at 10:05 ends the capture silently).
- Close anything that restarts the machine (Windows Update, restart prompts).
- Sync the system clock (Settings -> Time -> Sync now). Lag is measured against local arrival time.

## Capture

```bash
# control window (Tue 10-06):
python src/diffusion/diffusion_cli.py capture --placebo-at "2026-10-06 10:00"
# the event (Wed 10-07):
python src/diffusion/diffusion_cli.py capture --event "RBI MPC Oct 2026"
```

- Start by 09:40; it sleeps until 09:50 and records through 10:30.
- A heartbeat line prints every 30s (`ticks=... by_kind=...`). `ticks` must be rising. A `STALL:` error line means no ticks decoded for 60s -- see the preflight table.
- Reconnects are automatic (a reconnect's stale snapshot is dropped, BUG-5); a malformed frame is counted and skipped; any unexpected error is logged with a traceback and retried until the window closes.
- NOT covered: the process being killed or the machine losing power. If that happens mid-window, restart `capture` immediately; the pre-window is not recoverable but the post-window still is.
- Logs: `data/logs/`. Ticks: `data/diffusion_ticks.db`. Exit code 2 = **zero ticks captured**; don't use that event.
- When the window closes it analyses automatically and writes `data/results/<event>.json` plus charts in `data/diffusion_charts/` (the `*_adjustment_progress.png` is the one to show).

NIFTY's weekly expiry falls on Tuesdays, so the 10-06 control is an expiry day (today's expiry contracts are excluded from the reference table by design). That is a caveat to state, which is why a Thursday control is also suggested.

## Reading the result

```bash
python src/diffusion/diffusion_cli.py analyze --event "RBI MPC Oct 2026"
python src/diffusion/diffusion_cli.py report      # pooled summary -> data/results/SUMMARY.md
```

- `PRIMARY=` is the pre-registered number (BUGS.md DEC-10). The `sensitivity:` values next to it are checks, not alternatives to choose from. If they disagree in sign, report the event as method-sensitive.
- `false-positive check ... k/n series fired with no news` is the noise floor: if the detector fires on quiet windows at a similar rate to the real-event detections, the real-event lag is not distinguishable from noise.
- `basket` shows whether the headline option leg is representative; `future` shows whether "options lag" is really "the spot index is slow".
- `feed delay` and the `exchange-time (experimental)` line show how much of a few-second lag could be feed jitter.
- Do **not** change z, window or method after looking (`--z` exists for sensitivity checks only). Any change is a new DEC entry applied to all events.

## After the event

1. Check `warnings`, tick counts (`n_pre` >= 10 needed for the returns detector, 50+ preferred), `max_post_gap_s`.
2. If capture failed or data is dirty, skip the event and note it in BUGS.md (ROADMAP §8: a clean small dataset beats a noisy large one).
3. Back up `data/diffusion_ticks.db` (gitignored, unrepeatable).
4. Update the README's "Real Numbers" with what was measured -- as descriptive, n=1.

## CPI release (after-hours): three snapshots (Mon 2026-10-12, after the interview)

| When (IST) | Command |
|---|---|
| Mon 10-12 ~15:25 | `... gap --event "CPI September 2026" --label pre_close` |
| Tue 10-13 ~09:17 | `... gap --event "CPI September 2026" --label post_open` |
| Tue 10-13 ~09:45 | `... gap --event "CPI September 2026" --label post_open_settled` |

Then `analyze --event "CPI September 2026"`. Windows Task Scheduler works if you won't be at the machine (action: `python`, arguments: the full command, start in: the repo root).

## What you will and will not have on 2026-10-07

- n = 1 real event: descriptive only. No significance claim and no "spot leads options" conclusion -- the false-positive check is what lets you say how much the number can be trusted.
- If the RBI decision is fully priced in (rate unchanged as expected), there may be little to detect; the detectors return "no first move" rather than inventing one. That is a result, not a failure.
- Index ticks arrive ~1/s while option/futures quotes update on every change; a few-second lag can be partly feed granularity. `exchange_ts_ms` and the feed-delay stats exist to check this.
- IV units are whatever Upstox sends and are unverified; IV is used only as a time series.
