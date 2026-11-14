"""Swing style (2-8 weeks): system prompt, prompt section, guardrails and the deterministic fallback dashboard.
Same Decision Dashboard JSON schema as the short-term style, so reports, storage and the backtest are shared."""
from __future__ import annotations

from typing import Any, Dict

from .decision_scale import SCALE_PROMPT, band_for_score
from .delivery import as_chip_structure

SWING_POLICY = """## Swing Trading Baseline (holding period 2-8 weeks, cash delivery; must be followed strictly)

### 1. Trade only Stage-2 uptrends (trend template)
- Price > MA50 > MA150 > MA200, and MA200 rising for at least a month
- Prefer stocks within 25% of their 52-week high and well above their 52-week low
- Never buy below a falling MA200

### 2. Relative strength first
- Prefer stocks outperforming NIFTY over 3 and 6 months; leaders lead in the next rally

### 3. Buy only at a defined setup, never in the middle of a move
- Pullback to a rising MA20/MA50 on lighter volume, or a breakout above a base/30-session high on at least 1.5x volume
- A stock more than 15% above MA50 is extended: wait for a pullback ("Watch"), do not chase

### 4. Risk first
- Stop-loss below the recent swing low or 2x ATR, and never more than 8% below entry
- Targets at 2R and 3R (R = entry - stop); a trade without at least 2:1 reward-to-risk is not a Buy
- Position size from a fixed % of capital at risk; cash delivery only, no leverage

### 5. Accumulation and holding structure
- Up-day volume above down-day volume over 50 sessions; healthy NSE delivery % supports the move

### 6. Event and risk screening (India)
- Quarterly results due within the holding period, SEBI orders, promoter pledge/selling, ASM/GSM surveillance, auditor
  exits, rating downgrades, F&O ban, block-deal exits; mention each as a risk with its date when known

### 7. Exits
- Exit on a close below the stop or MA50, at the targets, or when the setup fails (breakout back inside the base)"""

_SCHEMA_SWAPS = [
    ('"ideal_buy": "Ideal buy point: ₹XX (near MA5)"', '"ideal_buy": "Entry: ₹XX (pullback to MA20/MA50 or breakout pivot)"'),
    ('"secondary_buy": "Secondary buy point: ₹XX (near MA10)"', '"secondary_buy": "Add-on: ₹XX (after the first target or a second setup)"'),
    ('"stop_loss": "Stop-loss: ₹XX (break below MA20 or X%)"', '"stop_loss": "Stop-loss: ₹XX (below the swing low / 2x ATR, max 8%)"'),
    ('"take_profit": "Target: ₹XX (previous high/round-number level)"', '"take_profit": "Target 1: ₹XX (2R) · Target 2: ₹YY (3R)"'),
    ('"✅/⚠️/❌ Check 1: bullish alignment"', '"✅/⚠️/❌ Check 1: Stage-2 trend template (price > MA50 > MA150 > MA200, MA200 rising)"'),
    ('"✅/⚠️/❌ Check 2: reasonable bias (may be relaxed for strong trends)"', '"✅/⚠️/❌ Check 2: relative strength vs NIFTY"'),
    ('"✅/⚠️/❌ Check 3: volume confirms"', '"✅/⚠️/❌ Check 3: defined setup (pullback/breakout), not extended"'),
    ('"✅/⚠️/❌ Check 5: healthy delivery structure"', '"✅/⚠️/❌ Check 5: accumulation (up/down volume, delivery %)"'),
    ('"✅/⚠️/❌ Check 6: reasonable P/E valuation"', '"✅/⚠️/❌ Check 6: reward-to-risk >= 2 and no results inside the holding period"'),
    ('"short_term_outlook": "Short-term outlook (1-3 days)"', '"short_term_outlook": "Outlook for the next 1-2 weeks"'),
    ('"medium_term_outlook": "Medium-term outlook (1-2 weeks)"', '"medium_term_outlook": "Outlook for the 1-2 month holding period"'),
    ('"time_sensitivity": "Act now/Today/This week/No rush"', '"time_sensitivity": "This week/Next 2 weeks/No rush (swing)"'),
]

SWING_SCORING = """## Scoring Criteria (swing)

### Strong Buy (80-100): Stage-2 trend template fully met, strong relative strength, a fresh pullback or breakout setup, accumulation, reward-to-risk >= 3
### Buy (60-79): trend template mostly met, positive relative strength, a valid setup, reward-to-risk >= 2
### Watch (40-59): uptrend but extended or no setup yet; or setup present but trend/relative strength weak
### Reduce (20-39): trend template failing (price below MA50, MA200 flattening), distribution
### Sell (0-19): price below a falling MA200, breakdown, or a major negative event"""


