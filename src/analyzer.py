"""Decision Dashboard analysis (the original's src/analyzer.py, adapted to India).

- SYSTEM_PROMPT: trading baseline + canonical score scale + the Decision Dashboard JSON schema + scoring criteria +
  principles. Same schema as the original; the India changes are the risk-screening list (SEBI, promoter pledge,
  ASM/GSM surveillance, auditor exit, promoter selling, F&O ban) and delivery % in place of A-share chip distribution
  (kept in the `chip_structure` slot so the report layout is unchanged).
- build_prompt(): the per-stock "Decision Dashboard Analysis Request" in Markdown tables.
- parse_response(): JSON extraction and repair; check_integrity() with one complement retry, then placeholder fill.
- apply_guardrails(): canonical score/action consistency and the structural stability guard.
- trend_fallback(): a full dashboard from the deterministic trend analysis when no LLM is available or all fail.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .decision_scale import SCALE_PROMPT, band_for_score
from .delivery import as_chip_structure
from .india.market import format_inr, format_volume
from .llm.providers import AllProvidersFailed, LLMRouter

logger = logging.getLogger(__name__)

CORE_POLICY_INDIA = """## Default Trading Baseline (must be followed strictly)

### 1. Strict entry (no chasing)
- **Never chase highs**: when the price is more than 5% above MA5, do not buy
- Bias < 2%: best entry zone; bias 2-5%: a small position is acceptable; bias > 5%: no chasing, classify as "Watch"

### 2. Trend trading (follow the trend)
- **Required bullish alignment**: MA5 > MA10 > MA20; stay away from bearish alignment
- Diverging, rising moving averages are better than converging ones

### 3. Efficiency first (holding structure: NSE delivery %)
- Indian exchanges publish the share of traded volume taken into delivery each day; it replaces A-share chip data
- Delivery % >= 45% and at/above its 20-day average on up days: genuine accumulation (healthy)
- Price rising while delivery % falls, or delivery < 25%: intraday/speculative move (caution)
- High delivery % on a falling day: distribution by holders (treat as capital outflow)

### 4. Entry preference (pullback to support)
- **Best entry**: low-volume pullback to MA5 that finds support; **secondary**: pullback to MA10
- Stay on the sidelines when the price breaks below MA20

### 5. Risk screening (India)
- SEBI orders or investigations, promoter share pledging or pledge increases, promoter/insider selling, auditor
  resignation, credit-rating downgrades, ASM/GSM surveillance listing, F&O ban period, large block-deal exits, tax raids,
  governance issues, weak or missed quarterly results

### 6. Valuation (P/E, P/B)
- When P/E is clearly above the sector norm, mention it in the risk points

### 7. Strong-trend relaxation
- Strong trend stocks may relax the bias requirement; track with a light position but always set a stop-loss"""

DASHBOARD_SCHEMA = """## Output Format: Decision Dashboard JSON

Output strictly in the following JSON format. This is a complete Decision Dashboard:

