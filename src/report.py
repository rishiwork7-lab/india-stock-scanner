"""Markdown reports (the original's notification.generate_dashboard_report / generate_single_stock_report layout):
header with buy/watch/sell counts -> summary list sorted by score -> per stock: intelligence, core conclusion,
position advice, data perspective, phase decision, battle plan, checklist."""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from .analyzer import AnalysisResult
from .india.market import now_ist

DISCLAIMER = "*AI/technical analysis for information only; not investment advice. Markets carry risk.*"


def _signal(r: AnalysisResult):
    if r.decision_type == "buy":
        return "Buy", "🟢"
    if r.decision_type == "sell":
        return ("Sell" if r.sentiment_score < 20 else "Reduce"), "🔴"
    return (r.operation_advice or "Watch"), "🟡"


def _clean(v) -> str:
    return "N/A" if v in (None, "") else str(v)


def _stock_block(r: AnalysisResult, full: bool) -> List[str]:
    text, emoji = _signal(r)
    d = r.dashboard or {}
    out = [f"## {emoji} {r.name} ({r.code})", ""]
    intel = d.get("intelligence") or {}
    if intel and full:
        out += ["### 📰 Key Information", ""]
        if intel.get("sentiment_summary"):
            out.append(f"**💭 Sentiment**: {intel['sentiment_summary']}")
        if intel.get("earnings_outlook"):
            out.append(f"**📊 Earnings outlook**: {intel['earnings_outlook']}")
        if intel.get("risk_alerts"):
            out += ["", "**🚨 Risk alerts**:"] + [f"- {a}" for a in intel["risk_alerts"]]
        if intel.get("positive_catalysts"):
            out += ["", "**✨ Positive catalysts**:"] + [f"- {c}" for c in intel["positive_catalysts"]]
        if intel.get("latest_news"):
            out += ["", f"**📢 Latest news**: {intel['latest_news']}"]
        out.append("")
    core = d.get("core_conclusion") or {}
    pos = core.get("position_advice") or {}
    out += ["### 📌 Core Conclusion", "", f"**{emoji} {text}** | {r.trend_prediction} | Score {r.sentiment_score}/100 | Confidence {r.confidence_level}", "",
            f"> **One sentence**: {core.get('one_sentence') or r.analysis_summary}", "",
            f"⏰ **Time sensitivity**: {core.get('time_sensitivity', 'No rush')}", ""]
    if pos:
        out += ["| Position | Advice |", "|---------|---------|", f"| 🆕 **No position** | {_clean(pos.get('no_position'))} |",
                f"| 💼 **Holding** | {_clean(pos.get('has_position'))} |", ""]
    if r.guardrail_reason:
        out += [f"🛡️ *Guardrail*: {r.guardrail_reason}", ""]
    if r.current_price is not None:
        chg = f" ({r.change_pct:+.2f}%)" if isinstance(r.change_pct, (int, float)) else ""
        out += [f"💹 **Price**: ₹{r.current_price:,.2f}{chg}", ""]
    sw = r.swing or {}
    if r.style == "swing" and sw:
        size = sw.get("position_size") or {}
        out += [f"📐 **Swing**: setup *{sw.get('setup')}* · trend template {(sw.get('trend_points') or 0) // 5}/5 · "
                f"RS vs NIFTY 3m {sw.get('rs_3m')} pts · entry ₹{sw.get('entry')} / stop ₹{sw.get('stop')} ({sw.get('risk_pct')}%) / "
                f"target 1 ₹{sw.get('target1')} (2R) · target 2 ₹{sw.get('target2')} (3R) · size {size.get('shares', 0)} shares (₹{size.get('value', 0):,.0f})", ""]
    if full:
        dp = d.get("data_perspective") or {}
        if dp:
            out += ["### 📊 Data Perspective", ""]
            ts = dp.get("trend_status") or {}
            if ts:
                out += [f"**MA alignment**: {_clean(ts.get('ma_alignment'))} | Bullish: {'✅ Yes' if ts.get('is_bullish') else '❌ No'} | "
                        f"Trend strength: {_clean(ts.get('trend_score'))}/100", ""]
            pp = dp.get("price_position") or {}
            if pp:
                out += ["| Price metric | Value |", "|---------|------|", f"| Current price | {_clean(pp.get('current_price'))} |",
                        f"| MA5 | {_clean(pp.get('ma5'))} |", f"| MA10 | {_clean(pp.get('ma10'))} |", f"| MA20 | {_clean(pp.get('ma20'))} |",
                        f"| Bias (MA5) | {_clean(pp.get('bias_ma5'))}% {_clean(pp.get('bias_status'))} |",
                        f"| Support | {_clean(pp.get('support_level'))} |", f"| Resistance | {_clean(pp.get('resistance_level'))} |", ""]
            va = dp.get("volume_analysis") or {}
            if va:
                out += [f"**Volume**: ratio {_clean(va.get('volume_ratio'))} ({_clean(va.get('volume_status'))}) | turnover rate {_clean(va.get('turnover_rate'))}%",
                        f"💡 *{_clean(va.get('volume_meaning'))}*", ""]
            cs = dp.get("chip_structure") or {}
            if cs.get("available") is False:
                out += [f"**Delivery structure**: {cs.get('reason')}", ""]
            elif cs:
                out += [f"**Delivery structure**: {_clean(cs.get('profit_ratio'))} | {_clean(cs.get('avg_cost'))} | {_clean(cs.get('concentration'))} "
                        f"→ **{_clean(cs.get('chip_health'))}**", ""]
        ph = d.get("phase_decision") or {}
        if ph:
            out += ["### 🕒 Phase Decision", "", f"**{_clean(ph.get('action_window'))}** → {_clean(ph.get('immediate_action'))} "
                    f"(next check: {_clean(ph.get('next_check_time'))})"]
            if ph.get("watch_conditions"):
                out += [f"- Watch: {w}" for w in ph["watch_conditions"]]
            out.append("")
    bp = d.get("battle_plan") or {}
    sp = bp.get("sniper_points") or {}
    if sp:
        out += ["### 🎯 Battle Plan", "", "| Level | Price |", "|---------|------|", f"| 🎯 Ideal buy | {_clean(sp.get('ideal_buy'))} |",
                f"| 🔵 Secondary buy | {_clean(sp.get('secondary_buy'))} |", f"| 🛑 Stop-loss | {_clean(sp.get('stop_loss'))} |",
                f"| 🎊 Target | {_clean(sp.get('take_profit'))} |", ""]
    ps = bp.get("position_strategy") or {}
    if ps and full:
        out += [f"**Position**: {_clean(ps.get('suggested_position'))} | {_clean(ps.get('entry_plan'))} | {_clean(ps.get('risk_control'))}", ""]
    cl = bp.get("action_checklist") or []
    if cl and full:
        out += ["**✅ Checklist**", ""] + [f"- {c}" for c in cl] + [""]
    if not full and (d.get("intelligence") or {}).get("risk_alerts"):
        out += ["**🚨 Risks**: " + " | ".join(str(a) for a in d["intelligence"]["risk_alerts"][:3]), ""]
    if full and r.analysis_summary:
        out += [f"**📝 Summary**: {r.analysis_summary}", ""]
    if r.risk_warning and full:
        out += [f"**⚠️ Risk warning**: {r.risk_warning}", ""]
    src = f"Model: {r.model_used or 'n/a'}" + (" (technical-only fallback)" if r.is_fallback else "")
    out += [f"<sub>{src} | data: {r.data_sources or 'n/a'}</sub>", "", "---", ""]
    return out


