"""Shared pieces for the data providers: a browser-like HTTP session with retries, a circuit breaker (as in the original's
data_provider/base.py: a failing source is skipped for a cool-down instead of being hammered), and the normalised daily-bar
DataFrame schema every provider returns: date, open, high, low, close, volume, amount (₹), pct_chg."""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "application/json,text/plain,text/csv,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}
DAILY_COLUMNS = ["date", "open", "high", "low", "close", "volume", "amount", "pct_chg"]


class DataUnavailable(Exception):
    """The source answered but has no data for this request (not a source failure)."""


class SourceFailure(Exception):
    """The source failed (network, blocked, bad format); counts toward its circuit breaker."""


class CircuitBreaker:
    def __init__(self, threshold: int = 3, cooldown_s: float = 300.0):
        self.threshold, self.cooldown_s = threshold, cooldown_s
        self.failures, self.open_until = 0, 0.0
        self.lock = threading.Lock()

    def allow(self) -> bool:
        with self.lock:
            return time.time() >= self.open_until

    def record(self, ok: bool) -> None:
        with self.lock:
            if ok:
                self.failures = 0
                return
            self.failures += 1
            if self.failures >= self.threshold:
                self.open_until = time.time() + self.cooldown_s
                self.failures = 0


class HttpClient:
    def __init__(self, referer: Optional[str] = None, timeout: float = 20.0, retries: int = 2):
        self.s = requests.Session()
        self.s.headers.update(BROWSER_HEADERS)
        if referer:
            self.s.headers["Referer"] = referer
        self.timeout, self.retries = timeout, retries
        self.lock = threading.Lock()

    def get(self, url: str, **kw) -> requests.Response:
        last = None
        for k in range(self.retries + 1):
            try:
                r = self.s.get(url, timeout=self.timeout, **kw)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise SourceFailure(f"HTTP {r.status_code}")
                return r
            except (requests.RequestException, SourceFailure) as e:
                last = e
                time.sleep(1.0 + 2 * k)
        raise SourceFailure(f"{url}: {last}")


def normalise_daily(df: pd.DataFrame) -> pd.DataFrame:
    """Sort, de-duplicate and fill derived columns so every provider returns the same schema."""
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    if "amount" not in df or df["amount"].isna().all():
        df["amount"] = df["close"] * df["volume"]
    df = df.dropna(subset=["close"]).drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
    df["pct_chg"] = df["close"].pct_change() * 100
    return df[DAILY_COLUMNS]
