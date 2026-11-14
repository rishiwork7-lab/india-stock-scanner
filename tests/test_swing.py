from datetime import date

import numpy as np
import pandas as pd

from src.analyzer import SWING_SYSTEM_PROMPT, StockAnalyzer, apply_guardrails, build_prompt, result_from_json
from src.india.symbols import parse_symbol
from src.llm.providers import LLMRouter
from src.swing_analyzer import analyze_swing, position_size
from tests.test_pipeline import make


def frame(closes, vols=None):
    n = len(closes)
    c = np.array(closes, dtype=float)
    return pd.DataFrame({"date": pd.bdate_range("2025-01-01", periods=n), "open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
                         "volume": vols if vols is not None else np.full(n, 1e6)})


def nifty(n, slope=0.0):
    return pd.Series(np.linspace(20000, 20000 * (1 + slope), n), index=pd.bdate_range("2025-01-01", periods=n))


def test_stage2_pullback_is_a_buy_with_levels():
    # steady uptrend, then a 3-day dip to the rising MA20 on light volume
    up = list(np.linspace(100, 200, 300))
    closes = up + [196.0, 194.5, 194.0]
    vols = [1.2e6 if i % 2 else 0.8e6 for i in range(300)] + [5e5] * 3
    r = analyze_swing(frame(closes, vols), "UP", nifty(303, 0.10))
    assert r.trend_points == 25 and r.rs_3m > 0 and r.signal in ("Buy", "Watch")
    assert r.stop < r.entry and r.risk_pct <= 8.0 and r.target1 == round(r.entry + 2 * (r.entry - r.stop), 2)


def test_downtrend_is_avoid_and_extended_is_not_buy():
    down = analyze_swing(frame(list(np.linspace(200, 100, 300))), "DN", nifty(300))
    assert down.signal == "Avoid" and down.trend_points <= 10
    hot = analyze_swing(frame(list(np.linspace(100, 150, 280)) + [150 * 1.03 ** i for i in range(1, 15)]), "HOT", nifty(294))
    assert hot.setup == "extended" and hot.signal != "Buy"


def test_stop_never_more_than_8_percent_and_position_size():
    wild = list(np.linspace(100, 200, 280)) + [200, 170, 205, 165, 210]
    r = analyze_swing(frame(wild), "W", nifty(285))
    assert r.risk_pct <= 8.0 + 1e-9
    ps = position_size(100.0, 95.0, 100000, 1.0)
    assert ps["shares"] == 200 and ps["risk_amount"] == 1000.0
    assert position_size(100.0, 99.9, 10000, 2.0)["limited_by_cash"] is True            # cash delivery: capital caps shares


def test_short_history_reported():
    r = analyze_swing(frame([100.0] * 30), "S")
    assert not r.ok and r.risks


def test_swing_prompt_and_guardrail():
    assert "Stage-2" in SWING_SYSTEM_PROMPT and "max 8%" in SWING_SYSTEM_PROMPT and "near MA5" not in SWING_SYSTEM_PROMPT
    sw = analyze_swing(frame(list(np.linspace(100, 150, 280)) + [150 * 1.03 ** i for i in range(1, 15)]), "HOT", nifty(294)).to_dict()
    ctx = {"code": "HOT", "stock_name": "Hot Ltd", "style": "swing", "swing": sw, "today": {"close": sw["price"]},
           "position_size": {"shares": 10}, "capital": 100000, "risk_pct_setting": 1.0}
    p = build_prompt(ctx, None)
    assert "Swing Analysis (2-8 week horizon" in p and "Stage-2 uptrend (trend template)" in p
    r = result_from_json({"sentiment_score": 78, "decision_type": "buy", "action": "buy", "operation_advice": "Buy", "dashboard": {}}, "HOT", "Hot")
    apply_guardrails(r, ctx)
    assert r.decision_type == "hold" and "extended" in r.guardrail_reason


def test_swing_pipeline_end_to_end(tmp_path, monkeypatch):
    p, post, provider, fetcher = make(tmp_path)
    p.analyzer = StockAnalyzer(LLMRouter([]))          # no AI: deterministic swing fallback
    monkeypatch.setattr(p.calendar, "latest_completed_session", lambda now=None: date(2026, 11, 13))
    res = p.run([parse_symbol("RELIANCE")], send_notification=False, style="swing")
    r = res[0]
    assert r.style == "swing" and r.is_fallback and r.swing["entry"] and r.swing["position_size"]["shares"] >= 0
    assert p.db.count_daily("RELIANCE") >= 260                       # swing fetched the longer history
    hist = p.db.list_history(style="swing")
    assert len(hist) == 1 and hist[0]["style"] == "swing" and p.db.list_history(style="short") == []
    rep = next((tmp_path / "reports").glob("report_swing_*.md")).read_text()
    assert "Swing (2-8 weeks) Decision Dashboard" in rep and "📐 **Swing**" in rep
    # a short-term run on the same stock is stored separately
    p.run([parse_symbol("RELIANCE")], send_notification=False, style="short")
    assert len(p.db.list_history(style="short")) == 1
