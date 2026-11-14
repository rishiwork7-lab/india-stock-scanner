"""Event checker: the dates that make a stock jump, and how big those jumps usually are.

For each stock:
  next results    NSE board-meeting calendar (confirmed dates), else Yahoo's expected earnings date (marked estimate)
  corporate acts  dividends, splits, bonuses with their ex-dates (NSE)
  results moves   how far the stock moved over its past results: close of the session before the board meeting to the
                  close of the session after it (2 sessions, so it covers results released during or after market
                  hours), compared with a normal 2-session move over the last year
  warnings        results inside a short-term (<= 5 sessions) or swing (<= 40 sessions) holding period, ex-dates soon
Market: NSE holidays in the next 30 days.

These are facts and risk warnings, not trade signals. Skipping swing entries before results was tested (E2,
research/EDGE_RESEARCH.md, 80,350 trades): no difference in average return or worst-5% loss, a slightly higher stop rate.
Sources are tried in order (NSE, then Yahoo); NSE often refuses cloud servers such as GitHub Actions, so every part
works from Yahoo alone with less detail.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

SHORT_HOLD = 5          # sessions: a short-term trade (days)
SWING_HOLD = 40         # sessions: the swing plan's 8-week time stop
CACHE_S = 3 * 86400     # per-stock history and corporate actions; newly announced results come from the daily
                        # market-wide calendar call (EventService.load_upcoming), so they are never 3 days late


def is_results(purpose: str, desc: str = "") -> bool:
    t = f"{purpose} {desc}".lower()
    return "result" in t


def sessions_until(calendar, start: date, end: date) -> int:
    """Trading sessions after `start` up to and including `end` (0 if end <= start)."""
    if end <= start:
        return 0
    n, d = 0, start
    while d < end:
        d += timedelta(days=1)
        if calendar.is_trading_day(d):
            n += 1
    return n


def results_reaction(df: pd.DataFrame, dates: Iterable[date], last_n: int = 8) -> Optional[Dict]:
    """Moves over past results (session before -> session after the meeting date), vs a normal 2-session move."""
    if df is None or len(df) < 30:
        return None
    d = df.sort_values("date").reset_index(drop=True)
    days = pd.to_datetime(d["date"]).dt.date.tolist()
    close = d["close"].astype(float).tolist()
    moves = []
    for ev in sorted(set(dates)):
        pre = max((i for i, x in enumerate(days) if x < ev), default=None)
        post = min((i for i, x in enumerate(days) if x > ev), default=None)
        if pre is None or post is None or post - pre > 3:      # event outside the price history, or a long gap
            continue
        moves.append({"date": ev.isoformat(), "move_pct": round((close[post] / close[pre] - 1) * 100, 2)})
    moves = moves[-last_n:]
    if not moves:
        return None
    c = pd.Series(close).tail(252)
    normal = float((c.shift(-2) / c - 1).abs().median() * 100)
    absm = [abs(m["move_pct"]) for m in moves]
    biggest = max(moves, key=lambda m: abs(m["move_pct"]))
    med = float(pd.Series(absm).median())
    return {"n": len(moves), "median_abs_pct": round(med, 2), "max_abs_pct": round(abs(biggest["move_pct"]), 2),
            "max_date": biggest["date"], "up_count": sum(m["move_pct"] > 0 for m in moves),
            "normal_2d_pct": round(normal, 2), "times_normal": round(med / normal, 1) if normal > 0 else None, "moves": moves}


def stock_events(today: date, calendar, df: Optional[pd.DataFrame], raw: Dict) -> Dict:
    """Combine one stock's raw event data (from EventService.raw) into dates, moves and warnings."""
    future = [e for e in raw.get("results", []) if e["date"] >= today]
    confirmed = [e for e in future if e["source"] == "NSE"]
    upcoming = sorted(confirmed or future, key=lambda e: e["date"])      # a confirmed NSE date beats a Yahoo estimate
    nxt = None
    if upcoming:
        e = upcoming[0]
        nxt = {"date": e["date"].isoformat(), "source": e["source"], "confirmed": e["source"] == "NSE",
               "sessions": sessions_until(calendar, today, e["date"]), "days": (e["date"] - today).days}
    past = [e["date"] for e in raw.get("results", []) if e["date"] < today]
    reaction = results_reaction(df, past) if df is not None else None
    actions = [{"ex_date": a["ex_date"].isoformat(), "subject": a["subject"], "sessions": sessions_until(calendar, today, a["ex_date"])}
               for a in sorted(raw.get("actions", []), key=lambda a: a["ex_date"]) if today <= a["ex_date"] <= today + timedelta(days=60)]
    last = max(past) if past else None
    warnings: List[Dict] = []
    typical = f" This stock has moved a median ±{reaction['median_abs_pct']}% on results ({reaction['times_normal']}× a normal 2-day move; biggest ±{reaction['max_abs_pct']}%)." if reaction else ""
    if nxt:
        when = f"{nxt['date']}{'' if nxt['confirmed'] else ' (expected, not yet confirmed)'}"
        if nxt["sessions"] <= SHORT_HOLD:
            warnings.append({"level": "high", "kind": "results", "text": f"Results in {nxt['sessions']} sessions, {when}.{typical} "
                             "Expect a gap. In our test (E2), swing trades started just before results did about as well on average "
                             "but hit the stop slightly more often (57% vs 56%); size so that a gap through the stop is affordable."})
        elif nxt["sessions"] <= SWING_HOLD:
            warnings.append({"level": "medium", "kind": "results", "text": f"Results on {when}, {nxt['sessions']} sessions away: inside a "
                             f"swing trade's 2–8 week hold.{typical}"})
    if last and sessions_until(calendar, last, today) <= 3:
        mv = next((m for m in (reaction or {}).get("moves", []) if m["date"] == last.isoformat()), None)
        warnings.append({"level": "info", "kind": "results", "text": f"Results just out ({last.isoformat()})" + (f": moved {mv['move_pct']:+.2f}%." if mv else ".")})
    for a in actions:
        s = a["subject"].lower()
        if any(k in s for k in ("split", "bonus", "demerger", "rights")):
            warnings.append({"level": "medium", "kind": "action", "text": f"{a['subject']}: ex-date {a['ex_date']}. The share price adjusts that day; "
                             "set stops and targets again after it."})
        elif "dividend" in s and a["sessions"] <= 10:
            warnings.append({"level": "low", "kind": "action", "text": f"{a['subject']}: ex-date {a['ex_date']}. The price drops by about the dividend "
                             "that morning (holders on the record date get it)."})
    return {"next_results": nxt, "last_results": last.isoformat() if last else None, "results_reaction": reaction,
            "corporate_actions": actions, "warnings": warnings, "sources": raw.get("sources", [])}


