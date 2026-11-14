import numpy as np
import pandas as pd

from src.decision_scale import band_for_score, signal_key_for_score
from src.delivery import analyze_delivery, as_chip_structure
from src.stock_analyzer import BuySignal, StockTrendAnalyzer, TrendStatus


def frame(closes, volumes=None):
    n = len(closes)
    c = np.array(closes, dtype=float)
    return pd.DataFrame({"date": pd.bdate_range("2026-01-01", periods=n), "open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
                         "volume": volumes if volumes is not None else np.full(n, 1e6)})


def test_scale_bands():
    assert [signal_key_for_score(s) for s in (95, 80, 79, 60, 59, 40, 39, 20, 19, 0)] == \
        ["strong_buy", "strong_buy", "buy", "buy", "watch", "watch", "reduce", "reduce", "sell", "sell"]
    assert band_for_score(101) is None and band_for_score("x") is None


def test_steady_uptrend_with_pullback_scores_as_buy():
    closes = list(np.linspace(100, 160, 60)) + [158.5]          # rising trend, small dip on the last day
    vols = [1e6] * 60 + [5e5]                                     # on light volume
    r = StockTrendAnalyzer().analyze(frame(closes, vols), "TEST")
    assert r.trend_status in (TrendStatus.BULL, TrendStatus.STRONG_BULL)
    assert r.volume_status.name == "SHRINK_VOLUME_DOWN"
    assert r.signal_score >= 60 and r.buy_signal in (BuySignal.BUY, BuySignal.STRONG_BUY)


def test_downtrend_is_sell_and_overextension_is_flagged():
    down = StockTrendAnalyzer().analyze(frame(list(np.linspace(160, 100, 60))), "D")
    # oversold RSI lifts the score into the Watch band; the original checks the band before the bear trend, so it is Watch
    assert down.trend_status in (TrendStatus.BEAR, TrendStatus.STRONG_BEAR) and down.buy_signal not in (BuySignal.BUY, BuySignal.STRONG_BUY)
    assert any("not suitable for longs" in x for x in down.risk_factors)
    # strong trend relaxes the chase limit from 5% to 7.5% (bias 5.2% -> "light position", not "do not chase")
    mild = StockTrendAnalyzer().analyze(frame([100.0] * 40 + [100 + 3 * i for i in range(1, 8)]), "S")
    assert 5 < mild.bias_ma5 < 7.5 and any("light position" in x for x in mild.signal_reasons)
    hot = StockTrendAnalyzer().analyze(frame([100.0] * 40 + [100 * 1.06 ** i for i in range(1, 8)]), "H")
    assert hot.bias_ma5 > 7.5 and any("do not chase" in x for x in hot.risk_factors)


def test_short_history_is_reported_not_crashed():
    r = StockTrendAnalyzer().analyze(frame([100.0] * 10), "SHORT")
    assert r.signal_score == 0 and r.risk_factors


def test_delivery_health():
    rows = [{"date": f"2026-11-{i:02d}", "close": 100 + i, "volume": 1e6, "deliv_qty": 6e5, "deliv_pct": 60.0} for i in range(1, 21)]
    d = analyze_delivery(rows)
    assert d["health"] == "Healthy" and d["delivery_trend"] == "steady" and d["delivery_vwap_20d"] > 100
    falling = [dict(r, deliv_pct=60.0 if i < 15 else 30.0) for i, r in enumerate(rows)]
    assert analyze_delivery(falling)["health"] == "Caution"           # price up while delivery collapses
    assert analyze_delivery([])is None
    assert as_chip_structure(None)["available"] is False
    assert as_chip_structure(d)["chip_health"] == "Healthy"
