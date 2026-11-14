"""Historical check of the swing score (src/swing_analyzer.py). Rules fixed before the first run (2026-11-14).

Question: do stocks the swing analyser rates "Buy" do better over the next weeks than (a) the Nifty 500 index and (b) the
average stock in the same universe on the same date, after Indian delivery charges?

Universe   today's Nifty 500 members (NSE list). This is survivorship-biased (stocks that fell out are missing), so the
           main yardstick is (b): the average member on the same date, which carries the same bias.
Data       Yahoo Finance daily bars (split/dividend adjusted), ~4.5 years; NIFTY 50 for relative strength; Nifty 500
           index (^CRSLDX) as benchmark.
Dates      every Friday close with at least 260 sessions of history before it and 40 after it.
Entry      next session's open. Costs 0.30% per round trip (STT 0.1% each side, exchange/SEBI/stamp/GST/DP).
Outcomes   (1) fixed 20-session hold: close 20 sessions after entry
           (2) managed trade as the analyser prescribes: exit at the stop (gap-aware), at target 2 (3R), or after
               40 sessions at the close
Pass rule  "Buy" average 20-session return minus the same-date universe average > 0 in both halves of the period, with a
           t-statistic >= 2 computed over dates (each date's Buy signals averaged first, so busy weeks don't dominate).
Run: .venv/bin/python tools/swing_check.py   (downloads ~500 tickers; a few minutes)
"""
from __future__ import annotations

import json
import math
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.swing_analyzer import analyze_swing  # noqa: E402

OUT = ROOT / "data" / "swing_check"
OUT.mkdir(parents=True, exist_ok=True)
COST = 0.30
H = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"}


def universe():
    import requests
    f = OUT / "nifty500.csv"
    if not f.exists():
        r = requests.get("https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv", headers=H, timeout=30)
        r.raise_for_status()
        f.write_text(r.text)
    return [s.strip() for s in pd.read_csv(f)["Symbol"].tolist()]


def prices(symbols):
    f = OUT / "prices.pkl"
    if f.exists():
        return pd.read_pickle(f)
    import yfinance as yf
    tickers = [f"{s}.NS" for s in symbols] + ["^NSEI", "^CRSLDX"]
    data = yf.download(tickers, period="5y", auto_adjust=True, group_by="ticker", threads=True, progress=False)
    data.to_pickle(f)
    return data


def frames(data, sym):
    try:
        d = data[sym].dropna(subset=["Close"])
    except KeyError:
        return None
    if len(d) < 300:
        return None
    return pd.DataFrame({"date": d.index.tz_localize(None) if d.index.tz else d.index, "open": d["Open"].values, "high": d["High"].values,
                         "low": d["Low"].values, "close": d["Close"].values, "volume": d["Volume"].values})


def evaluate_symbol(args):
    sym, df, nifty, fridays = args
    rows = []
    dates = list(df["date"])
    pos = {d: i for i, d in enumerate(dates)}
    for fd in fridays:
        i = pos.get(fd)
        if i is None or i < 260 or i + 41 >= len(df):
            continue
        hist = df.iloc[: i + 1]
        r = analyze_swing(hist, sym, nifty.loc[:fd])
        if not r.ok:
            continue
        e = float(df["open"].iloc[i + 1])
        if not e or e != e:
            continue
        fwd20 = (float(df["close"].iloc[i + 21]) / e - 1) * 100 - COST
        exit_px, reason = float(df["close"].iloc[i + 41]), "time"
        for j in range(i + 1, i + 41):
            o, lo, hi = float(df["open"].iloc[j]), float(df["low"].iloc[j]), float(df["high"].iloc[j])
            if lo <= r.stop:
                exit_px, reason = (min(o, r.stop) if j > i + 1 else r.stop), "stop"
                break
            if hi >= r.target2:
                exit_px, reason = (max(o, r.target2) if j > i + 1 else r.target2), "target"
                break
        managed = (exit_px / e - 1) * 100 - COST
        rows.append({"date": fd.strftime("%Y-%m-%d"), "sym": sym, "score": r.score, "signal": r.signal, "setup": r.setup,
                     "fwd20": round(fwd20, 3), "managed": round(managed, 3), "exit": reason})
    return rows


