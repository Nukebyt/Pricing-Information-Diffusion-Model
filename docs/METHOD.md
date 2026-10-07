# Method

A short, plain-language description of what the project measures and how it keeps the results trustworthy. For decisions and fixes as they happened, see [`../BUGS.md`](../BUGS.md).

## The question

When news hits, where does the market react first: the index, its futures, or its options? And how long does it take to settle at the new level?

A fair answer needs an event whose **timing is known exactly**, so the project uses scheduled announcements: RBI policy decisions (10:00 IST), the Union Budget (11:00 IST) and CPI inflation releases (16:00 IST, after the market closes).

## What is recorded

For each event, a live feed from Upstox is recorded from 10 minutes before to 30 minutes after the announcement:

- the **NIFTY 50** and **BANK NIFTY** index prices
- the nearest **futures** contracts for both
- every **option** contract on both indices for the nearest expiries (about 740 contracts)

Each update is stored with the time it arrived and the exchange's own timestamp. Options and futures are priced at the **middle of the best bid and ask**, and only when both sides are quoted, so a missing side is never turned into a made-up price.

CPI is released after the market closes, so there is nothing to stream. Instead three snapshots are taken (before the close, right after the next open, and a little later) to show how much of the move happened overnight.

## What is measured

| Measure | In plain words |
|---|---|
| **First reaction** | The first moment a price moves outside its own normal range. The "normal range" is built only from data recorded *before* the announcement, so the move being measured can never leak into its own baseline. |
| **Lead-lag** | The difference between two series' first-reaction times (index vs. futures, index vs. option, NIFTY vs. BANK NIFTY). Positive means the first-named series moved first. |
| **Settle speed** | How long a series takes to cover 90% of its move. The "settled level" is the median of the final minute, so one stray tick can't distort it. |
| **Overnight gap** (after-hours events) | How much of the total move happened while the market was closed versus after it reopened. |

**Two ways of spotting a move.** One compares the price level to its pre-announcement average. The other compares the last five ticks' change to the usual size of five-tick changes, which is not thrown off by slow drifts. The second is the **primary** method; the first is always reported alongside as a cross-check. Each can require 1 or 3 consecutive unusual ticks, and 3 is primary, to filter single stray quotes.

## Safeguards

- **Analysis plan fixed in advance.** The primary method, threshold (3 standard deviations), option contract selection and the way results are pooled were written down on 5 October, before the first real event, and are applied unchanged to every event ([`BUGS.md` DEC-10](../BUGS.md)). Alternatives are reported as cross-checks, never swapped in after seeing results.
- **Control windows.** The same detector runs on stretches where nothing was scheduled, to learn how often "a reaction" appears by chance. This is the project's measure of everyday noise.
- **Representative contracts.** The option compared with the index is the nearest-expiry, closest-to-the-money contract; a basket of five strikes around it is reported too, so no single thin contract carries a result.
- **A futures check.** Futures often react before a computed index, so index-vs-futures is reported to separate "the option is slow" from "the index is slow".
- **Recording gaps handled honestly.** If the connection drops, the period without data is marked. A move that first appears right after a gap is reported as *happened during the gap*, not stamped with the reconnection time. The recorder detects a silent dead connection within 10 seconds and reconnects (BUGS.md BUG-7).
- **Exchange timestamps.** Alongside arrival times, the exchange's own timestamps are stored so network delay can be examined rather than assumed.
- **Tested against known answers.** Most analysis code is tested on artificial prices with planted delays, including in the reverse direction and with no move at all; 147 tests in total.

## What the first event showed about the method

The 7 October RBI announcement produced a gradual rise over half an hour rather than a sharp jump at 10:00. The descriptive numbers in the result summary show this directly: NIFTY's largest 5-second move after the announcement (4.2 points) stayed within its pre-announcement range (6.8 points). With no sharp jump, how quickly each series first "crosses a threshold" depends as much on that series' own noise level as on how fast news reaches it. For example, on BANK NIFTY the index's ordinary 5-second moves are large (up to about 23 points beforehand), so a roughly 10-point step three seconds after 10:00 stayed inside its normal range, while the options and futures, measured against their own baselines, crossed their thresholds within a few seconds. For that reason the project reports this event's response as it unfolded rather than as a single lead-lag figure.

This points to the next improvement: measuring lead-lag across the whole window with cross-correlation of second-by-second returns, which does not depend on a single threshold crossing. Because any change to the method has to be applied to every event, it will be introduced as a new, dated decision rather than applied retroactively.

## Limits

- **One event is one data point.** Patterns need many events; the calendar offers about one a month.
- **One broker, one feed.** Results describe Upstox's view of NSE.
- **Arrival-time precision.** Timing is accurate to roughly a second, which suits minute-scale moves but not millisecond claims.
- **Read-only.** The project never places orders.
