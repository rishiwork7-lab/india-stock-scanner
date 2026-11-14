"""E3 (research/EDGE_RESEARCH.md): does holding the strongest sectors beat holding the Nifty 50?

12 NSE sector indices (Yahoo, price indices, since 2011). Month-end: rank by the average of the 3- and 6-month returns
(63 and 126 sessions), hold the top 3 equal weight for the next month. Rule B: hold cash (6%/yr) instead when the Nifty 50
closes below its 200-day average on the rebalance day. Costs 0.15% per side on traded value. Benchmark: Nifty 50 index.
Pass: higher annual return than the Nifty 50 in both halves, t >= 2 on monthly excess returns, max drawdown no more than
5 points worse. Trials: 2 (A, B).
Run: .venv/bin/python tools/edge_sector_rotation.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "edge"
OUT.mkdir(parents=True, exist_ok=True)
EVID = ROOT / "evidence"
SECTORS = {"^NSEBANK": "Bank", "^CNXPSUBANK": "PSU Bank", "^CNXIT": "IT", "^CNXPHARMA": "Pharma", "^CNXAUTO": "Auto",
           "^CNXFMCG": "FMCG", "^CNXMETAL": "Metal", "^CNXREALTY": "Realty", "^CNXENERGY": "Energy", "^CNXPSE": "PSE",
           "^CNXINFRA": "Infra", "NIFTY_FIN_SERVICE.NS": "Financial Services"}
BENCH = "^NSEI"
COST_SIDE = 0.0015
CASH_YIELD = 0.06
TOP = 3


def load() -> pd.DataFrame:
    f = OUT / "sector_indices.pkl"
    if f.exists():
        return pd.read_pickle(f)
    import yfinance as yf
    raw = yf.download(list(SECTORS) + [BENCH], start="2010-01-01", auto_adjust=True, progress=False, group_by="ticker")
    close = pd.DataFrame({t: raw[t]["Close"] for t in list(SECTORS) + [BENCH]})
    close.index = close.index.tz_localize(None) if close.index.tz is not None else close.index
    close.to_pickle(f)
    return close


def clean(close: pd.DataFrame):
    bad = []
    out = close.copy()
    for c in out:
        r = out[c].pct_change()
        for d in r[r.abs() > 0.25].index:
            bad.append({"series": c, "date": d.strftime("%Y-%m-%d"), "move_pct": round(float(r[d] * 100), 1)})
            out.loc[d, c] = np.nan
        out[c] = out[c].ffill()
    return out, bad


def max_dd(eq: pd.Series) -> float:
    return round(float((eq / eq.cummax() - 1).min() * 100), 1)


def cagr(eq: pd.Series) -> float:
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    return round(float((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1) * 100, 2) if yrs > 0 else float("nan")


def backtest(close: pd.DataFrame, cash_filter: bool) -> pd.DataFrame:
    """Monthly: returns per month for the strategy and the benchmark, plus holdings."""
    daily = close.dropna(how="all")
    month_ends = daily.groupby(daily.index.to_period("M")).tail(1).index
    nifty = daily[BENCH]
    ma200 = nifty.rolling(200).mean()
    rows, prev = [], set()
    for a, b in zip(month_ends[:-1], month_ends[1:]):
        hist = daily.loc[:a]
        if len(hist) < 200:
            continue
        mom = {}
        for t in SECTORS:
            s = hist[t].dropna()
            if len(s) > 127:
                mom[t] = ((s.iloc[-1] / s.iloc[-64] - 1) + (s.iloc[-1] / s.iloc[-127] - 1)) / 2
        if len(mom) < TOP + 2:
            continue
        risk_off = cash_filter and nifty.loc[a] < ma200.loc[a]
        hold = set() if risk_off else set(sorted(mom, key=mom.get, reverse=True)[:TOP])
        # turnover: fraction of the portfolio that changes (equal weights), both sides charged
        w_new = {t: 1 / TOP for t in hold}
        w_old = {t: 1 / TOP for t in prev}
        turnover = sum(abs(w_new.get(t, 0) - w_old.get(t, 0)) for t in set(w_new) | set(w_old))
        if hold:
            gross = float(np.mean([daily.loc[b, t] / daily.loc[a, t] - 1 for t in hold]))
        else:
            gross = CASH_YIELD / 12
        net = gross - turnover * COST_SIDE
        rows.append({"start": a, "end": b, "ret": net, "bench": float(nifty.loc[b] / nifty.loc[a] - 1), "turnover": turnover,
                     "holdings": ",".join(SECTORS[t] for t in sorted(hold)) or "cash"})
        prev = hold
    return pd.DataFrame(rows).set_index("end")


def summarise(m: pd.DataFrame) -> dict:
    eq, bq = (1 + m["ret"]).cumprod(), (1 + m["bench"]).cumprod()
    ex = m["ret"] - m["bench"]
    t = float(ex.mean() / (ex.std() / math.sqrt(len(ex)))) if ex.std() > 0 else None
    mid = m.index[len(m) // 2]
    halves = {}
    for name, part in (("first_half", m[m.index <= mid]), ("second_half", m[m.index > mid])):
        halves[name] = {"from": part["start"].iloc[0].strftime("%Y-%m"), "to": part.index[-1].strftime("%Y-%m"),
                        "strategy_cagr_pct": cagr((1 + part["ret"]).cumprod()), "nifty_cagr_pct": cagr((1 + part["bench"]).cumprod())}
    years = {}
    for y, part in m.groupby(m.index.year):
        years[str(y)] = {"strategy_pct": round(float(((1 + part["ret"]).prod() - 1) * 100), 1),
                         "nifty_pct": round(float(((1 + part["bench"]).prod() - 1) * 100), 1)}
    s = {"months": len(m), "from": m["start"].iloc[0].strftime("%Y-%m-%d"), "to": m.index[-1].strftime("%Y-%m-%d"),
         "strategy_cagr_pct": cagr(eq), "nifty_cagr_pct": cagr(bq), "strategy_max_dd_pct": max_dd(eq), "nifty_max_dd_pct": max_dd(bq),
         "avg_monthly_excess_pct": round(float(ex.mean() * 100), 3), "t_monthly_excess": round(t, 2) if t is not None else None,
         "months_beating_nifty_pct": round(float((ex > 0).mean() * 100), 1), "avg_turnover_per_month": round(float(m["turnover"].mean()), 2),
         "cash_months": int((m["holdings"] == "cash").sum()), **halves, "by_year": years, "latest_holdings": m["holdings"].iloc[-1]}
    ok = (halves["first_half"]["strategy_cagr_pct"] > halves["first_half"]["nifty_cagr_pct"]
          and halves["second_half"]["strategy_cagr_pct"] > halves["second_half"]["nifty_cagr_pct"]
          and (t or 0) >= 2 and s["strategy_max_dd_pct"] >= s["nifty_max_dd_pct"] - 5)
    s["verdict"] = "PASS" if ok else "FAIL"
    return s


def main():
    close, bad = clean(load())
    res = {"test": "E3 sector rotation (top 3 of 12 by 3+6 month momentum, monthly)", "pre_registered": "research/EDGE_RESEARCH.md",
           "trials": 2, "cost_per_side_pct": COST_SIDE * 100, "bad_prints_removed": bad,
           "note_on_bad_prints": "A flagged move is set to the previous close for one day only, so a real jump moves to the next "
                                 "session; check each flagged move by hand.",
           "series_start": {SECTORS.get(c, "Nifty 50"): close[c].first_valid_index().strftime("%Y-%m-%d") for c in close}}
    for key, flt in (("A_top3", False), ("B_top3_cash_when_nifty_below_200dma", True)):
        m = backtest(close, flt)
        m.to_csv(OUT / f"sector_{key}.csv")
        res[key] = summarise(m)
    EVID.mkdir(exist_ok=True)
    (EVID / "edge_sector_rotation.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps({k: v for k, v in res.items() if k not in ("bad_prints_removed",)}, indent=1, default=str)[:6000])
    print("bad prints:", bad)


if __name__ == "__main__":
    main()