def swing_system_prompt(base_schema: str, principles: str) -> str:
    schema = base_schema
    for a, b in _SCHEMA_SWAPS:
        schema = schema.replace(a, b)
    return ("You are a swing-trading focused Indian equity (NSE/BSE) analyst. The user holds positions for 2-8 weeks, cash "
            "delivery only. Produce a professional Decision Dashboard analysis report.\n\n" + SWING_POLICY + "\n\n" + SCALE_PROMPT
            + "\n\n" + schema + "\n\n" + SWING_SCORING + "\n\n" + principles)


def _v(x, nd=2):
    return "N/A" if x is None else f"{x:,.{nd}f}" if isinstance(x, float) else str(x)


def swing_section(ctx: Dict[str, Any]) -> str:
    s = ctx.get("swing") or {}
    if not s:
        return ""
    tt = s.get("trend_template") or {}
    mark = lambda b: "✅" if b else "❌"  # noqa: E731
    size = ctx.get("position_size") or {}
    rr = ""
    if s.get("entry") and s.get("stop") and s.get("target1"):
        rr = f"{(s['target1'] - s['entry']) / (s['entry'] - s['stop']):.1f}"
    return f"""
## 📐 Swing Analysis (2-8 week horizon; deterministic, based on the swing baseline)
| Item | Value | Judgement |
|------|------|------|
| Swing score | **{s.get('score')}/100** | system signal **{s.get('signal')}** |
| Trend template | {mark(tt.get('price_above_ma50'))} price>MA50 {mark(tt.get('ma50_above_ma150'))} MA50>MA150 {mark(tt.get('ma150_above_ma200'))} MA150>MA200 {mark(tt.get('ma200_rising'))} MA200 rising | {s.get('trend_points')}/25 |
| MA20 / MA50 / MA150 / MA200 | {_v(s.get('ma20'))} / {_v(s.get('ma50'))} / {_v(s.get('ma150'))} / {_v(s.get('ma200'))} | extension vs MA50: {_v(s.get('extension_pct'))}% |
| 52-week high / low | ₹{_v(s.get('high_52w'))} / ₹{_v(s.get('low_52w'))} | {_v(s.get('pct_from_high'))}% from high, {_v(s.get('pct_above_low'))}% above low |
| Return 3m / 6m | {_v(s.get('ret_3m'))}% / {_v(s.get('ret_6m'))}% | vs NIFTY: {_v(s.get('rs_3m'))} / {_v(s.get('rs_6m'))} pts |
| Setup | {s.get('setup')} | {s.get('setup_text')} |
| ATR(14) | ₹{_v(s.get('atr14'))} ({_v(s.get('atr_pct'))}% of price) | volatility |
| Up/down volume (50 sessions) | {_v(s.get('ud_volume_ratio'))} | >1.2 accumulation, <0.9 distribution |
| Weekly RSI(14) | {_v(s.get('weekly_rsi'), 1)} | 50-70 ideal |
| Planned entry / stop | ₹{_v(s.get('entry'))} / ₹{_v(s.get('stop'))} | risk {_v(s.get('risk_pct'))}% |
| Target 1 (2R) / Target 2 (3R) | ₹{_v(s.get('target1'))} / ₹{_v(s.get('target2'))} | reward-to-risk {rr or 'N/A'} at target 1 |
| Position size | {size.get('shares', 'N/A')} shares (₹{_v(size.get('value'))}) | risking ₹{_v(size.get('risk_amount'))} = {ctx.get('risk_pct_setting')}% of ₹{_v(ctx.get('capital'))} capital |

**Swing reasons**:
{chr(10).join('- ' + x for x in s.get('reasons', [])) or '- None'}

**Swing risks**:
{chr(10).join('- ' + x for x in s.get('risks', [])) or '- None'}

> Use these levels unless the news or fundamentals give a clear reason to change them; say why if you do.
"""


SWING_FOCUS = """
### Key focus (must be answered explicitly):
1. ❓ Is the stock in a Stage-2 uptrend (trend template)? Is it outperforming NIFTY?
2. ❓ Is there a defined setup now (pullback to a rising MA20/MA50, or a volume breakout), or is it extended?
3. ❓ Where exactly are the entry, stop (max 8%) and 2R/3R targets, and is reward-to-risk at least 2?
4. ❓ Are quarterly results or other events due inside the 2-8 week holding period?
5. ❓ Any major negative news? (SEBI action, pledge, surveillance, downgrades)
"""


