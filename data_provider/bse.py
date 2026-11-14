"""BSE sources: the daily equity bhavcopy (UDiFF format) maps 6-digit scrip codes to ticker symbols and ISINs, and gives
daily bars for BSE-only stocks. Files are cached on disk."""
from __future__ import annotations

import csv
import io
import logging
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from src.india.market import now_ist

from .base import CircuitBreaker, DataUnavailable, HttpClient, SourceFailure, normalise_daily

logger = logging.getLogger(__name__)
URL = "https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{d:%Y%m%d}_F_0000.CSV"


def _num(v) -> Optional[float]:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


class BSEArchive:
    name = "bse_archive"

    def __init__(self, cache_dir: Path, http: Optional[HttpClient] = None):
        self.cache_dir = cache_dir / "bse_bhav"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.http = http or HttpClient(referer="https://www.bseindia.com/")
        self.breaker = CircuitBreaker()
        self._mem: Dict[date, Optional[Dict[str, Dict]]] = {}
        self._lock = threading.Lock()

    def day(self, d: date) -> Optional[Dict[str, Dict]]:
        """{scrip_code: row} for date d (equity stocks only), or None when there was no session."""
        with self._lock:
            if d in self._mem:
                return self._mem[d]
            f, miss = self.cache_dir / f"{d:%Y%m%d}.csv", self.cache_dir / f"{d:%Y%m%d}.missing"
            if f.exists():
                text = f.read_text()
            elif miss.exists() or d.weekday() >= 5:
                self._mem[d] = None
                return None
            else:
                if not self.breaker.allow():
                    raise SourceFailure("BSE archive circuit open")
                try:
                    r = self.http.get(URL.format(d=d))
                except SourceFailure:
                    self.breaker.record(False)
                    raise
                self.breaker.record(True)
                if r.status_code == 200 and "FinInstrmId" in r.text[:300]:
                    text = r.text
                    f.write_text(text)
                else:
                    if r.status_code == 404 and d < now_ist().date():
                        miss.write_text("")
                    self._mem[d] = None
                    return None
            rows = list(csv.reader(io.StringIO(text)))
            head = rows[0]
            # accept only a file whose own trade date is the requested date (holiday URLs can serve stale files)
            if "TradDt" in head and len(rows) > 1 and rows[1][head.index("TradDt")] != d.isoformat():
                f.unlink(missing_ok=True)
                miss.write_text("")
                self._mem[d] = None
                return None
            out = {}
            for r in rows[1:]:
                row = dict(zip(head, r))
                if row.get("FinInstrmTp") == "STK":
                    out[row["FinInstrmId"]] = row
            self._mem[d] = out
            return out

    def latest_day(self, max_lookback: int = 10) -> date:
        d = now_ist().date()
        for _ in range(max_lookback):
            if self.day(d) is not None:
                return d
            d -= timedelta(days=1)
        raise DataUnavailable("no recent BSE bhavcopy")

    def lookup(self, scrip_code: str) -> Dict:
        """{'symbol', 'isin', 'name', 'group'} for a 6-digit BSE scrip code."""
        row = (self.day(self.latest_day()) or {}).get(scrip_code)
        if not row:
            raise DataUnavailable(f"BSE scrip {scrip_code} not in the latest bhavcopy")
        return {"symbol": row["TckrSymb"], "isin": row["ISIN"], "name": row["FinInstrmNm"], "group": row["SctySrs"]}

    def daily(self, scrip_code: str, days: int, max_lookback: int = 400) -> pd.DataFrame:
        recs, d = [], now_ist().date()
        for _ in range(max_lookback):
            day = self.day(d)
            if day is not None and scrip_code in day:
                row = day[scrip_code]
                recs.append({"date": d, "open": _num(row["OpnPric"]), "high": _num(row["HghPric"]), "low": _num(row["LwPric"]),
                             "close": _num(row["ClsPric"]), "volume": _num(row["TtlTradgVol"]), "amount": _num(row["TtlTrfVal"])})
                if len(recs) >= days:
                    break
            d -= timedelta(days=1)
        if not recs:
            raise DataUnavailable(f"BSE scrip {scrip_code}: no bars")
        return normalise_daily(pd.DataFrame(recs))
