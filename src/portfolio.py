"""Model portfolios: five-ish holdings for ₹1 lakh, and paper records that follow them (research/EDGE_RESEARCH.md, P1 and L1).

P1, the first plan:
  40%    NIFTYBEES (Nifty 50 ETF): the core. Tests E1-E4 found nothing that reliably beats the index.
  10%    GOLDBEES (gold ETF): a cushion, because gold often holds up when shares fall. Not a return bet.
  3x16.7% Nifty 500 stocks that pass every check: swing signal "Buy" (never "Avoid", the one filter with proof),
         >= ₹10 crore traded a day, no results within the next 5 sessions, daily range (ATR) <= 5% of the price, not
         stale, at most one per industry; ranked by 12-month return excluding the last month (E4 rule A).
  A stock slot that no stock fills stays in cash.
Challengers C1-C4 each change one thing (SPECS). All records start with ₹1,00,000, buy whole shares at the close and
rebalance on the first build of each new month. Held stocks are kept while they stay among the 10 best that pass.
Charges: 0.25% per side for stocks, 0.15% per side for ETFs. Yardstick: ₹1,00,000 put into NIFTYBEES and held.
A challenger replaces the champion only under the promotion rule in `promotion_check` (written down in L1).
"""
from __future__ import annotations

import copy
import math
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

CAPITAL = 100_000.0
CORE_WHY = "The Nifty 50 in one ETF. Our tests (E1–E4) found nothing that reliably beats the index, so it gets the biggest share."
GOLD_WHY = "Gold often holds up when shares fall, so it softens bad months. A cushion, not a return bet."
MIN_TV_CR, MAX_ATR_PCT, RESULTS_BLOCK, KEEP_RANK = 10.0, 5.0, 5, 10
COST = {"stock": 0.0025, "etf": 0.0015}
MIN_TRADE = 1000.0                        # rebalance trades smaller than this are skipped
BENCH = "NIFTYBEES"
PROMOTE = {"min_months": 12, "min_gain_pts": 2.0, "max_extra_dd_pts": 3.0}


@dataclass(frozen=True)
class Spec:
    id: str
    name: str
    change: str                        # what differs from P1, in one line
    core: float = 0.40                 # NIFTYBEES
    gold: float = 0.10                 # GOLDBEES
    slots: int = 3
    picks: str = "momentum"            # momentum | calm | sector_etf

    @property
    def slot_weight(self) -> float:
        return (1 - self.core - self.gold) / self.slots


SPECS: List[Spec] = [
    Spec("P1", "First plan", "The starting rules"),
    Spec("C1", "No gold", "Gold's 10% goes to the Nifty 50 ETF (50% core)", core=0.50, gold=0.0),
    Spec("C2", "Sector ETFs", "The 3 stock slots hold the 3 strongest sector ETFs instead", picks="sector_etf"),
    Spec("C3", "Calmer stocks", "Among stocks that pass, the calmest (lowest daily range) win, not the strongest", picks="calm"),
    Spec("C4", "Six stocks", "6 stocks at 8.3% each instead of 3 at 16.7%", slots=6),
]
BY_ID = {s.id: s for s in SPECS}
P1 = BY_ID["P1"]
# kept for the page and older callers
CORE = {"code": "NIFTYBEES", "weight": P1.core, "role": "Core", "why": CORE_WHY}
CUSHION = {"code": "GOLDBEES", "weight": P1.gold, "role": "Cushion", "why": GOLD_WHY}
STOCK_SLOTS, STOCK_WEIGHT = P1.slots, P1.slot_weight


def momentum_12_1(r: Dict) -> Optional[float]:
    y, m = r.get("ret_1y"), r.get("ret_1m")
    if y is None or m is None:
        return None
    return round(((1 + y / 100) / (1 + m / 100) - 1) * 100, 2)


def _results_in(r: Dict) -> Optional[int]:
    nr = (r.get("events") or {}).get("next_results") or {}
    return nr.get("sessions")


def candidates(scan: Dict, order: str = "momentum") -> List[Dict]:
    """Stocks that pass every check, best first (strongest, or calmest), at most one per industry."""
    rows = []
    for r in scan.get("stocks", []):
        sw = r.get("swing") or {}
        mom = momentum_12_1(r)
        sess = _results_in(r)
        if (sw.get("signal") != "Buy" or r.get("stale") or mom is None or (r.get("traded_value_cr") or 0) < MIN_TV_CR
                or (sw.get("atr_pct") or 99) > MAX_ATR_PCT or (sess is not None and sess <= RESULTS_BLOCK) or not r.get("price")):
            continue
        rows.append((mom, r))
    key = (lambda x: (x[1]["swing"]["atr_pct"], -x[0])) if order == "calm" else (lambda x: -x[0])
    out, seen = [], set()
    for mom, r in sorted(rows, key=key):
        ind = r.get("industry") or r["code"]
        if ind in seen:
            continue
        seen.add(ind)
        out.append({**r, "momentum": mom})
    return out


