"""Model portfolio: five holdings for ₹1 lakh, and a paper record that follows them.

Rules (written down 2026-11-17 in research/EDGE_RESEARCH.md, "P1", before any result was seen):
  40%    NIFTYBEES (Nifty 50 ETF): the core. Tests E1-E4 found nothing that reliably beats the index.
  10%    GOLDBEES (gold ETF): a cushion, because gold often holds up when shares fall. Not a return bet.
  3x16.7% Nifty 500 stocks that pass every check: swing signal "Buy" (never "Avoid", the one filter with proof),
         >= ₹10 crore traded a day, no results within the next 5 sessions, daily range (ATR) <= 5% of the price, not
         stale, at most one per industry; ranked by 12-month return excluding the last month (E4 rule A).
  A stock slot that no stock fills stays in cash.

Paper record: starts with ₹1,00,000 on the first build, buys whole shares at that day's close, and rebalances on the first
build of each new month. Held stocks are kept while they stay among the 10 best that pass the checks; others are sold.
Charges: 0.25% per side for stocks, 0.15% per side for ETFs. Yardstick: ₹1,00,000 put into NIFTYBEES on the same day
and simply held.
"""
from __future__ import annotations

import copy
import math
from typing import Dict, List, Optional

CAPITAL = 100_000.0
CORE = {"code": "NIFTYBEES", "weight": 0.40, "role": "Core",
        "why": "The Nifty 50 in one ETF. Our tests (E1–E4) found nothing that reliably beats the index, so it gets the biggest share."}
CUSHION = {"code": "GOLDBEES", "weight": 0.10, "role": "Cushion",
           "why": "Gold often holds up when shares fall, so it softens bad months. A cushion, not a return bet."}
STOCK_SLOTS, STOCK_WEIGHT = 3, 0.50 / 3
MIN_TV_CR, MAX_ATR_PCT, RESULTS_BLOCK, KEEP_RANK = 10.0, 5.0, 5, 10
COST = {"stock": 0.0025, "etf": 0.0015}
MIN_TRADE = 1000.0                        # rebalance trades smaller than this are skipped
BENCH = "NIFTYBEES"


def momentum_12_1(r: Dict) -> Optional[float]:
    y, m = r.get("ret_1y"), r.get("ret_1m")
    if y is None or m is None:
        return None
    return round(((1 + y / 100) / (1 + m / 100) - 1) * 100, 2)


def _results_in(r: Dict) -> Optional[int]:
    nr = (r.get("events") or {}).get("next_results") or {}
    return nr.get("sessions")


def candidates(scan: Dict) -> List[Dict]:
    """Stocks that pass every check, best first, at most one per industry."""
    out, seen = [], set()
    rows = []
    for r in scan.get("stocks", []):
        sw = r.get("swing") or {}
        mom = momentum_12_1(r)
        sess = _results_in(r)
        if (sw.get("signal") != "Buy" or r.get("stale") or mom is None or (r.get("traded_value_cr") or 0) < MIN_TV_CR
                or (sw.get("atr_pct") or 99) > MAX_ATR_PCT or (sess is not None and sess <= RESULTS_BLOCK) or not r.get("price")):
            continue
        rows.append((mom, r))
    for mom, r in sorted(rows, key=lambda x: -x[0]):
        ind = r.get("industry") or r["code"]
        if ind in seen:
            continue
        seen.add(ind)
        out.append({**r, "momentum": mom})
    return out


def _stock_why(r: Dict) -> str:
    sw = r.get("swing") or {}
    return f"Up {r['momentum']:+.0f}% over 12 months (leaving out the last month); swing signal Buy: {sw.get('setup_text') or sw.get('setup')}."


