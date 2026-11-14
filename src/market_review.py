"""India market review (the original's src/market_analyzer.py with an India region profile).

Data: NSE allIndices (NIFTY 50/NEXT 50/BANK/MIDCAP/SMALLCAP/500, INDIA VIX, sector indices, NSE-wide breadth), SENSEX from
yfinance, circuit counts (upper/lower) computed from the latest NSE bhavcopy and the price-band list (India's version of the
A-share limit-up/down counts), cash-market turnover, FII/DII flows, and market news. A deterministic data section is always
written; the LLM adds the recap commentary, with a rule-based template when no LLM is available (as in the original).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .india.market import circuit_status, now_ist
from .llm.providers import AllProvidersFailed

logger = logging.getLogger(__name__)

MAIN_INDICES = ["NIFTY 50", "NIFTY NEXT 50", "NIFTY BANK", "NIFTY MIDCAP 100", "NIFTY SMALLCAP 250", "NIFTY 500", "INDIA VIX"]
YF_FALLBACK = {"NIFTY 50": "^NSEI", "NIFTY BANK": "^NSEBANK", "INDIA VIX": "^INDIAVIX", "NIFTY NEXT 50": "^NSMIDCP", "NIFTY 500": "^CRSLDX"}
NEWS_QUERIES = ["Sensex Nifty closing market today", "Indian stock market sectors FII", "Nifty outlook tomorrow"]

STRATEGY_BLOCK = """## Strategy Blueprint: India Market Regime Strategy
Focus on NIFTY/BANK NIFTY trend, India VIX, FII/DII flows and sector rotation to define the next-session risk posture.

### Strategy Principles
- Read the regime from NIFTY 50, BANK NIFTY and the midcap/smallcap indices first; check whether breadth confirms.
- Treat FII/FPI cash flows as the key sentiment driver and DII flows as the support/absorption side.
- Translate the recap into a risk-on / neutral / risk-off stance with clear invalidation levels.

### Analysis Dimensions
- Trend Regime: momentum, range or risk-off. Are NIFTY, BANK NIFTY and broader indices aligned? Did breadth confirm?
- Flows and Volatility: FII vs DII net flows, India VIX level and direction, rupee/crude/US-yield narrative.
- Sector Themes: persistent leaders vs laggards (IT, banks, PSU, metals, pharma, auto, FMCG, realty, energy, defence).

