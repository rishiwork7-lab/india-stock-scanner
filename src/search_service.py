"""News intelligence (the original's src/search_service.py, adapted to India).

Providers, tried in order for each search until one returns results:
  Tavily, SerpAPI (Google News), Brave News  - when API keys are configured (keys rotate round-robin)
  Google News RSS, India edition             - free, no key; always available as the last resort
The 'announcements' dimension comes from NSE's own corporate announcements (official exchange filings).

Multi-dimension intel per stock (search_comprehensive_intel), same dimensions as the original:
  latest_news, announcements, market_analysis, risk_check, earnings, industry
"""
from __future__ import annotations

import html
import itertools
import logging
import re
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Dict, List, Optional
from urllib.parse import quote_plus

import requests

logger = logging.getLogger(__name__)
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"}


@dataclass
class SearchResult:
    title: str
    snippet: str = ""
    url: str = ""
    published_date: Optional[str] = None
    source: str = ""

    def to_dict(self) -> Dict:
        return {"title": self.title, "snippet": self.snippet, "url": self.url, "published": self.published_date, "source": self.source}


@dataclass
class SearchResponse:
    query: str
    provider: str
    results: List[SearchResult] = field(default_factory=list)
    success: bool = True
    error: Optional[str] = None


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(s or ""))).strip()


