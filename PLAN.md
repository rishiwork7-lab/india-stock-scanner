# Development plan: Daily Stock Analysis for India (NSE/BSE)

The original project is [ZhuLinsen/daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis), commit `70f2505`,
analysed on 2026-11-14. This project replicates its core daily-analysis product for Indian equities.

## 1. What the original does (analysis summary)

**Pipeline** (`main.py` → `src/core/pipeline.py`), per stock, run in a thread pool:

1. **Resolve and fetch.** Normalise the code, then resume: skip the download if today's bars are already in SQLite. Otherwise
   fetch daily OHLCV through a multi-source manager with priority, fallback and a circuit breaker.
2. **Realtime quote.** Price, volume ratio, turnover rate, PE/PB, market cap and 60-day change.
3. **Chip distribution** (A-share specific): profit ratio, average cost and concentration.
4. **Fundamentals.** Financial report fields, dividends and capital flow, with failures downgraded gracefully.
5. **Trend analysis** (`src/stock_analyzer.py`), deterministic:
   - MA5/10/20/60 alignment, giving seven trend states;
   - bias from MA5;
   - volume state (ratio to the 5-day average);
   - MA support and resistance;
   - MACD (12/26/9) states;
   - RSI 6/12/24 (Wilder).

   These produce a **100-point score**: trend 30 + bias 20 + volume 15 + support 10 + MACD 15 + RSI 10. The score maps to the
   canonical scale (80+ strong buy, 60–79 buy, 40–59 watch, 20–39 reduce, 0–19 sell), which gives the buy signal.
6. **News intelligence.** Five searches per stock: latest news, analyst views, risk check, earnings and industry. They run
   across providers with fallback, and the results are stored in SQLite.
7. **Prompt.** A Markdown "Decision Dashboard request" is built from the stock basics, market phase, technical tables, realtime
   data, financials, capital flow, chip data, the trend pre-analysis with its reasons, and the news. A system prompt sets out
   the trading baseline (no chasing above 5% bias, bullish alignment, entry on pullbacks to MA5/MA10, risk screening), the
   canonical score scale and a strict **JSON schema** (`dashboard`: `core_conclusion`, `data_perspective`, `intelligence`,
   `battle_plan`, `phase_decision`, `signal_attribution`, plus summary fields).
8. **LLM call** with model fallback, JSON extraction and repair, an integrity check (missing fields are retried or filled
   with placeholders), and the score/action guardrails. If the LLM fails, the trend analysis is used as a fallback.
9. **Persist** the analysis history (with the sniper levels for the backtest), then **report**: a decision-dashboard Markdown
   (summary list sorted by score, then per stock: intelligence, core conclusion, position advice, data perspective, battle
   plan, checklist).
10. **Notify** on every configured channel. Reports are saved to `reports/`.

**Market review** (`src/market_analyzer.py`, region profiles):
- **Data:** index quotes, breadth (up/down/limit counts, turnover) and sector rankings.
- **News:** market news searches.
- **Report:** an LLM Markdown recap that follows a fixed template (summary, index commentary, breadth, sectors, news catalysts,
  strategy), with a template fallback when no LLM is available. It is merged with the stock report when both run.

**Other:**
- **Scheduler:** daily at a set time; skips non-trading days unless `--force-run`.
- **Backtest:** each stored analysis is checked against the next N daily bars, recording direction correctness,
  stop-loss/take-profit hits and the simulated return.
- **API and Web UI:** FastAPI plus a React workspace.
- **Bots, agent chat with 15 strategies, portfolio, image import and the Electron desktop app.**

## 2. Scope of the India replica

