from datetime import date, timedelta

import pandas as pd
import pytest

from data_provider.base import DataUnavailable, SourceFailure, normalise_daily
from data_provider.bse import BSEArchive
from data_provider.manager import DataFetcherManager
from data_provider.nse import NSEArchive
from src.india.symbols import parse_symbol

NSE_HEAD = "SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER"


def nse_csv(d: date, close: float, deliv: float) -> str:
    return (NSE_HEAD + "\n"
            f"RELIANCE, EQ, {d:%d-%b-%Y}, {close - 5}, {close - 4}, {close + 6}, {close - 9}, {close}, {close}, {close - 1}, 1000000, {close * 10:.2f}, 5000, {int(deliv * 1e4)}, {deliv}\n"
            f"RELIANCE, BL, {d:%d-%b-%Y}, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1\n")


class FakeResp:
    def __init__(self, status, text=""):
        self.status_code, self.text = status, text


class FakeHttp:
    """Serves bhavcopies for weekdays in `days`; 404 otherwise. Counts calls to prove caching."""

    def __init__(self, days):
        self.days, self.calls = days, 0

    def get(self, url):
        self.calls += 1
        for d, (close, deliv) in self.days.items():
            if f"{d:%d%m%Y}" in url:
                return FakeResp(200, nse_csv(d, close, deliv))
        return FakeResp(404)


def weekdays_back(n, end=date(2026, 11, 13)):
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def test_nse_archive_delivery_daily_and_cache(tmp_path, monkeypatch):
    days = weekdays_back(6)
    import data_provider.nse as nse_mod
    monkeypatch.setattr(nse_mod, "now_ist", lambda: pd.Timestamp("2026-11-13 20:00").to_pydatetime())
    http = FakeHttp({d: (1000 + i * 10, 40 + i) for i, d in enumerate(days)})
    a = NSEArchive(tmp_path, http=http)
    dl = a.delivery("RELIANCE", 5)
    assert [x["deliv_pct"] for x in dl] == [41, 42, 43, 44, 45] and dl[-1]["series"] == "EQ"   # EQ row wins over BL
    df = a.daily("RELIANCE", 5)
    assert list(df["close"]) == [1010, 1020, 1030, 1040, 1050] and df["amount"].iloc[-1] == pytest.approx(10500 * 1e5)
    calls = http.calls
    b = NSEArchive(tmp_path, http=http)                       # a new instance reads the disk cache
    b.delivery("RELIANCE", 5)
    assert http.calls == calls
    with pytest.raises(DataUnavailable):
        a.delivery("NOSUCH", 3)


def test_holiday_file_with_previous_sessions_date_is_ignored(tmp_path, monkeypatch):
    import data_provider.nse as nse_mod
    monkeypatch.setattr(nse_mod, "now_ist", lambda: pd.Timestamp("2026-11-13 20:00").to_pydatetime())
    mon, tue = date(2026, 11, 9), date(2026, 11, 10)

    class H:
        def get(self, url):
            if f"{mon:%d%m%Y}" in url or f"{tue:%d%m%Y}" in url:
                return FakeResp(200, nse_csv(mon, 1000, 50))      # Tuesday's URL serves Monday's file
            return FakeResp(404)
    a = NSEArchive(tmp_path, http=H())
    assert a.day(tue) is None and a.day(mon) is not None
    assert (tmp_path / "nse_bhav" / "20261110.missing").exists()


class FakeYF:
    def __init__(self, fail=False, last_date="2026-11-12"):
        self.fail, self.last_date = fail, last_date
        self.breaker = None

    def daily(self, sym, days):
        if self.fail:
            raise SourceFailure("blocked")
        dates = pd.bdate_range(end=self.last_date, periods=80)
        return normalise_daily(pd.DataFrame({"date": dates, "open": 100.0, "high": 101.0, "low": 99.0, "close": [100.0 + i for i in range(80)],
                                             "volume": 1000.0}))

    def info(self, sym):
        return {"longName": "Reliance Industries Limited", "currentPrice": 1050.0, "floatShares": 2e6, "trailingPE": 20.0}

    quote_from_info = staticmethod(lambda info: {"name": info["longName"], "price": info["currentPrice"],
                                                 "float_shares": info["floatShares"], "pe_ratio": info["trailingPE"]})
    fundamentals_from_info = staticmethod(lambda info: {"sector": "Energy"})


