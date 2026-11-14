"""Delivery analysis: the India replacement for the original's A-share chip distribution (筹码分布).

A-share reports describe the holding structure from chip distribution (profit ratio, average cost, concentration). Indian
exchanges publish no chip data, but NSE publishes, for every stock every day, the share of traded volume that was taken
into delivery (held overnight, not squared off intraday). High and rising delivery % on up days means genuine accumulation;
delivery collapsing while price rises means speculative, intraday-driven moves.

Fields map onto the dashboard's chip_structure slot so the report layout is unchanged:
  profit_ratio  -> "delivery %" today        avg_cost      -> delivery-weighted average price (20 sessions)
  concentration -> delivery % vs 20-day avg   chip_health   -> Healthy / Average / Caution
"""
from __future__ import annotations

from typing import Dict, List, Optional


def analyze_delivery(rows: Optional[List[Dict]], price: Optional[float] = None) -> Optional[Dict]:
    if not rows:
        return None
    rows = [r for r in rows if r.get("deliv_pct") is not None and r.get("volume")]
    if not rows:
        return None
    last = rows[-1]
    pct = [r["deliv_pct"] for r in rows]
    avg20 = sum(pct) / len(pct)
    last5 = pct[-5:]
    avg5 = sum(last5) / len(last5)
    dq = [(r.get("deliv_qty") or 0, r.get("close") or 0) for r in rows]
    tot = sum(q for q, _ in dq)
    vwap_delivery = sum(q * c for q, c in dq) / tot if tot else None
    trend = "rising" if avg5 > avg20 * 1.1 else "falling" if avg5 < avg20 * 0.9 else "steady"
    # price direction over the window, to judge whether delivery confirms the move
    up = rows[-1]["close"] > rows[0]["close"] if rows[0].get("close") and rows[-1].get("close") else None
    if last["deliv_pct"] >= max(45.0, avg20) and trend != "falling":
        health = "Healthy"
    elif last["deliv_pct"] < 25 or (up and trend == "falling"):
        health = "Caution"
    else:
        health = "Average"
    p = price or rows[-1].get("close")
    vs_cost = round((p / vwap_delivery - 1) * 100, 2) if p and vwap_delivery else None
    if health == "Healthy":
        meaning = "Most traded shares are being taken into delivery: genuine holding interest supports the price"
    elif health == "Caution":
        meaning = ("Low delivery: moves are driven by intraday/speculative trading"
                   if last["deliv_pct"] < 25 else "Price rising while delivery falls: the move lacks holding support")
    else:
        meaning = "Delivery in a normal range: no strong accumulation or distribution signal"
    return {
        "date": last["date"], "delivery_pct": round(last["deliv_pct"], 2), "delivery_pct_avg5": round(avg5, 2),
        "delivery_pct_avg20": round(avg20, 2), "delivery_qty": last.get("deliv_qty"), "delivery_trend": trend,
        "delivery_vwap_20d": round(vwap_delivery, 2) if vwap_delivery else None, "price_vs_delivery_vwap_pct": vs_cost,
        "health": health, "meaning": meaning, "sessions": len(rows),
    }


def as_chip_structure(d: Optional[Dict]) -> Dict:
    """Dashboard chip_structure slot (layout kept identical to the original's report)."""
    if not d:
        return {"available": False, "reason": "Delivery data unavailable (BSE-only, index, or NSE archive not reachable)"}
    return {"profit_ratio": f"Delivery {d['delivery_pct']}%", "avg_cost": f"Delivery VWAP(20d) {d['delivery_vwap_20d']}",
            "concentration": f"vs 20d avg {d['delivery_pct_avg20']}% ({d['delivery_trend']})", "chip_health": d["health"]}
