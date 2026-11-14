import pytest

from src.config import Config
from src.india.symbols import parse_symbol
from src.search_service import GoogleNewsRSS, SearchResult, SearchService

RSS = b"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title>Reliance Q2 profit rises 9% - Economic Times</title><link>https://example.com/a</link>
<pubDate>Thu, 12 Nov 2026 10:00:00 GMT</pubDate><description>&lt;a href="x"&gt;Reliance Q2 profit rises 9%&lt;/a&gt; Jio and retail drive growth</description>
<source url="https://economictimes.com">Economic Times</source></item>
<item><title>Brokerages raise Reliance target - Mint</title><link>https://example.com/b</link>
<pubDate>Wed, 11 Nov 2026 08:00:00 GMT</pubDate><description>Target raised to 1600</description><source url="https://livemint.com">Mint</source></item>
</channel></rss>"""


class R:
    def __init__(self, status, content):
        self.status_code, self.content = status, content


def test_google_news_rss_parsing_and_india_edition():
    urls = []
    p = GoogleNewsRSS(get=lambda u: urls.append(u) or R(200, RSS))
    res = p.search("Reliance results", 5, 7)
    assert "gl=IN" in urls[0] and "when%3A7d" in urls[0]
    assert res[0].title == "Reliance Q2 profit rises 9%" and res[0].source == "Economic Times"
    assert res[0].published_date == "2026-11-12" and "Jio and retail" in res[0].snippet


class Flaky:
    name = "flaky"

    def search(self, q, n, d):
        raise RuntimeError("quota exceeded")


class Fixed:
    name = "fixed"

    def __init__(self):
        self.queries = []

    def search(self, q, n, d):
        self.queries.append(q)
        return [SearchResult(f"{q} headline", "snippet text long enough here", "u", "2026-11-12", "Src")]


def test_fallback_across_providers_and_comprehensive_intel():
    fixed = Fixed()
    ann = [{"title": "Board meeting outcome", "summary": "Dividend declared", "date": "13-Nov-2026 18:25:01", "source": "NSE"}]
    svc = SearchService(Config(), announcements=lambda t: ann, providers=[Flaky(), fixed])
    intel = svc.search_comprehensive_intel(parse_symbol("RELIANCE"), "Reliance Industries Limited")
    assert set(intel) == {"latest_news", "market_analysis", "risk_check", "earnings", "industry", "announcements"}
    assert all(r.provider == "fixed" for k, r in intel.items() if k != "announcements")
    assert intel["announcements"].results[0].published_date == "2026-11-13"
    assert any("SEBI" in q for q in fixed.queries)                        # India-specific risk screening
    text = svc.format_intel_report(intel, "Reliance")
    assert text.index("Latest News") < text.index("Exchange Announcements") < text.index("Risk Check")


def test_etf_queries_use_index_constraints():
    fixed = Fixed()
    SearchService(Config(), providers=[fixed]).search_comprehensive_intel(parse_symbol("NIFTYBEES"), "Nippon Nifty BeES")
    assert not any("SEBI" in q for q in fixed.queries) and any("tracking error" in q for q in fixed.queries)


def test_all_providers_failing_is_reported():
    resp = SearchService(Config(), providers=[Flaky()]).search("x")
    assert not resp.success and "quota" in resp.error


@pytest.mark.network
def test_live_google_news():
    res = GoogleNewsRSS().search("Reliance Industries share", 3, 7)
    assert res and res[0].title
