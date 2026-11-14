# Edge research: pre-registered tests

Written 2026-11-14, **before any of these tests was run**. Rules, data, costs and pass rules below are fixed; nothing is
tuned after seeing results. Every variant tried is listed and counted. A failed test is reported as failed.

Yardstick for every test: after Indian costs, does it beat the simple alternative (the average stock on the same dates, or
holding the Nifty 50)? "Edge" means yes, in **both halves** of the period, with a t-statistic of at least 2.

Common data: Yahoo Finance adjusted daily bars (the 5-year cache from `tools/swing_check.py`, 2021-11 → 2026-11), today's
Nifty 500 members (survivorship-biased: stocks that fell out of the index are missing, so absolute returns look better
than reality; comparisons against the same-date average of the same universe carry the same bias and are the main measure).
Costs: 0.30% per round trip for stocks (STT 0.1% each side, exchange, SEBI, stamp, GST, DP); entry at the next session's
open after the signal.

## E1. Short-term score (the original project's rules, `src/stock_analyzer.py`)

- **Question:** do stocks with a high short-term score beat the average stock over the next days?
- **Signals:** every Friday close, every stock with ≥ 150 sessions of history; the score uses only data up to that close.
- **Outcomes:** return from the next session's open to the close 5 sessions later (primary) and 10 sessions later
  (secondary), minus 0.30% costs, minus the same-date average of all stocks (same costs).
- **Groups:** score bands 80+ (Strong Buy), 60–79 (Buy), 40–59 (Watch), below 40 (Reduce/Sell); and the **top 10 scores**
  each Friday ("top picks", the stricter cut a scanner would show).
- **Statistic:** each date's group average first, then the mean and t-statistic over dates (so busy weeks don't dominate).
- **Pass:** for Buy (≥ 60) and for top-10 picks separately: 5-session excess return > 0 in both halves (split at the
  middle date) and t ≥ 2 overall.
- **Secondary (descriptive only):** the same for Nifty 50 members alone.
- **Consequence for the website:** if a group passes, the scanner's "Buy only" filter for short-term uses that group; if
  none passes, the page says the short-term score has no proven edge and the filter stays as it is.

## E2. Results inside the hold (event checker, `src/events.py`)

- **Question:** do swing trades that run into company results do worse, so that the event checker should block them?
- **Trades:** the 80,350 swing plans from `tools/swing_metrics.py` (same rules, levels and exits: method A, all out at
  target 2, the stop or 40 sessions).
- **Results dates:** NSE board-meeting history per stock (purpose contains "result"), cached.
- **Rule tested (one rule):** skip a new swing entry when results are due within the **first 10 sessions** of the trade.
- **Measures:** trades the rule would skip vs trades it keeps: average return after costs, stop-hit rate, the worst 5% of
  returns (5th percentile).
- **Pass (as a risk rule):** the skipped trades have a lower average return (t ≥ 2, Welch test) **or** a 5th percentile
  at least 1 percentage point worse, with an average return that is not significantly higher (t < 2).
- **Known limitation:** results dates are announced only days ahead (SEBI requires at least 2 working days' notice) and
  Yahoo estimates them earlier; the test assumes the date was known at entry.

## E3. ETF sector rotation

- **Question:** does holding the strongest sectors beat simply holding the Nifty 50?
- **Universe:** 12 NSE sector indices with Yahoo history since 2011 and an ETF that tracks them or something close:
  Bank, PSU Bank, IT, Pharma, Auto, FMCG, Metal, Realty, Energy, PSE, Infra, Financial Services. Price indices on
  both sides (no dividends), so the comparison is like for like.
- **Rule A:** at each month's last session, rank sectors by the average of their 3-month and 6-month returns (the
  ranking the website shows); hold the top 3, equal weight, for the next month.
- **Rule B:** the same, but hold cash (6% a year) instead whenever the Nifty 50 closes below its 200-day average on the
  rebalance day.
- **Costs:** 0.15% per side on the traded value at each rebalance (ETF spread, brokerage, STT, charges).
- **Benchmark:** Nifty 50 index, held throughout.
- **Pass:** annual return above the Nifty 50 in both halves (2011–2018 and 2019–2026), t ≥ 2 on the monthly excess
  returns, and maximum drawdown no more than 5 percentage points worse than the Nifty 50's.
- **Trials:** 2 (A and B). Nothing else is tried in this round.
- **Data checks:** daily moves above 25% in an index series are treated as bad prints and reported.

Results are written to `evidence/edge_*.json` and shown on the website's Evidence tab.
