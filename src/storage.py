"""SQLite storage (the original's src/storage.py tables that this replica uses): stock_daily (resume support), news_intel,
analysis_history (with parsed sniper levels for the backtest), backtest_results and backtest_summaries. stdlib sqlite3,
one connection per call, WAL mode so the API server can read while a run writes."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_daily (
  code TEXT NOT NULL, date TEXT NOT NULL, open REAL, high REAL, low REAL, close REAL, volume REAL, amount REAL, pct_chg REAL,
  data_source TEXT, created_at TEXT, PRIMARY KEY (code, date));
CREATE TABLE IF NOT EXISTS news_intel (
  id INTEGER PRIMARY KEY AUTOINCREMENT, query_id TEXT, code TEXT, name TEXT, dimension TEXT, query TEXT, provider TEXT,
  title TEXT, snippet TEXT, url TEXT, published TEXT, fetched_at TEXT);
CREATE INDEX IF NOT EXISTS ix_news_code ON news_intel(code, fetched_at);
CREATE TABLE IF NOT EXISTS analysis_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT, query_id TEXT, code TEXT NOT NULL, name TEXT, report_type TEXT,
  sentiment_score INTEGER, operation_advice TEXT, trend_prediction TEXT, decision_type TEXT, analysis_summary TEXT,
  raw_result TEXT, news_content TEXT, context_snapshot TEXT, ideal_buy REAL, secondary_buy REAL, stop_loss REAL,
  take_profit REAL, current_price REAL, analysis_date TEXT, model_used TEXT, created_at TEXT, style TEXT DEFAULT 'short');
CREATE INDEX IF NOT EXISTS ix_hist_code ON analysis_history(code, created_at);
CREATE TABLE IF NOT EXISTS backtest_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT, analysis_id INTEGER NOT NULL, code TEXT, eval_window_days INTEGER, engine_version TEXT,
  eval_status TEXT, data TEXT, created_at TEXT, UNIQUE(analysis_id, eval_window_days, engine_version));
CREATE TABLE IF NOT EXISTS backtest_summaries (
  code TEXT NOT NULL, eval_window_days INTEGER NOT NULL, engine_version TEXT NOT NULL, data TEXT, updated_at TEXT,
  PRIMARY KEY (code, eval_window_days, engine_version));
"""

_NUM = re.compile(r"(?<![\w.])(?:₹|Rs\.?|INR)?\s*(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)")