### Action Framework
- Risk-on: broad advance with positive FII flows and falling VIX.
- Neutral: mixed index signals; focus on selective relative strength.
- Risk-off: failed breakouts, FII selling and rising VIX; prioritise capital preservation."""


@dataclass
class MarketOverview:
    date: str
    indices: List[Dict] = field(default_factory=list)
    advances: Optional[int] = None
    declines: Optional[int] = None
    unchanged: Optional[int] = None
    upper_circuit: Optional[int] = None
    lower_circuit: Optional[int] = None
    turnover_cr: Optional[float] = None
    top_sectors: List[Dict] = field(default_factory=list)
    bottom_sectors: List[Dict] = field(default_factory=list)
    fii_dii: List[Dict] = field(default_factory=list)
    limits: List[str] = field(default_factory=list)


class MarketAnalyzer:
    def __init__(self, fetcher, search=None, router=None):
        self.fetcher, self.search, self.router = fetcher, search, router

    def get_market_overview(self) -> MarketOverview:
        ov = MarketOverview(date=now_ist().strftime("%Y-%m-%d"))
        data = self.fetcher.get_all_indices()
        if data and data.get("data"):
            rows = {r["index"]: r for r in data["data"]}
            for name in MAIN_INDICES:
                r = rows.get(name)
                if r:
                    ov.indices.append({"name": name, "last": r.get("last"), "pct_change": r.get("percentChange"), "change": r.get("variation"),
                                       "high": r.get("high"), "low": r.get("low"), "advances": _int(r.get("advances")),
                                       "declines": _int(r.get("declines")), "pe": r.get("pe")})
            ov.advances, ov.declines, ov.unchanged = _int(data.get("advances")), _int(data.get("declines")), _int(data.get("unchanged"))
            ts = str(data.get("timestamp") or "")
            if ts:
                try:
                    from datetime import datetime as _dt
                    ov.date = _dt.strptime(ts.split(" ")[0], "%d-%b-%Y").date().isoformat()
                except ValueError:
                    ov.date = ts.split(" ")[0]
            sectors = [r for r in data["data"] if r.get("key") == "SECTORAL INDICES" and r.get("percentChange") is not None]
            sectors.sort(key=lambda r: float(r["percentChange"]), reverse=True)
            ov.top_sectors = [{"name": r["index"], "pct_change": r["percentChange"]} for r in sectors[:5]]
            ov.bottom_sectors = [{"name": r["index"], "pct_change": r["percentChange"]} for r in sectors[-5:][::-1]]
        else:
            ov.limits.append("NSE index data unavailable; index levels from Yahoo Finance, no breadth or sector data")
            for name, sym in YF_FALLBACK.items():
                q = self.fetcher.get_index_quote_yf(sym)
                if q:
                    ov.indices.append({"name": name, "last": round(q["last"], 2), "pct_change": q["pct_change"]})
        sensex = self.fetcher.get_index_quote_yf("^BSESN")
        if sensex:
            ov.indices.insert(1 if ov.indices else 0, {"name": "SENSEX", "last": round(sensex["last"], 2), "pct_change": sensex["pct_change"]})
        self._circuits_and_turnover(ov)
        ov.fii_dii = self.fetcher.get_fii_dii()
        if not ov.fii_dii:
            ov.limits.append("FII/DII flow data unavailable")
        return ov

    def _circuits_and_turnover(self, ov: MarketOverview) -> None:
        arch = getattr(self.fetcher, "nse_archive", None)
        if arch is None or not getattr(self.fetcher, "nse_enabled", True):
            return
        try:
            days = arch.recent_days(1)
            rows = arch.day(days[-1]) if days else None
            if not rows:
                return
            up = down = 0
            turnover = 0.0
            for sym, row in rows.items():
                try:
                    close, prev = float(row["CLOSE_PRICE"]), float(row["PREV_CLOSE"])
                    turnover += float(row["TURNOVER_LACS"] or 0)
                except (KeyError, ValueError):
                    continue
                band = (arch.price_band(sym) or {}).get("band_pct")
                st = circuit_status(close, prev, band)
                up += st == "upper"
                down += st == "lower"
            ov.upper_circuit, ov.lower_circuit, ov.turnover_cr = up, down, round(turnover / 100, 0)
        except Exception as e:
            logger.info("circuit counts unavailable: %s", e)
            ov.limits.append("Circuit counts and turnover unavailable (NSE archive not reachable)")

    def mood(self, ov: MarketOverview) -> Dict:
        """Market light: a 0-100 mood score from NIFTY's move, breadth and India VIX (the original's market light snapshot)."""
        nifty = next((i for i in ov.indices if i["name"] == "NIFTY 50"), None)
        vix = next((i for i in ov.indices if i["name"] == "INDIA VIX"), None)
        score, reasons = 50.0, []
        if nifty and nifty.get("pct_change") is not None:
            ch = float(nifty["pct_change"])
            score += max(-25, min(25, ch * 15))
            reasons.append(f"NIFTY 50 {ch:+.2f}%")
        if ov.advances and ov.declines:
            ratio = ov.advances / max(1, ov.declines)
            score += max(-15, min(15, (ratio - 1) * 15))
            reasons.append(f"breadth {ov.advances}:{ov.declines}")
        if vix and vix.get("pct_change") is not None:
            v = float(vix["pct_change"])
            score -= max(-10, min(10, v))
            reasons.append(f"VIX {v:+.2f}%")
        score = int(max(0, min(100, round(score))))
        light = "🟢 Risk-on" if score >= 60 else "🔴 Risk-off" if score < 40 else "🟡 Neutral"
        return {"score": score, "light": light, "reasons": reasons}

    def data_block(self, ov: MarketOverview) -> str:
        lines = ["### 📊 Major Indices", "", "| Index | Last | Change |", "|------|------|------|"]
        for i in ov.indices:
            ch = i.get("pct_change")
            # India VIX is a fear gauge: a falling VIX is calming, so it is not coloured as a gain or loss
            arrow = "⚪" if i["name"] == "INDIA VIX" else "🟢" if (ch or 0) > 0 else "🔴" if (ch or 0) < 0 else "⚪"
            lines.append(f"| {i['name']} | {i.get('last')} | {arrow} {ch:+.2f}% |" if ch is not None else f"| {i['name']} | {i.get('last')} | N/A |")
        if ov.advances is not None:
            lines += ["", "### 📈 Market Breadth (NSE, all traded securities)", "",
                      f"Advances: {ov.advances} | Declines: {ov.declines} | Unchanged: {ov.unchanged}"
                      + (f" | Upper circuit: {ov.upper_circuit} | Lower circuit: {ov.lower_circuit}" if ov.upper_circuit is not None else "")
                      + (f" | Cash turnover: ₹{ov.turnover_cr:,.0f} Cr" if ov.turnover_cr else "")]
        if ov.top_sectors:
            fmt = lambda xs: ", ".join(f"{x['name'].replace('NIFTY ', '')} ({float(x['pct_change']):+.2f}%)" for x in xs)  # noqa: E731
            lines += ["", "### 🔥 Sector Performance", "", f"Leading: {fmt(ov.top_sectors)}", f"Lagging: {fmt(ov.bottom_sectors)}"]
        if ov.fii_dii:
            lines += ["", "### 💰 Institutional Flows (cash market, ₹ Cr)", ""]
            for f in ov.fii_dii:
                if f.get("net_cr") is not None:
                    lines.append(f"- {f['category']} ({f.get('date')}): net {f['net_cr']:+,.2f} (buy {f.get('buy_cr'):,.2f} / sell {f.get('sell_cr'):,.2f})")
        if ov.limits:
            lines += ["", "### Data Limits", ""] + [f"- {x}" for x in ov.limits]
        return "\n".join(lines)

    def search_market_news(self) -> List:
        if not self.search or not self.search.is_available:
            return []
        try:
            return self.search.search_market_news(NEWS_QUERIES)
        except Exception as e:
            logger.warning("market news search failed: %s", e)
            return []

    def _prompt(self, ov: MarketOverview, news: List) -> str:
        news_text = "\n".join(f"- {n.title} ({n.source}, {n.published_date})" for n in news[:10]) or "- No market news found"
        return f"""You are a professional Indian equity market (NSE/BSE) analyst. Please produce a concise market recap report based on the data below.

