"""What the scanner covers: the Nifty 500 stocks (with their index and sector memberships) and a fixed list of liquid
NSE ETFs.

Index member lists come from NSE (nsearchives ind_<index>list.csv), refreshed weekly into data/universe/, with copies
kept in src/india/indices/ so a run still works when NSE blocks the request (it often blocks cloud servers such as
GitHub Actions). Every stock scanned is a Nifty 500 member (the owner's scope); sector and thematic indices are shown
with their Nifty 500 members only, and the page says how many members fall outside.

The ETF list was chosen on 2026-11-14 from Yahoo Finance history and 60-day average traded value: broad market,
sectors, factors, global and commodities. `index` is the Yahoo ticker of the index an ETF tracks, where Yahoo has it
(used for longer history in research; the ETF itself is what you would buy).
"""
from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

LIST_URL = "https://nsearchives.nseindia.com/content/indices/ind_{}list.csv"
BUNDLED = Path(__file__).resolve().parent / "india" / "indices"
REFRESH_S = 7 * 86400
BASE_INDEX = "nifty500"

# (key, label, group, NSE file name)
INDEX_LISTS: List[Tuple[str, str, str, str]] = [
    ("nifty50", "Nifty 50", "Broad market", "nifty50"),
    ("niftynext50", "Nifty Next 50", "Broad market", "niftynext50"),
    ("nifty100", "Nifty 100", "Broad market", "nifty100"),
    ("nifty200", "Nifty 200", "Broad market", "nifty200"),
    ("nifty500", "Nifty 500", "Broad market", "nifty500"),
    ("niftylargemidcap250", "Nifty LargeMidcap 250", "Broad market", "niftylargemidcap250"),
    ("niftymidcap50", "Nifty Midcap 50", "Broad market", "niftymidcap50"),
    ("niftymidcap100", "Nifty Midcap 100", "Broad market", "niftymidcap100"),
    ("niftymidcap150", "Nifty Midcap 150", "Broad market", "niftymidcap150"),
    ("niftysmallcap100", "Nifty Smallcap 100", "Broad market", "niftysmallcap100"),
    ("niftysmallcap250", "Nifty Smallcap 250", "Broad market", "niftysmallcap250"),
    ("niftybank", "Nifty Bank", "Sector", "niftybank"),
    ("niftyprivatebank", "Nifty Private Bank", "Sector", "nifty_privatebank"),
    ("niftypsubank", "Nifty PSU Bank", "Sector", "niftypsubank"),
    ("niftyfinance", "Nifty Financial Services", "Sector", "niftyfinance"),
    ("niftyit", "Nifty IT", "Sector", "niftyit"),
    ("niftypharma", "Nifty Pharma", "Sector", "niftypharma"),
    ("niftyhealthcare", "Nifty Healthcare", "Sector", "niftyhealthcare"),
    ("niftyauto", "Nifty Auto", "Sector", "niftyauto"),
    ("niftyfmcg", "Nifty FMCG", "Sector", "niftyfmcg"),
    ("niftyconsumerdurables", "Nifty Consumer Durables", "Sector", "niftyconsumerdurables"),
    ("niftymetal", "Nifty Metal", "Sector", "niftymetal"),
    ("niftyrealty", "Nifty Realty", "Sector", "niftyrealty"),
    ("niftyenergy", "Nifty Energy", "Sector", "niftyenergy"),
    ("niftyoilgas", "Nifty Oil & Gas", "Sector", "niftyoilgas"),
    ("niftymedia", "Nifty Media", "Sector", "niftymedia"),
    ("niftyinfra", "Nifty Infrastructure", "Theme", "niftyinfra"),
    ("niftypse", "Nifty PSE (public sector)", "Theme", "niftypse"),
    ("niftycpse", "Nifty CPSE", "Theme", "niftycpse"),
    ("niftyindiadefence", "Nifty India Defence", "Theme", "niftyindiadefence_"),
    ("niftycommodities", "Nifty Commodities", "Theme", "niftycommodities"),
    ("niftyconsumption", "Nifty India Consumption", "Theme", "niftyconsumption"),
    ("niftymnc", "Nifty MNC", "Theme", "niftymnc"),
    ("niftyindiadigital", "Nifty India Digital", "Theme", "niftyindiadigital_"),
    ("niftyindiamanufacturing", "Nifty India Manufacturing", "Theme", "niftyindiamanufacturing_"),
    ("niftyindiatourism", "Nifty India Tourism", "Theme", "niftyindiatourism_"),
]


@dataclass(frozen=True)
class ETF:
    symbol: str
    name: str
    category: str          # Broad market / Sector / Factor / Global / Commodity
    tracks: str
    index: Optional[str] = None


