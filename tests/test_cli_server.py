import time
from datetime import datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

import main as cli
import server
from src.analyzer import StockAnalyzer
from src.backtest import BacktestService
from src.config import Config
from src.india.market import IST, TradingCalendar
from src.llm.providers import LLMRouter
from src.market_review import MarketAnalyzer
from src.notification import NotificationService
from src.pipeline import StockAnalysisPipeline
from src.scheduler import next_run_time, run_scheduler
from src.storage import Database
from tests.test_market_review import Fetcher as MarketFetcher, Search as MarketSearch
from tests.test_pipeline import FakeFetcher, FakeSearch, JSONProvider


def test_cli_flags_match_original():
    a = cli.parse_args(["--stocks", "RELIANCE,500325", "--no-notify", "--workers", "2", "--force-run", "--no-market-review",
                        "--serve", "--port", "9000", "--backtest-days", "20"])
    assert a.stocks == "RELIANCE,500325" and a.no_notify and a.workers == 2 and a.force_run and a.no_market_review
    assert a.serve and a.port == 9000 and a.backtest_days == 20
    assert cli.parse_args(["--webui-only"]).serve_only and cli.parse_args(["--serve-only"]).serve_only


def test_scheduler_times_in_ist():
    assert next_run_time("18:30", datetime(2026, 11, 13, 10, 0, tzinfo=IST)) == datetime(2026, 11, 13, 18, 30, tzinfo=IST)
    assert next_run_time("18:30", datetime(2026, 11, 13, 19, 0, tzinfo=IST)).day == 14
    clock = {"t": datetime(2026, 11, 13, 18, 29, 30, tzinfo=IST)}
    runs = []

    def sleep(s):
        clock["t"] = clock["t"].replace(minute=31)
    run_scheduler(lambda: runs.append(1), "18:30", run_immediately=True, sleep=sleep, clock=lambda: clock["t"], max_runs=2)
    assert len(runs) == 2


def build_fake_app(tmp_path, token=""):
    cfg = Config(db_path=tmp_path / "s.db", reports_dir=tmp_path / "r", api_auth_token=token, trading_day_check_enabled=True)
    db = Database(cfg.db_path)
    cal = TradingCalendar(fetch_holidays=lambda: [])
    router = LLMRouter([JSONProvider()])
    notifier = NotificationService(cfg, post=lambda *a, **k: SimpleNamespace(status_code=200))
    pipe = StockAnalysisPipeline(cfg, FakeFetcher(), db, FakeSearch(), StockAnalyzer(router), notifier, cal)
    return SimpleNamespace(config=cfg, db=db, calendar=cal, router=router, notifier=notifier, pipeline=pipe,
                           search=SimpleNamespace(providers=[]), market=MarketAnalyzer(MarketFetcher(), MarketSearch()),
                           backtest=BacktestService(db))


def wait(client, tid, headers=None):
    for _ in range(100):
        t = client.get(f"/api/v1/tasks/{tid}", headers=headers or {}).json()
        if t["status"] != "running":
            return t
        time.sleep(0.05)
    raise AssertionError("task did not finish")


def test_api_analysis_history_review_backtest(tmp_path):
    server.set_app(build_fake_app(tmp_path))
    c = TestClient(server.app)
    assert c.get("/").status_code == 200 and "India Stock Scanner" in c.get("/").text
    assert c.get("/api/v1/health").json()["status"] == "ok"
    r = c.post("/api/v1/analysis", json={"stocks": ["RELIANCE", "bad code!", "TCS"]}).json()
    assert r["stocks"] == ["RELIANCE", "TCS"] and r["skipped"] == ["bad code!"]
    t = wait(c, r["task_id"])
    assert t["status"] == "done" and len(t["results"]) == 2 and "<table" in t["report_html"]
    hist = c.get("/api/v1/history").json()
    item = c.get(f"/api/v1/history/{hist[0]['id']}").json()
    assert item["item"]["code"] in ("RELIANCE", "TCS") and "Battle Plan" in item["report"]
    rv = wait(c, c.post("/api/v1/market-review", json={}).json()["task_id"])
    assert rv["status"] == "done" and "India Market Review" in rv["report"]
    assert "__overall__" in c.get("/api/v1/backtest?days=5").json()["summaries"]
    assert c.post("/api/v1/analysis", json={"stocks": ["!!"]}).status_code == 400


def test_api_token(tmp_path):
    server.set_app(build_fake_app(tmp_path, token="s3cret"))
    c = TestClient(server.app)
    assert c.get("/api/v1/health").status_code == 401
    assert c.get("/api/v1/health", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_run_once_skips_non_trading_day(tmp_path, monkeypatch):
    app = build_fake_app(tmp_path)
    monkeypatch.setattr(cli, "now_ist", lambda: datetime(2026, 11, 14, 18, 30, tzinfo=IST))   # Saturday
    args = cli.parse_args(["--stocks", "RELIANCE"])
    assert cli.run_once(app, args) == 0 and app.db.list_history() == []
    args = cli.parse_args(["--stocks", "RELIANCE", "--force-run", "--no-market-review", "--no-notify"])
    cli.run_once(app, args)
    assert len(app.db.list_history()) == 1


def test_ui_endpoints(tmp_path):
    server.set_app(build_fake_app(tmp_path))
    c = TestClient(server.app)
    assert "stock_list" in c.get("/api/v1/config").json()
    assert c.get("/api/v1/market-review/latest").status_code == 404
    wait(c, c.post("/api/v1/analysis", json={"stocks": ["RELIANCE", "TCS"]}).json()["task_id"])
    latest = c.get("/api/v1/latest").json()
    assert [r["code"] for r in latest] == ["RELIANCE", "TCS"] and latest[0]["dashboard"]["battle_plan"]
    assert [r["code"] for r in c.get("/api/v1/latest?codes=TCS").json()] == ["TCS"]
    bars = c.get("/api/v1/candles/RELIANCE?days=30").json()
    assert len(bars) == 30 and bars[-1]["ma20"] is not None
    assert c.get("/api/v1/candles/INFY").status_code == 404
    wait(c, c.post("/api/v1/market-review", json={}).json()["task_id"])
    m = c.get("/api/v1/market-review/latest").json()
    assert m["mood"]["light"] and m["overview"]["indices"] and "report_html" in m


def test_api_swing_style(tmp_path):
    server.set_app(build_fake_app(tmp_path))
    c = TestClient(server.app)
    wait(c, c.post("/api/v1/analysis", json={"stocks": ["RELIANCE"], "style": "swing"}).json()["task_id"])
    swing = c.get("/api/v1/latest?style=swing").json()
    assert swing and swing[0]["style"] == "swing" and swing[0]["swing"]["entry"]
    assert c.get("/api/v1/latest?style=short").json() == []
    bars = c.get("/api/v1/candles/RELIANCE?days=250").json()
    assert bars[-1]["ma200"] is not None and bars[-1]["ma50"] is not None
    assert c.get("/api/v1/backtest?days=5&style=swing").json()["analyses"] == 1
    cfg = c.get("/api/v1/config").json()
    assert cfg["trading_style"] == "short" and cfg["capital"] == 100000
