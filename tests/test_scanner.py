import json
from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd

from src.events import EventService, is_results, results_reaction, sessions_until, stock_events
from src.india.market import TradingCalendar
from src.scanner import INDEX, run_scan, sector_ranking
from src.universe import ETFS, INDEX_LISTS, load_universe

CAL = TradingCalendar(fetch_holidays=lambda: [date(2026, 11, 24)])
TODAY = date(2026, 11, 16)    # a Monday


def bars(n=300, start=100.0, drift=0.001, end="2026-11-13", jumps=None, seed=1):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=end, periods=n)
    r = drift + rng.normal(0, 0.01, n)
    for d, j in (jumps or {}).items():
        r[list(dates.date).index(d)] = j
    c = start * np.cumprod(1 + r)
    return pd.DataFrame({"date": dates, "open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1e6})


# ---- universe ---------------------------------------------------------------------------------------------------------
def csv(syms, series="EQ"):
    return "Company Name,Industry,Symbol,Series,ISIN Code\n" + "\n".join(f"Co {s},Ind,{s},{series},INE000" for s in syms)


def fake_nse(u):
    if "nifty500list" in u:
        return SimpleNamespace(status_code=200, text=csv([f"S{i}" for i in range(60)]) + "\nDummy,Ind,DUMMYX,EQ,I\nReit,Ind,REITX,RR,I\nT2T,Ind,BEX,BE,I")
    if "niftymedialist" in u:
        return SimpleNamespace(status_code=200, text=csv(["S1", "S2", "OUTSIDE1", "OUTSIDE2"]))
    return SimpleNamespace(status_code=200, text=csv(["S0", "S1", "S2"]))


def test_universe_lists_memberships_and_fallback(tmp_path):
    u = load_universe(tmp_path, http_get=fake_nse)
    codes = [m["symbol"] for m in u["members"]]
    assert len(codes) == 61 and "BEX" in codes and "DUMMYX" not in codes and "REITX" not in codes   # BE kept; placeholders and REITs out
    assert set(u["indices"]) == {k for k, *_ in INDEX_LISTS} and u["stale_lists"] == []
    assert u["indices"]["niftymedia"] == {"label": "Nifty Media", "group": "Sector", "codes": ["S1", "S2"], "total": 4}
    assert (tmp_path / "niftyindiadefence.csv").exists()                     # cached under the clean name

    def down(url):
        raise ConnectionError("blocked")
    real = load_universe(tmp_path / "empty", http_get=down)                   # bundled copies of the real lists
    assert len(real["members"]) >= 490 and len(real["indices"]["nifty50"]["codes"]) == 50 and len(real["stale_lists"]) == len(INDEX_LISTS)


def test_etf_list_is_unique_and_categorised():
    syms = [e.symbol for e in ETFS]
    assert len(syms) == len(set(syms)) and {e.category for e in ETFS} == {"Broad market", "Sector", "Factor", "Global", "Commodity"}


# ---- events -----------------------------------------------------------------------------------------------------------
def test_sessions_and_results_words():
    assert sessions_until(CAL, TODAY, TODAY) == 0
    assert sessions_until(CAL, TODAY, date(2026, 11, 20)) == 4                 # Tue..Fri
    assert sessions_until(CAL, date(2026, 11, 20), date(2026, 11, 27)) == 4    # skips the weekend and the 24th holiday
    assert is_results("Financial Results") and is_results("Results/Dividend") and not is_results("Fund Raising", "bonds")


def test_results_reaction_measures_two_session_move():
    d1, d2 = date(2026, 7, 20), date(2026, 10, 19)
    df = bars(jumps={d1: 0.08, d2: -0.06}, drift=0.0)
    rr = results_reaction(df, [d1, d2, date(2010, 1, 1)])                     # 2010: outside the price history
    assert rr["n"] == 2 and rr["max_abs_pct"] >= 7 and rr["times_normal"] > 3
    assert {m["date"] for m in rr["moves"]} == {d1.isoformat(), d2.isoformat()} and rr["up_count"] == 1


def test_stock_events_warnings():
    df = bars()
    raw = {"results": [{"date": date(2026, 8, 3), "source": "NSE"}, {"date": date(2026, 11, 19), "source": "NSE"}],
           "actions": [{"ex_date": date(2026, 11, 18), "subject": "Interim Dividend - Rs 5 Per Share"},
                       {"ex_date": date(2026, 12, 10), "subject": "Face Value Split (Sub-Division) - From Rs 10/- To Rs 2/-"}],
           "sources": ["NSE board meetings"]}
    ev = stock_events(TODAY, CAL, df, raw)
    assert ev["next_results"] == {"date": "2026-11-19", "source": "NSE", "confirmed": True, "sessions": 3, "days": 3}
    levels = [w["level"] for w in ev["warnings"]]
    assert levels[0] == "high" and "medium" in levels and "low" in levels
    raw["results"][1] = {"date": date(2026, 12, 21), "source": "Yahoo estimate"}
    ev = stock_events(TODAY, CAL, df, raw)
    assert ev["next_results"]["confirmed"] is False and ev["warnings"][0]["level"] == "medium" and "expected" in ev["warnings"][0]["text"]


class FakeNSE:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, 0

    upcoming = []

    def event_calendar(self, sym, start=None, end=None):
        if sym is None:
            return self.upcoming
        self.calls += 1
        if self.fail:
            raise ConnectionError("403")
        return [{"date": date(2026, 7, 20), "purpose": "Financial Results", "desc": ""},
                {"date": date(2026, 7, 20), "purpose": "Results/Dividend", "desc": ""},
                {"date": date(2026, 9, 1), "purpose": "Fund Raising", "desc": "bonds"}]

    def corporate_actions(self, sym):
        if self.fail:
            raise ConnectionError("403")
        return [{"ex_date": date(2026, 11, 20), "subject": "Dividend"}]


def test_event_service_nse_yahoo_fallback_and_cache(tmp_path):
    now = lambda: pd.Timestamp("2026-11-16 12:00").timestamp()  # noqa: E731
    yahoo = lambda s, need_past=True: {"next": date(2027, 1, 20), "past": [date(2026, 4, 20)] if need_past else [], "ex_dividend": None}  # noqa: E731
    nse = FakeNSE()
    svc = EventService(tmp_path, nse, yahoo, now)
    raw = svc.raw("ABC")
    assert [e["source"] for e in raw["results"]] == ["NSE", "Yahoo estimate"]
    nse.upcoming = [{"symbol": "ABC", "date": date(2026, 12, 2), "purpose": "Financial Results", "desc": ""},
                    {"symbol": "XYZ", "date": date(2026, 12, 3), "purpose": "Dividend", "desc": ""}]
    assert svc.load_upcoming() == 1
    ev = stock_events(TODAY, CAL, None, svc.raw("ABC"))
    assert ev["next_results"]["date"] == "2026-12-02" and ev["next_results"]["confirmed"]       # confirmed NSE date beats the estimate
    svc.confirmed = {}
    raw = svc.raw("ABC")
    assert [e["source"] for e in raw["results"]] == ["NSE", "Yahoo estimate"]      # duplicate date dropped, non-results ignored
    svc.raw("ABC")
    assert nse.calls == 1                                                          # second call served from the disk cache
    raw = EventService(tmp_path / "b", FakeNSE(fail=True), yahoo, now).raw("ABC")
    assert [e["source"] for e in raw["results"]] == ["Yahoo", "Yahoo estimate"] and raw["sources"] == ["Yahoo calendar"]


# ---- scanner ------------------------------------------------------------------------------------------------------------
def prices():
    p = {INDEX: bars(seed=9, drift=0.0004), "UPCO": bars(seed=2, drift=0.003), "DOWNCO": bars(seed=3, drift=-0.002),
         "OLDCO": bars(seed=4, end="2026-11-12")}
    for i, e in enumerate(ETFS[:6]):
        p[e.symbol] = bars(seed=10 + i, drift=0.001 * (i - 2))
    return p


def test_run_scan_rows_ranking_and_flags():
    members = [{"symbol": s, "name": s.title(), "industry": "X"} for s in ("UPCO", "DOWNCO", "OLDCO", "NODATA")]
    nav = {ETFS[0].symbol: {"price": 101.0, "nav": 100.0, "nav_date": "2026-11-12"}}
    out = run_scan(members, prices(), TODAY, CAL, None, nav)
    codes = [r["code"] for r in out["stocks"]]
    assert set(codes) == {"UPCO", "DOWNCO", "OLDCO"} and "NODATA" in out["missing"]
    assert out["stocks"][0]["swing"]["score"] >= out["stocks"][-1]["swing"]["score"]
    up = next(r for r in out["stocks"] if r["code"] == "UPCO")
    assert up["swing"]["signal"] in ("Buy", "Watch") and up["short"]["score"] >= 0 and up["ret_1y"] > 0
    assert next(r for r in out["stocks"] if r["code"] == "DOWNCO")["swing"]["signal"] == "Avoid"
    assert next(r for r in out["stocks"] if r["code"] == "OLDCO")["stale"] is True and not up["stale"]
    etf = next(r for r in out["etfs"] if r["code"] == ETFS[0].symbol)
    assert etf["premium_pct"] == 1.0 and etf["category"] == "Broad market"
    assert all(r["code"] in {e.symbol for e in ETFS[:6]} for r in out["etfs"])
    json.dumps(out, default=str)                                                   # the site writes it as JSON


def test_sector_ranking_orders_by_momentum():
    rows = [{"code": c, "category": "Sector", "tracks": c, "ret_1m": 0, "ret_3m": m3, "ret_6m": m6, "above_ma200": True, "swing": {"signal": "Watch"}}
            for c, m3, m6 in (("A", 5, 5), ("B", 10, 20), ("C", -3, 1))] + [{"code": "G", "category": "Commodity", "ret_3m": 50, "swing": {}}]
    r = sector_ranking(rows)
    assert [x["code"] for x in r] == ["B", "A", "C"] and r[0]["rank"] == 1 and r[0]["momentum"] == 15


# ---- static site ------------------------------------------------------------------------------------------------------
def test_build_site_writes_static_files(tmp_path, monkeypatch):
    from src import site_builder
    from tests.test_cli_server import build_fake_app
    app = build_fake_app(tmp_path)
    app.config.nse_enabled, app.config.stock_list = False, ["RELIANCE"]
    app.fetcher = app.pipeline.fetcher
    monkeypatch.setattr(site_builder, "load_universe", lambda d: {"members": [{"symbol": "UPCO", "name": "Up Co", "industry": "X"}],
                        "indices": {"nifty50": {"label": "Nifty 50", "group": "Broad market", "codes": ["UPCO", "GONE"], "total": 2}}, "stale_lists": []})
    meta = site_builder.build_site(app, tmp_path / "site", downloader=lambda syms: prices(),
                                   event_yahoo=lambda s, need_past=True: {"next": date(2027, 1, 20), "past": [], "ex_dividend": None})
    data = tmp_path / "site" / "data"
    assert (tmp_path / "site" / "index.html").exists() and (tmp_path / "site" / ".nojekyll").exists()
    scan = json.loads((data / "scan.json").read_text())
    assert [r["code"] for r in scan["stocks"]] == ["UPCO"] and len(scan["etfs"]) == 6
    assert scan["indices"]["nifty50"]["codes"] == ["UPCO"] and scan["indices"]["nifty50"]["total"] == 2
    assert (data / "candles" / "UPCO.json").exists() and (data / "candles" / "RELIANCE.json").exists()
    analysis = json.loads((data / "analysis.json").read_text())
    assert analysis["swing"][0]["code"] == "RELIANCE" and analysis["short"][0]["style"] == "short"
    assert json.loads((data / "market.json").read_text())["mood"]
    assert meta["stocks"] == 1 and json.loads((data / "meta.json").read_text())["data_date"] == "2026-11-13"
    candle = json.loads((data / "candles" / "UPCO.json").read_text())
    assert len(candle["time"]) == 260 == len(candle["close"]) and candle["ma200"][-1] is not None