class FakeArchive:
    def __init__(self):
        self.d = date(2026, 11, 13)

    def recent_days(self, n):
        return [self.d]

    def day(self, d):
        return {"RELIANCE": {"OPEN_PRICE": "1040", "HIGH_PRICE": "1060", "LOW_PRICE": "1030", "CLOSE_PRICE": "1050",
                             "TTL_TRD_QNTY": "5000", "TURNOVER_LACS": "52.5", "SERIES": "EQ"}}

    def daily(self, sym, days):
        raise DataUnavailable("not used")


def test_manager_overlays_newer_nse_bar_and_computes_quote(tmp_path):
    m = DataFetcherManager(tmp_path, yf=FakeYF(), nse_archive=FakeArchive(), nse_api=object(), bse_archive=object())
    t = parse_symbol("RELIANCE")
    df, src = m.get_daily_data(t, 80)
    assert src == "yfinance" and str(df["date"].iloc[-1].date()) == "2026-11-13" and df["close"].iloc[-1] == 1050
    q = m.get_realtime_quote(t, df)
    assert q["volume_ratio"] == 5.0 and q["turnover_rate"] == pytest.approx(0.25) and q["change_60d"] is not None
    assert m.get_stock_name(t) == "Reliance Industries Limited"


def test_manager_falls_back_to_bhavcopy_when_yahoo_fails(tmp_path):
    class Arch(FakeArchive):
        def daily(self, sym, days):
            return normalise_daily(pd.DataFrame({"date": pd.bdate_range(end="2026-11-13", periods=30), "open": 1.0, "high": 1.0,
                                                 "low": 1.0, "close": 1.0, "volume": 1.0}))
    m = DataFetcherManager(tmp_path, yf=FakeYF(fail=True), nse_archive=Arch(), nse_api=object(), bse_archive=object())
    df, src = m.get_daily_data(parse_symbol("RELIANCE"), 80)
    assert src == "nse_archive" and len(df) == 30


def test_bse_code_resolves_to_ticker(tmp_path, monkeypatch):
    import data_provider.bse as bse_mod
    monkeypatch.setattr(bse_mod, "now_ist", lambda: pd.Timestamp("2026-11-13 20:00").to_pydatetime())
    head = "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol,TtlTrfVal"
    row = "2026-11-13,2026-11-13,CM,BSE,STK,500325,INE002A01018,RELIANCE,A,RELIANCE INDUSTRIES LTD.,1337,1351,1324,1339.6,1553535,2080195954"

    class H:
        def get(self, url):
            return FakeResp(200, head + "\n" + row + "\n") if "20261113" in url else FakeResp(404)
    arch = BSEArchive(tmp_path, http=H())
    m = DataFetcherManager(tmp_path, yf=FakeYF(), nse_archive=FakeArchive(), nse_api=object(), bse_archive=arch)
    t = parse_symbol("500325")
    assert m.yf_symbol(t) == "RELIANCE.BO" and arch.lookup("500325")["isin"] == "INE002A01018"


@pytest.mark.network
def test_live_sources(tmp_path):
    m = DataFetcherManager(tmp_path)
    t = parse_symbol("RELIANCE")
    df, src = m.get_daily_data(t, 120)
    assert len(df) >= 60 and df["close"].iloc[-1] > 0
    dl = m.get_delivery(t, 5)
    assert dl and 0 < dl[-1]["deliv_pct"] <= 100
    assert m.get_all_indices()["data"]
    assert m.yf_symbol(parse_symbol("500325")) == "RELIANCE.BO"


def test_failed_info_is_retried_not_cached_forever(tmp_path, monkeypatch):
    calls = {"n": 0}

    class YF(FakeYF):
        def info(self, sym):
            calls["n"] += 1
            if calls["n"] == 1:
                raise SourceFailure("rate limited")
            return {"longName": "ITC Limited"}
    m = DataFetcherManager(tmp_path, yf=YF(), nse_archive=FakeArchive(), nse_api=object(), bse_archive=object())
    t = parse_symbol("ITC")
    assert m.get_stock_name(t) == "ITC"                       # first lookup failed
    import data_provider.manager as mm
    now = mm.time.time()
    monkeypatch.setattr(mm.time, "time", lambda: now + 61)    # after the 1-minute retry window
    assert m.get_stock_name(t) == "ITC Limited" and calls["n"] == 2
    assert m.get_stock_name(t) == "ITC Limited" and calls["n"] == 2   # success cached