[Requirements]
- Output pure Markdown only; no JSON; no code blocks
- Use emoji sparingly in headings (at most one per heading)
- Base every statement on the data below; never invent index levels, breadth, flows or news

---

# Today's Market Data

## Date
{ov.date}

{self.data_block(ov)}

## Market News
{news_text}

{STRATEGY_BLOCK}

---

# Output Template (follow this structure; do not repeat the data tables)

### 1. Market Summary
(2-3 sentences: the day's character, the regime, and the headline driver)

### 2. Index Commentary
(NIFTY 50, SENSEX, BANK NIFTY, midcap/smallcap: alignment, key levels, India VIX)

### 3. Breadth and Flows
(advance/decline, circuits, turnover, FII vs DII)

### 4. Sector Themes
(leaders and laggards, rotation)

### 5. News Catalysts
(the 2-4 items that matter for tomorrow)

### 6. Next-Session Plan
(risk-on / neutral / risk-off stance, position sizing, levels that invalidate the view)
"""

    def _template_review(self, ov: MarketOverview, news: List, mood: Dict) -> str:
        nifty = next((i for i in ov.indices if i["name"] == "NIFTY 50"), {})
        ch = nifty.get("pct_change")
        tone = ("a broad advance" if (ch or 0) > 0.5 else "a broad decline" if (ch or 0) < -0.5 else "a range-bound session")
        fii = next((f for f in ov.fii_dii if str(f.get("category", "")).startswith("FII")), None)
        flow = f"FII/FPI net {fii['net_cr']:+,.0f} Cr" if fii and fii.get("net_cr") is not None else "FII flows unavailable"
        stance = {"🟢 Risk-on": "lean into relative-strength leaders with normal size",
                  "🔴 Risk-off": "keep exposure light and protect capital until breadth and flows improve",
                  "🟡 Neutral": "stay selective; trade only clear setups near support"}[mood["light"]]
        lines = ["### 1. Market Summary", "",
                 f"NIFTY 50 {('%+.2f%%' % ch) if ch is not None else 'N/A'} in {tone}; market light {mood['light']} (score {mood['score']}/100: "
                 f"{', '.join(mood['reasons'])}). {flow}.", ""]
        if news:
            lines += ["### 5. News Catalysts", ""] + [f"- {n.title} ({n.source})" for n in news[:5]] + [""]
        lines += ["### 6. Next-Session Plan", "", f"Stance: {stance}.", ""]
        lines.append("*Template recap: no AI model configured or available.*")
        return "\n".join(lines)

    def run_daily_review(self) -> str:
        return self.run_review_payload()["report"]

    def run_review_payload(self) -> Dict:
        """The review as structured data (for the web UI) plus the Markdown report."""
        from dataclasses import asdict
        ov = self.get_market_overview()
        news = self.search_market_news()
        mood = self.mood(ov)
        head = [f"# 🎯 {ov.date} India Market Review", "", f"> Market light: **{mood['light']}** (score {mood['score']}/100)", ""]
        body = None
        if self.router is not None and self.router.available:
            try:
                body = self.router.generate("You are a professional Indian stock-market analysis assistant. Never fabricate data.",
                                            self._prompt(ov, news), json_mode=False, max_tokens=8000).text.strip()
            except AllProvidersFailed as e:
                logger.error("market review LLM failed (%s); using the template", e)
        if not body:
            body = self._template_review(ov, news, mood)
        report = "\n".join(head) + "\n" + self.data_block(ov) + "\n\n---\n\n" + body + \
            "\n\n*Market data for information only; not investment advice.*"
        return {"report": report, "commentary": body, "overview": asdict(ov), "mood": mood,
                "news": [n.to_dict() for n in news[:10]], "generated_ist": now_ist().isoformat(timespec="minutes"),
                "ai": bool(self.router is not None and self.router.available)}


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
