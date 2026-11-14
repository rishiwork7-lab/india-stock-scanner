"""E1 (research/EDGE_RESEARCH.md): does the original short-term score pick stocks that beat the average stock?

Every Friday close, every Nifty 500 stock (today's members) with >= 150 sessions of history is scored with
src/stock_analyzer.py on the last 250 bars. Outcome: next session's open -> close 5 sessions later (primary) and 10
sessions later, minus 0.30% costs, minus the same-date average of all stocks. Groups: score bands and the top 10 scores
per Friday. Each date's group average first, then mean and t over dates; halves split at the middle date.
Run: .venv/bin/python tools/edge_short_term.py   (uses the price cache from tools/swing_check.py)
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
sys.path.insert(0, str(ROOT / "tools"))
from src.stock_analyzer import analyze_stock  # noqa: E402
from swing_check import COST, frames, prices, universe  # noqa: E402

OUT = ROOT / "data" / "edge"
OUT.mkdir(parents=True, exist_ok=True)
EVID = ROOT / "evidence"


def band(s: int) -> str:
    return "80+ Strong Buy" if s >= 80 else "60-79 Buy" if s >= 60 else "40-59 Watch" if s >= 40 else "<40 Reduce/Sell"


def evaluate_symbol(args):
    sym, df, fridays = args
    pos = {d: i for i, d in enumerate(df["date"])}
    rows = []
    for fd in fridays:
        i = pos.get(fd)
        if i is None or i < 150 or i + 10 >= len(df):
            continue
        t = analyze_stock(df.iloc[max(0, i - 249): i + 1].reset_index(drop=True), sym)
        entry = float(df["open"].iloc[i + 1])
        if not entry or entry != entry:
            continue
        r5 = (float(df["close"].iloc[i + 5]) / entry - 1) * 100 - COST
        r10 = (float(df["close"].iloc[i + 10]) / entry - 1) * 100 - COST
        rows.append({"date": fd.strftime("%Y-%m-%d"), "sym": sym, "score": t.signal_score, "r5": r5, "r10": r10})
    return rows


def tstat(x: pd.Series):
    x = x.dropna()
    if len(x) < 3 or x.std() == 0:
        return None
    return round(float(x.mean() / (x.std() / math.sqrt(len(x)))), 2)


def group_stats(df: pd.DataFrame, mask: pd.Series, col: str) -> dict:
    per_date = df[mask].groupby("date")[f"ex_{col}"].mean()
    if per_date.empty:
        return {}
    dates = sorted(per_date.index)
    mid = dates[len(dates) // 2]
    h1, h2 = per_date[per_date.index < mid], per_date[per_date.index >= mid]
    raw = df[mask].groupby("date")[col].mean()
    return {"signals": int(mask.sum()), "dates": len(per_date), "avg_return_pct": round(float(raw.mean()), 3),
            "avg_vs_universe_pct": round(float(per_date.mean()), 3), "t": tstat(per_date),
            "first_half": round(float(h1.mean()), 3), "second_half": round(float(h2.mean()), 3),
            "win_rate_pct": round(float((df[mask][col] > 0).mean() * 100), 1)}


def analyse(df: pd.DataFrame) -> dict:
    for col in ("r5", "r10"):
        df[f"ex_{col}"] = df[col] - df.groupby("date")[col].transform("mean")
    df["band"] = df["score"].map(band)
    df["rank"] = df.groupby("date")["score"].rank(method="first", ascending=False)
    out = {}
    for col, label in (("r5", "5 sessions"), ("r10", "10 sessions")):
        g = {b: group_stats(df, df["band"] == b, col) for b in ("80+ Strong Buy", "60-79 Buy", "40-59 Watch", "<40 Reduce/Sell")}
        g["Buy (60+)"] = group_stats(df, df["score"] >= 60, col)
        g["Top 10 per Friday"] = group_stats(df, df["rank"] <= 10, col)
        out[label] = g
    p = out["5 sessions"]
    verdict = {}
    for k in ("Buy (60+)", "Top 10 per Friday"):
        s = p[k]
        ok = bool(s and s["first_half"] > 0 and s["second_half"] > 0 and (s["t"] or 0) >= 2)
        verdict[k] = "PASS" if ok else "FAIL"
    out["verdict"] = verdict
    out["share_rated_buy_pct"] = round(float((df["score"] >= 60).mean() * 100), 1)
    return out


def main():
    syms = universe()
    data = prices(syms)
    n500 = data["^CRSLDX"]["Close"].dropna()
    if n500.index.tz is not None:
        n500.index = n500.index.tz_localize(None)
    fridays = [d for d in n500.index if d.weekday() == 4]
    jobs = [(s, df, fridays) for s in syms if (df := frames(data, f"{s}.NS")) is not None]
    rows = []
    with ProcessPoolExecutor() as ex:
        for part in ex.map(evaluate_symbol, jobs, chunksize=8):
            rows.extend(part)
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "short_term_rows.csv", index=False)
    n50 = set(pd.read_csv(ROOT / "src" / "india" / "indices" / "nifty50.csv")["Symbol"].str.strip())
    res = {"test": "E1 short-term score", "pre_registered": "research/EDGE_RESEARCH.md", "costs_pct": COST,
           "from": df["date"].min(), "to": df["date"].max(), "signals": len(df), "stocks": df["sym"].nunique(),
           "nifty500": analyse(df.copy()), "nifty50_only": analyse(df[df["sym"].isin(n50)].copy())}
    EVID.mkdir(exist_ok=True)
    (EVID / "edge_short_term.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
