"""NSE India sources.

NSEArchive  - the daily "full bhavcopy" (sec_bhavdata_full_DDMMYYYY.csv): OHLC, volume, turnover and DELIVERY quantity/% for
              every NSE security. Used for delivery analysis (the India replacement for A-share chip distribution) and as a
              daily-bar fallback. Files are cached on disk; one file serves every stock that day.
              Also the price-band list (sec_list.csv): circuit band % and surveillance remarks (GSM/ASM stages).
NSEApi      - public website JSON: all indices (levels, breadth), FII/DII cash flows, trading holidays, corporate
              announcements. Per-stock quote endpoints are blocked for scripts (HTTP 403), so quotes come from yfinance.
"""
from __future__ import annotations

import csv
import io
import logging
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from src.india.market import now_ist

from .base import CircuitBreaker, DataUnavailable, HttpClient, SourceFailure, normalise_daily

logger = logging.getLogger(__name__)

ARCHIVE = "https://nsearchives.nseindia.com"
API = "https://www.nseindia.com/api"
SERIES_PRIORITY = ("EQ", "BE", "BZ", "SM", "ST", "IV", "RR")


def _clean(rows: List[List[str]]) -> List[Dict[str, str]]:
    head = [h.strip() for h in rows[0]]
    return [{h: (v or "").strip() for h, v in zip(head, r)} for r in rows[1:] if r]


def _num(v) -> Optional[float]:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


