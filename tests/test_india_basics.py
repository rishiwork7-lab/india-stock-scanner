from datetime import date, datetime

import pytest

from src.india.market import IST, TradingCalendar, circuit_status, format_inr, market_phase
from src.india.symbols import UnsupportedSymbol, parse_list, parse_symbol


@pytest.mark.parametrize("raw,exchange,symbol,yf,kind", [
    ("RELIANCE", "NSE", "RELIANCE", "RELIANCE.NS", "equity"),
    ("nse:tcs", "NSE", "TCS", "TCS.NS", "equity"),
    ("INFY.NS", "NSE", "INFY", "INFY.NS", "equity"),
    ("M&M", "NSE", "M&M", "M&M.NS", "equity"),
    ("BAJAJ-AUTO", "NSE", "BAJAJ-AUTO", "BAJAJ-AUTO.NS", "equity"),
    ("500325", "BSE", "500325", "500325.BO", "equity"),
    ("BSE:500325", "BSE", "500325", "500325.BO", "equity"),
    ("RELIANCE.BO", "BSE", "RELIANCE", "RELIANCE.BO", "equity"),
    ("NIFTYBEES", "NSE", "NIFTYBEES", "NIFTYBEES.NS", "etf"),
    ("nifty 50", "INDEX", "NIFTY50", "^NSEI", "index"),
    ("^BSESN", "INDEX", "SENSEX", "^BSESN", "index"),
])
def test_parse_symbol(raw, exchange, symbol, yf, kind):
    t = parse_symbol(raw)
    assert (t.exchange, t.symbol, t.yf_symbol, t.kind) == (exchange, symbol, yf, kind)


def test_bad_codes_and_dedup():
    with pytest.raises(UnsupportedSymbol):
        parse_symbol("12345")             # numeric NSE symbols do not exist; 5 digits is not a BSE code either
    with pytest.raises(UnsupportedSymbol):
        parse_symbol("")
    targets, bad = parse_list(["RELIANCE", "NSE:RELIANCE", "RELIANCE.NS", "500325", "??"])
    assert [t.code for t in targets] == ["RELIANCE", "BSE:500325"] and len(bad) == 1


def test_calendar_and_phases():
    cal = TradingCalendar(fetch_holidays=lambda: [date(2026, 11, 9)])
    assert not cal.is_trading_day(date(2026, 11, 9))       # listed holiday
    assert not cal.is_trading_day(date(2026, 11, 14))      # Saturday
    assert cal.is_trading_day(date(2026, 11, 10))
    assert cal.previous_trading_day(date(2026, 11, 10)) == date(2026, 11, 6)
    assert cal.latest_completed_session(datetime(2026, 11, 10, 10, 0, tzinfo=IST)) == date(2026, 11, 6)
    assert cal.latest_completed_session(datetime(2026, 11, 10, 16, 0, tzinfo=IST)) == date(2026, 11, 10)
    assert market_phase(datetime(2026, 11, 10, 9, 5, tzinfo=IST), cal)["phase"] == "premarket"
    assert market_phase(datetime(2026, 11, 10, 11, 0, tzinfo=IST), cal)["phase"] == "intraday"
    assert market_phase(datetime(2026, 11, 10, 18, 30, tzinfo=IST), cal)["phase"] == "postmarket"
    assert market_phase(datetime(2026, 11, 9, 11, 0, tzinfo=IST), cal)["phase"] == "non_trading"


def test_calendar_without_holiday_data_is_approximate():
    def boom():
        raise RuntimeError("blocked")
    cal = TradingCalendar(fetch_holidays=boom)
    assert cal.is_trading_day(date(2026, 11, 9)) and cal.approximate


def test_inr_and_circuits():
    assert format_inr(1.5e9) == "₹150.00 Cr"
    assert format_inr(2.5e5) == "₹2.50 L"
    assert format_inr(-3e7) == "-₹3.00 Cr"
    assert format_inr(None) == "N/A"
    assert circuit_status(110, 100, 10) == "upper" and circuit_status(95, 100, 5) == "lower"
    assert circuit_status(104, 100, 5) is None and circuit_status(104, 100, None) is None
