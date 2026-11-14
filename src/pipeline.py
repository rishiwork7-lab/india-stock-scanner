"""The analysis pipeline (the original's src/core/pipeline.py), per stock:

  1  resolve name; resume check (skip the download when the latest completed session is already stored); fetch + save bars
  2  quote: volume ratio, turnover rate, valuation, market cap
  3  delivery analysis (India's replacement for chip distribution); circuit band and surveillance remarks
  4  fundamentals (financials, holdings, analyst targets)
  5  trend analysis (deterministic score)
  6  multi-dimension news intelligence (+ NSE announcements), saved to the database
  7  context assembly (market phase, market context, flows) -> LLM Decision Dashboard (or technical fallback)
  8  save the analysis history (with sniper levels for the backtest)
Run: thread pool over the watchlist, then one aggregate report (or one per stock with --single-notify), saved and sent.
"""
from __future__ import annotations

import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from data_provider.base import DataUnavailable, SourceFailure

from .analyzer import AnalysisResult, StockAnalyzer
from .delivery import analyze_delivery
from .india.market import circuit_status, market_phase, now_ist
from .india.symbols import Target
from .report import generate_dashboard_report, generate_single_stock_report
from .stock_analyzer import StockTrendAnalyzer
from .swing_analyzer import analyze_swing, position_size

SWING_MIN_BARS, SWING_HISTORY_DAYS = 260, 400

logger = logging.getLogger(__name__)