```json
{
    "stock_name": "Company name (common English name)",
    "sentiment_score": integer 0-100,
    "trend_prediction": "Strong Bullish/Bullish/Sideways/Bearish/Strong Bearish",
    "operation_advice": "Buy/Add Position/Hold/Reduce/Sell/Watch",
    "decision_type": "buy/hold/sell",
    "action": "buy/add/hold/reduce/sell/watch/avoid/alert",
    "guardrail_reason": "Reason for the downgrade/upgrade when the score band and the final action differ; otherwise leave empty",
    "confidence_level": "High/Medium/Low",

    "dashboard": {
        "core_conclusion": {
            "one_sentence": "One-sentence core conclusion (at most 30 words, tell the user exactly what to do)",
            "signal_type": "🟢Buy Signal/🟡Hold and Watch/🔴Sell Signal/⚠️Risk Warning",
            "time_sensitivity": "Act now/Today/This week/No rush",
            "position_advice": {
                "no_position": "Advice for those without a position: concrete action",
                "has_position": "Advice for holders: concrete action"
            }
        },
        "data_perspective": {
            "trend_status": {"ma_alignment": "Description of the moving-average alignment", "is_bullish": true/false, "trend_score": 0-100},
            "price_position": {
                "current_price": number, "ma5": number, "ma10": number, "ma20": number, "bias_ma5": bias percentage number,
                "bias_status": "Safe/Caution/Danger", "support_level": support price, "resistance_level": resistance price
            },
            "volume_analysis": {
                "volume_ratio": number, "volume_status": "High volume/Low volume/Normal volume",
                "turnover_rate": turnover rate percentage, "volume_meaning": "Interpretation of volume"
            },
            "chip_structure": {
                "profit_ratio": "today's delivery % (e.g. 'Delivery 52%')",
                "avg_cost": "20-day delivery-weighted price",
                "concentration": "delivery % vs its 20-day average and trend",
                "chip_health": "Healthy/Average/Caution"
            }
        },
        "intelligence": {
            "latest_news": "[Latest News] summary of recent important news",
            "risk_alerts": ["Risk 1: specific description", "Risk 2: specific description"],
            "positive_catalysts": ["Catalyst 1: specific description", "Catalyst 2: specific description"],
            "earnings_outlook": "Earnings outlook (quarterly results, guidance, analyst estimates)",
            "sentiment_summary": "One-sentence summary of news sentiment"
        },
        "battle_plan": {
            "sniper_points": {
                "ideal_buy": "Ideal buy point: ₹XX (near MA5)",
                "secondary_buy": "Secondary buy point: ₹XX (near MA10)",
                "stop_loss": "Stop-loss: ₹XX (break below MA20 or X%)",
                "take_profit": "Target: ₹XX (previous high/round-number level)"
            },
            "position_strategy": {
                "suggested_position": "Suggested position: X/10",
                "entry_plan": "Description of the staged entry plan",
                "risk_control": "Description of the risk-control strategy"
            },
            "action_checklist": [
                "✅/⚠️/❌ Check 1: bullish alignment",
                "✅/⚠️/❌ Check 2: reasonable bias (may be relaxed for strong trends)",
                "✅/⚠️/❌ Check 3: volume confirms",
                "✅/⚠️/❌ Check 4: no major negative news (SEBI, pledge, surveillance, results)",
                "✅/⚠️/❌ Check 5: healthy delivery structure",
                "✅/⚠️/❌ Check 6: reasonable P/E valuation"
            ]
        },
        "phase_decision": {
            "phase_context": {"phase": "premarket/intraday/closing_auction/postmarket/non_trading/unknown"},
            "action_window": "Pre-market plan/Intraday tracking/Pre-close risk control/Post-market review/Non-trading-day watch",
            "immediate_action": "Act now/Wait for confirmation/Watch/Stop-loss or take-profit alert/Do not chase/No intraday action",
            "watch_conditions": ["Watch condition 1", "Watch condition 2"],
            "next_check_time": "Next checkpoint in IST",
            "confidence_reason": "Reason for the confidence level, including phase and data-quality limits",
            "data_limitations": ["Limitation 1", "Limitation 2"]
        },
        "signal_attribution": {
            "technical_indicators": 0-100, "news_sentiment": 0-100, "fundamentals": 0-100, "market_conditions": 0-100,
            "strongest_bullish_signal": "Name of the strongest bullish signal",
            "strongest_bearish_signal": "Name of the strongest bearish signal"
        }
    },

    "analysis_summary": "Overall analysis summary (about 100 words)",
    "key_points": "3-5 key points, comma separated",
    "risk_warning": "Risk warning",
    "buy_reason": "Reason for the action, citing the trading principles",
    "trend_analysis": "Price pattern analysis",
    "short_term_outlook": "Short-term outlook (1-3 days)",
    "medium_term_outlook": "Medium-term outlook (1-2 weeks)",
    "technical_analysis": "Overall technical analysis",
    "ma_analysis": "Moving-average analysis",
    "volume_analysis": "Volume analysis",
    "pattern_analysis": "Candlestick pattern analysis",
    "fundamental_analysis": "Fundamental analysis",
    "sector_position": "Sector and industry analysis",
    "company_highlights": "Company highlights/risks",
    "news_summary": "News summary",
    "market_sentiment": "Market sentiment (NIFTY trend, FII/DII flows)",
    "hot_topics": "Related hot topics",
    "search_performed": true/false,
    "data_sources": "Description of data sources"
}
```"""

SCORING = """## Scoring Criteria

### Strong Buy (80-100):
- ✅ Bullish alignment: MA5 > MA10 > MA20
- ✅ Low bias: <2%, best entry
- ✅ Low-volume pullback or high-volume breakout
- ✅ Healthy delivery structure (high delivery %, rising on up days)
- ✅ Positive news catalyst

### Buy (60-79):
- ✅ Bullish or weakly bullish alignment
- ✅ Bias <5%
- ✅ Normal volume
- ⚪ One minor condition may be unmet

### Watch (40-59):
- ⚠️ Bias >5% (chasing risk)
- ⚠️ Tangled moving averages, unclear trend
- ⚠️ Risk events present

### Reduce (20-39):
- ⚠️ Weakening trend or break below key moving averages
- ⚠️ Delivery-backed selling or weakening volume; risk clearly outweighs reward

### Sell (0-19):
- ❌ Bearish alignment or clearly deteriorating trend
- ❌ Break below key support/stop-loss
- ❌ High-volume decline or major negative news (SEBI action, pledge invocation, results shock)"""

PRINCIPLES = """## Decision Dashboard Core Principles

1. **Conclusion first**: say clearly in one sentence whether to buy or sell
2. **Position-specific advice**: give different advice to those without a position and to holders
3. **Precise sniper levels**: always give concrete ₹ prices, never vague wording
4. **Visual checklist**: use ✅⚠️❌ to show the result of each check
5. **Risk priority**: highlight risk points from the news clearly

## Actionability and Stability Constraints