def tstat(xs):
    n = len(xs)
    if n < 3:
        return None
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return round(m / sd * math.sqrt(n), 2) if sd else None


def main():
    syms = universe()
    data = prices(syms)
    nifty = data["^NSEI"]["Close"].dropna()
    nifty.index = nifty.index.tz_localize(None) if nifty.index.tz else nifty.index
    n500 = data["^CRSLDX"]["Close"].dropna()
    n500.index = n500.index.tz_localize(None) if n500.index.tz else n500.index
    cal = list(n500.index)
    fridays = [d for d in cal if d.weekday() == 4]
    jobs = []
    for s in syms:
        df = frames(data, f"{s}.NS")
        if df is not None:
            jobs.append((s, df, nifty, fridays))
    print(f"{len(jobs)} of {len(syms)} Nifty 500 stocks have enough history", flush=True)
    rows = []
    with ProcessPoolExecutor() as ex:
        for part in ex.map(evaluate_symbol, jobs, chunksize=8):
            rows.extend(part)
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "signals.csv", index=False)
    # benchmarks per date
    idx_pos = {d.strftime("%Y-%m-%d"): i for i, d in enumerate(cal)}

    def idx_fwd(d):
        i = idx_pos.get(d)
        return (float(n500.iloc[i + 21]) / float(n500.iloc[i + 1]) - 1) * 100 if i is not None and i + 21 < len(n500) else None
    uni = df.groupby("date")["fwd20"].mean()
    df["vs_universe"] = df["fwd20"] - df["date"].map(uni)
    df["vs_index"] = df["fwd20"] - df["date"].map(idx_fwd)
    mid = sorted(df["date"].unique())[len(df["date"].unique()) // 2]
    out = {"signals": len(df), "dates": int(df["date"].nunique()), "from": df["date"].min(), "to": df["date"].max(), "split": mid, "groups": {}}
    for name, g in [("ALL stocks (universe average)", df)] + [(f"signal={s}", df[df.signal == s]) for s in ("Buy", "Watch", "Avoid")] + \
            [(f"score {a}-{b}", df[(df.score >= a) & (df.score <= b)]) for a, b in ((80, 100), (60, 79), (40, 59), (0, 39))] + \
            [(f"Buy + setup={s}", df[(df.signal == "Buy") & (df.setup == s)]) for s in ("pullback", "breakout", "tight_base")]:
        if g.empty:
            continue
        per_date = g.groupby("date")["vs_universe"].mean()
        first, second = per_date[per_date.index < mid], per_date[per_date.index >= mid]
        out["groups"][name] = {
            "n": len(g), "dates": len(per_date), "avg_fwd20_after_cost": round(g["fwd20"].mean(), 2), "win_rate_fwd20": round((g["fwd20"] > 0).mean() * 100, 1),
            "avg_vs_nifty500": round(g["vs_index"].mean(), 2), "avg_vs_universe": round(g["vs_universe"].mean(), 2),
            "vs_universe_first_half": round(first.mean(), 2) if len(first) else None, "vs_universe_second_half": round(second.mean(), 2) if len(second) else None,
            "t_vs_universe_by_date": tstat(list(per_date)), "avg_managed_after_cost": round(g["managed"].mean(), 2),
            "managed_exits": g["exit"].value_counts().to_dict(),
        }
    b = out["groups"].get("signal=Buy", {})
    out["passed"] = bool(b and (b["vs_universe_first_half"] or 0) > 0 and (b["vs_universe_second_half"] or 0) > 0 and (b["t_vs_universe_by_date"] or 0) >= 2)
    (OUT / "summary.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
