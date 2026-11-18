import numpy as np
import pandas as pd

from src import journal
from src.scorecard import groups, outcomes, scorecard


def px(start, daily, n=80, first="2026-01-01"):
    dates = pd.bdate_range(first, periods=n)
    c = start * np.cumprod(np.full(n, 1 + daily))
    return pd.DataFrame({"date": dates, "open": c, "high": c, "low": c, "close": c, "volume": 1e6})


def scan(date):
    def st(code, sig, score, mom):
        return {"code": code, "industry": "X", "price": 100.0, "ret_1y": mom, "ret_1m": 0.0, "traded_value_cr": 20,
                "swing": {"signal": sig, "setup": "pullback", "atr_pct": 2.0}, "short": {"score": score, "label": "Buy"}, "events": {}}
    return {"date": date, "stocks": [st("UP", "Buy", 85, 50), st("FLAT", "Avoid", 30, 0), st("OLD", "Watch", 50, 10) | {"stale": True}],
            "etfs": [{"code": "ETF1", "price": 10.0, "tracks": "Gold", "swing": {"signal": "Watch"}, "short": {}}]}


def test_diary_rows_save_load_and_recent_symbols(tmp_path):
    rows = journal.rows_from_scan(scan("2026-01-05"), 38, "Risk-off", True)
    assert [r["code"] for r in rows] == ["UP", "FLAT", "ETF1"] and rows[0]["mom_12_1"] == 50.0 and rows[0]["mood"] == "Risk-off"
    f = journal.save(tmp_path, rows)
    b1 = f.read_bytes()
    journal.save(tmp_path, rows)
    assert f.read_bytes() == b1                                                 # same day, same data -> identical file
    journal.save(tmp_path, rows[:1])
    assert f.read_bytes() == b1                                                 # fewer rows for the same day: kept the fuller file
    journal.save(tmp_path, journal.rows_from_scan(scan("2026-01-06")))
    d = journal.load(tmp_path)
    assert len(d) == 6 and list(d.columns) == journal.FIELDS and len(journal.load(tmp_path, since="2026-01-06")) == 3
    assert journal.recent_symbols(tmp_path) == ["ETF1", "FLAT", "UP"]


def test_outcomes_use_next_open_and_subtract_costs_and_the_average():
    diary = pd.DataFrame(journal.rows_from_scan(scan("2026-01-05")))
    prices = {"UP": px(100, 0.01), "FLAT": px(100, 0.0), "ETF1": px(10, 0.0)}
    d = outcomes(diary, prices).set_index("code")
    up5 = (1.01 ** 5 / 1.01 ** 1 - 1) * 100 - 0.30                              # bar 2 (05 Jan) -> open bar 3, close bar 7
    assert abs(d.loc["UP", "ret_5"] - up5) < 1e-9 and abs(d.loc["FLAT", "ret_5"] + 0.30) < 1e-9 and abs(d.loc["ETF1", "ret_5"] + 0.15) < 1e-9
    assert abs(d.loc["UP", "ex_5"] - (up5 - (up5 - 0.30) / 2)) < 1e-9
    assert not np.isnan(d.loc["UP", "ret_60"]) and np.isnan(outcomes(diary, {"UP": px(100, 0.01, n=20)}).set_index("code").loc["UP", "ret_60"])


def test_scorecard_groups_and_too_early_label():
    rows = []
    for day in pd.bdate_range("2026-01-05", periods=25):
        rows += journal.rows_from_scan(scan(day.strftime("%Y-%m-%d")), 70, "Risk-on", True)
    diary = pd.DataFrame(rows)
    sc = scorecard(diary, {"UP": px(100, 0.01, n=120), "FLAT": px(100, 0.0, n=120), "ETF1": px(10, 0.0, n=120)})
    b = sc["horizons"]["5"]["Swing signal: Buy"]
    assert sc["days_recorded"] == 25 and b["dates"] == 25 and b["status"] == "holding up" and b["vs_average_pct"] > 0
    assert sc["horizons"]["5"]["Swing signal: Avoid"]["status"] == "worse than average"
    assert "Swing Buy when the market mood is risk-on" in sc["horizons"]["5"]
    g = groups(outcomes(diary.head(3), {}))
    assert g["12-month strength, fifth 5 (strongest)"].sum() == 1


def test_lagging_tickers_are_downloaded_again():
    from src.site_builder import refetch_lagging
    first = {"A": px(100, 0, n=10), "B": px(100, 0, n=9), "C": px(100, 0, n=10), "^NSEI": px(100, 0, n=8)}
    calls = []

    def dl(syms):
        calls.append(list(syms))
        return {s: px(100, 0.01, n=10) for s in syms}
    out = refetch_lagging(first, dl, lambda m: None)
    assert calls == [["B"]] and len(out["B"]) == 10 and len(out["^NSEI"]) == 8
