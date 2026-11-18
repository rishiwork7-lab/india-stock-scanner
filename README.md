# Daily Stock Analysis for India (NSE/BSE)

An India-market replica of [daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis). It analyses your
watchlist every trading day, writes an AI **Decision Dashboard** and an **India market review**, and publishes them on a
**website** (GitHub Pages) together with a **Nifty 50 + ETF scanner** and an **event checker**. Messaging apps (Telegram,
WhatsApp, Discord, Slack...) are deliberately not supported; email is the only optional channel. The design and the
analysis rules are in `PLAN.md`.

## The website

| Tab | What it shows |
|---|---|
| **Scanner** | Pick a list: Nifty 50, Next 50, 100, 200, 500, LargeMidcap 250, Midcap 50/100/150, Smallcap 100/250, 15 sectors (Bank, IT, Pharma, Auto, FMCG, Metal, Realty, Energy…) or 10 themes (PSE, Defence, Consumption, Digital…). All Nifty 500 stocks are scanned every day; sector lists show their Nifty 500 members. Each stock gets the swing plan (score, Buy/Watch/Avoid, setup, entry/stop/targets, size) and the short-term score; sortable; filters for Buy only and "results inside the hold"; click a stock for the chart, both plans and its events |
| **₹1 lakh plan** (`#portfolio`) | Five holdings for ₹1,00,000 with the share of money, rupees and whole shares for each: 40% Nifty 50 ETF, 10% gold ETF, three Nifty 500 stocks that pass every check (rules: `src/portfolio.py`, research P1). Below it, a paper record that follows the plan from its first build (monthly rebalance, charges paid) against simply holding the Nifty 50 ETF, and **plans racing each other**: four challengers (no gold, sector ETFs, calmer stocks, six stocks) with their own records; one takes over only under the fixed promotion rule (research L1) |
| **ETFs** | 24 liquid NSE ETFs in five groups (broad market, sector, factor, global, commodity): returns, trend, swing signal, ₹ traded a day, premium/discount to NAV; sector strength ranking (descriptive, not a tested rotation rule) |
| **Events** | Coming results (confirmed by NSE, or Yahoo's estimate), dividends/splits, market holidays; how much each stock has moved on its last 8 results compared with a normal 2-day move |
| **Market** | India market review (indices, breadth, FII/DII, sectors, news) |
| **Watchlist (AI)** | Full Decision Dashboard for `STOCK_LIST`, swing and short-term (AI when a key is set, otherwise technical) |
| **Evidence** | Historical checks of the swing score and the accuracy matrix; the live record of past calls | Top: the **live scorecard** from the signal diary (how each kind of signal did 5/20/60 sessions later vs the average stock, no survivorship bias)

**Your own AI key, on the page.** Click **🔑 Add your AI key** at the top. Pick OpenAI, Anthropic (Claude), Google Gemini or
any OpenAI-compatible provider (OpenRouter, DeepSeek, Groq...), paste the key and press **Load models** to test it. Then
use **Ask AI** under any stock or ETF. The AI gets that instrument's data from the page (plans, events, market mood, test
results) plus your question. The key stays in your browser (for the tab only, unless you tick "Remember on this device")
and goes straight to the provider; it is never sent to GitHub. The chart library is pinned with an integrity hash, so a
changed copy on the CDN cannot run on the page. The automatic daily AI analysis is separate and uses a GitHub secret.

Build it: `python main.py --build-site` writes `site/` (one HTML page + JSON files, about 3.5 MB, no server needed).
Locally, `python main.py --serve-only` shows it at http://127.0.0.1:8790 with a "Refresh data" button.

**Publish on GitHub Pages** (free; the address is `https://<user>.github.io/<repo>/`):
1. Push this folder to a GitHub repository.
2. Settings → Pages → Source: **GitHub Actions**.
3. Optional, Settings → Secrets and variables → Actions: variable `STOCK_LIST`; secret `OPENAI_API_KEY` plus variable
   `OPENAI_MODEL` for AI analysis.