class NSEArchive:
    name = "nse_archive"

    def __init__(self, cache_dir: Path, http: Optional[HttpClient] = None):
        self.cache_dir = cache_dir / "nse_bhav"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.http = http or HttpClient(referer="https://www.nseindia.com/")
        self.breaker = CircuitBreaker()
        self._mem: Dict[date, Optional[Dict[str, Dict]]] = {}
        self._locks: Dict[date, threading.Lock] = {}
        self._glock = threading.Lock()
        self._bands: Optional[Dict[str, Dict]] = None

    # ---- one day's bhavcopy ---------------------------------------------------------------------------------------
    def day(self, d: date) -> Optional[Dict[str, Dict]]:
        """{symbol: row} for date d, or None when there was no session (holiday / not yet published)."""
        if d in self._mem:
            return self._mem[d]
        with self._glock:
            lock = self._locks.setdefault(d, threading.Lock())
        with lock:
            if d in self._mem:
                return self._mem[d]
            f = self.cache_dir / f"{d:%Y%m%d}.csv"
            miss = self.cache_dir / f"{d:%Y%m%d}.missing"
            text = None
            if f.exists():
                text = f.read_text()
            elif miss.exists() or d.weekday() >= 5:
                self._mem[d] = None
                return None
            else:
                if not self.breaker.allow():
                    raise SourceFailure("NSE archive circuit open")
                try:
                    r = self.http.get(f"{ARCHIVE}/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv")
                except SourceFailure:
                    self.breaker.record(False)
                    raise
                self.breaker.record(True)
                if r.status_code == 200 and "SYMBOL" in r.text[:200]:
                    text = r.text
                    f.write_text(text)
                elif r.status_code == 404:
                    # a past date with no file was a holiday; today's file may simply not be published yet (~18:00 IST)
                    if d < now_ist().date():
                        miss.write_text("")
                    self._mem[d] = None
                    return None
                else:
                    raise SourceFailure(f"NSE bhavcopy {d}: HTTP {r.status_code}")
            rows = _clean(list(csv.reader(io.StringIO(text))))
            # On some holidays the archive serves the previous session's file under the holiday's date (e.g. the
            # 10-Nov-2026 file carries DATE1 09-Nov-2026). Only accept a file whose own date is the requested date.
            file_dates = {r.get("DATE1") for r in rows[:50] if r.get("DATE1")}
            if file_dates and f"{d:%d-%b-%Y}" not in file_dates:
                logger.info("NSE bhavcopy for %s carries %s: holiday, ignored", d, sorted(file_dates))
                f.unlink(missing_ok=True)
                miss.write_text("")
                self._mem[d] = None
                return None
            out: Dict[str, Dict] = {}
            for row in rows:
                sym, series = row.get("SYMBOL"), row.get("SERIES")
                if not sym or series not in SERIES_PRIORITY:
                    continue
                prev = out.get(sym)
                if prev is None or SERIES_PRIORITY.index(series) < SERIES_PRIORITY.index(prev["SERIES"]):
                    out[sym] = row
            self._mem[d] = out
            return out

    def recent_days(self, n: int, end: Optional[date] = None, max_lookback: int = 400) -> List[date]:
        """The last n dates with a published bhavcopy, newest last."""
        d = end or now_ist().date()
        found: List[date] = []
        for _ in range(max_lookback):
            if self.day(d) is not None:
                found.append(d)
                if len(found) >= n:
                    break
            d -= timedelta(days=1)
        return sorted(found)

    # ---- per-symbol views ---------------------------------------------------------------------------------------------
    def daily(self, symbol: str, days: int) -> pd.DataFrame:
        recs = []
        for d in self.recent_days(days):
            row = (self.day(d) or {}).get(symbol)
            if row:
                recs.append({"date": d, "open": _num(row["OPEN_PRICE"]), "high": _num(row["HIGH_PRICE"]), "low": _num(row["LOW_PRICE"]),
                             "close": _num(row["CLOSE_PRICE"]), "volume": _num(row["TTL_TRD_QNTY"]),
                             "amount": (_num(row["TURNOVER_LACS"]) or 0) * 1e5})
        if not recs:
            raise DataUnavailable(f"{symbol} not in recent NSE bhavcopies")
        return normalise_daily(pd.DataFrame(recs))

    def delivery(self, symbol: str, days: int = 20) -> List[Dict]:
        """Daily delivery quantity and % (delivered shares / traded shares), oldest first."""
        out = []
        for d in self.recent_days(days):
            row = (self.day(d) or {}).get(symbol)
            if not row:
                continue
            out.append({"date": d.isoformat(), "close": _num(row["CLOSE_PRICE"]), "volume": _num(row["TTL_TRD_QNTY"]),
                        "deliv_qty": _num(row["DELIV_QTY"]), "deliv_pct": _num(row["DELIV_PER"]), "series": row["SERIES"]})
        if not out:
            raise DataUnavailable(f"no delivery data for {symbol}")
        return out

    def price_band(self, symbol: str) -> Optional[Dict]:
        """{'band_pct': 2|5|10|20|None (no band: F&O stock), 'remarks': surveillance note} from sec_list.csv (cached daily)."""
        if self._bands is None:
            f = self.cache_dir / f"sec_list_{now_ist().date():%Y%m%d}.csv"
            if f.exists():
                text = f.read_text()
            else:
                r = self.http.get(f"{ARCHIVE}/content/equities/sec_list.csv")
                if r.status_code != 200:
                    raise SourceFailure(f"sec_list HTTP {r.status_code}")
                text = r.text
                f.write_text(text)
            self._bands = {}
            for row in _clean(list(csv.reader(io.StringIO(text)))):
                band = (row.get("Band") or "").strip()
                self._bands[row.get("Symbol", "")] = {"band_pct": _num(band), "series": row.get("Series"),
                                                     "remarks": (row.get("Remarks") or "").strip(" -") or None}
        return self._bands.get(symbol)


