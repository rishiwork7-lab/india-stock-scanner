"""Signal diary (research/EDGE_RESEARCH.md, L1): one row per stock and ETF per build day, never edited afterwards.

Files: records/signals/<YYYY-MM-DD>.csv.gz (the daily GitHub run commits them, so they survive cache expiry and keep
stocks that later leave the Nifty 500). A rebuild on the same data date replaces that day's file.
"""
from __future__ import annotations

import gzip
import io
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from .portfolio import momentum_12_1

FIELDS = ["date", "code", "kind", "industry", "price", "traded_value_cr", "ret_1w", "ret_1m", "ret_3m", "ret_6m", "ret_1y", "mom_12_1",
          "swing_signal", "swing_score", "swing_setup", "swing_stop", "swing_target1", "swing_target2", "rs_3m", "rs_6m", "atr_pct",
          "pct_from_high", "ud_volume_ratio", "extension_pct", "weekly_rsi", "trend_points",
          "short_signal", "short_score", "rsi12", "volume_ratio", "results_in_sessions", "results_confirmed",
          "mood_score", "mood", "nifty_above_ma200"]


def rows_from_scan(scan: Dict, mood_score: Optional[float] = None, mood: Optional[str] = None,
                   nifty_above_ma200: Optional[bool] = None) -> List[Dict]:
    out = []
    for kind, rows in (("stock", scan.get("stocks", [])), ("etf", scan.get("etfs", []))):
        for r in rows:
            if r.get("stale") or not r.get("price"):
                continue
            sw, sh = r.get("swing") or {}, r.get("short") or {}
            nr = (r.get("events") or {}).get("next_results") or {}
            out.append({"date": scan["date"], "code": r["code"], "kind": kind, "industry": r.get("industry") or r.get("tracks"),
                        "price": r["price"], "traded_value_cr": r.get("traded_value_cr"),
                        **{k: r.get(k) for k in ("ret_1w", "ret_1m", "ret_3m", "ret_6m", "ret_1y")}, "mom_12_1": momentum_12_1(r),
                        "swing_signal": sw.get("signal"), "swing_score": sw.get("score"), "swing_setup": sw.get("setup"),
                        "swing_stop": sw.get("stop"), "swing_target1": sw.get("target1"), "swing_target2": sw.get("target2"),
                        **{k: sw.get(k) for k in ("rs_3m", "rs_6m", "atr_pct", "pct_from_high", "ud_volume_ratio", "extension_pct", "weekly_rsi", "trend_points")},
                        "short_signal": sh.get("label"), "short_score": sh.get("score"), "rsi12": sh.get("rsi12"), "volume_ratio": sh.get("volume_ratio"),
                        "results_in_sessions": nr.get("sessions"), "results_confirmed": nr.get("confirmed"),
                        "mood_score": mood_score, "mood": mood, "nifty_above_ma200": nifty_above_ma200})
    return out


def save(records_dir: Path, rows: List[Dict]) -> Optional[Path]:
    if not rows:
        return None
    d = Path(records_dir) / "signals"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{rows[0]['date']}.csv.gz"
    if f.exists() and len(pd.read_csv(f, usecols=["code"])) > len(rows):
        return f                                          # a rebuild with patchier data never replaces a fuller day
    buf = io.StringIO()
    pd.DataFrame(rows, columns=FIELDS).to_csv(buf, index=False)
    f.write_bytes(gzip.compress(buf.getvalue().encode(), mtime=0))       # mtime 0: same content -> same bytes, no empty commits
    return f


def load(records_dir: Path, since: Optional[str] = None) -> pd.DataFrame:
    files = sorted((Path(records_dir) / "signals").glob("*.csv.gz"))
    if since:
        files = [f for f in files if f.name[:10] >= since]
    if not files:
        return pd.DataFrame(columns=FIELDS)
    return pd.concat([pd.read_csv(f) for f in files], ignore_index=True)


def recent_symbols(records_dir: Path, last_files: int = 90) -> List[str]:
    """Codes in the last ~90 diary days: their prices keep being downloaded so outcomes can be measured even after a stock
    leaves the Nifty 500."""
    files = sorted((Path(records_dir) / "signals").glob("*.csv.gz"))[-last_files:]
    codes = set()
    for f in files:
        codes.update(pd.read_csv(f, usecols=["code"])["code"].astype(str))
    return sorted(codes)