def parse_price(text: Any) -> Optional[float]:
    """First price-like number in a sniper-level string ("Ideal buy: ₹1,335.50 (near MA5)" -> 1335.5). Percentages and
    MA names are skipped."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text) if text > 0 else None
    s = str(text)
    s = re.sub(r"MA\d+", " ", s)
    for m in _NUM.finditer(s):
        end = m.end()
        if end < len(s) and s[end:end + 1] == "%":
            continue
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        if v > 0:
            return v
    return None


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self.conn() as c:
            c.executescript(SCHEMA)
            cols = {r["name"] for r in c.execute("PRAGMA table_info(analysis_history)")}
            if "style" not in cols:                      # databases created before swing mode
                c.execute("ALTER TABLE analysis_history ADD COLUMN style TEXT DEFAULT 'short'")

    @contextmanager
    def conn(self):
        c = sqlite3.connect(str(self.path), timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        try:
            yield c
            c.commit()
        finally:
            c.close()

    # ---- daily bars -------------------------------------------------------------------------------------------------
    def has_today_data(self, code: str, target_date: date) -> bool:
        with self.conn() as c:
            return c.execute("SELECT 1 FROM stock_daily WHERE code=? AND date=?", (code, target_date.isoformat())).fetchone() is not None

    def save_daily_data(self, df: pd.DataFrame, code: str, source: str) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        rows = [(code, pd.Timestamp(r.date).date().isoformat(), r.open, r.high, r.low, r.close, r.volume, r.amount,
                 None if pd.isna(r.pct_chg) else float(r.pct_chg), source, now) for r in df.itertuples()]
        with self._lock, self.conn() as c:
            before = c.execute("SELECT COUNT(*) FROM stock_daily WHERE code=?", (code,)).fetchone()[0]
            c.executemany("INSERT OR REPLACE INTO stock_daily VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
            after = c.execute("SELECT COUNT(*) FROM stock_daily WHERE code=?", (code,)).fetchone()[0]
        return after - before

    def get_daily(self, code: str, days: int = 250) -> pd.DataFrame:
        with self.conn() as c:
            rows = c.execute("SELECT date, open, high, low, close, volume, amount, pct_chg FROM stock_daily WHERE code=? "
                             "ORDER BY date DESC LIMIT ?", (code, days)).fetchall()
        df = pd.DataFrame([dict(r) for r in rows])
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").reset_index(drop=True)

    def count_daily(self, code: str) -> int:
        with self.conn() as c:
            return c.execute("SELECT COUNT(*) FROM stock_daily WHERE code=?", (code,)).fetchone()[0]

    def get_bars_after(self, code: str, start: date, limit: int) -> List[Dict]:
        with self.conn() as c:
            rows = c.execute("SELECT date, high, low, close FROM stock_daily WHERE code=? AND date>? ORDER BY date LIMIT ?",
                             (code, start.isoformat(), limit)).fetchall()
        return [dict(r) for r in rows]

    # ---- news -------------------------------------------------------------------------------------------------------
    def save_news_intel(self, code: str, name: str, dimension: str, query: str, provider: str, results: List[Dict],
                        query_id: Optional[str] = None) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self.conn() as c:
            c.executemany("INSERT INTO news_intel (query_id, code, name, dimension, query, provider, title, snippet, url, published, fetched_at) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          [(query_id, code, name, dimension, query, provider, r.get("title"), r.get("snippet"), r.get("url"),
                            r.get("published"), now) for r in results])

    # ---- analysis history -------------------------------------------------------------------------------------------
    def save_analysis(self, result: Dict, query_id: Optional[str], report_type: str, news_content: Optional[str],
                      context_snapshot: Optional[Dict], analysis_date: date) -> int:
        sniper = (((result.get("dashboard") or {}).get("battle_plan") or {}).get("sniper_points") or {})
        with self._lock, self.conn() as c:
            cur = c.execute(
                "INSERT INTO analysis_history (query_id, code, name, report_type, sentiment_score, operation_advice, trend_prediction, "
                "decision_type, analysis_summary, raw_result, news_content, context_snapshot, ideal_buy, secondary_buy, stop_loss, "
                "take_profit, current_price, analysis_date, model_used, created_at, style) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (query_id, result.get("code"), result.get("name"), report_type, result.get("sentiment_score"), result.get("operation_advice"),
                 result.get("trend_prediction"), result.get("decision_type"), result.get("analysis_summary"),
                 json.dumps(result, ensure_ascii=False, default=str), news_content,
                 json.dumps(context_snapshot, ensure_ascii=False, default=str) if context_snapshot else None,
                 parse_price(sniper.get("ideal_buy")), parse_price(sniper.get("secondary_buy")), parse_price(sniper.get("stop_loss")),
                 parse_price(sniper.get("take_profit")), result.get("current_price"), analysis_date.isoformat(), result.get("model_used"),
                 datetime.now().isoformat(timespec="seconds"), result.get("style") or "short"))
            return int(cur.lastrowid)

    def list_history(self, limit: int = 50, code: Optional[str] = None, style: Optional[str] = None) -> List[Dict]:
        q = ("SELECT id, code, name, sentiment_score, operation_advice, trend_prediction, analysis_date, model_used, created_at, "
             "COALESCE(style, 'short') AS style FROM analysis_history WHERE 1=1")
        args: List[Any] = []
        if code:
            q += " AND code=?"
            args.append(code)
        if style:
            q += " AND COALESCE(style, 'short')=?"
            args.append(style)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self.conn() as c:
            return [dict(r) for r in c.execute(q, args).fetchall()]

    def get_history(self, hid: int) -> Optional[Dict]:
        with self.conn() as c:
            r = c.execute("SELECT * FROM analysis_history WHERE id=?", (hid,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["raw_result"] = json.loads(d["raw_result"]) if d.get("raw_result") else None
        return d

    def analyses_for_backtest(self, code: Optional[str] = None, style: Optional[str] = None) -> List[Dict]:
        q = ("SELECT id, code, operation_advice, analysis_date, current_price, stop_loss, take_profit, COALESCE(style, 'short') AS style "
             "FROM analysis_history WHERE 1=1")
        args: List[Any] = []
        if code:
            q += " AND code=?"
            args.append(code)
        if style:
            q += " AND COALESCE(style, 'short')=?"
            args.append(style)
        with self.conn() as c:
            return [dict(r) for r in c.execute(q + " ORDER BY id", args).fetchall()]

    # ---- backtest ---------------------------------------------------------------------------------------------------
    def has_backtest(self, analysis_id: int, window: int, version: str) -> bool:
        with self.conn() as c:
            r = c.execute("SELECT eval_status FROM backtest_results WHERE analysis_id=? AND eval_window_days=? AND engine_version=?",
                          (analysis_id, window, version)).fetchone()
        return r is not None and r["eval_status"] == "completed"

    def save_backtest(self, analysis_id: int, code: str, window: int, version: str, data: Dict) -> None:
        with self._lock, self.conn() as c:
            c.execute("INSERT OR REPLACE INTO backtest_results (analysis_id, code, eval_window_days, engine_version, eval_status, data, created_at) "
                      "VALUES (?,?,?,?,?,?,?)", (analysis_id, code, window, version, data.get("eval_status"),
                                                 json.dumps(data, default=str), datetime.now().isoformat(timespec="seconds")))

    def backtest_results(self, code: Optional[str], window: int, version: str) -> List[Dict]:
        q = "SELECT data FROM backtest_results WHERE eval_window_days=? AND engine_version=?"
        args: List[Any] = [window, version]
        if code:
            q += " AND code=?"
            args.append(code)
        with self.conn() as c:
            return [json.loads(r["data"]) for r in c.execute(q, args).fetchall()]

    def save_backtest_summary(self, code: str, window: int, version: str, data: Dict) -> None:
        with self._lock, self.conn() as c:
            c.execute("INSERT OR REPLACE INTO backtest_summaries VALUES (?,?,?,?,?)",
                      (code, window, version, json.dumps(data, default=str), datetime.now().isoformat(timespec="seconds")))

    def get_backtest_summary(self, code: str, window: int, version: str) -> Optional[Dict]:
        with self.conn() as c:
            r = c.execute("SELECT data FROM backtest_summaries WHERE code=? AND eval_window_days=? AND engine_version=?",
                          (code, window, version)).fetchone()
        return json.loads(r["data"]) if r else None