| Original feature | India replica |
|---|---|
| CLI: `--stocks --dry-run --debug --no-notify --single-notify --workers --schedule --no-run-immediately --market-review --no-market-review --force-run --serve --serve-only --webui --port --host --backtest --backtest-code --backtest-days --backtest-force` | **Same flags** |
| Multi-source data manager with fallback and circuit breaker | **Same design.** Sources: **NSE India public API**, **yfinance (.NS/.BO)**, **NSE bhavcopy archive** |
| Realtime quote: volume ratio, turnover rate, PE, PB, market cap, 60-day change | **Same fields**, from NSE quote and yfinance; amounts in ₹ crore |
| Chip distribution (A-share only data) | **Delivery analysis:** delivery % today, 5/20-day averages and trend; the closest Indian measure of genuine holding versus intraday speculation |
| Main capital flow | **FII/DII cash-market flows** (NSE) plus the stock's delivery volume |
| Limit up/down (涨跌停) | **NSE circuit bands** (price band %, upper/lower circuit hit) |
| A-share trading calendar / phases | **NSE calendar** (holiday API + weekends) and **IST phases** (pre-open 09:00–09:15, open 09:15–15:30, post-close) |
| Trend analyzer and scoring | **Exact port** (same thresholds, weights and states), with English labels |
| News: SerpAPI, Tavily, Bocha, Brave… | **Google News RSS (India edition, free, no key) by default**, plus optional Tavily, SerpAPI and Brave; NSE corporate announcements as the `announcements` dimension |
| LLM: Gemini, OpenAI-compatible, DeepSeek, Claude, Ollama via LiteLLM | **Claude (official `anthropic` SDK, default `claude-opus-5-5`), Gemini (REST), any OpenAI-compatible endpoint** (OpenAI, DeepSeek, OpenRouter, Ollama, Groq), with fallback across providers |
| Decision Dashboard JSON schema and prompts | **Same schema.** India baseline: delivery replaces chips; risk screening covers SEBI orders, promoter pledging, ASM/GSM surveillance, auditor exits, promoter selling and F&O ban |
| Integrity check, guardrails, trend fallback | **Same** |
| Market review (indices, breadth, sectors, news, LLM recap, template fallback) | **India profile:** NIFTY 50, SENSEX, NIFTY BANK, MIDCAP, SMALLCAP, INDIA VIX; NIFTY 500 advance/decline, circuits, sector indices, FII/DII |
| SQLite storage: daily bars, news intel, analysis history, backtest | **Same tables** (stdlib `sqlite3`) |
| Notifications: Telegram, Email, Discord, Slack, custom webhook, ntfy, Pushover, WeChat Work, Feishu… | **Removed at the owner's request (2026-11-14):** results are read on the website (GitHub Pages). Email is the only optional channel |
| Backtest engine | **Exact port** of the evaluation logic and summary |
| GitHub Actions daily workflow, Docker | **Same.** The cron runs after the NSE close: 18:30 IST (13:00 UTC), Mon–Fri |
| FastAPI service and React Web UI | **FastAPI service** (run analysis, history, report, market review, backtest) and a **single-page HTML UI**. The full React workspace is not rebuilt |
| Agent strategy chat (15 strategies), bots, portfolio, image/CSV import, Electron desktop, ETF rotation | **Out of scope for this build:** each is a separate product on top of the core. The modules are laid out so they can be added later |

## 3. Architecture (new folder `daily_stock_analysis_india/`)

```
main.py                    CLI entry (same flags)
server.py                  FastAPI app (uvicorn server:app)
src/config.py              .env-driven settings
src/india/symbols.py       NSE/BSE code parsing: RELIANCE, NSE:TCS, 500325, BSE:500325, .NS/.BO, indices
src/india/market.py        IST phases, NSE calendar, circuit bands, ₹ crore/lakh formatting
data_provider/             base manager (priority, fallback, circuit breaker); nse, yfinance and bhavcopy fetchers
src/storage.py             SQLite repositories
src/stock_analyzer.py      trend analyzer (exact port)
src/delivery.py            delivery-based "chip" analysis
src/search_service.py      Google News RSS / Tavily / SerpAPI / Brave + NSE announcements
src/llm/                   provider clients (anthropic SDK, gemini, openai-compatible) + router with fallback
src/analyzer.py            prompts, Decision Dashboard schema, parsing/repair, integrity, guardrails, fallback
src/pipeline.py            per-stock flow and run loop
src/market_review.py       India market review
src/report.py              dashboard, simple and single-stock Markdown reports
src/notification.py        channels
src/backtest.py            evaluation engine and runner
src/scheduler.py           daily schedule in IST, with the trading-day check
web/index.html             single-page UI
tests/                     pytest, offline (fixtures and mocks); `-m network` for live smoke tests
.github/workflows/daily-analysis.yml, Dockerfile, .env.example, README.md
```

## 4. Build steps (each one tested before the next)

1. Skeleton, config, logging, symbols and Indian market utilities, with tests.
2. Data providers and the fallback manager, with tests (mocked HTTP) and a live smoke test.
3. Storage, with tests.
4. Trend analyzer port and delivery analysis, with tests that check the scores against hand-computed cases.
5. Search service, with tests (RSS parsing fixtures).
6. LLM providers, prompts and parsing, with tests (fake provider responses, JSON repair, integrity, guardrails, fallback).
7. Pipeline, reports and notifications, with an end-to-end test using fake providers.
8. Market review, with tests.
9. Backtest, with tests that reuse the original engine's logic cases.
10. Scheduler, CLI, FastAPI and the web UI, with tests (FastAPI TestClient).
11. Live run on real NSE/BSE data (technical fallback, because no LLM key is configured), plus docs, the workflow and the
    Dockerfile.

## 5. Honest limits

- **Data reliability:** NSE's public website API needs browser-like headers and cookies, and may rate-limit or block cloud
  IPs (GitHub Actions). Every NSE call has a yfinance fallback, and missing data is labelled "unavailable", never invented.
- **AI analysis:** without an LLM key the report is technical-only, as in the original's fallback.
- **Not investment advice;** the same disclaimer as the original.
