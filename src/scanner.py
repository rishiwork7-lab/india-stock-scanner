"""Daily scanner: every Nifty 50 stock and the ETF list (src/universe.py), scored by both trading styles, with events.

Per stock: short-term score (src/stock_analyzer.py, the original project's rules), swing plan (src/swing_analyzer.py:
score, Buy/Watch/Avoid, setup, entry/stop/targets, size), returns over 1 week to 1 year, traded value, and events
(src/events.py). Per ETF: the same scores plus premium/discount to NAV (NSE) and liquidity. Sectors are ranked by
3- and 6-month momentum; that ranking is a description of what is leading, not a tested rotation rule.

Prices: one batch download from Yahoo Finance (adjusted daily bars, ~2 years). Nothing here is tuned to past results.
"""
from __future__ import annotations

import logging
import math
from datetime import date
from typing import Callable, Dict, List, Optional

import pandas as pd

from .decision_scale import band_for_score
from .events import EventService, stock_events, upcoming_holidays
from .stock_analyzer import analyze_stock
from .swing_analyzer import analyze_swing, position_size
from .universe import ETFS

logger = logging.getLogger(__name__)
INDEX = "^NSEI"
THIN_ETF_CR = 5.0          # ETFs trading less than ₹5 crore a day: wide spreads, use limit orders


def _ret(c: pd.Series, n: int) -> Optional[float]:
    return round((float(c.iloc[-1]) / float(c.iloc[-1 - n]) - 1) * 100, 2) if len(c) > n else None


def _num(x):
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return x
    return None if math.isnan(f) or math.isinf(f) else round(f, 2)


def download_prices(symbols: List[str], period: str = "2y") -> Dict[str, pd.DataFrame]:
    """Yahoo batch download -> {symbol: DataFrame(date, open, high, low, close, volume)}. NSE symbols get '.NS'."""
    import yfinance as yf
    tickers = [s if s.startswith("^") else f"{s}.NS" for s in symbols]
    raw = yf.download(tickers, period=period, auto_adjust=True, group_by="ticker", threads=True, progress=False)
    out = {}
    for s, t in zip(symbols, tickers):
        try:
            d = raw[t].dropna(subset=["Close"])
        except KeyError:
            continue
        if d.empty:
            continue
        idx = d.index.tz_localize(None) if d.index.tz is not None else d.index
        out[s] = pd.DataFrame({"date": idx, "open": d["Open"].values, "high": d["High"].values, "low": d["Low"].values,
                               "close": d["Close"].values, "volume": d["Volume"].fillna(0).values}).reset_index(drop=True)
    return out


def _common(df: pd.DataFrame) -> Dict:
    c = df["close"].astype(float)
    value_cr = (df["close"] * df["volume"]).tail(20).mean() / 1e7
    return {"price": _num(c.iloc[-1]), "change_pct": _ret(c, 1), "ret_1w": _ret(c, 5), "ret_1m": _ret(c, 21), "ret_3m": _ret(c, 63),
            "ret_6m": _ret(c, 126), "ret_1y": _ret(c, 250), "traded_value_cr": _num(value_cr),
            "data_date": pd.Timestamp(df["date"].iloc[-1]).date().isoformat()}


def _short(df: pd.DataFrame, code: str, bias_threshold: float) -> Dict:
    t = analyze_stock(df, code, bias_threshold)
    band = band_for_score(t.signal_score)
    return {"score": t.signal_score, "label": band.label if band else "–", "signal": t.buy_signal.value, "trend": t.trend_status.value,
            "ma_alignment": t.ma_alignment, "ma5": _num(t.ma5), "ma10": _num(t.ma10), "ma20": _num(t.ma20), "bias_ma5": _num(t.bias_ma5),
            "volume_ratio": _num(t.volume_ratio_5d), "rsi12": _num(t.rsi_12), "macd": t.macd_status.value,
            "support": [_num(x) for x in t.support_levels[:2]], "resistance": [_num(x) for x in t.resistance_levels[:2]],
            "reasons": t.signal_reasons[:6], "risks": t.risk_factors[:6]}


def _swing(df: pd.DataFrame, code: str, nifty: Optional[pd.Series], delivery, capital: float, risk_pct: float) -> Dict:
    s = analyze_swing(df, code, nifty, delivery)
    keep = ("ok", "score", "signal", "setup", "setup_text", "trend_template", "trend_points", "ma20", "ma50", "ma150", "ma200",
            "high_52w", "low_52w", "pct_from_high", "pct_above_low", "rs_3m", "rs_6m", "atr14", "atr_pct", "ud_volume_ratio",
            "weekly_rsi", "extension_pct", "entry", "stop", "target1", "target2", "risk_pct", "reasons", "risks", "parts")
    out = {k: (_num(v) if isinstance(v, float) else v) for k, v in s.to_dict().items() if k in keep}
    out["position_size"] = position_size(s.entry or 0, s.stop or 0, capital, risk_pct) if s.ok and s.entry and s.stop else {}
    return out


def scan_stock(member: Dict, df: pd.DataFrame, nifty: Optional[pd.Series], today: date, calendar, events: Optional[EventService],
               delivery=None, capital: float = 100000, risk_pct: float = 1.0, bias_threshold: float = 5.0) -> Dict:
    code = member["symbol"]
    row = {"code": code, "name": member.get("name", code), "industry": member.get("industry", ""), **_common(df)}
    row["short"] = _short(df, code, bias_threshold)
    row["swing"] = _swing(df, code, nifty, delivery, capital, risk_pct)
    ev = stock_events(today, calendar, df, events.raw(code)) if events is not None else None
    row["events"] = ev
    row["event_flag"] = max((w["level"] for w in (ev or {}).get("warnings", [])), key=["info", "low", "medium", "high"].index, default=None)
    return row