4. Actions → "Daily scanner website" → **Run workflow** for the first build. After that it runs every weekday at 18:30 IST.

Notes:
- **The website is public:** anyone with the address can open it (GitHub Pages sites are public on free plans, even from
  a private repository). It contains no keys or personal data.
- **NSE may block GitHub's servers.** The build then uses Yahoo alone: results dates become Yahoo estimates and ETF NAV
  premiums are missing. The page lists any such "data notes" at the bottom.

## Workflow (same as the original)

```
watchlist → daily bars (resume-aware, multi-source fallback) → quote (volume ratio, turnover, valuation)
→ delivery analysis (India's replacement for chip distribution) → circuit band + surveillance → fundamentals
→ trend score (exact port: MA alignment, bias, volume, support, MACD, RSI = 100 points)
→ news intelligence (5 dimensions + NSE announcements) → Decision Dashboard prompt → LLM (or technical fallback)
→ guardrails → history (SQLite) → report → notifications        +  market review  +  backtest of past calls
```

## Quick start

```bash
cd daily_stock_analysis_india
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # set STOCK_LIST and, optionally, an AI key
.venv/bin/python main.py --stocks RELIANCE,TCS,HDFCBANK,500325 --no-notify --force-run
```

**Reports:**
- `reports/report_YYYYMMDD.md`: the Decision Dashboard.
- `reports/market_review_YYYYMMDD.md`: the market review.

| Command | What it does |
|---|---|
| `python main.py` | Analyse `STOCK_LIST`, run the market review, notify (skips NSE holidays and weekends) |
| `--stocks RELIANCE,NSE:TCS,500325,NIFTYBEES` | Override the watchlist. BSE scrip codes and ETFs are supported |
| `--dry-run` | Fetch and store data only |
| `--market-review` / `--no-market-review` | Run only the review / skip it |
| `--schedule [--no-run-immediately]` | Run daily at `SCHEDULE_TIME` (IST, default 18:30) |
| `--serve` / `--serve-only` | Web UI and API at http://127.0.0.1:8790 (`--port` to change) |
| `--backtest [--backtest-code TCS --backtest-days 10 --backtest-force]` | Judge past recommendations |
| `--single-notify`, `--no-notify`, `--check-notify`, `--workers N`, `--force-run`, `--force-refresh`, `--debug` | As in the original |

## Two trading styles

The UI has a switch between the two styles. On the command line use `--style swing`; to make one the default, set
`TRADING_STYLE` in `.env`.