def apply_swing_guardrails(r, ctx: Dict[str, Any]) -> list:
    """Swing-specific structural guard for model output: no buys when extended, outside a Stage-2 trend, or without a
    2:1 reward-to-risk. Returns the reasons applied."""
    s = ctx.get("swing") or {}
    reasons = []
    if r.decision_type != "buy" or not s:
        return reasons
    if s.get("setup") == "extended":
        reasons.append(f"Buy downgraded to Watch: {s.get('extension_pct')}% above MA50 (extended), wait for a pullback")
    elif (s.get("trend_points") or 0) < 15:
        reasons.append(f"Buy downgraded to Watch: trend template only {(s.get('trend_points') or 0) // 5}/5 (not a Stage-2 uptrend)")
    elif s.get("entry") and s.get("stop") and s.get("target1") and (s["target1"] - s["entry"]) < 2 * (s["entry"] - s["stop"]) * 0.99:
        reasons.append("Buy downgraded to Watch: reward-to-risk below 2:1")
    if reasons:
        r.decision_type, r.action, r.operation_advice = "hold", "watch", "Watch"
        r.sentiment_score = max(40, min(59, r.sentiment_score))
    return reasons


def swing_fallback(ctx: Dict[str, Any], reason: str):
    from .analyzer import AnalysisResult
    s = ctx.get("swing") or {}
    code, name = ctx["code"], ctx.get("stock_name") or ctx["code"]
    score = int(s.get("score") or 50)
    sig = s.get("signal") or "Watch"
    advice = {"Buy": "Buy", "Avoid": "Avoid"}.get(sig, "Watch")
    decision = "buy" if sig == "Buy" else "sell" if sig == "Avoid" and score < 40 else "hold"
    band = band_for_score(score)
    tt = s.get("trend_template") or {}
    size = ctx.get("position_size") or {}
    trend_pred = ("Strong Bullish" if (s.get("trend_points") or 0) == 25 and (s.get("rs_3m") or 0) > 0 else
                  "Bullish" if (s.get("trend_points") or 0) >= 15 else "Bearish" if (s.get("trend_points") or 0) <= 5 else "Sideways")
    rr_ok = s.get("entry") and s.get("stop") and s.get("target1")
    checks = [
        f"{'✅' if (s.get('trend_points') or 0) >= 20 else '⚠️' if (s.get('trend_points') or 0) >= 15 else '❌'} Check 1: trend template {(s.get('trend_points') or 0) // 5}/5",
        f"{'✅' if (s.get('rs_3m') or -1) >= 0 else '❌'} Check 2: relative strength vs NIFTY (3m {s.get('rs_3m')} pts, 6m {s.get('rs_6m')} pts)",
        f"{'✅' if s.get('setup') in ('pullback', 'breakout') else '⚠️' if s.get('setup') == 'tight_base' else '❌'} Check 3: setup: {s.get('setup_text') or s.get('setup')}",
        "⚠️ Check 4: news and results dates not judged (technical-only fallback)",
        f"{'✅' if (s.get('ud_volume_ratio') or 0) >= 1.2 else '⚠️'} Check 5: up/down volume {s.get('ud_volume_ratio')}"
        + (f", delivery {ctx['delivery']['health']}" if ctx.get("delivery") else ""),
        f"{'✅' if rr_ok else '⚠️'} Check 6: stop {s.get('risk_pct')}% below entry, targets at 2R/3R",
    ]
    if sig == "Buy":
        no_pos = (f"Buy {size.get('shares', '?')} shares near ₹{s.get('entry'):,.2f} with a stop at ₹{s.get('stop'):,.2f}"
                  if s.get("entry") and s.get("stop") else "Buy at the setup with a stop below the swing low")
        has_pos = "Hold; trail the stop up to MA20 after target 1"
    elif sig == "Avoid":
        no_pos, has_pos = "Do not buy: no long-term uptrend", "Exit or reduce: trend template is failing"
    else:
        no_pos = "Wait: " + (s.get("setup_text") or "no clean setup yet")
        has_pos = "Hold while the price stays above MA50; exit on a close below it"
    one = (f"{advice} (swing): {s.get('setup_text') or 'no setup'}; trend template {(s.get('trend_points') or 0) // 5}/5, "
           f"swing score {score}/100 (technical-only; AI analysis unavailable)")
    r = AnalysisResult(
        code=code, name=name, sentiment_score=score, trend_prediction=trend_pred, operation_advice=advice, decision_type=decision,
        action=(band.action if band else "watch") if sig != "Avoid" else "avoid", confidence_level="Low",
        analysis_summary=one + ". " + " ".join(s.get("reasons", [])[:3]), key_points=", ".join(s.get("reasons", [])[:4]),
        risk_warning="; ".join(s.get("risks") or []) or "None from the technicals", buy_reason="; ".join(s.get("reasons") or []),
        short_term_outlook=s.get("setup_text", ""), medium_term_outlook=f"Holding period 2-8 weeks; target 1 ₹{s.get('target1')} (2R), target 2 ₹{s.get('target2')} (3R)",
        current_price=(ctx.get("today") or {}).get("close"), change_pct=(ctx.get("today") or {}).get("pct_chg"),
        data_sources="price:" + str(ctx.get("data_source")) + ",swing:fallback", model_used="technical fallback (swing)", is_fallback=True,
        error_message=reason, style="swing",
        dashboard={
            "core_conclusion": {"one_sentence": one, "signal_type": {"buy": "🟢Buy Signal", "sell": "🔴Sell Signal"}.get(decision, "🟡Hold and Watch"),
                                "time_sensitivity": "This week" if sig == "Buy" else "No rush (swing)",
                                "position_advice": {"no_position": no_pos, "has_position": has_pos}},
            "data_perspective": {
                "trend_status": {"ma_alignment": " / ".join(k.replace("_", " ") for k, v in tt.items() if v) or "trend template not met",
                                 "is_bullish": (s.get("trend_points") or 0) >= 15, "trend_score": (s.get("trend_points") or 0) * 4},
                "price_position": {"current_price": s.get("price"), "ma5": None, "ma10": None, "ma20": s.get("ma20"),
                                   "bias_ma5": s.get("extension_pct"), "bias_status": "Danger" if s.get("setup") == "extended" else "Safe",
                                   "support_level": s.get("stop"), "resistance_level": s.get("high_52w")},
                "volume_analysis": {"volume_ratio": s.get("ud_volume_ratio"), "volume_status": "Up/down volume (50 sessions)",
                                    "turnover_rate": (ctx.get("realtime") or {}).get("turnover_rate"),
                                    "volume_meaning": "Accumulation" if (s.get("ud_volume_ratio") or 0) >= 1.2 else "No clear accumulation"},
                "chip_structure": as_chip_structure(ctx.get("delivery")),
            },
            "intelligence": {"latest_news": "Not analysed (AI unavailable)", "risk_alerts": s.get("risks") or [], "positive_catalysts": [],
                             "earnings_outlook": "Not analysed: check the results date before entering", "sentiment_summary": "Not analysed"},
            "battle_plan": {
                "sniper_points": {"ideal_buy": f"Entry: ₹{s.get('entry')} ({s.get('setup')})", "secondary_buy": f"Add-on: above ₹{s.get('target1')} (after target 1)",
                                  "stop_loss": f"Stop-loss: ₹{s.get('stop')} ({s.get('risk_pct')}% risk; swing low / 2x ATR, max 8%)",
                                  "take_profit": f"Target 1: ₹{s.get('target1')} (2R) · Target 2: ₹{s.get('target2')} (3R)"},
                "position_strategy": {"suggested_position": (f"{size.get('shares', 0)} shares (₹{size.get('value', 0):,.0f}, {size.get('capital_pct', 0)}% of capital)"
                                                             if sig == "Buy" else f"No new position (swing signal: {sig})"),
                                      "entry_plan": "Enter at the setup; add only after target 1", "risk_control":
                                      f"Risk ₹{size.get('risk_amount', 0):,.0f} ({ctx.get('risk_pct_setting')}% of capital); trail the stop to MA20 after 2R"},
                "action_checklist": checks,
            },
            "phase_decision": {"phase_context": {"phase": (ctx.get("market_phase") or {}).get("phase", "unknown")},
                               "action_window": "Weekly swing plan", "immediate_action": "Act at the setup" if sig == "Buy" else "Watch",
                               "watch_conditions": [f"Close below ₹{s.get('stop')} = exit", "Close below MA50 = trend weakening",
                                                    f"Breakout above ₹{s.get('high_52w')} (52-week high) = strength"],
                               "next_check_time": "Weekly, Friday close", "confidence_reason": "Technical-only swing fallback",
                               "data_limitations": [reason]},
        },
        swing={**s, "position_size": size})
    return r
