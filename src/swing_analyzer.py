"""Swing analyser (holding period 2-8 weeks), the India replica's second trading style next to the original's
short-term trend analyser (src/stock_analyzer.py).

Rules are standard swing-trading practice (a trend template in the style of Minervini/O'Neil), written down before any
testing. 100 points:
  Long-term trend   25  price > MA50 > MA150 > MA200, MA200 rising over 20 sessions
  Relative strength 20  3- and 6-month return versus NIFTY 50
  52-week position  15  near the 52-week high, well above the 52-week low
  Setup             20  pullback to the rising MA20/MA50, or a base breakout on volume; tight range scores less;
                        extended (>15% above MA50) scores little
  Accumulation      10  up-day vs down-day volume over 50 sessions (+ NSE delivery health when available)
  Weekly momentum   10  weekly RSI(14) in the 50-70 zone
Levels: entry at the setup, stop below the 10-session swing low or 2x ATR(14), never more than 8% below entry;
targets at 2R and 3R; position size for a fixed % risk of capital.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from .decision_scale import signal_key_for_score

MAX_STOP_PCT = 8.0


@dataclass
class SwingResult:
    code: str
    ok: bool = True
    score: int = 0
    signal: str = "Watch"                 # Buy / Watch / Avoid
    setup: str = "none"                   # pullback / breakout / tight_base / extended / none
    setup_text: str = ""
    trend_template: Dict[str, bool] = field(default_factory=dict)
    trend_points: int = 0
    price: float = 0.0
    ma20: Optional[float] = None
    ma50: Optional[float] = None
    ma150: Optional[float] = None
    ma200: Optional[float] = None
    ma200_rising: Optional[bool] = None
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None
    pct_from_high: Optional[float] = None
    pct_above_low: Optional[float] = None
    ret_3m: Optional[float] = None
    ret_6m: Optional[float] = None
    rs_3m: Optional[float] = None         # stock minus NIFTY, percentage points
    rs_6m: Optional[float] = None
    atr14: Optional[float] = None
    atr_pct: Optional[float] = None
    ud_volume_ratio: Optional[float] = None
    weekly_rsi: Optional[float] = None
    extension_pct: Optional[float] = None   # % above MA50
    entry: Optional[float] = None
    stop: Optional[float] = None
    target1: Optional[float] = None
    target2: Optional[float] = None
    risk_pct: Optional[float] = None
    reasons: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    parts: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    d = series.diff()
    g = d.where(d > 0, 0).ewm(alpha=1 / period, adjust=False).mean()
    l = (-d.where(d < 0, 0)).ewm(alpha=1 / period, adjust=False).mean()
    return (100 - 100 / (1 + g / l)).fillna(50)


def _ret(c: pd.Series, n: int) -> Optional[float]:
    return round((c.iloc[-1] / c.iloc[-1 - n] - 1) * 100, 2) if len(c) > n else None


def analyze_swing(df: pd.DataFrame, code: str, index_close: Optional[pd.Series] = None, delivery: Optional[Dict] = None) -> SwingResult:
    r = SwingResult(code=code)
    if df is None or len(df) < 60:
        r.ok = False
        r.risks.append("Not enough history for swing analysis (needs at least 60 sessions; 250 for the full trend template)")
        return r
    df = df.sort_values("date").reset_index(drop=True)
    c, h, l, v = df["close"].astype(float), df["high"].astype(float), df["low"].astype(float), df["volume"].astype(float)
    r.price = float(c.iloc[-1])
    ma = {n: c.rolling(n).mean() for n in (20, 50, 150, 200)}
    r.ma20, r.ma50 = _last(ma[20]), _last(ma[50])
    r.ma150, r.ma200 = _last(ma[150]), _last(ma[200])
    if r.ma200 is not None and len(ma[200].dropna()) > 20:
        r.ma200_rising = bool(ma[200].iloc[-1] > ma[200].iloc[-21])
    win = min(len(df), 250)
    r.high_52w, r.low_52w = float(h.tail(win).max()), float(l.tail(win).min())
    r.pct_from_high = round((r.price / r.high_52w - 1) * 100, 2)
    r.pct_above_low = round((r.price / r.low_52w - 1) * 100, 2)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    r.atr14 = round(float(tr.rolling(14).mean().iloc[-1]), 2)
    r.atr_pct = round(r.atr14 / r.price * 100, 2)
    r.ret_3m, r.ret_6m = _ret(c, 63), _ret(c, 126)
    if index_close is not None and len(index_close) > 0:
        idx = index_close.reindex(df["date"].values, method="ffill") if hasattr(index_close, "reindex") else None
        if idx is not None and idx.notna().sum() > 130:
            idx = idx.reset_index(drop=True)
            i3, i6 = _ret(idx, 63), _ret(idx, 126)
            r.rs_3m = round(r.ret_3m - i3, 2) if r.ret_3m is not None and i3 is not None else None
            r.rs_6m = round(r.ret_6m - i6, 2) if r.ret_6m is not None and i6 is not None else None
    recent = df.tail(50)
    upv = recent["volume"][recent["close"].diff() > 0].sum()
    dnv = recent["volume"][recent["close"].diff() < 0].sum()
    r.ud_volume_ratio = round(float(upv / dnv), 2) if dnv else None
    weekly = df.set_index(pd.to_datetime(df["date"]))["close"].resample("W-FRI").last().dropna()
    r.weekly_rsi = round(float(_rsi(weekly).iloc[-1]), 1) if len(weekly) >= 15 else None
    r.extension_pct = round((r.price / r.ma50 - 1) * 100, 2) if r.ma50 else None

    # 1) long-term trend (25)
    tt = {
        "price_above_ma50": r.ma50 is not None and r.price > r.ma50,
        "ma50_above_ma150": r.ma50 is not None and r.ma150 is not None and r.ma50 > r.ma150,
        "ma150_above_ma200": r.ma150 is not None and r.ma200 is not None and r.ma150 > r.ma200,
        "ma200_rising": bool(r.ma200_rising),
        "price_above_ma200": r.ma200 is not None and r.price > r.ma200,
    }
    r.trend_template = tt
    pts = sum(5 for x in tt.values() if x)
    r.trend_points = pts
    if pts >= 20:
        r.reasons.append(f"✅ Stage-2 uptrend: {pts // 5}/5 trend-template checks met")
    elif pts <= 10:
        r.risks.append(f"⚠️ No long-term uptrend: only {pts // 5}/5 trend-template checks met")
    if r.ma200 is None:
        r.risks.append("⚠️ Under 200 sessions of history: long-term trend only partly assessed")

    # 2) relative strength (20)
    rs = [x for x in (r.rs_3m, r.rs_6m) if x is not None]
    if rs:
        avg = sum(rs) / len(rs)
        rs_pts = 20 if avg >= 10 else 14 if avg >= 0 else 6 if avg >= -10 else 0
        (r.reasons if avg >= 0 else r.risks).append(
            f"{'✅' if avg >= 0 else '⚠️'} Relative strength vs NIFTY: 3m {_pp(r.rs_3m)}, 6m {_pp(r.rs_6m)}")
    else:
        rs_pts = 8
        r.risks.append("⚠️ Relative strength not measured (NIFTY series unavailable)")

    # 3) 52-week position (15)
    if r.pct_from_high >= -15 and r.pct_above_low >= 30:
        pos_pts = 15
        r.reasons.append(f"✅ Near the 52-week high ({r.pct_from_high:+.1f}%), {r.pct_above_low:.0f}% above the low")
    elif r.pct_from_high >= -25:
        pos_pts = 10
    elif r.pct_from_high >= -40:
        pos_pts = 5
    else:
        pos_pts = 0
        r.risks.append(f"⚠️ Far below the 52-week high ({r.pct_from_high:+.1f}%)")

    # 4) setup (20)
    prior_high = float(c.iloc[-31:-1].max()) if len(c) > 31 else None
    vol50 = float(v.iloc[-51:-1].mean()) if len(v) > 51 else float(v.mean())
    ma20_up = len(ma[20].dropna()) > 5 and ma[20].iloc[-1] > ma[20].iloc[-6]
    ma50_up = len(ma[50].dropna()) > 5 and ma[50].iloc[-1] > ma[50].iloc[-6]
    near20 = r.ma20 and 0 <= (r.price / r.ma20 - 1) * 100 <= 3 and ma20_up
    near50 = r.ma50 and 0 <= (r.price / r.ma50 - 1) * 100 <= 3 and ma50_up
    rng10 = float(h.tail(10).max() - l.tail(10).min())
    if r.extension_pct is not None and r.extension_pct > 15:
        r.setup, set_pts = "extended", 4
        r.setup_text = f"Extended {r.extension_pct:.1f}% above MA50: wait for a pullback"
        r.risks.append(f"❌ {r.setup_text}")
    elif prior_high and r.price > prior_high and v.iloc[-1] >= 1.5 * vol50:
        r.setup, set_pts = "breakout", 20
        r.setup_text = f"Breakout above the 30-session high ₹{prior_high:,.2f} on {v.iloc[-1] / vol50:.1f}x average volume"
        r.reasons.append(f"✅ {r.setup_text}")
    elif near20 or near50:
        which = "MA20" if near20 else "MA50"
        lvl = r.ma20 if near20 else r.ma50
        r.setup, set_pts = "pullback", 20
        r.setup_text = f"Pullback to the rising {which} (₹{lvl:,.2f})"
        r.reasons.append(f"✅ {r.setup_text}")
    elif r.atr14 and rng10 <= 2.5 * r.atr14:
        r.setup, set_pts = "tight_base", 12
        r.setup_text = f"Tight 10-session range (₹{rng10:,.2f} ≤ 2.5x ATR): a base is forming; buy on a breakout above ₹{float(h.tail(10).max()):,.2f}"
        r.reasons.append(f"⚡ {r.setup_text}")
    else:
        r.setup, set_pts = "none", 6
        r.setup_text = "No clean entry setup right now"

    # 5) accumulation (10)
    acc = 7 if (r.ud_volume_ratio or 0) >= 1.2 else 4 if (r.ud_volume_ratio or 0) >= 1.0 else 1
    if delivery and delivery.get("health") == "Healthy":
        acc += 3
    elif delivery and delivery.get("health") == "Average":
        acc += 1
    if (r.ud_volume_ratio or 0) >= 1.2:
        r.reasons.append(f"✅ Accumulation: up-day volume {r.ud_volume_ratio}x down-day volume (50 sessions)")
    elif r.ud_volume_ratio is not None and r.ud_volume_ratio < 0.9:
        r.risks.append(f"⚠️ Distribution: up/down volume {r.ud_volume_ratio}")

    # 6) weekly momentum (10)
    w = r.weekly_rsi
    mom = 5 if w is None else 10 if 50 <= w <= 70 else 5 if w > 70 or 40 <= w < 50 else 0
    if w is not None and w > 75:
        r.risks.append(f"⚠️ Weekly RSI {w} overbought")

    r.parts = {"trend": pts, "relative_strength": rs_pts, "52w_position": pos_pts, "setup": set_pts, "accumulation": min(10, acc),
               "weekly_momentum": mom}
    r.score = int(sum(r.parts.values()))

    # levels
    entry = r.price
    if r.setup == "tight_base":           # buy the breakout from the base; pullback/breakout setups enter at the price
        entry = round(float(h.tail(10).max()) * 1.005, 2)
    swing_low = float(l.tail(10).min()) * 0.99
    atr_stop = entry - 2 * (r.atr14 or 0)
    stop = min(swing_low, atr_stop) if r.atr14 else swing_low
    stop = max(stop, entry * (1 - MAX_STOP_PCT / 100))
    if stop >= entry:
        stop = entry * 0.95
    risk = entry - stop
    r.entry, r.stop = round(entry, 2), round(stop, 2)
    r.target1, r.target2 = round(entry + 2 * risk, 2), round(entry + 3 * risk, 2)
    r.risk_pct = round(risk / entry * 100, 2)

    key = signal_key_for_score(r.score)
    if pts <= 10 and r.ma200_rising is False:
        r.signal = "Avoid"
    elif key in ("strong_buy", "buy") and pts >= 15 and r.setup in ("pullback", "breakout", "tight_base"):
        r.signal = "Buy"
    elif key in ("reduce", "sell"):
        r.signal = "Avoid"
    else:
        r.signal = "Watch"
    return r


def position_size(entry: float, stop: float, capital: float, risk_pct: float) -> Dict[str, Any]:
    """Shares so that a stop-out loses risk_pct of capital (cash delivery: never more shares than capital buys)."""
    risk_per_share = entry - stop
    if risk_per_share <= 0 or entry <= 0:
        return {"shares": 0, "value": 0.0, "risk_amount": 0.0}
    by_risk = math.floor(capital * risk_pct / 100 / risk_per_share)
    by_cash = math.floor(capital / entry)
    shares = max(0, min(by_risk, by_cash))
    return {"shares": shares, "value": round(shares * entry, 2), "risk_amount": round(shares * risk_per_share, 2),
            "capital_pct": round(shares * entry / capital * 100, 1) if capital else None, "limited_by_cash": by_cash < by_risk}


def _last(s: pd.Series) -> Optional[float]:
    x = s.iloc[-1]
    return None if x != x else round(float(x), 2)


def _pp(x) -> str:
    return "n/a" if x is None else f"{x:+.1f} pts"