class StockAnalysisPipeline:
    def __init__(self, config, fetcher, db, search, analyzer: StockAnalyzer, notifier, calendar, progress=None):
        self.config, self.fetcher, self.db, self.search = config, fetcher, db, search
        self.analyzer, self.notifier, self.calendar = analyzer, notifier, calendar
        self.trend = StockTrendAnalyzer(config.bias_threshold)
        self._market_ctx: Optional[Dict] = None
        self.progress = progress or (lambda pct, msg: None)

    # ---- step 1 -----------------------------------------------------------------------------------------------------
    def fetch_and_save_stock_data(self, t: Target, force_refresh: bool = False, now: Optional[datetime] = None, style: str = "short"):
        try:
            target_date = self.calendar.latest_completed_session(now)
            days = SWING_HISTORY_DAYS if style == "swing" else self.config.history_days
            enough = style != "swing" or self.db.count_daily(t.code) >= SWING_MIN_BARS
            if not force_refresh and enough and self.db.has_today_data(t.code, target_date):
                logger.info("%s: %s bars already stored, skipping download (resume)", t.code, target_date)
                return True, None, "cache"
            df, src = self.fetcher.get_daily_data(t, days)
            n = self.db.save_daily_data(df, t.code, src)
            logger.info("%s: saved %d new bars from %s", t.code, n, src)
            return True, None, src
        except (DataUnavailable, SourceFailure) as e:
            return False, str(e), None

    # ---- market context (once per run) -------------------------------------------------------------------------------
    MARKET_CTX_TTL_S = 900.0

    def market_context(self) -> Dict:
        """Index levels and FII/DII flows for the prompt; fetched once per run and refreshed every 15 minutes, so a
        long-running API server does not keep yesterday's market."""
        if self._market_ctx is not None and self._market_ctx.get("_at", 0) + self.MARKET_CTX_TTL_S > time.time():
            return self._market_ctx
        lines, fii, dii = [], None, None
        data = self.fetcher.get_all_indices()
        wanted = {"NIFTY 50", "NIFTY BANK", "NIFTY MIDCAP 100", "NIFTY SMALLCAP 250", "INDIA VIX"}
        if data and data.get("data"):
            for row in data["data"]:
                if row.get("index") in wanted:
                    lines.append(f"| {row['index']} | {row.get('last')} | {row.get('percentChange')}% |")
            if data.get("advances") is not None:
                lines.append(f"| NSE breadth | {data.get('advances')} advances / {data.get('declines')} declines | |")
        for row in self.fetcher.get_fii_dii():
            v = row.get("net_cr")
            s = f"{'+' if (v or 0) >= 0 else '-'}₹{abs(v or 0):,.0f} Cr ({row.get('date')})" if v is not None else None
            if row.get("category", "").startswith("FII"):
                fii = s
            elif row.get("category") == "DII":
                dii = s
        text = None
        if lines:
            text = "| Index | Last | Change |\n|------|------|------|\n" + "\n".join(lines)
            if fii or dii:
                text += f"\n\nCash-market flows: FII/FPI net {fii or 'N/A'}, DII net {dii or 'N/A'}"
        self._market_ctx = {"text": text, "fii": fii, "dii": dii, "_at": time.time()}
        return self._market_ctx

    # ---- steps 2-8 --------------------------------------------------------------------------------------------------
    def nifty_series(self) -> Optional[pd.Series]:
        """NIFTY 50 daily closes for relative strength (cached 15 minutes)."""
        cached = getattr(self, "_nifty", None)
        if cached is not None and cached[0] > time.time():
            return cached[1]
        try:
            df = self.fetcher.yf.daily("^NSEI", SWING_HISTORY_DAYS)
            ser = pd.Series(df["close"].values, index=pd.to_datetime(df["date"]))
        except Exception as e:
            logger.warning("NIFTY series unavailable for relative strength: %s", e)
            ser = None
        self._nifty = (time.time() + 900, ser)
        return ser

    def analyze_stock(self, t: Target, query_id: str, data_source: Optional[str] = None, style: str = "short") -> Optional[AnalysisResult]:
        name = self.fetcher.get_stock_name(t)
        df = self.db.get_daily(t.code, SWING_HISTORY_DAYS if style == "swing" else 250)
        if df is None or df.empty:
            logger.error("%s: no stored bars, cannot analyse", t.code)
            return None
        quote = self.fetcher.get_realtime_quote(t, df)
        dv_rows = self.fetcher.get_delivery(t, 20) if self.config.enable_delivery else None
        delivery = analyze_delivery(dv_rows, quote.get("price"))
        band = self.fetcher.get_price_band(t)
        fundamentals = self.fetcher.get_fundamentals(t)
        trend = self.trend.analyze(df, t.code)
        news_context, intel = None, {}
        if self.search is not None and self.search.is_available:
            intel = self.search.search_comprehensive_intel(t, name)
            if any(r.success and r.results for r in intel.values()):
                news_context = self.search.format_intel_report(intel, name)
            for dim, resp in intel.items():
                if resp.success and resp.results:
                    try:
                        self.db.save_news_intel(t.code, name, dim, resp.query, resp.provider, [x.to_dict() for x in resp.results], query_id)
                    except Exception as e:  # pragma: no cover
                        logger.warning("%s: saving news failed: %s", t.code, e)
        ctx = self.build_context(t, name, df.tail(250).reset_index(drop=True), quote, delivery, band, fundamentals, trend, data_source)
        if style == "swing":
            sw = analyze_swing(df, t.code, self.nifty_series(), delivery)
            ctx["style"], ctx["swing"] = "swing", sw.to_dict()
            ctx["capital"], ctx["risk_pct_setting"] = self.config.capital, self.config.risk_per_trade_pct
            ctx["position_size"] = position_size(sw.entry or 0, sw.stop or 0, self.config.capital, self.config.risk_per_trade_pct) if sw.ok else {}
        result, raw = self.analyzer.analyze(ctx, news_context)
        result.analysis_date = ctx["date"]
        try:
            snap = {k: v for k, v in ctx.items() if k != "trend_text"} if self.config.save_context_snapshot else None
            self.db.save_analysis(result.to_dict(), query_id, self.config.report_type, news_context, snap,
                                  pd.Timestamp(ctx["date"]).date())
        except Exception as e:
            logger.warning("%s: saving history failed: %s", t.code, e)
        return result

    def build_context(self, t, name, df, quote, delivery, band, fundamentals, trend, data_source) -> Dict:
        df = df.sort_values("date").reset_index(drop=True)
        last, prev = df.iloc[-1], (df.iloc[-2] if len(df) > 1 else None)
        mas = {f"ma{n}": round(float(df["close"].tail(n).mean()), 2) for n in (5, 10, 20)}
        today = {"close": float(last["close"]), "open": float(last["open"]), "high": float(last["high"]), "low": float(last["low"]),
                 "volume": float(last["volume"]), "amount": float(last["amount"]) if last["amount"] == last["amount"] else None,
                 "pct_chg": round(float(last["pct_chg"]), 2) if last["pct_chg"] == last["pct_chg"] else None, **mas}
        c, m5, m10, m20 = today["close"], mas["ma5"], mas["ma10"], mas["ma20"]
        ma_status = ("Bullish alignment 📈" if c > m5 > m10 > m20 else "Bearish alignment 📉" if c < m5 < m10 < m20 else
                     "Short-term up ↗️" if c > m5 and m5 > m10 else "Short-term down ↘️" if c < m5 and m5 < m10 else "Tangled ↔️")
        mk = self.market_context()
        ctx = {
            "code": t.code, "stock_name": name, "exchange": t.exchange, "date": str(pd.Timestamp(last["date"]).date()),
            "today": today, "yesterday": {"close": float(prev["close"]), "volume": float(prev["volume"])} if prev is not None else {},
            "ma_status": ma_status, "realtime": quote, "fundamentals": fundamentals, "sector": fundamentals.get("sector"),
            "industry": fundamentals.get("industry"), "delivery": delivery, "price_band": band,
            "circuit_hit": circuit_status(c, float(prev["close"]), (band or {}).get("band_pct")) if prev is not None and band else None,
            "trend_analysis": trend.to_dict(), "trend_text": self.trend.format_analysis(trend),
            "market_phase": market_phase(now_ist(), self.calendar), "market_context": mk.get("text"),
            "is_index_etf": t.is_index_etf, "data_source": data_source, "news_days": self.config.news_max_age_days,
        }
        from .analyzer import _flow_bias
        bias, reason = _flow_bias(ctx)
        ctx["flow"] = {"bias": bias, "reason": reason, "fii": mk.get("fii"), "dii": mk.get("dii")}
        return ctx

    def process_single_stock(self, t: Target, query_id: str, dry_run: bool, force_refresh: bool, style: str = "short") -> Optional[AnalysisResult]:
        ok, err, src = self.fetch_and_save_stock_data(t, force_refresh, style=style)
        if not ok:
            logger.error("%s: data fetch failed: %s", t.code, err)
            if self.db.get_daily(t.code, 5).empty:
                return None
            logger.warning("%s: analysing with the stored bars", t.code)
        if dry_run:
            return None
        try:
            return self.analyze_stock(t, query_id, src, style)
        except Exception as e:
            logger.exception("%s: analysis failed: %s", t.code, e)
            return None

    def run(self, targets: List[Target], dry_run: bool = False, send_notification: bool = True,
            merge_notification: bool = False, force_refresh: bool = False, style: Optional[str] = None) -> List[AnalysisResult]:
        start = time.time()
        style = style or self.config.trading_style
        query_id = uuid.uuid4().hex[:12]
        results: List[AnalysisResult] = []
        workers = max(1, min(self.config.max_workers, len(targets) or 1))
        logger.info("Analysing %d stocks (%s style) with %d worker(s); LLM: %s", len(targets), style, workers, self.analyzer.router.describe())
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {}
            for i, t in enumerate(targets):
                if i and self.config.analysis_delay:
                    time.sleep(self.config.analysis_delay)
                futures[ex.submit(self.process_single_stock, t, query_id, dry_run, force_refresh, style)] = t
            for n, f in enumerate(as_completed(futures), 1):
                t = futures[f]
                r = f.result()
                self.progress(int(n / len(futures) * 100), f"{t.code} done")
                if r is None:
                    continue
                results.append(r)
                if send_notification and self.config.single_stock_notify and not dry_run and self.notifier.is_available():
                    self.notifier.send(generate_single_stock_report(r), f"{r.name} ({r.code}): {r.operation_advice}")
        logger.info("Analysis finished: %d/%d stocks in %.1fs", len(results), len(targets), time.time() - start)
        if results and not dry_run:
            report = generate_dashboard_report(results, report_type=self.config.report_type)
            path = self.notifier.save_report(report, self.config.reports_dir,
                                             f"report_{'swing_' if style == 'swing' else ''}{datetime.now():%Y%m%d}.md")
            logger.info("Report saved: %s", path)
            if send_notification and not merge_notification and not self.config.single_stock_notify and self.notifier.is_available():
                sent = self.notifier.send(report, f"🎯 {now_ist():%Y-%m-%d} Decision Dashboard")
                logger.info("Notifications: %s", sent)
        return results