class EventService:
    """Collects raw event data per stock (disk cache, 3 days) plus the market-wide calendar of confirmed results
    meetings (one NSE call per run). Fetchers are injectable for tests."""

    def __init__(self, cache_dir: Path, nse=None, yahoo: Optional[Callable[..., Dict]] = None, now: Callable[[], float] = time.time):
        self.cache_dir = Path(cache_dir) / "events"
        self.nse = nse
        self.yahoo = yahoo if yahoo is not None else yahoo_events
        self.now = now
        self.confirmed: Dict[str, List[date]] = {}

    def load_upcoming(self, days: int = 60) -> int:
        """One NSE call: every company's announced board meetings for results in the next `days` days."""
        if self.nse is None:
            return 0
        today = datetime.fromtimestamp(self.now()).date()
        try:
            rows = self.nse.event_calendar(None, today, today + timedelta(days=days))
        except Exception as e:
            logger.info("NSE results calendar unavailable (%s)", e)
            return 0
        for r in rows:
            if r.get("symbol") and is_results(r["purpose"], r["desc"]):
                self.confirmed.setdefault(r["symbol"], []).append(r["date"])
        return sum(len(v) for v in self.confirmed.values())

    def prefetch(self, symbols: Iterable[str], workers: int = 4) -> None:
        """Fill the per-stock cache a few stocks at a time (NSE and Yahoo are slow per request)."""
        from concurrent.futures import ThreadPoolExecutor
        todo = [s for s in symbols if self._cached(s) is None]
        if todo:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                list(ex.map(self.raw, todo))

    def _with_confirmed(self, sym: str, raw: Dict) -> Dict:
        extra = [d for d in self.confirmed.get(sym, []) if d not in {e["date"] for e in raw["results"]}]
        if not extra:
            return raw
        return {**raw, "results": raw["results"] + [{"date": d, "source": "NSE"} for d in extra],
                "sources": raw["sources"] + ([] if "NSE results calendar" in raw["sources"] else ["NSE results calendar"])}

    def _cached(self, sym: str) -> Optional[Dict]:
        f = self.cache_dir / f"{sym}.json"
        if not f.exists():
            return None
        try:
            d = json.loads(f.read_text())
            if self.now() - float(d.get("fetched", 0)) > CACHE_S:
                return None
            return _decode(d)
        except Exception:
            return None

    def raw(self, sym: str) -> Dict:
        hit = self._cached(sym)
        if hit is not None:
            return self._with_confirmed(sym, hit)
        out: Dict = {"results": [], "actions": [], "sources": []}
        if self.nse is not None:
            try:
                rows = self.nse.event_calendar(sym)
                seen = set()
                for r in rows:
                    if is_results(r["purpose"], r["desc"]) and r["date"] not in seen:
                        seen.add(r["date"])
                        out["results"].append({"date": r["date"], "source": "NSE"})
                out["sources"].append("NSE board meetings")
            except Exception as e:
                logger.info("%s: NSE event calendar unavailable (%s)", sym, e)
            try:
                out["actions"] = [{"ex_date": a["ex_date"], "subject": a["subject"]} for a in self.nse.corporate_actions(sym)]
                out["sources"].append("NSE corporate actions")
            except Exception as e:
                logger.info("%s: NSE corporate actions unavailable (%s)", sym, e)
        today = datetime.fromtimestamp(self.now()).date()
        has_future = any(e["date"] >= today for e in out["results"])
        if not has_future:
            try:
                y = self.yahoo(sym, not out["results"])      # past dates only needed when NSE gave none
                if not out["results"]:                      # NSE gave no history: use Yahoo's past earnings dates
                    out["results"] = [{"date": d, "source": "Yahoo"} for d in y.get("past", [])]
                if y.get("next") and y["next"] >= today:
                    out["results"].append({"date": y["next"], "source": "Yahoo estimate"})
                if not out["actions"] and y.get("ex_dividend") and y["ex_dividend"] >= today:
                    out["actions"].append({"ex_date": y["ex_dividend"], "subject": "Dividend"})
                out["sources"].append("Yahoo calendar")
            except Exception as e:
                logger.info("%s: Yahoo calendar unavailable (%s)", sym, e)
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            (self.cache_dir / f"{sym}.json").write_text(json.dumps({**_encode(out), "fetched": self.now()}))
        except OSError:
            pass
        return self._with_confirmed(sym, out)