def _stock_why(r: Dict, order: str) -> str:
    sw = r.get("swing") or {}
    lead = (f"Calm: moves about {sw.get('atr_pct')}% a day; up {r['momentum']:+.0f}% over 12 months (leaving out the last month)" if order == "calm"
            else f"Up {r['momentum']:+.0f}% over 12 months (leaving out the last month)")
    return f"{lead}; swing signal Buy: {sw.get('setup_text') or sw.get('setup')}."


def targets(scan: Dict, keep: Optional[List[str]] = None, spec: Spec = P1) -> List[Dict]:
    """The plan's slots: [{code, name, kind, role, weight, price, why, ...}]. `keep`: holdings now (kept while in the top 10)."""
    etfs = {e["code"]: e for e in scan.get("etfs", [])}
    out = []
    for code, w, role, why in (("NIFTYBEES", spec.core, "Core", CORE_WHY), ("GOLDBEES", spec.gold, "Cushion", GOLD_WHY)):
        e = etfs.get(code)
        if w > 0 and e and e.get("price"):
            out.append({"code": code, "name": e["name"], "kind": "etf", "role": role, "weight": w, "price": e["price"], "why": why,
                        "ret_1y": e.get("ret_1y"), "industry": e.get("tracks")})
    if spec.picks == "sector_etf":
        ranked = [s for s in scan.get("sectors", []) if s.get("above_ma200") and s["code"] in etfs and etfs[s["code"]].get("price")]
        top = [s["code"] for s in ranked[:KEEP_RANK // 2]]
        chosen = [c for c in (keep or []) if c in top][:spec.slots]
        for s in ranked:
            if len(chosen) >= spec.slots:
                break
            if s["code"] not in chosen:
                chosen.append(s["code"])
        sec = {s["code"]: s for s in ranked}
        for code in chosen:
            e = etfs[code]
            out.append({"code": code, "name": e["name"], "kind": "etf", "role": "Sector", "weight": spec.slot_weight, "price": e["price"],
                        "industry": e.get("tracks"), "ret_1y": e.get("ret_1y"),
                        "why": f"Sector rank {sec[code]['rank']}: average of 3- and 6-month return {sec[code]['momentum']:+.1f}%, above its 200-day average."})
        return out
    cands = candidates(scan, spec.picks)
    top = {c["code"] for c in cands[:max(KEEP_RANK, spec.slots * 2)]}
    chosen = [c for c in cands if c["code"] in set(keep or []) and c["code"] in top][:spec.slots]
    used_ind = {c.get("industry") for c in chosen}
    for c in cands:
        if len(chosen) >= spec.slots:
            break
        if c in chosen or c.get("industry") in used_ind:
            continue
        chosen.append(c)
        used_ind.add(c.get("industry"))
    for c in chosen:
        sw = c.get("swing") or {}
        out.append({"code": c["code"], "name": c["name"], "kind": "stock", "role": "Stock", "weight": spec.slot_weight, "price": c["price"],
                    "why": _stock_why(c, spec.picks), "industry": c.get("industry"), "momentum": c["momentum"], "ret_1y": c.get("ret_1y"),
                    "stop": sw.get("stop"), "atr_pct": sw.get("atr_pct"), "next_results": (c.get("events") or {}).get("next_results")})
    return out


def plan(scan: Dict, capital: float = CAPITAL, spec: Spec = P1) -> Dict:
    """What to buy today with `capital`: whole shares at the latest close."""
    rows, spent = [], 0.0
    for t in targets(scan, spec=spec):
        qty = int(capital * t["weight"] // t["price"])
        amt = round(qty * t["price"], 2)
        spent += amt
        rows.append({**t, "weight_pct": round(t["weight"] * 100, 1), "amount": round(capital * t["weight"], 2), "qty": qty, "cost": amt})
    filled = sum(t["weight"] for t in rows)
    n = len(candidates(scan, spec.picks)) if spec.picks != "sector_etf" else sum(1 for s in scan.get("sectors", []) if s.get("above_ma200"))
    return {"date": scan.get("date"), "capital": capital, "spec": asdict(spec), "holdings": rows, "invested": round(spent, 2),
            "cash": round(capital - spent, 2), "unfilled_pct": round((1 - filled) * 100, 1), "candidates": n}


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


def rebalance(state: Dict, scan: Dict, date: str, spec: Spec = P1):
    px = _prices(scan)
    held = [c for c, h in state["holdings"].items() if c not in ("NIFTYBEES", "GOLDBEES")]
    tg = {t["code"]: t for t in targets(scan, keep=held, spec=spec)}
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


def step(state: Optional[Dict], scan: Dict, spec: Spec = P1) -> Dict:
    """Advance a paper portfolio to the scan's date. Same-date calls only re-mark; a new month rebalances."""
    date = scan["date"]
    px = _prices(scan)
    if state is None or not state.get("history"):
        state = {"version": 1, "plan": spec.id, "start": date, "capital": CAPITAL, "cash": CAPITAL, "holdings": {}, "trades": [], "history": [],
                 "fees": 0.0, "last_rebalance": None, "bench": {"code": BENCH, "units": 0.0}}
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
        rebalance(state, scan, date, spec)
    value = _value(state, px)
    bench = state["bench"]["units"] * px.get(BENCH, 0) if state["bench"]["units"] else None
    row = {"date": date, "value": round(value, 2), "bench": round(bench, 2) if bench else None}
    if state["history"] and state["history"][-1]["date"] == date:
        state["history"][-1] = row
    else:
        state["history"].append(row)
    return state


def _cagr(first: float, last: float, days: int) -> Optional[float]:
    return round(((last / first) ** (365.25 / days) - 1) * 100, 2) if days >= 1 and first > 0 else None


def report(state: Dict, scan: Dict) -> Dict:
    """What the page shows about one paper record."""
    import datetime as dt
    px = _prices(scan)
    total = _value(state, px)
    hold = []
    for code, h in state["holdings"].items():
        p = px.get(code, h["last_price"])
        hold.append({"code": code, "name": h["name"], "kind": h["kind"], "qty": h["qty"], "avg_price": round(h["avg_price"], 2), "price": p,
                     "value": round(h["qty"] * p, 2), "weight_pct": round(h["qty"] * p / total * 100, 1) if total else None,
                     "gain_pct": round((p / h["avg_price"] - 1) * 100, 2), "since": h["since"], "missing_price": code not in px})
    last = state["history"][-1]
    days = (dt.date.fromisoformat(last["date"]) - dt.date.fromisoformat(state["start"])).days
    return {"plan": state.get("plan", "P1"), "start": state["start"], "date": last["date"], "sessions": len(state["history"]), "days": days,
            "value": last["value"], "bench_value": last["bench"],
            "return_pct": round((last["value"] / state["capital"] - 1) * 100, 2),
            "bench_return_pct": round((last["bench"] / state["capital"] - 1) * 100, 2) if last["bench"] else None,
            "cagr_pct": _cagr(state["capital"], last["value"], days) if days >= 365 else None,
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


# ---- champion and challengers ------------------------------------------------------------------------------------------
def _shared(a: Dict, b: Dict) -> List[tuple]:
    hb = {h["date"]: h["value"] for h in b["history"]}
    return [(h["date"], h["value"], hb[h["date"]]) for h in a["history"] if h["date"] in hb]


def compare(challenger: Dict, champion: Dict) -> Dict:
    """Challenger vs champion over the dates both have: annual returns, worst falls, each half."""
    import datetime as dt
    rows = _shared(challenger, champion)
    if len(rows) < 2:
        return {"days": 0}
    days = (dt.date.fromisoformat(rows[-1][0]) - dt.date.fromisoformat(rows[0][0])).days
    ann = lambda i: _cagr(rows[0][i], rows[-1][i], max(days, 1))  # noqa: E731
    mid = len(rows) // 2
    half = lambda a, b, i: rows[b][i] / rows[a][i] - 1  # noqa: E731
    return {"days": days, "from": rows[0][0], "to": rows[-1][0], "challenger_ann_pct": ann(1), "champion_ann_pct": ann(2),
            "challenger_dd_pct": _dd([r[1] for r in rows]), "champion_dd_pct": _dd([r[2] for r in rows]),
            "ahead_first_half": half(0, mid, 1) > half(0, mid, 2), "ahead_second_half": half(mid, -1, 1) > half(mid, -1, 2)}


def promotion_check(states: Dict[str, Dict], champion: str, date: str) -> Optional[Dict]:
    """The L1 rule: >= 12 months, >= 2 points a year better, worst fall <= 3 points deeper, ahead in both halves.
    Returns the switch (best qualifying challenger) or None."""
    best = None
    for pid, st in states.items():
        if pid == champion or champion not in states:
            continue
        c = compare(st, states[champion])
        if c["days"] < PROMOTE["min_months"] * 30.4:
            continue
        gain = c["challenger_ann_pct"] - c["champion_ann_pct"]
        ok = (gain >= PROMOTE["min_gain_pts"] and c["challenger_dd_pct"] >= c["champion_dd_pct"] - PROMOTE["max_extra_dd_pts"]
              and c["ahead_first_half"] and c["ahead_second_half"])
        if ok and (best is None or gain > best["gain_pts"]):
            best = {**{f"shared_{k}" if k in ("from", "to") else k: v for k, v in c.items()},
                    "date": date, "from": champion, "to": pid, "gain_pts": round(gain, 2)}
    return best