- Do not flip between "Buy" and "Sell" just because of a single day's move or because the score crossed a threshold.
- Operation advice must consider price position (support/resistance), volume/delivery, institutional flows and risk events together.
- When the price is between support and resistance and flows are unclear, prefer actionable neutral advice such as "Hold/Sideways/Watch"; `decision_type` stays `hold`.
- Only give Buy near a confirmed support or after a valid breakout above resistance, with delivery/volume confirming; never chase near resistance while holders distribute.
- Only give Sell/Reduce after a break below key support, persistent delivery-backed selling, or a clear increase in risk.
- `dashboard.phase_decision` must contain all seven fields.
- Pre-market, on non-trading days or in an unknown phase, never fabricate today's intraday move; when data is stale, missing or partial, `confidence_level` must not be High.
- All prices are in Indian rupees (₹). Never invent prices, results or news; say "data unavailable" when a field is N/A."""

SYSTEM_PROMPT = ("You are a trend-trading focused Indian equity (NSE/BSE) analyst responsible for producing a professional "
                 "Decision Dashboard analysis report.\n\n" + CORE_POLICY_INDIA + "\n\n" + SCALE_PROMPT + "\n\n" + DASHBOARD_SCHEMA
                 + "\n\n" + SCORING + "\n\n" + PRINCIPLES)

from .swing_prompt import SWING_FOCUS, apply_swing_guardrails, swing_fallback, swing_section, swing_system_prompt  # noqa: E402

SWING_SYSTEM_PROMPT = swing_system_prompt(DASHBOARD_SCHEMA, PRINCIPLES)

TEXT_SYSTEM_PROMPT = """You are a professional Indian stock-market analysis assistant.
- Answers must be based on the data and context provided
- If information is insufficient, state the uncertainty clearly
- Never fabricate prices, financial results or news"""

ADVICE_FOR_BAND = {"strong_buy": "Buy", "buy": "Buy", "watch": "Watch", "reduce": "Reduce", "sell": "Sell"}
TREND_LABEL = {"STRONG_BULL": "Strong Bullish", "BULL": "Bullish", "WEAK_BULL": "Bullish", "CONSOLIDATION": "Sideways",
               "WEAK_BEAR": "Bearish", "BEAR": "Bearish", "STRONG_BEAR": "Strong Bearish"}


@dataclass
class AnalysisResult:
    code: str
    name: str
    sentiment_score: int = 50
    trend_prediction: str = "Sideways"
    operation_advice: str = "Watch"
    decision_type: str = "hold"
    action: str = "watch"
    confidence_level: str = "Medium"
    guardrail_reason: str = ""
    dashboard: Dict[str, Any] = field(default_factory=dict)
    analysis_summary: str = ""
    key_points: str = ""
    risk_warning: str = ""
    buy_reason: str = ""
    trend_analysis: str = ""
    short_term_outlook: str = ""
    medium_term_outlook: str = ""
    technical_analysis: str = ""
    ma_analysis: str = ""
    volume_analysis: str = ""
    pattern_analysis: str = ""
    fundamental_analysis: str = ""
    sector_position: str = ""
    company_highlights: str = ""
    news_summary: str = ""
    market_sentiment: str = ""
    hot_topics: str = ""
    search_performed: bool = False
    data_sources: str = ""
    success: bool = True
    error_message: Optional[str] = None
    current_price: Optional[float] = None
    change_pct: Optional[float] = None
    model_used: str = ""
    analysis_date: str = ""
    is_fallback: bool = False
    style: str = "short"                      # short | swing
    swing: Dict[str, Any] = field(default_factory=dict)   # swing analysis, levels and position size (swing style only)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def emoji(self) -> str:
        return {"buy": "🟢", "sell": "🔴"}.get(self.decision_type, "🟡")


# ---- prompt -------------------------------------------------------------------------------------------------------------
def _v(x, nd=2):
    if x is None:
        return "N/A"
    if isinstance(x, float):
        return f"{x:,.{nd}f}"
    return str(x)


def build_prompt(ctx: Dict[str, Any], news_context: Optional[str]) -> str:
    code, name = ctx["code"], ctx.get("stock_name") or ctx["code"]
    today = ctx.get("today", {})
    p = [f"""# Decision Dashboard Analysis Request

## 📊 Stock Basics
| Item | Data |
|------|------|
| Stock Code | **{code}** ({ctx.get('exchange', 'NSE')}) |
| Stock Name | **{name}** |
| Sector / Industry | {ctx.get('sector') or 'N/A'} / {ctx.get('industry') or 'N/A'} |
| Analysis Date | {ctx.get('date', 'N/A')} |

---
"""]
    ph = ctx.get("market_phase") or {}
    if ph:
        p.append(f"""
## 🕒 Market Phase (IST)
| Item | Value |
|------|------|
| Phase | {ph.get('phase')} - {ph.get('label')} |
| Time | {ph.get('time_ist')} |
| Latest daily bar | {ctx.get('date')} ({'complete session' if ph.get('daily_bar_complete') else 'today is still trading; the last bar may be partial'}) |
""")
    mk = ctx.get("market_context")
    if mk:
        p.append("\n## 🌐 Market Context\n" + mk + "\n")
    p.append(f"""
## 📈 Technical Data

### Latest Daily Bar
| Indicator | Value |
|------|------|
| Close | ₹{_v(today.get('close'))} |
| Open / High / Low | ₹{_v(today.get('open'))} / ₹{_v(today.get('high'))} / ₹{_v(today.get('low'))} |
| Change | {_v(today.get('pct_chg'))}% |
| Volume | {format_volume(today.get('volume'))} |
| Turnover (value) | {format_inr(today.get('amount'))} |

