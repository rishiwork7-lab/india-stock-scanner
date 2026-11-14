"""Indian market rules: IST trading phases, the NSE trading calendar, circuit bands, and ₹ number formatting.

Replaces the original's A-share calendar (src/core/trading_calendar.py) and phase logic (src/market_phase_*).
NSE equity session (IST): pre-open 09:00-09:15, normal market 09:15-15:30, closing session 15:40-16:00.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional, Set

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
PRE_OPEN, OPEN, CLOSE, POST_CLOSE_END = time(9, 0), time(9, 15), time(15, 30), time(16, 0)


def now_ist() -> datetime:
    return datetime.now(IST)


def to_ist(dt: datetime) -> datetime:
    return dt.astimezone(IST) if dt.tzinfo else dt.replace(tzinfo=IST)


class TradingCalendar:
    """NSE trading days = weekdays minus exchange holidays. Holidays come from NSE's holiday API (cached to disk);
    without them, weekdays are used and the caller is told the check is approximate."""

    def __init__(self, cache_file: Optional[Path] = None, fetch_holidays: Optional[Callable[[], Iterable[date]]] = None):
        self.cache_file = cache_file
        self._fetch = fetch_holidays
        self._holidays: Optional[Set[date]] = None
        self.approximate = False

    def holidays(self) -> Set[date]:
        if self._holidays is not None:
            return self._holidays
        hol: Set[date] = set()
        cached = self._read_cache()
        if cached is not None:
            hol = cached
        elif self._fetch is not None:
            try:
                hol = set(self._fetch())
                self._write_cache(hol)
            except Exception as e:  # network or NSE blocking
                logger.warning("NSE holiday list unavailable (%s); trading-day check uses weekdays only", e)
        self.approximate = not hol
        self._holidays = hol
        return hol

    def _read_cache(self) -> Optional[Set[date]]:
        if not self.cache_file or not self.cache_file.exists():
            return None
        try:
            d = json.loads(self.cache_file.read_text())
            if d.get("year") != now_ist().year:
                return None
            return {date.fromisoformat(x) for x in d["holidays"]}
        except Exception:
            return None

    def _write_cache(self, hol: Set[date]) -> None:
        if self.cache_file and hol:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(json.dumps({"year": now_ist().year, "holidays": sorted(d.isoformat() for d in hol)}))

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays()

    def previous_trading_day(self, d: date) -> date:
        d -= timedelta(days=1)
        while not self.is_trading_day(d):
            d -= timedelta(days=1)
        return d

    def latest_completed_session(self, now: Optional[datetime] = None) -> date:
        """The trading day whose daily bar is complete at `now` (today after 15:30 IST on a trading day)."""
        n = to_ist(now or now_ist())
        if self.is_trading_day(n.date()) and n.time() >= CLOSE:
            return n.date()
        return self.previous_trading_day(n.date())


def market_phase(now: Optional[datetime] = None, calendar: Optional[TradingCalendar] = None) -> dict:
    """Phase context fed to the prompt, matching the original's phase_decision vocabulary."""
    n = to_ist(now or now_ist())
    trading = calendar.is_trading_day(n.date()) if calendar else n.weekday() < 5
    t = n.time()
    if not trading:
        phase, label = "non_trading", "Non-trading day (weekend or NSE holiday)"
    elif t < PRE_OPEN:
        phase, label = "premarket", "Pre-market (before the 09:00 IST pre-open)"
    elif t < OPEN:
        phase, label = "premarket", "Pre-open call auction (09:00-09:15 IST)"
    elif t < CLOSE:
        phase, label = "intraday", "Market open (09:15-15:30 IST)"
    elif t < POST_CLOSE_END:
        phase, label = "closing_auction", "Closing session (15:30-16:00 IST)"
    else:
        phase, label = "postmarket", "Post-market (after the 15:30 IST close)"
    return {"phase": phase, "label": label, "time_ist": n.strftime("%Y-%m-%d %H:%M IST"), "is_trading_day": trading,
            "daily_bar_complete": trading and t >= CLOSE or not trading}


# ---- money and numbers ------------------------------------------------------------------------------------------------
def format_inr(amount: Optional[float], unit: str = "auto") -> str:
    """₹ amounts in Indian units: crore (1e7) and lakh (1e5)."""
    if amount is None:
        return "N/A"
    try:
        a = float(amount)
    except (TypeError, ValueError):
        return "N/A"
    sign = "-" if a < 0 else ""
    a = abs(a)
    if unit == "cr" or (unit == "auto" and a >= 1e7):
        return f"{sign}₹{a / 1e7:,.2f} Cr"
    if unit == "lakh" or (unit == "auto" and a >= 1e5):
        return f"{sign}₹{a / 1e5:,.2f} L"
    return f"{sign}₹{a:,.2f}"


def format_volume(v: Optional[float]) -> str:
    if v is None:
        return "N/A"
    v = float(v)
    if v >= 1e7:
        return f"{v / 1e7:.2f} Cr shares"
    if v >= 1e5:
        return f"{v / 1e5:.2f} L shares"
    return f"{v:,.0f} shares"


# ---- circuit bands ----------------------------------------------------------------------------------------------------
def circuit_status(price: float, prev_close: float, band_pct: Optional[float]) -> Optional[str]:
    """NSE price bands are 2/5/10/20% (no band for F&O stocks, which use dynamic bands). Returns 'upper', 'lower' or None."""
    if not band_pct or not prev_close or band_pct <= 0:
        return None
    move = (price / prev_close - 1) * 100
    if move >= band_pct - 0.05:
        return "upper"
    if move <= -band_pct + 0.05:
        return "lower"
    return None