def targets(scan: Dict, keep: Optional[List[str]] = None) -> List[Dict]:
    """The five slots: [{code, name, kind, role, weight, price, why, ...}]. `keep`: stocks held now (kept while in the top 10)."""
    etfs = {e["code"]: e for e in scan.get("etfs", [])}
    out = []
    for slot in (CORE, CUSHION):
        e = etfs.get(slot["code"])
        if e and e.get("price"):
            out.append({"code": slot["code"], "name": e["name"], "kind": "etf", "role": slot["role"], "weight": slot["weight"],
                        "price": e["price"], "why": slot["why"], "ret_1y": e.get("ret_1y"), "industry": e.get("tracks")})
    cands = candidates(scan)
    top = {c["code"] for c in cands[:KEEP_RANK]}
    chosen = [c for c in cands if c["code"] in set(keep or []) and c["code"] in top][:STOCK_SLOTS]
    used_ind = {c.get("industry") for c in chosen}
    for c in cands:
        if len(chosen) >= STOCK_SLOTS:
            break
        if c in chosen or c.get("industry") in used_ind:
            continue
        chosen.append(c)
        used_ind.add(c.get("industry"))
    for c in chosen:
        sw = c.get("swing") or {}
        out.append({"code": c["code"], "name": c["name"], "kind": "stock", "role": "Stock", "weight": STOCK_WEIGHT, "price": c["price"],
                    "why": _stock_why(c), "industry": c.get("industry"), "momentum": c["momentum"], "ret_1y": c.get("ret_1y"),
                    "stop": sw.get("stop"), "atr_pct": sw.get("atr_pct"), "next_results": (c.get("events") or {}).get("next_results")})
    return out


