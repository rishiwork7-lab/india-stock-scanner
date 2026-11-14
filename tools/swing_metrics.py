"""Accuracy matrix for the swing plans (src/swing_analyzer.py). Metrics fixed before the first run (2026-11-14).

Same universe, data, dates and costs as tools/swing_check.py: today's Nifty 500 members (survivorship-biased), Yahoo
adjusted daily bars, every Friday close Dec 2022 - Sep 2026, entry at the next session's open, 0.30% round-trip charges.

For every signal the plan's own levels are walked forward for up to 40 sessions (8 weeks), bar by bar:
  stop hit first            -> loss of 1R (gaps fill at the open, so a loss can exceed 1R)
  target 1 (2R) hit first   -> "T1 hit"; target 2 (3R) may follow later
  neither within 40 sessions -> "timed out" at the close
A bar that touches both the stop and a target is counted as the stop (the cautious assumption; daily bars cannot tell the
order). After target 1, trade B's remaining half starts on the next session.

Three ways to trade the same plan:
  A  all out at target 2 (3R), the stop, or 40 sessions
  B  half out at target 1, stop moved to entry; rest out at target 2, the stop, or 40 sessions
  C  all out at target 1 (2R), the stop, or 40 sessions
Reported per group (signal Buy / Watch / Avoid / all; Buy by setup; Buy by year):
  T1-before-stop %, T2-before-stop %, stop-first %, timed-out %, average sessions held,
  win rate (return > 0 after costs), average return % after costs, average R, and the Nifty 500 index return over the
  same holding days, so the difference is the edge over simply holding the index.
Nothing is tuned from these results.
Run: .venv/bin/python tools/swing_metrics.py   (uses the price cache written by swing_check.py)
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from src.swing_analyzer import analyze_swing  # noqa: E402
from swing_check import COST, OUT, frames, prices, universe  # noqa: E402

HOLD = 40


def run_trade(df, i, entry, stop, target, start_k=1):
    """Hold from session i+start_k to i+HOLD. Exit at the stop (gap-aware), the target (gap-aware) or the last close.
    The stop is checked first on every bar. Returns (exit_price, sessions_held, reason)."""
    for k in range(start_k, HOLD + 1):
        j = i + k
        o, lo, hi = float(df["open"].iloc[j]), float(df["low"].iloc[j]), float(df["high"].iloc[j])
        if lo <= stop:
            return (stop if (k == 1 or o > stop) else o), k, "stop"
        if hi >= target:
            return (target if (k == 1 or o < target) else o), k, "target"
    return float(df["close"].iloc[i + HOLD]), HOLD, "time"


def walk(df, i, entry, stop, t1, t2):
    a_px, a_days, a_why = run_trade(df, i, entry, stop, t2)            # A: all out at target 2
    c_px, c_days, c_why = run_trade(df, i, entry, stop, t1)            # C: all out at target 1
    if c_why == "target":                                               # B: half at T1, rest with a breakeven stop
        rest_px, rest_days, _ = run_trade(df, i, entry, entry, t2, start_k=c_days + 1) if c_days < HOLD else (c_px, c_days, "time")
        b_px, b_days = 0.5 * c_px + 0.5 * rest_px, rest_days
    else:
        b_px, b_days = c_px, c_days
    ret = lambda px: (px / entry - 1) * 100 - COST  # noqa: E731
    risk = entry - stop
    return {"hit_t1": c_why == "target", "hit_t2": a_why == "target", "stopped": c_why == "stop", "timed_out": c_why == "time",
            "days": b_days, "days_a": a_days, "days_c": c_days,
            "ret_a": ret(a_px), "ret_b": ret(b_px), "ret_c": ret(c_px),
            "r_a": (a_px - entry) / risk, "r_b": (b_px - entry) / risk, "r_c": (c_px - entry) / risk}


def evaluate_symbol(args):
    sym, df, nifty, fridays, idx_close = args
    rows = []
    pos = {d: i for i, d in enumerate(df["date"])}
    for fd in fridays:
        i = pos.get(fd)
        if i is None or i < 260 or i + HOLD + 1 >= len(df):
            continue
        r = analyze_swing(df.iloc[: i + 1], sym, nifty.loc[:fd])
        if not r.ok or not r.stop or not r.target1:
            continue
        entry = float(df["open"].iloc[i + 1])
        if not entry or entry != entry or entry <= r.stop:
            continue
        # levels re-anchored to the actual entry, keeping the plan's risk in rupees per share
        risk = r.entry - r.stop
        stop, t1, t2 = entry - risk, entry + 2 * risk, entry + 3 * risk
        w = walk(df, i, entry, stop, t1, t2)
        d0, d1 = df["date"].iloc[i], df["date"].iloc[min(i + w["days"], len(df) - 1)]
        try:
            idx_ret = (float(idx_close.loc[:d1].iloc[-1]) / float(idx_close.loc[:d0].iloc[-1]) - 1) * 100
        except (IndexError, KeyError):
            idx_ret = None
        rows.append({"date": fd.strftime("%Y-%m-%d"), "sym": sym, "signal": r.signal, "setup": r.setup, "score": r.score,
                     "risk_pct": round(risk / entry * 100, 2), "idx_ret": idx_ret, **w})
    return rows


def summarise(g: pd.DataFrame) -> dict:
    if g.empty:
        return {}
    pct = lambda s: round(float(s.mean()) * 100, 1)  # noqa: E731
    out = {"n": len(g), "avg_risk_pct": round(g["risk_pct"].mean(), 2), "t1_before_stop_pct": pct(g["hit_t1"]),
           "t2_before_stop_pct": pct(g["hit_t2"]), "stop_first_pct": pct(g["stopped"]),
           "timed_out_pct": pct(g["timed_out"]), "avg_days_held_B": round(g["days"].mean(), 1),
           "avg_index_return_same_days": round(g["idx_ret"].mean(), 2)}
    for k, label in (("a", "A_all_at_T2"), ("b", "B_half_T1_half_T2"), ("c", "C_all_at_T1")):
        rr = g[f"ret_{k}"]
        out[label] = {"win_rate_pct": pct(rr > 0), "avg_return_pct": round(rr.mean(), 2), "avg_R": round(g[f"r_{k}"].mean(), 2),
                      "vs_nifty500_pct": round((rr - g["idx_ret"]).mean(), 2)}
    return out


def main():
    data = prices(universe())
    nifty = data["^NSEI"]["Close"].dropna()
    n500 = data["^CRSLDX"]["Close"].dropna()
    for s in (nifty, n500):
        if s.index.tz is not None:
            s.index = s.index.tz_localize(None)
    fridays = [d for d in n500.index if d.weekday() == 4]
    jobs = [(s, df, nifty, fridays, n500) for s in universe() if (df := frames(data, f"{s}.NS")) is not None]
    rows = []
    with ProcessPoolExecutor() as ex:
        for part in ex.map(evaluate_symbol, jobs, chunksize=8):
            rows.extend(part)
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "metrics_rows.csv", index=False)
    groups = {"ALL signals": df}
    for s in ("Buy", "Watch", "Avoid"):
        groups[f"signal={s}"] = df[df.signal == s]
    for s in ("pullback", "breakout", "tight_base"):
        groups[f"Buy + {s}"] = df[(df.signal == "Buy") & (df.setup == s)]
    buy = df[df.signal == "Buy"]
    for y in sorted(buy["date"].str[:4].unique()):
        groups[f"Buy in {y}"] = buy[buy["date"].str[:4] == y]
    out = {"signals": len(df), "from": df["date"].min(), "to": df["date"].max(), "matrix": {k: summarise(g) for k, g in groups.items()}}
    (OUT / "metrics.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