ETFS: List[ETF] = [
    ETF("NIFTYBEES", "Nippon India ETF Nifty 50 BeES", "Broad market", "Nifty 50", "^NSEI"),
    ETF("JUNIORBEES", "Nippon India ETF Nifty Next 50 Junior BeES", "Broad market", "Nifty Next 50"),
    ETF("MIDCAPETF", "Mirae Asset Nifty Midcap 150 ETF", "Broad market", "Nifty Midcap 150"),
    ETF("HDFCSML250", "HDFC Nifty Smallcap 250 ETF", "Broad market", "Nifty Smallcap 250"),
    ETF("BANKBEES", "Nippon India ETF Nifty Bank BeES", "Sector", "Nifty Bank", "^NSEBANK"),
    ETF("PSUBNKBEES", "Nippon India ETF Nifty PSU Bank BeES", "Sector", "Nifty PSU Bank", "^CNXPSUBANK"),
    ETF("PVTBANIETF", "ICICI Prudential Nifty Private Bank ETF", "Sector", "Nifty Private Bank"),
    ETF("ITBEES", "Nippon India ETF Nifty IT", "Sector", "Nifty IT", "^CNXIT"),
    ETF("PHARMABEES", "Nippon India Nifty Pharma ETF", "Sector", "Nifty Pharma", "^CNXPHARMA"),
    ETF("HEALTHIETF", "ICICI Prudential Nifty Healthcare ETF", "Sector", "Nifty Healthcare"),
    ETF("AUTOBEES", "Nippon India Nifty Auto ETF", "Sector", "Nifty Auto", "^CNXAUTO"),
    ETF("FMCGIETF", "ICICI Prudential Nifty FMCG ETF", "Sector", "Nifty FMCG", "^CNXFMCG"),
    ETF("METALIETF", "ICICI Prudential Nifty Metal ETF", "Sector", "Nifty Metal", "^CNXMETAL"),
    ETF("CPSEETF", "CPSE ETF", "Sector", "Nifty CPSE (public-sector companies)"),
    ETF("MOREALTY", "Motilal Oswal Nifty Realty ETF", "Sector", "Nifty Realty", "^CNXREALTY"),
    ETF("MODEFENCE", "Motilal Oswal Nifty India Defence ETF", "Sector", "Nifty India Defence"),
    ETF("OILIETF", "ICICI Prudential Nifty Oil & Gas ETF", "Sector", "Nifty Oil & Gas"),
    ETF("ALPHA", "Kotak Nifty Alpha 50 ETF", "Factor", "Nifty Alpha 50"),
    ETF("LOWVOLIETF", "ICICI Prudential Nifty 100 Low Volatility 30 ETF", "Factor", "Nifty 100 Low Volatility 30"),
    ETF("MON100", "Motilal Oswal Nasdaq 100 ETF", "Global", "Nasdaq 100 (US)"),
    ETF("MAFANG", "Mirae Asset NYSE FANG+ ETF", "Global", "NYSE FANG+ (US)"),
    ETF("MASPTOP50", "Mirae Asset S&P 500 Top 50 ETF", "Global", "S&P 500 Top 50 (US)"),
    ETF("GOLDBEES", "Nippon India ETF Gold BeES", "Commodity", "Gold"),
    ETF("SILVERBEES", "Nippon India Silver ETF", "Commodity", "Silver"),
]
ETF_BY_SYMBOL: Dict[str, ETF] = {e.symbol: e for e in ETFS}


def _parse(text: str) -> List[Dict[str, str]]:
    df = pd.read_csv(io.StringIO(text))
    df.columns = [c.strip() for c in df.columns]
    rows = []
    for _, r in df.iterrows():
        sym = str(r["Symbol"]).strip()
        # EQ and BE (trade-to-trade) are shares; RR = REITs/InvITs are left out; DUMMY rows are NSE placeholders
        if str(r.get("Series", "EQ")).strip() not in ("EQ", "BE") or sym.upper().startswith("DUMMY"):
            continue
        rows.append({"symbol": sym, "name": str(r["Company Name"]).strip(), "industry": str(r["Industry"]).strip()})
    return rows


def _default_get(url: str):
    import requests
    return requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (Macintosh) Chrome/130.0"})


def index_list(file: str, cache_dir: Optional[Path] = None, http_get=None) -> Tuple[List[Dict[str, str]], str]:
    """One NSE index member list -> (rows, source). Order: data/ cache (< 7 days) -> NSE -> bundled copy."""
    name = file.rstrip("_")
    cache = (Path(cache_dir) / f"{name}.csv") if cache_dir else None
    if cache and cache.exists() and time.time() - cache.stat().st_mtime < REFRESH_S:
        try:
            return _parse(cache.read_text()), "cache"
        except Exception:
            pass
    try:
        r = (http_get or _default_get)(LIST_URL.format(file))
        if getattr(r, "status_code", 200) == 200 and "Symbol" in r.text[:400]:
            rows = _parse(r.text)
            if rows:
                if cache:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    cache.write_text(r.text)
                return rows, "NSE"
    except Exception as e:
        logger.info("%s list from NSE unavailable (%s); using the stored copy", name, e)
    if cache and cache.exists():
        return _parse(cache.read_text()), "cache (old)"
    return _parse((BUNDLED / f"{name}.csv").read_text()), "bundled copy"


def load_universe(cache_dir: Optional[Path] = None, http_get=None) -> Dict:
    """{"members": Nifty 500 rows, "indices": {key: {label, group, codes (Nifty 500 members), total}}, "stale_lists": [...]}"""
    base_rows, _src = index_list(BASE_INDEX, cache_dir, http_get)
    in_base = {r["symbol"] for r in base_rows}
    indices, stale = {}, []
    for key, label, group, file in INDEX_LISTS:
        rows, src = (base_rows, _src) if key == BASE_INDEX else index_list(file, cache_dir, http_get)
        if src in ("bundled copy", "cache (old)"):
            stale.append(label)
        syms = [r["symbol"] for r in rows]
        indices[key] = {"label": label, "group": group, "codes": [x for x in syms if x in in_base], "total": len(syms)}
    return {"members": base_rows, "indices": indices, "stale_lists": stale}