| | Short-term (the original project's style) | Swing (2–8 weeks) |
|---|---|---|
| Trend test | MA5 > MA10 > MA20 | Price > MA50 > MA150 > MA200, MA200 rising (trend template) |
| Strength | — | 3- and 6-month return vs NIFTY; position in the 52-week range |
| Entry | pullback to MA5/MA10 | pullback to a rising MA20/MA50, or a base breakout on 1.5× volume; >15% above MA50 = extended, no buy |
| Volume | 1-day volume ratio | 50-session up/down volume, NSE delivery health |
| Stop / targets | below MA20 | swing low or 2× ATR, never more than 8%; targets at 2R and 3R |
| Size | — | shares for `RISK_PER_TRADE_PCT` of `CAPITAL` (cash delivery, never more than the capital buys) |
| Backtest window | 10 sessions | 20 sessions |

**Historical check of the swing score** (`tools/swing_check.py`, rules fixed before the run): 81,366 signals across 186
Fridays (Dec 2022 – Sep 2026), today's Nifty 500 members, 4-week hold after 0.30% charges.

| Swing score said | Return vs the average Nifty 500 stock on the same date |
|---|---|
| **Buy** | **−0.02% (t −0.67): no edge**. The pre-set pass rule failed |
| Watch | +0.31% (t 4.2), noticed after the fact, so not proven |
| **Avoid** | **−0.36% (t −5.8, negative in both halves)** |

**Accuracy matrix** (`tools/swing_metrics.py`): 80,350 plans traded with their own levels for up to 8 weeks, after charges.

| Signal | Target 1 (2R) hit before the stop | Target 2 (3R) hit | Stop first | Win rate | Average per trade | vs Nifty 500, same days |
|---|---|---|---|---|---|---|
| Buy | 31.8% | 18.9% | 52.2% | 40.5% | +1.33% | +0.74% |
| Watch | 32.1% | 19.6% | 53.0% | 39.7% | +1.44% | +0.81% |
| Avoid | 28.4% | 15.7% | 56.6% | 35.7% | +0.31% | +0.01% |

- **By year (Buy):** 2023 +4.4% a trade; 2024 −0.0%; 2025 −0.1%; 2026 −0.2%. Almost all the gain came from the 2023 bull
  market.
- **A random walk** reaches a 2R target before a 1R stop about 33% of the time, so the Buy plans perform like a coin flip.

Use swing Buy as a disciplined plan (setup, stop, size), not as a prediction.

## The learning loop (research L1)

- **Signal diary** (`src/journal.py`): every daily build saves one row per Nifty 500 stock and ETF (signals, scores, setup, levels, momentum, results timing, market mood) to `records/signals/<date>.csv.gz`. The GitHub run commits it, so it is kept for good, and stocks that later leave the Nifty 500 stay in.
- **Live scorecard** (`src/scorecard.py`): what each kind of signal did afterwards, after charges, against the average stock that day; t-values allow for overlapping holds.
- **Challenger plans** (`src/portfolio.py` `SPECS`): each changes one thing in the ₹1 lakh plan; records in `records/plans/`. The plan shown switches only under the fixed promotion rule (12 months, +2 points a year, worst fall no more than 3 points deeper, ahead in both halves).

## Edge research (pre-registered: `research/EDGE_RESEARCH.md`)

| Test | Question | Result |
|---|---|---|
| E1 `tools/edge_short_term.py` | Does a high short-term score beat the average stock over 5–10 days? (94,599 signals, Nifty 500, 2022–2026) | **Failed.** No score band beats the average; among Nifty 50 stocks the weekly top 10 did slightly worse (−0.15%, t −2.3) |
| E2 `tools/edge_results.py` | Do swing trades with results in their first 10 sessions do worse? (80,350 trades) | **Failed as a filter.** Same average (+1.15% vs +1.01%) and worst 5% (−8.5%); stop hit slightly more often (57% vs 56%) |
| E3 `tools/edge_sector_rotation.py` | Does holding the 3 strongest of 12 sector indices (monthly) beat the Nifty 50? (2011–2026) | **Failed.** 10.6%/yr vs 11.4%; lost 2011–19, won 2019–26. With a 200-day cash filter: 10.9%/yr, worst fall −23% vs −29% |
| E4 `tools/edge_momentum.py` | Does holding the 20 strongest Nifty 500 stocks (monthly) beat the average stock and the Nifty 500? (2022–2026) | **Failed.** 12-1 rule: 43.6%/yr vs 21.6% average stock (t 2.2) but worst fall −35% vs −19%, on survivorship-biased data. The real Nifty 200 Momentum 30 ETF: 11.6%/yr vs Nifty 200 11.5%, worst fall −31% vs −18% |

None of the rules tested so far has a proven edge over simply holding the index after costs. The Avoid label is the part with evidence: it
picks out stocks that tend to lag. The rules were not re-tuned after the check, to avoid fitting the past.

## What changed for India

| Original (A-shares) | India replica |
|---|---|
| Chip distribution (profit ratio, average cost, concentration) | **NSE delivery %**: today, the 5/20-day averages, trend and delivery-weighted price, giving a Healthy / Average / Caution rating |
| Main capital flow | **Delivery-based flow bias** (above-average delivery on an up day = accumulation, on a down day = distribution) plus **FII/DII** cash flows |
| Limit up/down | **NSE circuit bands** (2/5/10/20%, none for F&O stocks); upper/lower circuit counts in the market review |
| A-share trading calendar | **NSE holiday calendar** and **IST phases** (pre-open 09:00, open 09:15–15:30) |
| Risk screening (share reductions, penalties…) | **SEBI orders, promoter pledge/selling, ASM/GSM surveillance, auditor exits, rating downgrades, F&O ban, results shocks** |
| Market review: SSE/SZSE/ChiNext, sectors, limits | **NIFTY 50, SENSEX, BANK NIFTY, NEXT 50, MIDCAP 100, SMALLCAP 250, NIFTY 500, INDIA VIX**; NSE breadth, circuits, turnover, sector indices, FII/DII, India market light |
| ¥ / 亿 | **₹ / crore / lakh** |

## Data sources (free, no key needed)

| Need | Source | Fallback |
|---|---|---|
| Daily bars | Yahoo Finance (`.NS`, `.BO`) | NSE bhavcopy (NSE), BSE bhavcopy (BSE codes) |
| Latest bar | NSE bhavcopy overlaid when Yahoo lags | |
| Delivery % | NSE full bhavcopy (`sec_bhavdata_full`), cached on disk | |
| Quote and fundamentals | Yahoo Finance (P/E, P/B, market cap, ROE, promoter holding, analyst targets) | |
| Circuit band, surveillance | NSE `sec_list.csv` | |
| Indices, breadth, FII/DII, holidays, announcements | NSE public API | Yahoo Finance for index levels |
| BSE code → ticker | BSE bhavcopy | |
| News | Google News India RSS | Optional Tavily, SerpAPI or Brave keys are tried first |

NSE's per-stock quote API refuses scripts (HTTP 403), so quotes come from Yahoo. Every NSE call has a fallback, and missing
data is reported as unavailable, never invented. NSE sometimes serves the previous session's file for a holiday date; such
files are detected and ignored.

## AI models

`LLM_PROVIDER_ORDER` (default `anthropic,gemini,openai`) is tried in order, with fallback:

| Provider | Setup |
|---|---|
| **Claude** | The official `anthropic` SDK. Default model `claude-opus-5-5` at `ANTHROPIC_EFFORT=medium`. The fixed system prompt is prompt-cached, and server-side refusal fallback is enabled |
| **Gemini** | `GEMINI_API_KEY` |
| **Any OpenAI-compatible endpoint** | `OPENAI_BASE_URL` + `OPENAI_MODEL`: OpenAI, DeepSeek, OpenRouter, Groq, or local Ollama (no key) |

With no model configured, or if every model fails, the report is a **technical-only** dashboard built from the trend score,
as in the original's fallback.

## API

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/v1/analysis` | Body `{"stocks": [...], "notify": false}`; returns a task id |
| `POST` | `/api/v1/market-review` | Returns a task id |
| `GET` | `/api/v1/tasks/{id}` | Task status and result |
| `GET` | `/api/v1/history` | Past analyses |
| `GET` | `/api/v1/history/{id}` | One analysis |
| `GET` | `/api/v1/backtest?code=&days=` | Backtest summary |
| `GET` | `/api/v1/health` | Health check |

Set `API_AUTH_TOKEN` to require a bearer token.

## Deploy

| Method | How |
|---|---|
| **GitHub Pages** | `.github/workflows/pages.yml` builds the website at 18:30 IST, Mon–Fri, and publishes it (see "The website") |
| **Docker** | `docker build -t dsa-india . && docker run --env-file .env -p 8790:8790 dsa-india` |

## Tests

```bash
.venv/bin/python -m pytest              # offline suite (network calls mocked)
.venv/bin/python -m pytest -m network   # live checks against NSE, BSE, Yahoo and Google News
```

The trend analyser and the backtest engine were checked against the original's code on 300 random price series and 400
random evaluations; there were 0 differences.

## Not included

The original's React web workspace, Electron desktop app, agent strategy chat (15 strategies), chat bots, portfolio
tracking, image/CSV import and ETF rotation are separate products on top of this core and are not rebuilt here.

## Disclaimer

For information and education only; not investment advice. Licensed portions of the original are acknowledged in
`THIRD_PARTY_NOTICES.md`.