def plan(scan: Dict, capital: float = CAPITAL) -> Dict:
    """What to buy today with `capital`: whole shares at the latest close."""
    rows, spent = [], 0.0
    for t in targets(scan):
        qty = int(capital * t["weight"] // t["price"])
        amt = round(qty * t["price"], 2)
        spent += amt
        rows.append({**t, "weight_pct": round(t["weight"] * 100, 1), "amount": round(capital * t["weight"], 2), "qty": qty, "cost": amt})
    filled = sum(t["weight"] for t in rows)
    return {"date": scan.get("date"), "capital": capital, "holdings": rows, "invested": round(spent, 2),
            "cash": round(capital - spent, 2), "unfilled_pct": round((1 - filled) * 100, 1), "candidates": len(candidates(scan))}


# ---- paper record -------------------------------------------------------------------------------------------------------
def _prices(scan: Dict) -> Dict[str, float]:
    return {r["code"]: r["price"] for r in scan.get("stocks", []) + scan.get("etfs", []) if r.get("price")}


def _value(state: Dict, px: Dict[str, float]) -> float:
    return state["cash"] + sum(h["qty"] * px.get(c, h["last_price"]) for c, h in state["holdings"].items())


def _trade(state: Dict, code: str, qty: int, price: float, kind: str, date: str, name: str, why: str):
    if qty == 0:
        return
    value = abs(qty) * price
    fee = value * COST[kind]
    state["cash"] += -value - fee if qty > 0 else value - fee
    h = state["holdings"].get(code)
    if qty > 0:
        if h:
            h["avg_price"] = (h["avg_price"] * h["qty"] + value) / (h["qty"] + qty)
            h["qty"] += qty
        else:
            state["holdings"][code] = {"qty": qty, "avg_price": price, "kind": kind, "name": name, "since": date, "last_price": price}
    else:
        h["qty"] += qty
        if h["qty"] <= 0:
            del state["holdings"][code]
    state["fees"] = round(state.get("fees", 0) + fee, 2)
    state["trades"].append({"date": date, "code": code, "side": "Buy" if qty > 0 else "Sell", "qty": abs(qty), "price": round(price, 2),
                            "value": round(value, 2), "fee": round(fee, 2), "why": why})


def rebalance(state: Dict, scan: Dict, date: str):
    px = _prices(scan)
    held_stocks = [c for c, h in state["holdings"].items() if h["kind"] == "stock"]
    tg = {t["code"]: t for t in targets(scan, keep=held_stocks)}
    for code, h in list(state["holdings"].items()):                         # sell what is no longer a target
        if code not in tg:
            _trade(state, code, -h["qty"], px.get(code, h["last_price"]), h["kind"], date, h["name"],
                   "No longer passes the checks / not in the top 10" if h["kind"] == "stock" else "No longer a target")
    total = _value(state, px)
    orders = []
    for code, t in tg.items():
        cur = state["holdings"].get(code, {}).get("qty", 0) * t["price"]
        diff = total * t["weight"] - cur
        if abs(diff) >= MIN_TRADE:
            orders.append((diff, code, t))
    for diff, code, t in sorted(orders, key=lambda o: o[0]):                  # sells first, then buys
        qty = int(diff / t["price"]) if diff < 0 else int(min(diff, state["cash"] / (1 + COST[t["kind"]])) // t["price"])
        _trade(state, code, qty, t["price"], t["kind"], date, t["name"], "Monthly rebalance" if code in state["holdings"] else "New pick: " + t["why"])
    state["last_rebalance"] = date[:7]


def step(state: Optional[Dict], scan: Dict) -> Dict:
    """Advance the paper portfolio to the scan's date. Same-date calls only re-mark; a new month rebalances."""
    date = scan["date"]
    px = _prices(scan)
    if state is None or not state.get("history"):
        state = {"version": 1, "start": date, "capital": CAPITAL, "cash": CAPITAL, "holdings": {}, "trades": [], "history": [], "fees": 0.0,
                 "last_rebalance": None, "bench": {"code": BENCH, "units": 0.0}}
        if BENCH in px:
            state["bench"]["units"] = CAPITAL * (1 - COST["etf"]) / px[BENCH]
    else:
        state = copy.deepcopy(state)
        if date < state["history"][-1]["date"]:
            return state
    for code, h in state["holdings"].items():
        if code in px:
            h["last_price"] = px[code]
    if state["last_rebalance"] != date[:7]:
        rebalance(state, scan, date)
    value = _value(state, px)
    bench = state["bench"]["units"] * px.get(BENCH, 0) if state["bench"]["units"] else None
    row = {"date": date, "value": round(value, 2), "bench": round(bench, 2) if bench else None}
    if state["history"] and state["history"][-1]["date"] == date:
        state["history"][-1] = row
    else:
        state["history"].append(row)
    return state


def report(state: Dict, scan: Dict) -> Dict:
    """What the page shows about the paper record."""
    px = _prices(scan)
    total = _value(state, px)
    hold = []
    for code, h in state["holdings"].items():
        p = px.get(code, h["last_price"])
        hold.append({"code": code, "name": h["name"], "kind": h["kind"], "qty": h["qty"], "avg_price": round(h["avg_price"], 2), "price": p,
                     "value": round(h["qty"] * p, 2), "weight_pct": round(h["qty"] * p / total * 100, 1) if total else None,
                     "gain_pct": round((p / h["avg_price"] - 1) * 100, 2), "since": h["since"], "missing_price": code not in px})
    last = state["history"][-1]
    days = len(state["history"])
    return {"start": state["start"], "date": last["date"], "sessions": days, "value": last["value"], "bench_value": last["bench"],
            "return_pct": round((last["value"] / state["capital"] - 1) * 100, 2),
            "bench_return_pct": round((last["bench"] / state["capital"] - 1) * 100, 2) if last["bench"] else None,
            "cash": round(state["cash"], 2), "fees": state.get("fees", 0), "holdings": sorted(hold, key=lambda x: -x["value"]),
            "history": state["history"], "trades": state["trades"][-40:], "max_dd_pct": _dd([h["value"] for h in state["history"]]),
            "bench_max_dd_pct": _dd([h["bench"] for h in state["history"] if h["bench"]])}


def _dd(xs: List[float]) -> Optional[float]:
    if not xs:
        return None
    peak, worst = xs[0], 0.0
    for x in xs:
        peak = max(peak, x)
        worst = min(worst, x / peak - 1)
    return round(worst * 100, 2) if not math.isnan(worst) else None