def yahoo_events(sym: str, need_past: bool = True) -> Dict:
    """Yahoo: expected next earnings date, next ex-dividend date and (if need_past) past earnings dates in IST."""
    import yfinance as yf
    t = yf.Ticker(f"{sym}.NS")
    out: Dict = {"next": None, "past": [], "ex_dividend": None}
    cal = t.calendar or {}
    ed = cal.get("Earnings Date") or []
    if ed:
        out["next"] = ed[0] if isinstance(ed[0], date) else pd.Timestamp(ed[0]).date()
    if cal.get("Ex-Dividend Date"):
        out["ex_dividend"] = pd.Timestamp(cal["Ex-Dividend Date"]).date()
    if not need_past:
        return out
    try:
        hist = t.get_earnings_dates(limit=12)
        if hist is not None and len(hist):
            idx = hist.index.tz_convert("Asia/Kolkata") if hist.index.tz is not None else hist.index
            today = date.today()
            out["past"] = sorted({ts.date() for ts in idx if ts.date() < today})
    except Exception:
        pass
    return out


def _encode(raw: Dict) -> Dict:
    return {"results": [{"date": e["date"].isoformat(), "source": e["source"]} for e in raw["results"]],
            "actions": [{"ex_date": a["ex_date"].isoformat(), "subject": a["subject"]} for a in raw["actions"]], "sources": raw["sources"]}


def _decode(d: Dict) -> Dict:
    return {"results": [{"date": date.fromisoformat(e["date"]), "source": e["source"]} for e in d.get("results", [])],
            "actions": [{"ex_date": date.fromisoformat(a["ex_date"]), "subject": a["subject"]} for a in d.get("actions", [])],
            "sources": d.get("sources", [])}


def upcoming_holidays(calendar, today: date, days: int = 30) -> List[str]:
    return sorted(d.isoformat() for d in calendar.holidays() if today <= d <= today + timedelta(days=days) and d.weekday() < 5)
