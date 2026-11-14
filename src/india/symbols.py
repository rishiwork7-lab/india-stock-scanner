"""Indian stock code parsing (replaces the original's A-share/HK/US code normaliser).

Accepted inputs (case-insensitive):
  RELIANCE, NSE:RELIANCE, RELIANCE.NS          -> NSE equity RELIANCE
  500325, BSE:500325, 500325.BO, RELIANCE.BO   -> BSE equity (6-digit numbers are BSE scrip codes)
  NIFTY50, NIFTY 50, ^NSEI, SENSEX, BANKNIFTY  -> index
  NIFTYBEES, GOLDBEES, ...                     -> ETF (analysed with index/ETF constraints, as in the original)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# index aliases -> (yfinance symbol, NSE index name used by allIndices, display name)
INDICES = {
    "NIFTY50": ("^NSEI", "NIFTY 50", "NIFTY 50"),
    "NIFTY": ("^NSEI", "NIFTY 50", "NIFTY 50"),
    "SENSEX": ("^BSESN", None, "S&P BSE SENSEX"),
    "BANKNIFTY": ("^NSEBANK", "NIFTY BANK", "NIFTY BANK"),
    "NIFTYBANK": ("^NSEBANK", "NIFTY BANK", "NIFTY BANK"),
    "NIFTYIT": ("^CNXIT", "NIFTY IT", "NIFTY IT"),
    "NIFTYNEXT50": ("^NSMIDCP", "NIFTY NEXT 50", "NIFTY NEXT 50"),
    "NIFTYMIDCAP100": ("NIFTY_MIDCAP_100.NS", "NIFTY MIDCAP 100", "NIFTY MIDCAP 100"),
    "NIFTY500": ("^CRSLDX", "NIFTY 500", "NIFTY 500"),
    "INDIAVIX": ("^INDIAVIX", "INDIA VIX", "INDIA VIX"),
    "FINNIFTY": ("NIFTY_FIN_SERVICE.NS", "NIFTY FINANCIAL SERVICES", "NIFTY FINANCIAL SERVICES"),
}
_YF_TO_INDEX = {v[0].upper(): k for k, v in INDICES.items()}

# common ETFs (index/commodity trackers); any symbol ending in BEES / ETF is also treated as an ETF
KNOWN_ETFS = {"NIFTYBEES", "BANKBEES", "GOLDBEES", "SILVERBEES", "JUNIORBEES", "ITBEES", "LIQUIDBEES", "CPSEETF",
              "MON100", "MAFANG", "SETFNIF50", "ICICIB22", "MOM100", "PSUBNKBEES", "HNGSNGBEES"}

_NSE_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")


@dataclass(frozen=True)
class Target:
    raw: str
    exchange: str            # NSE | BSE | INDEX
    symbol: str              # NSE symbol, BSE scrip code, or index alias
    kind: str                # equity | etf | index
    yf_symbol: str
    nse_index_name: Optional[str] = None

    @property
    def code(self) -> str:
        """Canonical display/storage code: RELIANCE, BSE:500325, ^NSEI-style index alias."""
        if self.exchange == "BSE":
            return f"BSE:{self.symbol}"
        return self.symbol

    @property
    def is_index_etf(self) -> bool:
        return self.kind in ("index", "etf")


class UnsupportedSymbol(ValueError):
    pass


def parse_symbol(raw: str) -> Target:
    s = (raw or "").strip().upper().replace(" ", "")
    if not s:
        raise UnsupportedSymbol("empty code")
    if s in _YF_TO_INDEX:
        s = _YF_TO_INDEX[s]
    if s in INDICES:
        yf, nse_name, _ = INDICES[s]
        return Target(raw, "INDEX", s, "index", yf, nse_name)
    exchange = None
    if s.startswith("NSE:"):
        exchange, s = "NSE", s[4:]
    elif s.startswith("BSE:"):
        exchange, s = "BSE", s[4:]
    elif s.endswith(".NS"):
        exchange, s = "NSE", s[:-3]
    elif s.endswith(".BO"):
        exchange, s = "BSE", s[:-3]
    if not s:
        raise UnsupportedSymbol(f"cannot parse {raw!r}")
    if exchange is None:
        exchange = "BSE" if s.isdigit() else "NSE"
    if exchange == "BSE":
        if (s.isdigit() and len(s) != 6) or (not s.isdigit() and not _NSE_SYMBOL.match(s)):
            raise UnsupportedSymbol(f"not a BSE scrip code or symbol: {raw!r}")
        return Target(raw, "BSE", s, "equity", f"{s}.BO")
    if s.isdigit() or not _NSE_SYMBOL.match(s):
        raise UnsupportedSymbol(f"not an NSE symbol: {raw!r}")
    kind = "etf" if (s in KNOWN_ETFS or s.endswith("BEES") or s.endswith("ETF")) else "equity"
    return Target(raw, "NSE", s, kind, f"{s}.NS")


def parse_list(codes) -> list:
    """Parse a list of codes; unsupported ones are skipped (logged by the caller), duplicates removed by canonical code."""
    seen, out, bad = set(), [], []
    for c in codes:
        try:
            t = parse_symbol(c)
        except UnsupportedSymbol as e:
            bad.append((c, str(e)))
            continue
        if t.code in seen:
            continue
        seen.add(t.code)
        out.append(t)
    return out, bad
