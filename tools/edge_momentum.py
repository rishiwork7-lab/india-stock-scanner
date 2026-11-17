"""E4 (research/EDGE_RESEARCH.md): does a monthly top-20 momentum portfolio beat the average stock and the Nifty 500?

Rule A ranks by the 12-1 month return; rule B by the NSE-style average z-score of 6- and 12-month return / 1-year
volatility. Rebalance signal at each month's last close, trade at the next open, hold until the next rebalance.
Costs 0.25% per side on the value traded. Plus a descriptive real-world check: the Nifty 200 Momentum 30 ETF
(MOMENTUM.NS) against the Nifty 200 index (^CNX200).
Run: .venv/bin/python tools/edge_momentum.py   (uses the price cache from tools/swing_check.py)
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from swing_check import prices, universe  # noqa: E402

OUT = ROOT / "data" / "edge"
EVID = ROOT / "evidence"
TOP, SIDE_COST, MIN_HIST, MIN_TV = 20, 0.0025, 260, 1e8      # ₹10 crore median daily traded value


def panels(data, syms):
    cols = [f"{s}.NS" for s in syms if f"{s}.NS" in data.columns.get_level_values(0)]
    get = lambda f: pd.DataFrame({c[:-3]: data[c][f] for c in cols})  # noqa: E731
    o, c, v = get("Open"), get("Close"), get("Volume")
    for p in (o, c, v):
        if p.index.tz is not None:
            p.index = p.index.tz_localize(None)
    return o, c, v


def tstat(x):
    x = pd.Series(x).dropna()
    return round(float(x.mean() / (x.std() / math.sqrt(len(x)))), 2) if len(x) > 2 and x.std() > 0 else None


def cagr(monthly, months_per_year=12):
    m = pd.Series(monthly).dropna()
    return round(((1 + m).prod() ** (months_per_year / len(m)) - 1) * 100, 2) if len(m) else None


def max_dd(curve):
    c = pd.Series(curve).dropna()
    return round(float((c / c.cummax() - 1).min() * 100), 1)


def scores(c, i, rule):
    hist = c.iloc[: i + 1]
    if rule == "A":
        return c.iloc[i - 21] / c.iloc[i - 252] - 1
    r6, r12 = c.iloc[i] / c.iloc[i - 126] - 1, c.iloc[i] / c.iloc[i - 252] - 1
    vol = np.log(hist.iloc[-253:]).diff().std() * math.sqrt(252)
    z = lambda s: (s - s.mean()) / s.std()  # noqa: E731
    return (z(r6 / vol) + z(r12 / vol)) / 2


def run(o, c, v, n500, rule):
    dates = c.index
    month_ends = [i for i in range(len(dates) - 1) if dates[i].month != dates[i + 1].month]
    tv_med = (c * v).rolling(60, min_periods=40).median()
    hist_n = c.notna().cumsum()
    cf = c.ffill()
    periods, daily, prev_w = [], [], None
    for k in range(len(month_ends) - 1):
        i, i2 = month_ends[k], month_ends[k + 1]
        if i < 252 or i2 + 1 >= len(dates):
            continue
        ok = (hist_n.iloc[i] >= MIN_HIST) & (tv_med.iloc[i] >= MIN_TV) & c.iloc[i].notna() & o.iloc[i + 1].notna()
        elig = ok[ok].index
        s = scores(c[elig], i, rule).dropna().sort_values(ascending=False)
        if len(s) < TOP * 3:
            continue
        pick = list(s.index[:TOP])
        entry = o.iloc[i + 1]
        exit_px = o.iloc[i2 + 1].fillna(cf.iloc[i2])                    # missing open -> last close
        ret = exit_px / entry - 1
        w = pd.Series(1 / TOP, index=pick)
        turnover = (w.reindex(prev_w.index.union(w.index), fill_value=0) - prev_w.reindex(prev_w.index.union(w.index), fill_value=0)).abs().sum() if prev_w is not None else 1.0
        cost = SIDE_COST * turnover
        gross = float(ret[pick].mean())
        net = (1 - cost) * (1 + gross) - 1
        drift = w * (1 + ret[pick])
        prev_w = drift / drift.sum()
        uni = float(ret[elig].mean())
        idx = float(n500.iloc[i2] / n500.iloc[i] - 1)
        # daily marks inside the month for an honest drawdown
        path = (cf[pick].iloc[i + 1: i2 + 1].div(entry[pick]).mean(axis=1)) * (1 - cost)
        daily.append(path.tolist() + [(1 + net)])
        periods.append({"signal": dates[i].strftime("%Y-%m-%d"), "eligible": len(elig), "picks": pick, "turnover": round(turnover, 3),
                        "gross": gross, "net": net, "universe": uni, "nifty500": idx})
    eq, curve = 1.0, [1.0]
    for p in daily:
        curve += [eq * x for x in p[:-1]]
        eq *= p[-1]
        curve.append(eq)
    return pd.DataFrame(periods), curve


def summary(df, curve, n500, start, end):
    ex = df["net"] - df["universe"]
    h = len(df) // 2
    halves = {"first": df.iloc[:h], "second": df.iloc[h:]}
    idx_path = n500.loc[start:end]
    res = {"months": len(df), "from": df["signal"].iloc[0], "to": df["signal"].iloc[-1], "split_after": df["signal"].iloc[h - 1],
           "cagr_pct": cagr(df["net"]), "cagr_gross_pct": cagr(df["gross"]), "universe_cagr_pct": cagr(df["universe"]),
           "nifty500_cagr_pct": cagr(df["nifty500"]), "excess_vs_universe_monthly_pct": round(float(ex.mean() * 100), 3),
           "t_vs_universe": tstat(ex), "max_dd_pct": max_dd(curve), "nifty500_max_dd_pct": max_dd(idx_path),
           "avg_turnover_pct": round(float(df["turnover"].iloc[1:].mean() * 50), 1),          # one-way, % of the portfolio
           "win_months_vs_universe_pct": round(float((ex > 0).mean() * 100), 1), "halves": {}}
    for name, d in halves.items():
        e = d["net"] - d["universe"]
        res["halves"][name] = {"from": d["signal"].iloc[0], "to": d["signal"].iloc[-1], "cagr_pct": cagr(d["net"]),
                               "universe_cagr_pct": cagr(d["universe"]), "nifty500_cagr_pct": cagr(d["nifty500"]),
                               "excess_vs_universe_monthly_pct": round(float(e.mean() * 100), 3), "t": tstat(e)}
    # approximate tax: 20% short-term on each calendar year's net gain (losses carried forward) vs 12.5% on the index gain
    yearly = df.assign(y=df["signal"].str[:4]).groupby("y")["net"].apply(lambda m: (1 + m).prod() - 1)
    val, carry = 1.0, 0.0
    for r in yearly:
        gain = val * r
        taxable = max(0.0, gain + carry)
        carry = min(0.0, gain + carry)
        val += gain - 0.20 * taxable
    years = len(df) / 12
    idx_gain = (1 + df["nifty500"]).prod() - 1
    res["approx_after_tax_cagr_pct"] = round((val ** (1 / years) - 1) * 100, 2)
    res["nifty500_after_tax_cagr_pct"] = round(((1 + idx_gain * (1 - 0.125)) ** (1 / years) - 1) * 100, 2)
    hs = res["halves"]
    checks = {"beats_universe_both_halves": hs["first"]["excess_vs_universe_monthly_pct"] > 0 and hs["second"]["excess_vs_universe_monthly_pct"] > 0,
              "t_at_least_2": (res["t_vs_universe"] or 0) >= 2,
              "beats_nifty500_both_halves": hs["first"]["cagr_pct"] > hs["first"]["nifty500_cagr_pct"] and hs["second"]["cagr_pct"] > hs["second"]["nifty500_cagr_pct"],
              "drawdown_within_5pp": res["max_dd_pct"] >= res["nifty500_max_dd_pct"] - 5}
    checks = {k: bool(x) for k, x in checks.items()}
    res["checks"] = checks
    res["verdict"] = "PASS" if all(checks.values()) else "FAIL"
    return res


def real_world_check():
    import yfinance as yf
    d = yf.download(["MOMENTUM.NS", "^CNX200"], start="2022-08-19", auto_adjust=True, progress=False)["Close"].dropna()
    if d.index.tz is not None:
        d.index = d.index.tz_localize(None)
    m = d.resample("ME").last().pct_change().dropna()
    m = m.iloc[1:]                                                            # first full month only
    ex = m["MOMENTUM.NS"] - m["^CNX200"]
    h = len(m) // 2
    part = lambda x: {"from": x.index[0].strftime("%Y-%m"), "to": x.index[-1].strftime("%Y-%m"), "etf_cagr_pct": cagr(x["MOMENTUM.NS"]),  # noqa: E731
                      "nifty200_cagr_pct": cagr(x["^CNX200"]), "t": tstat(x["MOMENTUM.NS"] - x["^CNX200"])}
    return {"etf": "Aditya Birla Sun Life Nifty 200 Momentum 30 ETF (MOMENTUM.NS)", "benchmark": "Nifty 200 price index (^CNX200)",
            "months": len(m), **part(m), "excess_monthly_pct": round(float(ex.mean() * 100), 3),
            "etf_max_dd_pct": max_dd(d["MOMENTUM.NS"]), "nifty200_max_dd_pct": max_dd(d["^CNX200"]),
            "halves": {"first": part(m.iloc[:h]), "second": part(m.iloc[h:])}}


def main():
    syms = universe()
    data = prices(syms)
    o, c, v = panels(data, syms)
    n500 = data["^CRSLDX"]["Close"].dropna()
    if n500.index.tz is not None:
        n500.index = n500.index.tz_localize(None)
    n500 = n500.reindex(c.index).ffill()
    res = {"test": "E4 momentum portfolio", "pre_registered": "research/EDGE_RESEARCH.md", "top_n": TOP, "cost_per_side_pct": SIDE_COST * 100,
           "min_traded_value_cr": MIN_TV / 1e7, "rules": {}}
    for rule, label in (("A", "12-1 month return"), ("B", "NSE-style 6m+12m risk-adjusted")):
        df, curve = run(o, c, v, n500, rule)
        df.drop(columns="picks").to_csv(OUT / f"momentum_{rule}.csv", index=False)
        s = summary(df, curve, n500, df["signal"].iloc[0], c.index[-1])
        s["label"] = label
        s["distinct_stocks_held"] = len({x for p in df["picks"] for x in p})
        res["rules"][rule] = s
    try:
        res["real_world_check"] = real_world_check()
    except Exception as e:                                                     # descriptive only
        res["real_world_check"] = {"error": str(e)}
    res["verdict"] = {k: v["verdict"] for k, v in res["rules"].items()}
    EVID.mkdir(exist_ok=True)
    (EVID / "edge_momentum.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
