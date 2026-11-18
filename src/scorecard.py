"""Live scorecard (research/EDGE_RESEARCH.md, L1): how each kind of signal in the diary actually did afterwards.

For each diary row: next session's open -> close 5, 20 and 60 sessions later, minus costs (0.30% stocks, 0.15% ETFs),
and that minus the same day's average stock. Each date's group average first, then mean and t over dates.
Descriptive only: it changes nothing by itself.
"""
from __future__ import annotations

import math
from typing import Dict

import numpy as np
import pandas as pd

HORIZONS = (5, 20, 60)
COST = {"stock": 0.30, "etf": 0.15}
MIN_DATES = 20


def outcomes(diary: pd.DataFrame, prices: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Adds ret_<N> (after costs, %) and ex_<N> (minus the same-day average stock) for N in HORIZONS. NaN = not matured."""
    d = diary.copy()
    for n in HORIZONS:
        d[f"ret_{n}"] = np.nan
    for code, idx in d.groupby("code").groups.items():
        px = prices.get(code)
        if px is None or px.empty:
            continue
        dates = pd.to_datetime(px["date"]).values
        o, c = px["open"].to_numpy(float), px["close"].to_numpy(float)
        sig = pd.to_datetime(d.loc[idx, "date"]).values
        pos = np.searchsorted(dates, sig, side="right") - 1                  # the signal day's bar (or the last before it)
        cost = d.loc[idx, "kind"].map(COST).fillna(0.30).to_numpy()
        for n in HORIZONS:
            ok = (pos >= 0) & (pos + n < len(dates))
            r = np.full(len(idx), np.nan)
            e = np.where(ok, o[np.clip(pos + 1, 0, len(o) - 1)], np.nan)
            x = np.where(ok, c[np.clip(pos + n, 0, len(c) - 1)], np.nan)
            r[ok] = (x[ok] / e[ok] - 1) * 100 - cost[ok]
            d.loc[idx, f"ret_{n}"] = r
    stocks = d["kind"] == "stock"
    for n in HORIZONS:
        avg = d[stocks].groupby("date")[f"ret_{n}"].mean()
        d[f"ex_{n}"] = d[f"ret_{n}"] - d["date"].map(avg)
    return d


def _t(x: pd.Series, lag: int = 0):
    """t of the mean with a Newey-West correction: daily signals held N sessions overlap, so neighbouring days are not
    independent and a plain t would be several times too large."""
    x = x.dropna().to_numpy(float)
    n = len(x)
    if n < 3:
        return None
    e = x - x.mean()
    var = e @ e / n
    for k in range(1, min(lag, n - 1) + 1):
        var += 2 * (1 - k / (lag + 1)) * (e[k:] @ e[:-k]) / n
    return round(float(x.mean() / math.sqrt(var / n)), 2) if var > 0 else None


def min_dates(n: int) -> int:
    return max(MIN_DATES, 2 * n)


def _stats(d: pd.DataFrame, n: int) -> Dict:
    m = d.dropna(subset=[f"ret_{n}"])
    if m.empty:
        return {"signals": 0, "dates": 0, "status": "too early"}
    per = m.groupby("date")[f"ex_{n}"].mean()
    dates = sorted(per.index)
    mid = dates[len(dates) // 2]
    h1, h2 = per[per.index < mid].mean(), per[per.index >= mid].mean()
    t = _t(per, lag=n - 1)
    status = ("too early" if len(per) < min_dates(n) else
              "holding up" if (t or 0) >= 2 and h1 > 0 and h2 > 0 else
              "worse than average" if (t or 0) <= -2 and h1 < 0 and h2 < 0 else "no clear difference")
    return {"signals": int(len(m)), "dates": int(len(per)), "avg_return_pct": round(float(m.groupby("date")[f"ret_{n}"].mean().mean()), 3),
            "vs_average_pct": round(float(per.mean()), 3), "t": t, "first_half": None if math.isnan(h1) else round(float(h1), 3),
            "second_half": None if math.isnan(h2) else round(float(h2), 3), "win_rate_pct": round(float((m[f"ret_{n}"] > 0).mean() * 100), 1),
            "status": status}


def groups(d: pd.DataFrame) -> Dict[str, pd.Series]:
    s = d["kind"] == "stock"
    band = pd.cut(d["short_score"], [-1, 39, 59, 79, 101], labels=["<40", "40-59", "60-79", "80+"]).astype(str)
    q = d[s].groupby("date")["mom_12_1"].rank(pct=True)
    fifth = pd.Series(pd.NA, index=d.index, dtype="object")
    fifth[q.index] = pd.cut(q, [0, .2, .4, .6, .8, 1.0], labels=["1 (weakest)", "2", "3", "4", "5 (strongest)"], include_lowest=True).astype(str)
    mood = pd.cut(d["mood_score"], [-1, 39, 59, 101], labels=["risk-off", "neutral", "risk-on"]).astype(str)
    g = {}
    for v in ("Buy", "Watch", "Avoid"):
        g[f"Swing signal: {v}"] = s & (d["swing_signal"] == v)
    for v in ("80+", "60-79", "40-59", "<40"):
        g[f"Short-term score {v}"] = s & (band == v)
    for v in sorted(d.loc[s & (d["swing_signal"] == "Buy"), "swing_setup"].dropna().unique()):
        g[f"Swing Buy, setup: {v}"] = s & (d["swing_signal"] == "Buy") & (d["swing_setup"] == v)
    for v in ("5 (strongest)", "4", "3", "2", "1 (weakest)"):
        g[f"12-month strength, fifth {v}"] = s & (fifth == v)
    for v in ("risk-on", "neutral", "risk-off"):
        g[f"Swing Buy when the market mood is {v}"] = s & (d["swing_signal"] == "Buy") & (mood == v)
    for v, lab in ((True, "above"), (False, "below")):
        g[f"Swing Buy when the Nifty 50 is {lab} its 200-day average"] = s & (d["swing_signal"] == "Buy") & (d["nifty_above_ma200"] == v)
    return g


def scorecard(diary: pd.DataFrame, prices: Dict[str, pd.DataFrame]) -> Dict:
    if diary.empty:
        return {"days_recorded": 0}
    d = outcomes(diary, prices)
    out = {"start": str(d["date"].min()), "last": str(d["date"].max()), "days_recorded": int(d["date"].nunique()), "rows": int(len(d)),
           "min_dates": MIN_DATES, "min_dates_by_horizon": {str(n): min_dates(n) for n in HORIZONS}, "matured": {}, "horizons": {}}
    for n in HORIZONS:
        out["matured"][str(n)] = int(d.dropna(subset=[f"ret_{n}"])["date"].nunique())
        out["horizons"][str(n)] = {k: _stats(d[m], n) for k, m in groups(d).items() if m.any()}
    return out
