"""Yahoo Finance via yfinance: daily bars (NSE .NS / BSE .BO / indices), realtime-ish quote, and fundamentals.
yfinance OHLC is split-adjusted; dividends are not removed (auto_adjust=False), matching exchange prices."""
from __future__ import annotations

import logging
from typing import Dict, Optional

import pandas as pd

from .base import CircuitBreaker, DataUnavailable, SourceFailure, normalise_daily

logger = logging.getLogger(__name__)


def _f(v) -> Optional[float]:
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


class YFinanceFetcher:
    name = "yfinance"

    def __init__(self):
        import yfinance  # noqa: F401  (fail early if missing)
        self.breaker = CircuitBreaker(threshold=4, cooldown_s=180)

    def _ticker(self, yf_symbol: str):
        import yfinance as yf
        return yf.Ticker(yf_symbol)

    def daily(self, yf_symbol: str, days: int) -> pd.DataFrame:
        if not self.breaker.allow():
            raise SourceFailure("yfinance circuit open")
        period = "2y" if days > 250 else "1y" if days > 120 else "6mo"
        try:
            h = self._ticker(yf_symbol).history(period=period, auto_adjust=False, actions=False)
        except Exception as e:
            self.breaker.record(False)
            raise SourceFailure(f"yfinance {yf_symbol}: {e}") from e
        self.breaker.record(True)
        if h is None or h.empty:
            raise DataUnavailable(f"yfinance has no bars for {yf_symbol}")
        df = pd.DataFrame({"date": h.index, "open": h["Open"].values, "high": h["High"].values, "low": h["Low"].values,
                           "close": h["Close"].values, "volume": h["Volume"].values})
        df = normalise_daily(df)
        if len(df) < 2:
            raise DataUnavailable(f"yfinance returned only {len(df)} bar(s) for {yf_symbol}")
        return df.tail(days).reset_index(drop=True)

    def info(self, yf_symbol: str) -> Dict:
        try:
            info = self._ticker(yf_symbol).info or {}
        except Exception as e:
            raise SourceFailure(f"yfinance info {yf_symbol}: {e}") from e
        if not info or (info.get("regularMarketPrice") is None and info.get("currentPrice") is None and not info.get("longName")):
            raise DataUnavailable(f"yfinance has no info for {yf_symbol}")
        return info

    @staticmethod
    def quote_from_info(info: Dict) -> Dict:
        price = _f(info.get("currentPrice")) or _f(info.get("regularMarketPrice"))
        return {
            "name": info.get("longName") or info.get("shortName"),
            "price": price,
            "prev_close": _f(info.get("regularMarketPreviousClose")) or _f(info.get("previousClose")),
            "open": _f(info.get("regularMarketOpen")), "high": _f(info.get("regularMarketDayHigh")), "low": _f(info.get("regularMarketDayLow")),
            "volume": _f(info.get("regularMarketVolume")), "avg_volume_10d": _f(info.get("averageDailyVolume10Day")),
            "pe_ratio": _f(info.get("trailingPE")), "forward_pe": _f(info.get("forwardPE")), "pb_ratio": _f(info.get("priceToBook")),
            "total_mv": _f(info.get("marketCap")), "float_shares": _f(info.get("floatShares")),
            "shares_outstanding": _f(info.get("sharesOutstanding")),
            "high_52w": _f(info.get("fiftyTwoWeekHigh")), "low_52w": _f(info.get("fiftyTwoWeekLow")),
            "currency": info.get("currency"),
        }

    @staticmethod
    def fundamentals_from_info(info: Dict) -> Dict:
        pct = lambda v: round(_f(v) * 100, 2) if _f(v) is not None else None  # noqa: E731
        dy = _f(info.get("dividendYield"))
        return {
            "sector": info.get("sector"), "industry": info.get("industry"),
            "revenue": _f(info.get("totalRevenue")), "net_profit": _f(info.get("netIncomeToCommon")),
            "operating_cash_flow": _f(info.get("operatingCashflow")),
            "roe_pct": pct(info.get("returnOnEquity")), "debt_to_equity": _f(info.get("debtToEquity")),
            "revenue_growth_pct": pct(info.get("revenueGrowth")), "earnings_growth_pct": pct(info.get("earningsGrowth")),
            # yfinance reports Indian dividend yield already in percent (e.g. 0.45 = 0.45%)
            "dividend_yield_pct": dy,
            "promoter_holding_pct": pct(info.get("heldPercentInsiders")),
            "institutional_holding_pct": pct(info.get("heldPercentInstitutions")),
            "analyst_target_mean": _f(info.get("targetMeanPrice")), "analyst_recommendation": info.get("recommendationKey"),
            "analyst_count": info.get("numberOfAnalystOpinions"),
        }