class NSEApi:
    name = "nse_api"

    def __init__(self, http: Optional[HttpClient] = None):
        self.http = http or HttpClient(referer="https://www.nseindia.com/")
        self.breaker = CircuitBreaker()

    def _json(self, path: str):
        if not self.breaker.allow():
            raise SourceFailure("NSE API circuit open")
        try:
            r = self.http.get(f"{API}{path}")
            if r.status_code != 200:
                raise SourceFailure(f"NSE API {path}: HTTP {r.status_code}")
            data = r.json()
        except (SourceFailure, ValueError) as e:
            self.breaker.record(False)
            raise SourceFailure(str(e)) from e
        self.breaker.record(True)
        return data

    def all_indices(self) -> Dict:
        return self._json("/allIndices")

    def fii_dii(self) -> List[Dict]:
        rows = self._json("/fiidiiTradeReact")
        return [{"category": r.get("category"), "date": r.get("date"), "buy_cr": _num(r.get("buyValue")),
                 "sell_cr": _num(r.get("sellValue")), "net_cr": _num(r.get("netValue"))} for r in rows]

    def holidays(self) -> List[date]:
        data = self._json("/holiday-master?type=trading")
        return [datetime.strptime(h["tradingDate"], "%d-%b-%Y").date() for h in data.get("CM", [])]

    def announcements(self, symbol: str, limit: int = 8) -> List[Dict]:
        rows = self._json(f"/corporate-announcements?index=equities&symbol={symbol}")
        out = []
        for r in rows[:limit]:
            out.append({"title": r.get("desc") or "Announcement", "date": r.get("an_dt"), "summary": (r.get("attchmntText") or "")[:500],
                        "url": r.get("attchmntFile") or "", "source": "NSE corporate announcements"})
        return out

    # ---- events (src/events.py) and ETFs ------------------------------------------------------------------------------
    @staticmethod
    def _range(symbol: Optional[str], start: Optional[date], end: Optional[date]) -> str:
        q = "?index=equities"
        if symbol:
            q += f"&symbol={symbol}"
        if start and end:
            q += f"&from_date={start:%d-%m-%Y}&to_date={end:%d-%m-%Y}"
        return q

    def event_calendar(self, symbol: Optional[str] = None, start: Optional[date] = None, end: Optional[date] = None) -> List[Dict]:
        """Board meetings (results, dividends, fund raising...). Per symbol: full history back to ~2006, upcoming included
        once announced. With a date range: every company's meetings in that range."""
        rows = self._json("/event-calendar" + self._range(symbol, start, end)) or []
        out = []
        for r in rows:
            try:
                d = datetime.strptime(r["date"], "%d-%b-%Y").date()
            except (KeyError, ValueError):
                continue
            out.append({"symbol": r.get("symbol"), "date": d, "purpose": r.get("purpose") or "", "desc": (r.get("bm_desc") or "")[:300]})
        return out

    def corporate_actions(self, symbol: Optional[str] = None, start: Optional[date] = None, end: Optional[date] = None) -> List[Dict]:
        """Dividends, splits, bonuses, rights, demergers with their ex-dates."""
        rows = self._json("/corporates-corporateActions" + self._range(symbol, start, end)) or []
        out = []
        for r in rows:
            try:
                d = datetime.strptime(r["exDate"], "%d-%b-%Y").date()
            except (KeyError, ValueError, TypeError):
                continue
            out.append({"symbol": r.get("symbol"), "ex_date": d, "subject": r.get("subject") or ""})
        return out

    def etfs(self) -> Dict[str, Dict]:
        """All NSE ETFs: the published NAV and the closing price of the same day, so the premium or discount to NAV
        compares like with like. NSE publishes NAV a day late: when its date is before the price date, the previous
        close is the matching price."""
        data = self._json("/etf")
        try:
            nav_day = datetime.strptime(data.get("navDate") or "", "%d-%b-%Y").date()
            px_day = datetime.strptime((data.get("timestamp") or "").split(" ")[0], "%d-%b-%Y").date()
        except (ValueError, TypeError):                 # NSE sends navDate: null before the market opens
            nav_day = px_day = None
        out = {}
        for r in data.get("data", []):
            same_day = nav_day is not None and nav_day == px_day
            price = _num(r.get("ltP")) if same_day else _num(r.get("prevClose"))
            out[r.get("symbol")] = {"price": price, "nav": _num(r.get("nav")), "nav_date": nav_day.isoformat() if nav_day else None,
                                    "traded_value_cr": (_num(r.get("trdVal")) or 0) / 1e7}
        return out
