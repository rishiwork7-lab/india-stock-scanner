import pytest

from src.llm.providers import LLMResult, LLMRouter, ProviderError
from src.market_review import MarketAnalyzer
from src.search_service import SearchResult

ALL_INDICES = {"timestamp": "13-Nov-2026 15:30", "advances": 3001, "declines": 6529, "unchanged": 61, "data": [
    {"key": "BROAD MARKET INDICES", "index": "NIFTY 50", "last": 25144.3, "percentChange": -0.61, "variation": -154.0},
    {"key": "BROAD MARKET INDICES", "index": "NIFTY BANK", "last": 61034.1, "percentChange": -0.79},
    {"key": "BROAD MARKET INDICES", "index": "INDIA VIX", "last": 15.5, "percentChange": 4.2},
    {"key": "SECTORAL INDICES", "index": "NIFTY IT", "last": 1, "percentChange": 1.2},
    {"key": "SECTORAL INDICES", "index": "NIFTY METAL", "last": 1, "percentChange": -2.4},
    {"key": "SECTORAL INDICES", "index": "NIFTY PHARMA", "last": 1, "percentChange": 0.3}]}


class Arch:
    def recent_days(self, n):
        return ["d"]

    def day(self, d):
        return {"AAA": {"CLOSE_PRICE": "105", "PREV_CLOSE": "100", "TURNOVER_LACS": "1000"},    # +5% with a 5% band -> upper
                "BBB": {"CLOSE_PRICE": "90", "PREV_CLOSE": "100", "TURNOVER_LACS": "500"},      # -10% with a 10% band -> lower
                "CCC": {"CLOSE_PRICE": "130", "PREV_CLOSE": "100", "TURNOVER_LACS": "500"}}     # F&O stock, no band

    def price_band(self, s):
        return {"AAA": {"band_pct": 5.0}, "BBB": {"band_pct": 10.0}, "CCC": {"band_pct": None}}[s]


class Fetcher:
    nse_enabled = True

    def __init__(self, nse=True):
        self.nse = nse
        self.nse_archive = Arch()

    def get_all_indices(self):
        return ALL_INDICES if self.nse else None

    def get_index_quote_yf(self, sym):
        return {"last": 81592.45 if sym == "^BSESN" else 25000.0, "pct_change": -0.5}

    def get_fii_dii(self):
        return [{"category": "FII/FPI", "date": "13-Nov-2026", "net_cr": 5736.55, "buy_cr": 18339.57, "sell_cr": 12603.02}] if self.nse else []


class Search:
    is_available = True

    def search_market_news(self, queries):
        return [SearchResult("Sensex falls 400 points as metals drag", source="Mint", published_date="2026-11-13")]


def test_overview_breadth_circuits_sectors():
    m = MarketAnalyzer(Fetcher(), Search())
    ov = m.get_market_overview()
    assert ov.date == "2026-11-13" and [i["name"] for i in ov.indices][:2] == ["NIFTY 50", "SENSEX"]
    assert (ov.upper_circuit, ov.lower_circuit, ov.turnover_cr) == (1, 1, 20.0)
    assert ov.top_sectors[0]["name"] == "NIFTY IT" and ov.bottom_sectors[0]["name"] == "NIFTY METAL"
    mood = m.mood(ov)
    assert mood["light"] == "🔴 Risk-off" and mood["score"] < 40       # NIFTY down, breadth 1:2, VIX up


def test_template_review_without_llm_and_llm_review():
    text = MarketAnalyzer(Fetcher(), Search(), LLMRouter([])).run_daily_review()
    assert "India Market Review" in text and "Advances: 3001" in text and "Template recap" in text and "metals drag" in text

    class P:
        name, model, available = "anthropic", "claude-opus-5-5", True

        def __init__(self):
            self.prompt = None

        def generate(self, system, prompt, json_mode, max_tokens=16000):
            self.prompt = prompt
            return LLMResult("### 1. Market Summary\nBroad decline led by metals.", "anthropic", "claude-opus-5-5")
    p = P()
    text = MarketAnalyzer(Fetcher(), Search(), LLMRouter([p])).run_daily_review()
    assert "Broad decline led by metals" in text and "FII/FPI" in p.prompt and "India Market Regime" in p.prompt


def test_review_degrades_when_nse_unavailable():
    ov = MarketAnalyzer(Fetcher(nse=False)).get_market_overview()
    assert ov.indices and ov.advances is None and any("unavailable" in x for x in ov.limits)
