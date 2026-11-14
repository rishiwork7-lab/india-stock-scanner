from datetime import date

import pandas as pd

from data_provider.base import normalise_daily
from src.backtest import BacktestEngine, BacktestService, format_summary
from src.storage import Database


def test_intent_reading():
    assert BacktestEngine.infer_direction_expected("Buy") == "up"
    assert BacktestEngine.infer_direction_expected("Range-bound watch") == "not_down"
    assert BacktestEngine.infer_direction_expected("Do not buy") == "flat"
    assert BacktestEngine.infer_position_recommendation("Reduce") == "cash"


def test_service_over_history(tmp_path):
    db = Database(tmp_path / "b.db")
    closes = [100.0, 101, 103, 104, 106, 108, 107, 109, 111, 112, 113, 115]
    df = normalise_daily(pd.DataFrame({"date": pd.bdate_range("2026-10-01", periods=12), "open": closes,
                                       "high": [c * 1.01 for c in closes], "low": [c * 0.99 for c in closes], "close": closes, "volume": 1.0}))
    db.save_daily_data(df, "TCS", "test")
    buy = {"code": "TCS", "name": "TCS", "sentiment_score": 70, "operation_advice": "Buy", "current_price": 100.0,
           "dashboard": {"battle_plan": {"sniper_points": {"stop_loss": "₹96", "take_profit": "Target ₹105"}}}}
    sell = dict(buy, operation_advice="Sell", sentiment_score=15)
    db.save_analysis(buy, "q", "simple", None, None, date(2026, 10, 1))
    db.save_analysis(sell, "q", "simple", None, None, date(2026, 10, 1))
    out = BacktestService(db).run(eval_window_days=5)
    s = out["summaries"]["TCS"]
    assert out["evaluated"] == 2 and s["completed_count"] == 2
    assert s["win_count"] == 1 and s["loss_count"] == 1                        # buy won (+6%), sell lost
    assert s["take_profit_trigger_rate"] == 100.0                               # 105 target hit within 5 bars
    assert BacktestService(db).run(eval_window_days=5)["evaluated"] == 0         # already evaluated, not repeated
    assert "direction accuracy 50.0%" in format_summary(s)
    short = BacktestService(db).run(eval_window_days=30, force=True)["summaries"]["TCS"]
    assert short["insufficient_count"] == 2