### Moving Averages (key indicators)
| MA | Value | Note |
|------|------|------|
| MA5 | {_v(today.get('ma5'))} | Short-term trend line |
| MA10 | {_v(today.get('ma10'))} | Short-to-medium-term trend line |
| MA20 | {_v(today.get('ma20'))} | Medium-term trend line |
| MA Pattern | {ctx.get('ma_status', 'N/A')} | Bullish/Bearish/Tangled |
""")
    rt = ctx.get("realtime")
    if rt:
        p.append(f"""
### Quote Details
| Indicator | Value | Interpretation |
|------|------|------|
| Current Price | ₹{_v(rt.get('price'))} | |
| **Volume Ratio** | **{_v(rt.get('volume_ratio'))}** | {rt.get('volume_ratio_desc', '')} |
| **Turnover Rate** | **{_v(rt.get('turnover_rate'), 3)}%** | volume / free-float shares |
| P/E (trailing) | {_v(rt.get('pe_ratio'))} | Forward P/E {_v(rt.get('forward_pe'))} |
| P/B | {_v(rt.get('pb_ratio'))} | |
| Market Cap | {format_inr(rt.get('total_mv'))} | |
| Free-float Market Cap | {format_inr(rt.get('circ_mv'))} | |
| 52-week High / Low | ₹{_v(rt.get('high_52w'))} / ₹{_v(rt.get('low_52w'))} | |
| 60-Day Change | {_v(rt.get('change_60d'))}% | Medium-term performance |
""")
    fd = ctx.get("fundamentals") or {}
    if fd:
        p.append(f"""
### Financials and Holdings
| Indicator | Value | Note |
|------|------|------|
| Revenue (TTM) | {format_inr(fd.get('revenue'))} | |
| Net Profit (TTM) | {format_inr(fd.get('net_profit'))} | |
| Operating Cash Flow | {format_inr(fd.get('operating_cash_flow'))} | |
| ROE | {_v(fd.get('roe_pct'))}% | |
| Debt / Equity | {_v(fd.get('debt_to_equity'))} | |
| Revenue / Earnings Growth (YoY) | {_v(fd.get('revenue_growth_pct'))}% / {_v(fd.get('earnings_growth_pct'))}% | |
| Dividend Yield | {_v(fd.get('dividend_yield_pct'))}% | |
| Promoter / Institutional Holding | {_v(fd.get('promoter_holding_pct'))}% / {_v(fd.get('institutional_holding_pct'))}% | |
| Analyst Target (mean) | ₹{_v(fd.get('analyst_target_mean'))} | {fd.get('analyst_recommendation') or 'N/A'}, {fd.get('analyst_count') or 'N/A'} analysts |

> If any field above is N/A, state clearly "data unavailable, cannot judge"; never fabricate.
""")
    dv = ctx.get("delivery")
    if dv:
        p.append(f"""
### Delivery Structure (NSE delivery %, the efficiency indicator; replaces chip distribution)
| Indicator | Value | Meaning |
|------|------|------|
| Delivery % (latest, {dv['date']}) | **{dv['delivery_pct']}%** | share of traded volume taken into delivery |
| 5-day / 20-day average | {dv['delivery_pct_avg5']}% / {dv['delivery_pct_avg20']}% | trend: {dv['delivery_trend']} |
| Delivery-weighted price (20d) | ₹{_v(dv['delivery_vwap_20d'])} | price vs it: {_v(dv['price_vs_delivery_vwap_pct'])}% |
| Health | **{dv['health']}** | {dv['meaning']} |
""")
    else:
        p.append("\n### Delivery Structure\nDelivery data unavailable for this instrument (BSE-only, index, or NSE archive unreachable). "
                 "Do not invent it; mark chip_structure as unavailable.\n")
    flow = ctx.get("flow")
    if flow:
        p.append(f"""
### Institutional Flow Filter
| Indicator | Value | Decision Meaning |
|------|------|------|
| Stock flow bias (delivery-based) | {flow['bias']} | {flow['reason']} |
| FII/FPI net (cash, latest day) | {flow.get('fii') or 'N/A'} | market-wide foreign flow |
| DII net (cash, latest day) | {flow.get('dii') or 'N/A'} | domestic institutions |

> Flows are only a filter on price position: never chase near resistance while holders distribute.
""")
    band = ctx.get("price_band")
    if band:
        p.append(f"""
### Exchange Rules
| Item | Value |
|------|------|
| Circuit band | {(str(band['band_pct']) + '%') if band.get('band_pct') else 'No fixed band (F&O stock, dynamic bands)'} |
| Circuit hit today | {ctx.get('circuit_hit') or 'No'} |
| Surveillance / remarks | {band.get('remarks') or 'None'} |
""")
    tr = ctx.get("trend_analysis")
    if tr:
        p.append(f"""
### Trend Pre-analysis (deterministic, based on the trading baseline)
| Indicator | Value | Judgement |
|------|------|------|
| Trend Status | {tr['trend_status']} | {tr['ma_alignment']} |
| Trend Strength | {tr['trend_strength']}/100 | |
| **Bias (MA5)** | **{tr['bias_ma5']:+.2f}%** | {'✅ Safe range' if tr['bias_ma5'] < 5 else '⚠️ Chasing risk'} |
| Bias (MA10) | {tr['bias_ma10']:+.2f}% | |
| Volume Status | {tr['volume_status']} | {tr['volume_trend']} |
| MACD | {tr['macd_status']} | {tr['macd_signal']} |
| RSI(6/12/24) | {tr['rsi_6']:.1f} / {tr['rsi_12']:.1f} / {tr['rsi_24']:.1f} | {tr['rsi_signal']} |
| System Signal | {tr['buy_signal']} | |
| System Score | {tr['signal_score']}/100 | |