def _parse_date(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


class _Provider:
    name = "base"

    def search(self, query: str, max_results: int, days: int) -> List[SearchResult]:
        raise NotImplementedError


class GoogleNewsRSS(_Provider):
    name = "google_news_rss"

    def __init__(self, get: Optional[Callable] = None):
        self._get = get or (lambda url: requests.get(url, headers=UA, timeout=15))

    def search(self, query, max_results, days):
        q = f"{query} when:{days}d" if days else query
        r = self._get(f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-IN&gl=IN&ceid=IN:en")
        if r.status_code != 200:
            raise RuntimeError(f"Google News RSS HTTP {r.status_code}")
        root = ET.fromstring(r.content if isinstance(r.content, bytes) else r.content.encode())
        out = []
        for item in root.iter("item"):
            title = _strip_html(item.findtext("title") or "")
            src = item.find("source")
            source = (src.text or "").strip() if src is not None else ""
            if source and title.endswith(f" - {source}"):
                title = title[: -len(source) - 3]
            d = _parse_date(item.findtext("pubDate"))
            snippet = _strip_html(item.findtext("description") or "")
            if snippet.startswith(title):
                snippet = snippet[len(title):].strip(" -")
            out.append(SearchResult(title, snippet[:400], item.findtext("link") or "", d.date().isoformat() if d else None, source))
            if len(out) >= max_results:
                break
        return out


class _Keyed(_Provider):
    def __init__(self, keys: List[str]):
        self._keys = itertools.cycle(keys)
        self._lock = threading.Lock()

    def key(self) -> str:
        with self._lock:
            return next(self._keys)


class Tavily(_Keyed):
    name = "tavily"

    def search(self, query, max_results, days):
        r = requests.post("https://api.tavily.com/search", timeout=20, json={
            "api_key": self.key(), "query": query, "max_results": max_results, "topic": "news", "days": days or 30,
            "include_answer": False})
        r.raise_for_status()
        return [SearchResult(x.get("title", ""), (x.get("content") or "")[:400], x.get("url", ""),
                             (x.get("published_date") or "")[:10] or None, x.get("url", "").split("/")[2] if x.get("url") else "")
                for x in r.json().get("results", [])]


class SerpAPI(_Keyed):
    name = "serpapi"

    def search(self, query, max_results, days):
        r = requests.get("https://serpapi.com/search.json", timeout=20, params={
            "engine": "google_news", "q": query, "gl": "in", "hl": "en", "api_key": self.key()})
        r.raise_for_status()
        out = []
        for x in r.json().get("news_results", [])[:max_results]:
            d = x.get("date") or ""
            out.append(SearchResult(x.get("title", ""), x.get("snippet", "") or "", x.get("link", ""), d[:10] or None,
                                    (x.get("source") or {}).get("name", "") if isinstance(x.get("source"), dict) else ""))
        return out


class BraveNews(_Keyed):
    name = "brave"

    def search(self, query, max_results, days):
        fresh = "pd" if days and days <= 1 else "pw" if days and days <= 7 else "pm"
        r = requests.get("https://api.search.brave.com/res/v1/news/search", timeout=20,
                         headers={"X-Subscription-Token": self.key(), "Accept": "application/json"},
                         params={"q": query, "count": max_results, "country": "IN", "freshness": fresh})
        r.raise_for_status()
        return [SearchResult(x.get("title", ""), _strip_html(x.get("description", ""))[:400], x.get("url", ""),
                             x.get("page_age", "")[:10] or None, (x.get("meta_url") or {}).get("hostname", ""))
                for x in r.json().get("results", [])]


DIM_LABELS = {"latest_news": "📰 Latest News", "announcements": "📋 Exchange Announcements (NSE)",
              "market_analysis": "📈 Analyst Views", "risk_check": "⚠️ Risk Check", "earnings": "📊 Earnings Outlook",
              "industry": "🏭 Industry"}
DISPLAY_ORDER = ["latest_news", "announcements", "market_analysis", "risk_check", "earnings", "industry"]


class SearchService:
    def __init__(self, config, announcements: Optional[Callable] = None, providers: Optional[List[_Provider]] = None):
        self.config = config
        self.announcements = announcements
        if providers is not None:
            self.providers = providers
        else:
            self.providers = []
            if config.tavily_api_keys:
                self.providers.append(Tavily(config.tavily_api_keys))
            if config.serpapi_api_keys:
                self.providers.append(SerpAPI(config.serpapi_api_keys))
            if config.brave_api_keys:
                self.providers.append(BraveNews(config.brave_api_keys))
            if config.google_news_rss_enabled:
                self.providers.append(GoogleNewsRSS())

    @property
    def is_available(self) -> bool:
        return bool(self.providers)

    def search(self, query: str, max_results: int = 5, days: Optional[int] = None) -> SearchResponse:
        days = self.config.news_max_age_days if days is None else days
        errors = []
        for p in self.providers:
            try:
                res = [r for r in p.search(query, max_results, days) if r.title]
                if res:
                    return SearchResponse(query, p.name, res[:max_results])
                errors.append(f"{p.name}: no results")
            except Exception as e:
                logger.warning("search provider %s failed for %r: %s", p.name, query, e)
                errors.append(f"{p.name}: {e}")
        return SearchResponse(query, "none", [], success=False, error="; ".join(errors))

    def search_comprehensive_intel(self, target, stock_name: str, max_searches: int = 5) -> Dict[str, SearchResponse]:
        name = stock_name if stock_name and stock_name != target.symbol else target.symbol
        sym = "" if target.kind == "index" else target.symbol
        etf = target.is_index_etf
        dims = [
            ("latest_news", f'"{name}" {sym} share news' if not etf else f"{name} index market news", self.config.news_max_age_days),
            ("market_analysis", f"{name} target price brokerage rating" if not etf else f"{name} outlook valuation", 30),
            ("risk_check", (f"{name} SEBI OR pledge OR fraud OR raid OR resignation OR penalty OR downgrade" if not etf
                            else f"{name} tracking error outflows risk"), self.config.news_max_age_days),
            ("earnings", f"{name} quarterly results profit revenue" if not etf else f"{name} constituents earnings season", 60),
            ("industry", f"{name} sector outlook competitors India" if not etf else f"{name} sector weights rotation", 30),
        ]
        out: Dict[str, SearchResponse] = {}
        for dim, query, days in dims[:max_searches]:
            out[dim] = self.search(query.strip(), max_results=5, days=days)
        if self.announcements is not None and target.kind == "equity" and target.exchange == "NSE":
            try:
                ann = self.announcements(target)
            except Exception as e:  # pragma: no cover - manager already swallows source errors
                ann, _ = [], e
            if ann:
                out["announcements"] = SearchResponse(f"NSE announcements {target.symbol}", "nse_api", [
                    SearchResult(a["title"], a.get("summary", ""), a.get("url", ""), _short_date(a.get("date")), a.get("source", "NSE"))
                    for a in ann[:5]])
        return out

    @staticmethod
    def format_intel_report(intel: Dict[str, SearchResponse], stock_name: str) -> str:
        lines = [f"[{stock_name} intelligence search results]"]
        for dim in DISPLAY_ORDER:
            if dim not in intel:
                continue
            resp = intel[dim]
            lines.append(f"\n{DIM_LABELS[dim]} (source: {resp.provider}):")
            if resp.success and resp.results:
                for i, r in enumerate(resp.results[:4], 1):
                    when = f" [{r.published_date}]" if r.published_date else ""
                    src = f" ({r.source})" if r.source else ""
                    lines.append(f"  {i}. {r.title}{when}{src}")
                    if r.snippet and len(r.snippet) > 20:
                        lines.append(f"     {r.snippet[:220]}")
            else:
                lines.append("  No relevant information found")
        return "\n".join(lines)

    def search_market_news(self, queries: List[str], per_query: int = 4) -> List[SearchResult]:
        seen, out = set(), []
        for q in queries:
            resp = self.search(q, max_results=per_query, days=2)
            for r in resp.results:
                if r.title not in seen:
                    seen.add(r.title)
                    out.append(r)
        return out


def _short_date(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return s[:10]
