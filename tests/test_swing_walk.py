import sys; from pathlib import Path; sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import pandas as pd
import swing_metrics as m
def df(bars):  # bars: list of (open, high, low, close) after a dummy signal bar
    rows = [(100, 100, 100, 100)] + bars + [(bars[-1][3],) * 4] * 45
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"])


def test_trade_walker():
    # entry 100    , stop 95, T1 110, T2 115
    w = m.walk(df([(100, 103, 99, 102), (102, 111, 101, 110), (110, 116, 109, 115)]), 0, 100, 95, 110, 115)
    assert w["hit_t1"] and w["hit_t2"] and round(w["r_c"], 2) == 2.0 and round(w["r_a"], 2) == 3.0 and round(w["r_b"], 2) == 2.5, w
    w = m.walk(df([(100, 101, 94, 96)]), 0, 100, 95, 110, 115)
    assert w["stopped"] and round(w["r_a"], 2) == -1.0 and round(w["r_b"], 2) == -1.0, w
    w = m.walk(df([(100, 101, 99, 100), (90, 92, 88, 91)]), 0, 100, 95, 110, 115)   # gap below the stop: fills at the open
    assert w["stopped"] and round(w["r_a"], 2) == -2.0, w
    w = m.walk(df([(100, 111, 99, 110), (110, 111, 99, 100)]), 0, 100, 95, 110, 115)  # T1, then back to breakeven
    assert w["hit_t1"] and not w["hit_t2"] and round(w["r_b"], 2) == 1.0 and round(w["r_a"], 2) == 0.0, w