def generate_dashboard_report(results: List[AnalysisResult], report_date: Optional[str] = None, report_type: str = "simple") -> str:
    report_date = report_date or now_ist().strftime("%Y-%m-%d")
    rs = sorted(results, key=lambda r: r.sentiment_score, reverse=True)
    buy = sum(r.decision_type == "buy" for r in rs)
    sell = sum(r.decision_type == "sell" for r in rs)
    swing = any(r.style == "swing" for r in rs)
    lines = [f"# 🎯 {report_date} {'Swing (2-8 weeks) ' if swing else ''}Decision Dashboard", "",
             f"> Analysed **{len(rs)}** stocks | 🟢 Buy: {buy} 🟡 Watch: {len(rs) - buy - sell} 🔴 Sell: {sell}", ""]
    if rs:
        lines += ["## 📊 Summary", ""]
        for r in rs:
            text, emoji = _signal(r)
            lines.append(f"{emoji} **{r.name} ({r.code})**: {text} | Score {r.sentiment_score} | {r.trend_prediction}")
        lines += ["", "---", ""]
    for r in rs:
        lines += _stock_block(r, full=(report_type == "full"))
    fallback = [r.code for r in rs if r.is_fallback]
    if fallback:
        lines += [f"> ⚠️ Technical-only (no AI analysis) for: {', '.join(fallback)}", ""]
    lines += [f"*Generated {now_ist():%Y-%m-%d %H:%M} IST*", "", DISCLAIMER]
    return "\n".join(lines)


def generate_single_stock_report(r: AnalysisResult) -> str:
    return "\n".join([f"# {now_ist():%Y-%m-%d} {r.name} ({r.code})", ""] + _stock_block(r, full=True) + [DISCLAIMER])