def scan_etf(etf, df: pd.DataFrame, nifty: Optional[pd.Series], nav_info: Optional[Dict], capital: float, risk_pct: float,
             bias_threshold: float) -> Dict:
    row = {"code": etf.symbol, "name": etf.name, "category": etf.category, "tracks": etf.tracks, **_common(df)}
    row["short"] = _short(df, etf.symbol, bias_threshold)
    row["swing"] = _swing(df, etf.symbol, nifty, None, capital, risk_pct)
    c = df["close"].astype(float)
    row["above_ma50"] = bool(len(c) >= 50 and c.iloc[-1] > c.tail(50).mean())
    row["above_ma200"] = bool(len(c) >= 200 and c.iloc[-1] > c.tail(200).mean()) if len(c) >= 200 else None
    row["history_sessions"] = len(df)
    row["thin"] = (row["traded_value_cr"] or 0) < THIN_ETF_CR
    if nav_info and nav_info.get("nav") and nav_info.get("price"):
        row["nav"] = _num(nav_info["nav"])
        row["nav_close"] = _num(nav_info["price"])          # closing price on the NAV date
        row["premium_pct"] = _num((nav_info["price"] / nav_info["nav"] - 1) * 100)
        row["nav_date"] = nav_info.get("nav_date")
    return row


def sector_ranking(etf_rows: List[Dict]) -> List[Dict]:
    """Sector ETFs ranked by the average of their 3- and 6-month returns (momentum). Descriptive only."""
    rows = []
    for r in etf_rows:
        if r["category"] != "Sector" or r.get("ret_3m") is None:
            continue
        m = r["ret_3m"] if r.get("ret_6m") is None else (r["ret_3m"] + r["ret_6m"]) / 2
        rows.append({"code": r["code"], "tracks": r["tracks"], "momentum": round(m, 2), "ret_1m": r.get("ret_1m"), "ret_3m": r["ret_3m"],
                     "ret_6m": r.get("ret_6m"), "above_ma200": r.get("above_ma200"), "swing_signal": r["swing"].get("signal")})
    rows.sort(key=lambda x: x["momentum"], reverse=True)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def run_scan(members: List[Dict], prices: Dict[str, pd.DataFrame], today: date, calendar, events: Optional[EventService] = None,
             etf_nav: Optional[Dict[str, Dict]] = None, delivery_fn: Optional[Callable[[str, float], Optional[Dict]]] = None,
             capital: float = 100000, risk_pct: float = 1.0, bias_threshold: float = 5.0, progress=None) -> Dict:
    nifty_df = prices.get(INDEX)
    nifty = pd.Series(nifty_df["close"].values, index=pd.to_datetime(nifty_df["date"])) if nifty_df is not None else None
    stocks, missing = [], []
    for i, m in enumerate(members):
        df = prices.get(m["symbol"])
        if df is None or len(df) < 60:
            missing.append(m["symbol"])
            continue
        try:
            dv = delivery_fn(m["symbol"], float(df["close"].iloc[-1])) if delivery_fn else None
        except Exception:
            dv = None
        try:
            stocks.append(scan_stock(m, df, nifty, today, calendar, events, dv, capital, risk_pct, bias_threshold))
        except Exception as e:
            logger.warning("%s: scan failed: %s", m["symbol"], e)
            missing.append(m["symbol"])
        if progress:
            progress(i + 1, len(members), m["symbol"])
    etfs = []
    for e in ETFS:
        df = prices.get(e.symbol)
        if df is None or len(df) < 60:
            missing.append(e.symbol)
            continue
        try:
            etfs.append(scan_etf(e, df, nifty, (etf_nav or {}).get(e.symbol), capital, risk_pct, bias_threshold))
        except Exception as ex:
            logger.warning("%s: ETF scan failed: %s", e.symbol, ex)
            missing.append(e.symbol)
    stocks.sort(key=lambda r: (r["swing"].get("score") or 0), reverse=True)
    latest = max((r["data_date"] for r in stocks + etfs), default=None)
    for r in stocks + etfs:
        r["stale"] = latest is not None and r["data_date"] < latest
    calendar_rows = []
    for r in stocks:
        nx = (r.get("events") or {}).get("next_results")
        if nx:
            calendar_rows.append({"date": nx["date"], "code": r["code"], "name": r["name"], "kind": "Results",
                                  "detail": "confirmed (NSE)" if nx["confirmed"] else "expected (Yahoo estimate)", "sessions": nx["sessions"],
                                  "typical_move": ((r["events"] or {}).get("results_reaction") or {}).get("median_abs_pct")})
        for a in (r.get("events") or {}).get("corporate_actions", []):
            calendar_rows.append({"date": a["ex_date"], "code": r["code"], "name": r["name"], "kind": "Ex-date", "detail": a["subject"],
                                  "sessions": a["sessions"], "typical_move": None})
    calendar_rows.sort(key=lambda x: (x["date"], x["code"]))
    return {"date": latest, "stocks": stocks, "etfs": etfs, "sectors": sector_ranking(etfs), "missing": missing,
            "calendar": calendar_rows, "holidays": upcoming_holidays(calendar, today, 45),
            "settings": {"capital": capital, "risk_pct": risk_pct, "short_hold_sessions": 5, "swing_hold_sessions": 40}}
