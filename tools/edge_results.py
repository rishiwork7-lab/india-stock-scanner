"""E2 (research/EDGE_RESEARCH.md): do swing trades that run into company results do worse?

Trades: the swing plans in data/swing_check/metrics_rows.csv (tools/swing_metrics.py; method A: all out at target 2, the
stop or 40 sessions, after costs). Results dates: NSE board-meeting history per stock (purpose contains "result"),
cached in data/edge/results_dates.json. Rule tested: skip the entry when results fall within the first 10 sessions of
the trade (after the signal Friday, up to the 10th session after it).
Run: .venv/bin/python tools/edge_results.py
"""
from __future__ import annotations

import json
import math
import sys
import time
from bisect import bisect_right
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from data_provider.nse import NSEApi  # noqa: E402
from src.events import is_results  # noqa: E402
from swing_check import prices, universe  # noqa: E402

OUT = ROOT / "data" / "edge"
OUT.mkdir(parents=True, exist_ok=True)
EVID = ROOT / "evidence"
WINDOW = 10


def results_dates(symbols) -> dict:
    f = OUT / "results_dates.json"
    have = json.loads(f.read_text()) if f.exists() else {}
    api = NSEApi()
    todo = [s for s in symbols if s not in have]
    for k, s in enumerate(todo):
        try:
            rows = api.event_calendar(s)
            have[s] = sorted({r["date"].isoformat() for r in rows if is_results(r["purpose"], r["desc"])})
        except Exception as e:
            print(f"{s}: {e}", flush=True)
            time.sleep(3)
            continue
        if k % 25 == 0:
            f.write_text(json.dumps(have))
            print(f"results dates {k + 1}/{len(todo)}", flush=True)
        time.sleep(0.25)
    f.write_text(json.dumps(have))
    return have


def welch_t(a: pd.Series, b: pd.Series):
    va, vb = a.var() / len(a), b.var() / len(b)
    return round(float((a.mean() - b.mean()) / math.sqrt(va + vb)), 2) if va + vb > 0 else None


def describe(g: pd.DataFrame) -> dict:
    r = g["ret_a"]
    return {"trades": len(g), "avg_return_pct": round(float(r.mean()), 3), "median_pct": round(float(r.median()), 3),
            "worst_5pct_pct": round(float(r.quantile(0.05)), 2), "stop_hit_pct": round(float((g["r_a"] <= -0.999).mean() * 100), 1),
            "std_pct": round(float(r.std()), 2)}


def compare(df: pd.DataFrame) -> dict:
    skip, keep = df[df["results_soon"]], df[~df["results_soon"]]
    s, k = describe(skip), describe(keep)
    t = welch_t(skip["ret_a"], keep["ret_a"])
    worse_avg = t is not None and t <= -2
    worse_tail = s["worst_5pct_pct"] <= k["worst_5pct_pct"] - 1 and (t is None or t < 2)
    return {"skipped_by_rule": s, "kept": k, "t_skipped_minus_kept": t,
            "verdict": "PASS" if (worse_avg or worse_tail) else "FAIL",
            "why": "skipped trades have a lower average (t<=-2)" if worse_avg else
                   "skipped trades have a worse 5% tail by >=1 pt without a higher average" if worse_tail else
                   "skipped trades are not clearly worse"}


def main():
    rows = pd.read_csv(ROOT / "data" / "swing_check" / "metrics_rows.csv")
    data = prices(universe())
    cal = data["^CRSLDX"]["Close"].dropna().index
    cal = [d.date() if hasattr(d, "date") else d for d in (cal.tz_localize(None) if cal.tz is not None else cal)]
    cal_s = [d.isoformat() for d in cal]
    dates = results_dates(sorted(rows["sym"].unique()))
    end_of_window = {}
    for d in rows["date"].unique():
        i = bisect_right(cal_s, d) - 1
        end_of_window[d] = cal_s[min(i + WINDOW, len(cal_s) - 1)]

    def soon(r):
        ds = dates.get(r.sym)
        if ds is None:
            return None
        end = end_of_window[r.date]
        j = bisect_right(ds, r.date)
        return j < len(ds) and ds[j] <= end

    rows["results_soon"] = [soon(r) for r in rows.itertuples()]
    known = rows[rows["results_soon"].notna()].copy()
    known["results_soon"] = known["results_soon"].astype(bool)
    res = {"test": "E2 results inside the first 10 sessions of a swing trade", "pre_registered": "research/EDGE_RESEARCH.md",
           "trades": len(known), "trades_without_results_history": int(rows["results_soon"].isna().sum()),
           "stocks_with_dates": sum(1 for v in dates.values() if v), "share_with_results_soon_pct": round(float(known["results_soon"].mean() * 100), 1),
           "all_signals": compare(known), "buy_signals": compare(known[known["signal"] == "Buy"])}
    EVID.mkdir(exist_ok=True)
    (EVID / "edge_results.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
