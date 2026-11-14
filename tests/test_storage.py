from datetime import date

import pandas as pd
import pytest

from data_provider.base import normalise_daily
from src.storage import Database, parse_price


@pytest.mark.parametrize("text,value", [
    ("Ideal buy point: ₹1,335.50 (near MA5)", 1335.5),
    ("Stop-loss: 1,290 (break below MA20 or 3%)", 1290.0),
    ("Target: Rs. 1450", 1450.0),
    ("Stop-loss: 3% below entry", None),
    ("N/A", None),
    (1420.0, 1420.0),
    ("Secondary entry: 12,34,567.25 near MA10", 1234567.25),
])
def test_parse_price(text, value):
    assert parse_price(text) == value


def test_daily_resume_history_and_backtest(tmp_path):
    db = Database(tmp_path / "t.db")
    df = normalise_daily(pd.DataFrame({"date": pd.bdate_range("2026-11-02", periods=5), "open": 1.0, "high": 2.0, "low": 0.5,
                                       "close": [10.0, 11, 12, 13, 14], "volume": 100.0}))
    assert db.save_daily_data(df, "RELIANCE", "yfinance") == 5
    assert db.save_daily_data(df, "RELIANCE", "yfinance") == 0           # idempotent
    assert db.has_today_data("RELIANCE", date(2026, 11, 6)) and not db.has_today_data("RELIANCE", date(2026, 11, 9))
    assert len(db.get_bars_after("RELIANCE", date(2026, 11, 3), 10)) == 3
    result = {"code": "RELIANCE", "name": "Reliance", "sentiment_score": 72, "operation_advice": "Buy", "current_price": 14.0,
              "dashboard": {"battle_plan": {"sniper_points": {"ideal_buy": "Ideal buy: 13.5 (near MA5)", "stop_loss": "Stop: 12.8",
                                                              "take_profit": "Target: ₹16"}}}}
    hid = db.save_analysis(result, "q1", "simple", "news", {"x": 1}, date(2026, 11, 6))
    h = db.get_history(hid)
    assert h["stop_loss"] == 12.8 and h["take_profit"] == 16 and h["ideal_buy"] == 13.5 and h["raw_result"]["code"] == "RELIANCE"
    assert db.list_history(code="RELIANCE")[0]["sentiment_score"] == 72
    db.save_backtest(hid, "RELIANCE", 10, "v1", {"eval_status": "completed", "outcome": "win"})
    assert db.has_backtest(hid, 10, "v1") and db.backtest_results("RELIANCE", 10, "v1")[0]["outcome"] == "win"