#### System Analysis Reasons
**Buy reasons**:
{chr(10).join('- ' + r for r in tr['signal_reasons']) or '- None'}

**Risk factors**:
{chr(10).join('- ' + r for r in tr['risk_factors']) or '- None'}
""")
    yday = ctx.get("yesterday") or {}
    if yday and today.get("volume") and yday.get("volume"):
        p.append(f"""
### Volume and Price Change
- Volume vs previous day: {today['volume'] / yday['volume']:.2f}x
- Price change vs previous day: {_v(today.get('pct_chg'))}%
""")
    if ctx.get("style") == "swing":
        p.append(swing_section(ctx))
    p.append("\n---\n\n## 📰 News Intelligence\n")
    if news_context:
        p.append(f"""
The following are news search results for **{name}** over the past {ctx.get('news_days', 7)} days. Extract:
1. 🚨 **Risk alerts**: SEBI action, promoter pledge or selling, surveillance, auditor exit, results shocks, downgrades
2. 🎯 **Positive catalysts**: results beats, orders, approvals, capacity, analyst upgrades
3. 📊 **Earnings expectations**: quarterly results, guidance

```
{news_context}
```
""")
    else:
        p.append("\nNo recent news found for this stock. Base the analysis mainly on the technical data and say so.\n")
    p.append(f"""
---

## ✅ Analysis Task

Generate the Decision Dashboard for **{name}({code})**, output strictly in JSON format.
""")
    if ctx.get("is_index_etf"):
        p.append("""
> ⚠️ **Index/ETF analysis constraints**: this instrument is an index-tracking ETF or a market index.
> - Risk analysis only covers: **index trend, tracking error, market liquidity**
> - Never include lawsuits, reputation or executive changes of the fund house in risk alerts
> - Earnings expectations are based on **the overall performance of the index constituents**
""")
    p.append(f"""
### ⚠️ Important: output the correct stock name format
The correct format is "Company name (code)", e.g. "Reliance Industries (RELIANCE)".
If the stock name shown above is just the code or incorrect, state the correct company name in `stock_name`.
""")
    if ctx.get("style") == "swing":
        p.append(SWING_FOCUS)
        return "".join(p)
    p.append("""
