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

## E4. Momentum portfolio (added 2026-11-17, before the test was run)

- **Why:** E1's side result: stocks the swing score rates "Watch" (mostly in uptrends) beat the average stock by
  +0.31% over 20 sessions (t 4.2). Momentum is the best documented stock-selection effect worldwide and in India (NSE
  runs a Nifty 200 Momentum 30 index). This test asks whether a simple version would have paid here, after costs.
- **Data:** the same 5-year cache (2021-11 → 2026-11); with a 12-month look-back the first rebalance is late 2022, so
  about 48 monthly holding periods. Today's Nifty 500 members only (see the bias note below).
- **Eligible stocks at each rebalance:** at least 260 sessions of history and a 60-session median traded value
  (close × volume) of at least ₹10 crore. Only data up to the rebalance close is used.
- **Rule A (classic 12-1):** at each month's last session, rank by the return from 252 sessions ago to 21 sessions ago;
  hold the top 20, equal weight, bought at the next session's open, until the next rebalance.
- **Rule B (NSE-style):** the same, but rank by the average of two z-scores across eligible stocks: 6-month return ÷
  1-year volatility and 12-month return ÷ 1-year volatility (the method of the Nifty 200 Momentum 30 index).
- **Costs:** 0.25% per side on the value traded at each rebalance (0.30% round-trip charges plus 0.10% per side for
  slippage); weights are reset to equal each month and only the changes are charged.
- **Benchmarks:** (1) the equal-weight average of all eligible stocks, rebalanced monthly with no costs (the same
  universe and bias, so this is the main test of stock selection); (2) the Nifty 500 index held throughout.
- **Pass (all needed, for A and B separately):** monthly excess return over benchmark (1) > 0 in both halves and t ≥ 2
  overall; annual return above the Nifty 500 index in both halves; maximum drawdown no more than 5 percentage points
  worse than the Nifty 500's.
- **Trials:** 2 (A and B). Nothing else is tried in this round.
- **Bias note:** stocks that fell out of the Nifty 500 are missing. This flatters momentum (some past winners that later
  crashed are gone), so a pass here is necessary but not enough.
- **Real-world check (descriptive, no survivorship bias):** the Aditya Birla Nifty 200 Momentum 30 ETF (MOMENTUM.NS, traded
  since 2022-08) against the Nifty 200 index (^CNX200) over the same months: annual return, drawdown, and each half.
  The ETF includes dividends and fund costs; the index is a price index (about 1.2% a year of dividends missing).
- **Tax (descriptive):** monthly rotation makes gains short-term (20% tax) while holding an index fund over a year is
  long-term (12.5%); an approximate after-tax figure is reported, not used for the verdict.

### E4 result (run 2026-11-17): FAILED

| | Per year | Average stock | Nifty 500 | vs average / month | t | Worst fall (vs Nifty 500) | Verdict |
|---|---|---|---|---|---|---|---|
| A: 12-1 month | 43.6% | 21.6% | 8.4% | +1.65% | 2.16 | −34.9% (−18.8%) | FAIL (drawdown) |
| B: NSE-style | 28.8% | 21.6% | 8.4% | +0.63% | 0.91 | −30.0% (−18.8%) | FAIL (t, drawdown) |
| Real ETF: Nifty 200 Momentum 30 vs Nifty 200, 2022-10 → 2026-11 | 11.6% | – | 11.5% (N200) | +0.06% | 0.16 | −30.8% (−18.1%) | check only |

- Rule A passed three of four checks (both halves vs the average stock and the index, t ≥ 2) but fell 16 points more
  than the index at worst. By the pre-registered rule it fails.
- The survivorship bias is large: the average surviving stock shows 21.6% a year against 8.4% for the index. Momentum
  benefits most from it, so the 43.6% is not a real-world number.
- The bias-free check agrees: the real momentum fund matched the Nifty 200 over 4 years (won 2022–24, lost 2024–26)
  with a far deeper fall.
- Not promoted to the website as a signal. Testing it properly needs point-in-time index membership (stocks that left
  the Nifty 500), which we do not have.

## P1. ₹1 lakh model portfolio: a forward test (written 2026-11-17, before the first run)

- **Why:** the owner asked for five holdings and a percentage for each, for ₹1 lakh. No tested rule has proven an edge
  (E1–E4), so the mix is built around the index and judged only on its live record from here on.
- **Holdings:** 40% NIFTYBEES (core), 10% GOLDBEES (cushion), 3 × 16.7% Nifty 500 stocks. A stock must have swing
  signal Buy, at least ₹10 crore traded a day, no results in the next 5 sessions, ATR at most 5% of the price, and fresh
  data. At most one stock per industry; the highest 12-1 month return wins (E4 rule A). Empty slots stay in cash.
- **Paper record (`src/portfolio.py`):** ₹1,00,000 from the first build, whole shares at the close, rebalanced on the
  first build of each month; a held stock stays while it ranks in the top 10 that pass. Charges are 0.25% per side for
  stocks and 0.15% for ETFs. No stop-losses inside the month.
- **Yardstick:** ₹1,00,000 in NIFTYBEES on the same day, held.
- **Verdict after 12 months:** it counts as working only if it beats the yardstick after charges with a worst fall no
  more than 5 points deeper. Before 12 months, the record is information, not proof.
- **Rules are frozen:** any change starts a new version (P2) with a fresh record; P1's record is kept.
