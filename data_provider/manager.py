"""DataFetcherManager: one entry point for every data need, with per-capability source priority, fallback and circuit
breakers (the original's data_provider/base.py manager, adapted to Indian sources).

Daily bars : NSE equity/ETF -> yfinance (.NS), fallback NSE bhavcopy; the newest NSE bhavcopy bar is overlaid when yfinance
             lags. BSE code -> resolved to its ticker through the BSE bhavcopy, then yfinance (.BO), fallback BSE bhavcopy.
             Index -> yfinance.
Quote      : yfinance info + daily bars (volume ratio, turnover rate, 60-day change, valuation).
Delivery   : NSE bhavcopy (NSE-listed only).
Market     : NSE API (indices, breadth, FII/DII, holidays), yfinance fallback for index levels.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from src.india.symbols import INDICES, Target

from .base import DataUnavailable, SourceFailure
from .bse import BSEArchive
from .nse import NSEApi, NSEArchive
from .yfinance_fetcher import YFinanceFetcher

logger = logging.getLogger(__name__)


class DataFetcherManager:
    def __init__(self, cache_dir: Path, priority: Optional[List[str]] = None, nse_enabled: bool = True,
                 yf: Optional[YFinanceFetcher] = None, nse_archive: Optional[NSEArchive] = None,
                 nse_api: Optional[NSEApi] = None, bse_archive: Optional[BSEArchive] = None):
        self.priority = list(priority or ["yfinance", "nse"])
        self.nse_enabled = nse_enabled
        self.yf = yf or YFinanceFetcher()
        self.nse_archive = nse_archive or NSEArchive(cache_dir)
        self.nse_api = nse_api or NSEApi()
        self.bse_archive = bse_archive or BSEArchive(cache_dir)
        self._info: Dict[str, Tuple[float, Dict]] = {}   # code -> (expires_at, info); short TTL so a server never goes stale
        self._bse_map: Dict[str, Dict] = {}
        self._lock = threading.Lock()

    # ---- identity ---------------------------------------------------------------------------------------------------
    def yf_symbol(self, t: Target) -> str:
        if t.exchange == "BSE" and t.symbol.isdigit():
            return f"{self.bse_lookup(t.symbol)['symbol']}.BO"
        return t.yf_symbol

    def bse_lookup(self, code: str) -> Dict:
        with self._lock:
            if code not in self._bse_map:
                self._bse_map[code] = self.bse_archive.lookup(code)
            return self._bse_map[code]

    INFO_TTL_S, INFO_FAIL_TTL_S = 600.0, 60.0

    def info(self, t: Target) -> Dict:
        """Yahoo company info, cached 10 minutes; a failed lookup (e.g. a brief rate limit) is retried after 1 minute
        instead of being remembered for the life of a long-running server."""
        key = t.code
        hit = self._info.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        try:
            info = self.yf.info(self.yf_symbol(t))
            self._info[key] = (time.time() + self.INFO_TTL_S, info)
        except (DataUnavailable, SourceFailure) as e:
            logger.warning("%s: Yahoo company info unavailable (%s); name/valuation fields will be missing", t.code, e)
            info = {}
            self._info[key] = (time.time() + self.INFO_FAIL_TTL_S, info)
        return info

    def get_stock_name(self, t: Target) -> str:
        if t.kind == "index":
            return INDICES[t.symbol][2]
        info = self.info(t)
        name = info.get("longName") or info.get("shortName")
        if not name and t.exchange == "BSE" and t.symbol.isdigit():
            try:
                name = self.bse_lookup(t.symbol)["name"]
            except (DataUnavailable, SourceFailure):
                pass
        return name or t.symbol

    # ---- daily bars -------------------------------------------------------------------------------------------------
    def get_daily_data(self, t: Target, days: int = 120) -> Tuple[pd.DataFrame, str]:
        errors = []
        if t.kind == "index":
            order = ["yfinance"]
        elif t.exchange == "BSE":
            order = ["yfinance", "bse_archive"]
        else:
            # DATA_SOURCE_PRIORITY decides which source is tried first ("yfinance": one call for long history;
            # "nse": the exchange's own bhavcopies, one cached file per session). The other is the fallback.
            order = ["nse_archive" if p == "nse" else p for p in self.priority if p in ("nse", "yfinance")] or ["yfinance", "nse_archive"]
            if not self.nse_enabled:
                order = ["yfinance"]
        for src in order:
            try:
                if src == "yfinance":
                    df = self.yf.daily(self.yf_symbol(t), days)
                elif src == "nse_archive":
                    df = self.nse_archive.daily(t.symbol, min(days, 60))
                else:
                    df = self.bse_archive.daily(t.symbol if t.symbol.isdigit() else self._bse_code_for(t), min(days, 60))
                if src == "yfinance" and t.exchange == "NSE" and self.nse_enabled:
                    df = self._overlay_nse_latest(t, df)
                return df, src
            except (DataUnavailable, SourceFailure) as e:
                errors.append(f"{src}: {e}")
                logger.warning("%s daily bars from %s failed: %s", t.code, src, e)
        raise DataUnavailable("; ".join(errors) or "no source")

    def _bse_code_for(self, t: Target) -> str:
        raise DataUnavailable("BSE bhavcopy fallback needs a 6-digit scrip code")

    def _overlay_nse_latest(self, t: Target, df: pd.DataFrame) -> pd.DataFrame:
        """Yahoo sometimes lags a session; if NSE's newest bhavcopy is newer, append/replace that bar."""
        try:
            last = self.nse_archive.recent_days(1)
        except (DataUnavailable, SourceFailure) as e:
            logger.debug("bhavcopy overlay skipped: %s", e)
            return df
        if not last:
            return df
        d = last[-1]
        row = (self.nse_archive.day(d) or {}).get(t.symbol)
        if not row:
            return df
        from .nse import _num
        bar = {"date": pd.Timestamp(d), "open": _num(row["OPEN_PRICE"]), "high": _num(row["HIGH_PRICE"]), "low": _num(row["LOW_PRICE"]),
               "close": _num(row["CLOSE_PRICE"]), "volume": _num(row["TTL_TRD_QNTY"]), "amount": (_num(row["TURNOVER_LACS"]) or 0) * 1e5}
        df = df[df["date"] != bar["date"]]
        df = pd.concat([df, pd.DataFrame([bar])], ignore_index=True).sort_values("date").reset_index(drop=True)
        df["pct_chg"] = df["close"].pct_change() * 100
        return df

    # ---- quote, delivery, bands, fundamentals -----------------------------------------------------------------------
    def get_realtime_quote(self, t: Target, df: Optional[pd.DataFrame]) -> Dict:
        info = self.info(t) if t.kind != "index" else {}
        q = self.yf.quote_from_info(info) if info else {}
        if df is not None and len(df):
            last = df.iloc[-1]
            q.setdefault("price", None)
            if q.get("price") is None:
                q["price"] = float(last["close"])
            vol_today = float(last["volume"] or 0)
            avg5 = float(df["volume"].iloc[-6:-1].mean()) if len(df) >= 6 else None
            q["volume_ratio"] = round(vol_today / avg5, 2) if avg5 else None
            if len(df) >= 61:
                q["change_60d"] = round((float(last["close"]) / float(df["close"].iloc[-61]) - 1) * 100, 2)
            q["amount"] = float(last["amount"]) if last["amount"] == last["amount"] else None
            fs = q.get("float_shares")
            q["turnover_rate"] = round(vol_today / fs * 100, 3) if fs else None
        if q.get("float_shares") and q.get("price"):
            q["circ_mv"] = q["float_shares"] * q["price"]
        vr = q.get("volume_ratio")
        q["volume_ratio_desc"] = ("" if vr is None else "very heavy" if vr >= 2.5 else "heavy" if vr >= 1.5 else
                                  "light" if vr <= 0.7 else "normal")
        return q

    def get_delivery(self, t: Target, days: int = 20) -> Optional[List[Dict]]:
        if t.exchange != "NSE" or t.kind == "index" or not self.nse_enabled:
            return None
        try:
            return self.nse_archive.delivery(t.symbol, days)
        except (DataUnavailable, SourceFailure) as e:
            logger.info("%s delivery data unavailable: %s", t.code, e)
            return None

    def get_price_band(self, t: Target) -> Optional[Dict]:
        if t.exchange != "NSE" or t.kind == "index" or not self.nse_enabled:
            return None
        try:
            return self.nse_archive.price_band(t.symbol)
        except (DataUnavailable, SourceFailure) as e:
            logger.info("price band unavailable: %s", e)
            return None

    def get_fundamentals(self, t: Target) -> Dict:
        if t.kind == "index":
            return {}
        info = self.info(t)
        return self.yf.fundamentals_from_info(info) if info else {}

    # ---- market-wide ------------------------------------------------------------------------------------------------
    def get_all_indices(self) -> Optional[Dict]:
        if not self.nse_enabled:
            return None
        try:
            return self.nse_api.all_indices()
        except SourceFailure as e:
            logger.warning("NSE allIndices unavailable: %s", e)
            return None

    def get_fii_dii(self) -> List[Dict]:
        if not self.nse_enabled:
            return []
        try:
            return self.nse_api.fii_dii()
        except SourceFailure as e:
            logger.warning("NSE FII/DII unavailable: %s", e)
            return []

    def get_index_quote_yf(self, yf_symbol: str) -> Optional[Dict]:
        try:
            df = self.yf.daily(yf_symbol, 5)
        except (DataUnavailable, SourceFailure):
            return None
        last, prev = df.iloc[-1], df.iloc[-2]
        return {"last": float(last["close"]), "previous_close": float(prev["close"]),
                "pct_change": round((float(last["close"]) / float(prev["close"]) - 1) * 100, 2), "date": str(last["date"].date())}

    def get_announcements(self, t: Target, limit: int = 8) -> List[Dict]:
        if t.exchange != "NSE" or t.kind != "equity" or not self.nse_enabled:
            return []
        try:
            return self.nse_api.announcements(t.symbol, limit)
        except SourceFailure as e:
            logger.info("%s announcements unavailable: %s", t.code, e)
            return []