### Key focus (must be answered explicitly):
1. ❓ Is the MA5>MA10>MA20 bullish alignment met?
2. ❓ Is the current bias within the safe range (<5%)? Above 5% must be marked "do not chase"
3. ❓ Does volume confirm (low-volume pullback/high-volume breakout)?
4. ❓ Is the delivery structure healthy?
5. ❓ Is there any major negative news? (SEBI action, pledge, surveillance, results shock, etc.)
""")
    return "".join(p)


# ---- response parsing -----------------------------------------------------------------------------------------------------
def extract_json(text: str) -> Dict[str, Any]:
    if not text:
        raise ValueError("empty response")
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    candidate = m.group(1) if m else None
    if candidate is None:
        start = text.find("{")
        if start < 0:
            raise ValueError("no JSON object in response")
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                esc = (ch == "\\") and not esc
                if ch == '"' and not esc:
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    break
        if candidate is None:
            candidate = text[start:]
    for attempt in (candidate, _repair_json(candidate)):
        try:
            data = json.loads(attempt)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    raise ValueError("response JSON could not be parsed")


def _repair_json(s: str) -> str:
    s = re.sub(r"//[^\n\"]*\n", "\n", s)                      # line comments
    s = re.sub(r",\s*([}\]])", r"\1", s)                      # trailing commas
    s = re.sub(r"\bTrue\b", "true", s)
    s = re.sub(r"\bFalse\b", "false", s)
    s = re.sub(r"\bNone\b", "null", s)
    s = re.sub(r"(?<=:)\s*(true)/(false)", r" \1", s)          # copied 'true/false' placeholder
    opens = s.count("{") - s.count("}")
    if opens > 0:                                             # truncated output: close what is open
        s = s.rstrip().rstrip(",") + "}" * opens
    return s


REQUIRED = [("analysis_summary",), ("dashboard", "core_conclusion", "one_sentence"), ("dashboard", "intelligence", "risk_alerts"),
            ("dashboard", "battle_plan", "sniper_points", "stop_loss")]


def _get(d: Dict, path: Tuple[str, ...]):
    for k in path:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def check_integrity(data: Dict[str, Any]) -> List[str]:
    missing = []
    for path in REQUIRED:
        v = _get(data, path)
        if v is None or (isinstance(v, str) and not v.strip()) or (path[-1] == "risk_alerts" and not isinstance(v, list)):
            missing.append(".".join(path))
    return missing


def fill_placeholders(data: Dict[str, Any], missing: List[str]) -> None:
    for path in missing:
        keys = path.split(".")
        d = data
        for k in keys[:-1]:
            d = d.setdefault(k, {}) if isinstance(d.get(k, {}), dict) else d.setdefault(k, {})
        d[keys[-1]] = [] if keys[-1] == "risk_alerts" else "Data unavailable (model did not provide this field)"


# ---- result assembly and guardrails -----------------------------------------------------------------------------------------
def result_from_json(data: Dict[str, Any], code: str, name: str) -> AnalysisResult:
    r = AnalysisResult(code=code, name=data.get("stock_name") or name)
    for f in AnalysisResult.__dataclass_fields__:
        if f in ("code", "name") or f not in data:
            continue
        v = data[f]
        if f == "sentiment_score":
            try:
                v = max(0, min(100, int(float(v))))
            except (TypeError, ValueError):
                v = 50
        setattr(r, f, v)
    if not isinstance(r.dashboard, dict):
        r.dashboard = {}
    return r


def _flow_bias(ctx: Dict[str, Any]) -> Tuple[str, str]:
    """India proxy for the original's main capital flow: delivery % against its average, signed by the day's move."""
    dv, today = ctx.get("delivery"), ctx.get("today", {})
    if not dv or today.get("pct_chg") is None:
        return "unavailable", "no delivery data"
    hot = dv["delivery_pct"] >= dv["delivery_pct_avg20"] * 1.1
    if hot and today["pct_chg"] > 0:
        return "inflow", f"above-average delivery ({dv['delivery_pct']}%) on an up day: accumulation"
    if hot and today["pct_chg"] < 0:
        return "outflow", f"above-average delivery ({dv['delivery_pct']}%) on a down day: distribution"
    return "neutral", f"delivery {dv['delivery_pct']}% near its 20-day average {dv['delivery_pct_avg20']}%"


def apply_guardrails(r: AnalysisResult, ctx: Dict[str, Any], bias_threshold: float = 5.0, structural: bool = True) -> None:
    """1) canonical score/action consistency; 2) structural stability (the original's stabilize_decision_with_structure
    with a delivery-based flow bias); 3) the no-chase rule of the baseline; 4) confidence capped on stale/partial data.
    The structural guard exists to stop an LLM over-reacting to one day's move, so it is skipped (structural=False) for
    the deterministic trend fallback, whose signal is the original system signal."""
    band = band_for_score(r.sentiment_score)
    if r.decision_type not in ("buy", "hold", "sell"):
        r.decision_type = band.decision_type if band else "hold"
    if not r.action:
        r.action = band.action if band else "watch"
    reasons = []
    tr = ctx.get("trend_analysis") or {}
    price = r.current_price or (ctx.get("today") or {}).get("close")
    support = (tr.get("support_levels") or [None])[0]
    resistance = (tr.get("resistance_levels") or [None])[0]
    pp = ((r.dashboard.get("data_perspective") or {}).get("price_position") or {}) if isinstance(r.dashboard, dict) else {}
    support = _num(pp.get("support_level")) or support
    resistance = _num(pp.get("resistance_level")) or resistance
    flow, flow_reason = _flow_bias(ctx)

    def downgrade(advice: str, why: str):
        r.decision_type, r.action, r.operation_advice = "hold", "watch" if advice != "Hold" else "hold", advice
        r.sentiment_score = max(40, min(59, r.sentiment_score))
        reasons.append(why)

    if not structural:
        pass
    elif ctx.get("style") == "swing":
        reasons.extend(apply_swing_guardrails(r, ctx))
    elif r.decision_type == "buy" and price:
        bias = tr.get("bias_ma5")
        strong = tr.get("trend_status") == "Strong bullish" and (tr.get("trend_strength") or 0) >= 70
        limit = bias_threshold * (1.5 if strong else 1.0)
        near_res = resistance is not None and price >= resistance * 0.97 and price <= resistance * 1.01
        mid = support is not None and resistance is not None and support * 1.03 < price < resistance * 0.97
        if bias is not None and bias > limit:
            downgrade("Watch", f"Buy downgraded to Watch: price is {bias:.1f}% above MA5 (limit {limit:.1f}%), do not chase")
        elif tr.get("trend_status") in ("Bearish alignment", "Strong bearish"):
            downgrade("Watch", "Buy downgraded to Watch: moving averages are in bearish alignment")
        elif near_res and flow != "inflow":
            downgrade("Range-bound watch", f"Buy near resistance ₹{resistance:,.2f} without delivery-backed buying ({flow_reason})")
        elif flow == "outflow" and not (resistance is not None and price > resistance * 1.01):
            downgrade("Range-bound watch", f"Buy while holders distribute ({flow_reason})")
        elif mid and flow == "neutral":
            downgrade("Range-bound watch", "Price mid-range between support and resistance with neutral flows")
    elif r.decision_type == "sell" and price and support is not None:
        broke = price < support * 0.985
        near_support = not broke and price <= support * 1.03
        risky = any(any(k in str(a).lower() for k in ("sebi", "fraud", "pledge", "default", "downgrade", "raid"))
                    for a in ((r.dashboard.get("intelligence") or {}).get("risk_alerts") or []))
        if near_support and flow != "outflow" and not risky:
            downgrade("Shakeout watch", f"Sell near support ₹{support:,.2f} without distribution: possible shakeout")
        elif flow == "inflow" and not broke and not risky:
            downgrade("Hold", f"Sell while delivery shows accumulation ({flow_reason})")

    if reasons:
        r.guardrail_reason = "; ".join(reasons)
        r.dashboard.setdefault("decision_stability", {})
        r.dashboard["decision_stability"].update({"applied": True, "reason": r.guardrail_reason, "flow_bias": flow})
        core = r.dashboard.setdefault("core_conclusion", {})
        core["signal_type"] = "🟡Hold and Watch"
    band = band_for_score(r.sentiment_score)
    if band and r.decision_type != band.decision_type and not r.guardrail_reason:
        r.guardrail_reason = f"Model action '{r.operation_advice}' differs from the score band '{band.label}'"
    ph = ctx.get("market_phase") or {}
    if (not ctx.get("delivery") or not ph.get("daily_bar_complete")) and str(r.confidence_level).lower() == "high":
        r.confidence_level = "Medium"


def _num(v) -> Optional[float]:
    from .storage import parse_price
    return parse_price(v)


# ---- deterministic fallback ---------------------------------------------------------------------------------------------
def trend_fallback(ctx: Dict[str, Any], reason: str) -> AnalysisResult:
    """A complete dashboard from the trend analysis (the original's _apply_trend_fallback), used without an LLM."""
    tr = ctx.get("trend_analysis") or {}
    code, name = ctx["code"], ctx.get("stock_name") or ctx["code"]
    score = int(tr.get("signal_score") or 50) or 50
    band = band_for_score(score)
    advice = {"Strong Buy": "Buy", "Buy": "Buy", "Watch": "Watch", "Sell": "Sell", "Strong Sell": "Sell", "Hold": "Hold"}.get(
        tr.get("buy_signal"), ADVICE_FOR_BAND.get(band.signal_key if band else "watch", "Watch"))
    decision = {"Buy": "buy", "Sell": "sell"}.get(advice, "hold")
    price = (ctx.get("today") or {}).get("close")
    ma5, ma10, ma20 = tr.get("ma5"), tr.get("ma10"), tr.get("ma20")
    support = (tr.get("support_levels") or [ma20])[0] if (tr.get("support_levels") or ma20) else None
    resistance = (tr.get("resistance_levels") or [None])[0]
    dv = ctx.get("delivery")
    trend_pred = {"Strong bullish": "Strong Bullish", "Bullish alignment": "Bullish", "Weak bullish": "Bullish", "Consolidation": "Sideways",
                  "Weak bearish": "Bearish", "Bearish alignment": "Bearish", "Strong bearish": "Strong Bearish"}.get(tr.get("trend_status"), "Sideways")
    bias = tr.get("bias_ma5") or 0.0
    checks = [
        f"{'✅' if tr.get('trend_status') in ('Strong bullish', 'Bullish alignment') else '❌'} Check 1: bullish alignment ({tr.get('ma_alignment', 'N/A')})",
        f"{'✅' if bias < 2 else '⚠️' if bias < 5 else '❌'} Check 2: bias {bias:+.1f}% from MA5",
        f"{'✅' if tr.get('volume_status') in ('Low-volume pullback', 'High-volume rise') else '⚠️'} Check 3: volume ({tr.get('volume_status', 'N/A')})",
        "⚠️ Check 4: news not judged (technical-only fallback)",
        f"{'✅' if dv and dv['health'] == 'Healthy' else '⚠️'} Check 5: delivery structure ({dv['health'] if dv else 'unavailable'})",
        "⚠️ Check 6: valuation not judged (technical-only fallback)",
    ]
    one = (f"{advice}: {tr.get('trend_status', 'trend unknown')}, bias {bias:+.1f}% from MA5, system score {score}/100 "
           f"(technical-only; AI analysis unavailable)")
    r = AnalysisResult(
        code=code, name=name, sentiment_score=score, trend_prediction=trend_pred, operation_advice=advice, decision_type=decision,
        action=band.action if band else "watch", confidence_level="Low",
        analysis_summary=one + ". " + " ".join(tr.get("signal_reasons", [])[:3]),
        key_points=", ".join((tr.get("signal_reasons") or [])[:4]), risk_warning="; ".join(tr.get("risk_factors") or []) or "None from the technicals",
        buy_reason="; ".join(tr.get("signal_reasons") or []), technical_analysis=ctx.get("trend_text", ""),
        ma_analysis=tr.get("ma_alignment", ""), volume_analysis=tr.get("volume_trend", ""), current_price=price,
        change_pct=(ctx.get("today") or {}).get("pct_chg"), data_sources="price:" + str(ctx.get("data_source")) + ",trend:fallback",
        model_used="technical fallback", is_fallback=True, error_message=reason,
        dashboard={
            "core_conclusion": {"one_sentence": one, "signal_type": {"buy": "🟢Buy Signal", "sell": "🔴Sell Signal"}.get(decision, "🟡Hold and Watch"),
                                "time_sensitivity": "No rush",
                                "position_advice": {"no_position": "Buy only near MA5/MA10 support with a stop below MA20" if decision == "buy" else "Wait for a bullish setup",
                                                    "has_position": "Hold with a stop below MA20" if decision != "sell" else "Reduce or exit; trend has deteriorated"}},
            "data_perspective": {
                "trend_status": {"ma_alignment": tr.get("ma_alignment"), "is_bullish": tr.get("trend_status") in ("Strong bullish", "Bullish alignment"),
                                 "trend_score": tr.get("trend_strength")},
                "price_position": {"current_price": price, "ma5": _r(ma5), "ma10": _r(ma10), "ma20": _r(ma20), "bias_ma5": round(bias, 2),
                                   "bias_status": "Safe" if bias < 2 else "Caution" if bias < 5 else "Danger",
                                   "support_level": _r(support), "resistance_level": _r(resistance)},
                "volume_analysis": {"volume_ratio": round(tr.get("volume_ratio_5d") or 0, 2), "volume_status": tr.get("volume_status"),
                                    "turnover_rate": (ctx.get("realtime") or {}).get("turnover_rate"), "volume_meaning": tr.get("volume_trend")},
                "chip_structure": as_chip_structure(dv),
            },
            "intelligence": {"latest_news": "Not analysed (AI unavailable)", "risk_alerts": tr.get("risk_factors") or [],
                             "positive_catalysts": [], "earnings_outlook": "Not analysed", "sentiment_summary": "Not analysed"},
            "battle_plan": {
                "sniper_points": {"ideal_buy": f"Ideal buy: ₹{_r(ma5)} (near MA5)", "secondary_buy": f"Secondary buy: ₹{_r(ma10)} (near MA10)",
                                  "stop_loss": f"Stop-loss: ₹{_r((ma20 or 0) * 0.98)} (2% below MA20)",
                                  "take_profit": f"Target: ₹{_r(resistance or (price or 0) * 1.08)} ({'recent high' if resistance else '+8%'})"},
                "position_strategy": {"suggested_position": "Suggested position: 3/10" if decision == "buy" else "Suggested position: 0/10",
                                      "entry_plan": "Stage entries at MA5 then MA10", "risk_control": "Exit on a close below MA20"},
                "action_checklist": checks,
            },
            "phase_decision": {"phase_context": {"phase": (ctx.get("market_phase") or {}).get("phase", "unknown")},
                               "action_window": "Post-market review", "immediate_action": "Wait for confirmation",
                               "watch_conditions": ["Close above MA5 with normal or higher delivery", "No close below MA20"],
                               "next_check_time": "Next session close (15:30 IST)",
                               "confidence_reason": "Technical-only fallback: no news or fundamentals judged",
                               "data_limitations": [reason]},
        })
    return r


def _r(x):
    try:
        return round(float(x), 2)
    except (TypeError, ValueError):
        return None


class StockAnalyzer:
    def __init__(self, router: LLMRouter, bias_threshold: float = 5.0):
        self.router, self.bias_threshold = router, bias_threshold

    def _fallback(self, ctx, reason):
        return swing_fallback(ctx, reason) if ctx.get("style") == "swing" else trend_fallback(ctx, reason)

    def analyze(self, ctx: Dict[str, Any], news_context: Optional[str]) -> Tuple[AnalysisResult, str]:
        """Returns (result, raw_text). Falls back to the trend (or swing) dashboard when no LLM works."""
        swing = ctx.get("style") == "swing"
        system = SWING_SYSTEM_PROMPT if swing else SYSTEM_PROMPT
        if not self.router.available:
            r = self._fallback(ctx, "No LLM provider configured")
            apply_guardrails(r, ctx, self.bias_threshold, structural=False)
            return r, ""
        prompt = build_prompt(ctx, news_context)
        try:
            res = self.router.generate(system, prompt, json_mode=True)
            data = extract_json(res.text)
            missing = check_integrity(data)
            if missing:
                logger.info("%s: model omitted %s; asking once more", ctx["code"], missing)
                retry = self.router.generate(system, prompt + "\n\nYour previous answer omitted these required fields: "
                                             + ", ".join(missing) + ". Output the complete JSON again with every field filled.", json_mode=True)
                try:
                    data2 = extract_json(retry.text)
                    if len(check_integrity(data2)) < len(missing):
                        data, res = data2, retry
                except ValueError:
                    pass
                fill_placeholders(data, check_integrity(data))
        except (AllProvidersFailed, ValueError) as e:
            logger.error("%s: AI analysis failed (%s); using the technical fallback", ctx["code"], e)
            r = self._fallback(ctx, f"AI analysis failed: {e}")
            apply_guardrails(r, ctx, self.bias_threshold, structural=False)
            return r, ""
        r = result_from_json(data, ctx["code"], ctx.get("stock_name") or ctx["code"])
        r.current_price = (ctx.get("today") or {}).get("close")
        r.change_pct = (ctx.get("today") or {}).get("pct_chg")
        r.model_used = f"{res.provider}:{res.model}"
        r.search_performed = bool(news_context)
        if swing:
            r.style, r.swing = "swing", {**(ctx.get("swing") or {}), "position_size": ctx.get("position_size") or {}}
        dp = r.dashboard.setdefault("data_perspective", {})
        if not dp.get("chip_structure") or not ctx.get("delivery"):
            dp["chip_structure"] = as_chip_structure(ctx.get("delivery"))
        apply_guardrails(r, ctx, self.bias_threshold)
        return r, res.text
