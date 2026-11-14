import json
from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd

from data_provider.base import normalise_daily
from src.analyzer import StockAnalyzer
from src.config import Config
from src.india.market import TradingCalendar
from src.india.symbols import parse_symbol
from src.llm.providers import LLMResult, LLMRouter
from src.notification import NotificationService, chunk, markdown_to_html, markdown_to_plain
from src.pipeline import StockAnalysisPipeline
from src.report import generate_dashboard_report
from src.search_service import SearchResponse, SearchResult
from src.storage import Database


class FakeFetcher:
    def __init__(self):
        self.downloads = 0

    def get_daily_data(self, t, days):
        self.downloads += 1
        n = max(90, days)
        dates = pd.bdate_range(end="2026-11-13", periods=n)
        c = np.linspace(1200, 1340, n)
        return normalise_daily(pd.DataFrame({"date": dates, "open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1e6})), "fake"

    def get_stock_name(self, t):
        return {"RELIANCE": "Reliance Industries Limited", "TCS": "Tata Consultancy Services"}[t.symbol]

    def get_realtime_quote(self, t, df):
        return {"price": float(df["close"].iloc[-1]), "volume_ratio": 1.0, "pe_ratio": 25.0}

    def get_delivery(self, t, days):
        return [{"date": f"2026-11-{i:02d}", "close": 1300 + i, "volume": 1e6, "deliv_qty": 5e5, "deliv_pct": 50.0} for i in range(1, 21)]

    def get_price_band(self, t):
        return {"band_pct": None, "remarks": None}

    def get_fundamentals(self, t):
        return {"sector": "Energy"}

    def get_all_indices(self):
        return {"data": [{"index": "NIFTY 50", "last": 25144.3, "percentChange": -0.61}], "advances": 3001, "declines": 6529}

    def get_fii_dii(self):
        return [{"category": "FII/FPI", "date": "13-Nov-2026", "net_cr": 5736.55}, {"category": "DII", "date": "13-Nov-2026", "net_cr": -2253.58}]


class FakeSearch:
    is_available = True

    def search_comprehensive_intel(self, t, name):
        return {"latest_news": SearchResponse("q", "fake", [SearchResult(f"{name} wins order", "big order", "u", "2026-11-12", "ET")])}

    def format_intel_report(self, intel, name):
        return f"[{name}] wins order"


class JSONProvider:
    name, model, available = "anthropic", "claude-opus-5-5", True

    def __init__(self):
        self.prompts = []

    def generate(self, system, prompt, json_mode, max_tokens=16000):
        self.prompts.append(prompt)
        code = "TCS" if "(TCS)" in prompt else "RELIANCE"
        score = 45 if code == "TCS" else 70
        return LLMResult(json.dumps({
            "stock_name": code, "sentiment_score": score, "trend_prediction": "Bullish", "operation_advice": "Buy" if score >= 60 else "Watch",
            "decision_type": "buy" if score >= 60 else "hold", "action": "buy" if score >= 60 else "watch", "confidence_level": "Medium",
            "analysis_summary": f"{code} summary",
            "dashboard": {"core_conclusion": {"one_sentence": f"{code} one sentence", "position_advice": {"no_position": "a", "has_position": "b"}},
                          "intelligence": {"risk_alerts": ["Risk 1: valuation"]},
                          "battle_plan": {"sniper_points": {"ideal_buy": "₹1,330", "stop_loss": "₹1,290", "take_profit": "₹1,450"}}}}),
            self.name, self.model)


class Post:
    def __init__(self):
        self.calls = []

    def __call__(self, url, **kw):
        self.calls.append((url, kw))
        return SimpleNamespace(status_code=200, text="ok")


def make(tmp_path, **cfg):
    config = Config(db_path=tmp_path / "t.db", reports_dir=tmp_path / "reports", max_workers=2, email_sender="me@gmail.com",
                    email_password="app-pass", **cfg)
    post = Post()
    provider = JSONProvider()
    fetcher = FakeFetcher()
    p = StockAnalysisPipeline(config, fetcher, Database(config.db_path), FakeSearch(), StockAnalyzer(LLMRouter([provider])),
                              NotificationService(config, post=post), TradingCalendar(fetch_holidays=lambda: []))
    p.notifier.send = lambda content, title="": post.calls.append(("email", {"title": title})) or {"email": True}
    return p, post, provider, fetcher


def test_full_run_end_to_end(tmp_path, monkeypatch):
    import src.india.market as mk
    p, post, provider, fetcher = make(tmp_path)
    monkeypatch.setattr(p.calendar, "latest_completed_session", lambda now=None: date(2026, 11, 13))
    results = p.run([parse_symbol("RELIANCE"), parse_symbol("TCS")])
    assert {r.code for r in results} == {"RELIANCE", "TCS"}
    prompt = provider.prompts[0]
    assert "NIFTY 50" in prompt and "FII/FPI" in prompt and "Delivery Structure" in prompt and "wins order" in prompt
    hist = p.db.list_history()
    assert len(hist) == 2
    reliance = next(h for h in hist if h["code"] == "RELIANCE")
    assert p.db.get_history(reliance["id"])["stop_loss"] == 1290
    report = next((tmp_path / "reports").glob("report_*.md")).read_text()
    assert any(u == "email" for u, _ in post.calls)
    assert report.index("RELIANCE") < report.index("TCS")             # sorted by score
    # resume: second run does not download again
    downloads = fetcher.downloads
    p.run([parse_symbol("RELIANCE")], send_notification=False)
    assert fetcher.downloads == downloads


def test_dry_run_fetches_only(tmp_path):
    p, post, provider, fetcher = make(tmp_path)
    assert p.run([parse_symbol("RELIANCE")], dry_run=True) == [] and fetcher.downloads == 1 and not provider.prompts and not post.calls


def test_report_modes_and_notification_helpers(tmp_path):
    p, post, provider, _ = make(tmp_path)
    rs = p.run([parse_symbol("RELIANCE")], send_notification=False)
    simple = generate_dashboard_report(rs, "2026-11-13", "simple")
    full = generate_dashboard_report(rs, "2026-11-13", "full")
    assert "Decision Dashboard" in simple and "Battle Plan" in simple and "Data Perspective" not in simple
    assert "Data Perspective" in full and "Delivery structure" in full
    parts = chunk("a" * 50 + "\n\n" + "b" * 50, 60)
    assert len(parts) == 2 and all(len(x) <= 60 for x in parts)
    assert "#" not in markdown_to_plain("## Title **bold**") and "<table" in markdown_to_html("| a | b |\n|---|---|\n| 1 | 2 |")


def test_email_channel_uses_html(tmp_path):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            sent["host"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, u, p):
            sent["login"] = u

        def sendmail(self, f, to, msg):
            sent["to"], sent["msg"] = to, msg

    c = Config(email_sender="me@gmail.com", email_password="app-pass", email_receivers=["a@x.in"])
    res = NotificationService(c, smtp_factory=FakeSMTP).send("# Title\n\n| a | b |\n|---|---|\n| 1 | 2 |", "Report")
    assert res == {"email": True} and sent["host"] == ("smtp.gmail.com", 465) and "text/html" in sent["msg"]
