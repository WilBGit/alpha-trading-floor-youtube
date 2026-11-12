"""Normalized, timestamped RSS news collector for Alpha Trading Floor.

The collector uses public RSS/Atom feeds only. It preserves the named publisher,
rejects stale/undated items, validates HTTP(S) URLs, and deduplicates syndicated
headline variants before they reach the catalyst scorer.
"""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from urllib.parse import quote_plus, urlparse
from xml.etree import ElementTree as ET

import httpx

logger = logging.getLogger("alpha_trading_floor")

USER_AGENT = "AlphaTradingFloor/1.0 market-news-reader"
MAX_AGE_HOURS = 96
MAX_ITEMS_PER_TICKER = 15
GENERIC_HEADLINE_PATTERNS = (
    "latest numbers",
    "release calendar",
    "schedule of releases",
    "upcoming releases",
)

STATIC_FEEDS = (
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml", "primary"),
    ("SEC", "https://www.sec.gov/news/pressreleases.rss", "primary"),
    ("BLS", "https://www.bls.gov/feed/bls_latest.rss", "primary"),
    ("CNBC Markets", "https://www.cnbc.com/id/10000664/device/rss/rss.html", "publisher"),
    ("MarketWatch", "https://feeds.marketwatch.com/marketwatch/topstories/", "publisher"),
)
INDEX_TICKERS = {"SPY", "QQQ", "DIA", "IWM"}
INDEX_NEWS_TERMS = {
    "bond yield", "cpi", "employment", "fed ", "federal reserve", "fomc",
    "gdp", "geopolit", "inflation", "interest rate", "jobs report",
    "oil price", "payroll", "recession", "s&p 500", "sanction", "tariff",
    "treasury", "trade war", "unemployment",
}
STOPWORDS = {
    "class", "common", "company", "corp", "corporation", "group", "holding",
    "holdings", "inc", "incorporated", "limited", "plc", "stock", "the",
}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(node: ET.Element, names: set[str]) -> str:
    for child in list(node):
        if _local_name(child.tag) in names and child.text:
            return child.text.strip()
    return ""


def _clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _parse_datetime(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _valid_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except ValueError:
        return False


def parse_feed(
    xml_text: str,
    fallback_source: str,
    max_age_hours: int = MAX_AGE_HOURS,
    now: datetime | None = None,
    source_type: str = "publisher",
) -> list[dict]:
    """Parse RSS or Atom XML into normalized, fresh news items."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    root = ET.fromstring(xml_text)
    items: list[dict] = []

    for node in root.iter():
        if _local_name(node.tag) not in {"item", "entry"}:
            continue
        headline = _clean_text(_child_text(node, {"title"}))
        published = _parse_datetime(_child_text(node, {"pubdate", "published", "updated", "date"}))
        if not headline or not published:
            continue
        if any(pattern in headline.lower() for pattern in GENERIC_HEADLINE_PATTERNS):
            continue
        age_hours = (now - published).total_seconds() / 3600
        if age_hours < -1 or age_hours > max_age_hours:
            continue

        link = _child_text(node, {"link"})
        if not link:
            for child in list(node):
                if _local_name(child.tag) == "link" and child.attrib.get("href"):
                    link = child.attrib["href"].strip()
                    break
        if not _valid_url(link):
            continue

        source = _clean_text(_child_text(node, {"source"})) or fallback_source
        snippet = _clean_text(_child_text(node, {"description", "summary", "content"}))
        items.append({
            "headline": headline,
            "source": source,
            "provider": fallback_source,
            "date": published.isoformat(),
            "url": link,
            "snippet": snippet[:500],
            "type": "rss",
            "source_type": source_type,
        })

    return items


def _headline_key(headline: str) -> str:
    words = re.findall(r"[a-z0-9]+", headline.lower())
    return " ".join(words)


def dedupe_news_items(items: list[dict], limit: int | None = None) -> list[dict]:
    """Deduplicate exact and punctuation-only headline variants, newest first."""
    def parsed_date(item: dict) -> datetime:
        return _parse_datetime(str(item.get("date", ""))) or datetime.min.replace(tzinfo=timezone.utc)

    result: list[dict] = []
    seen: set[str] = set()
    seen_urls: set[str] = set()
    for item in sorted(items, key=parsed_date, reverse=True):
        key = _headline_key(str(item.get("headline", "")))
        url = str(item.get("url", ""))
        if not key or key in seen or (url and url in seen_urls):
            continue
        seen.add(key)
        if url:
            seen_urls.add(url)
        result.append(item)
        if limit is not None and len(result) >= limit:
            break
    return result


@lru_cache(maxsize=16)
def _fetch_static_feed(source: str, url: str, source_type: str) -> tuple[dict, ...]:
    try:
        response = httpx.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"},
            timeout=15,
            follow_redirects=True,
        )
        response.raise_for_status()
        return tuple(parse_feed(response.text, source, source_type=source_type))
    except Exception as exc:
        logger.warning("RSS source %s failed: %s", source, exc)
        return ()


def _company_terms(ticker: str, company_name: str) -> set[str]:
    terms = {ticker.lower()}
    for word in re.findall(r"[A-Za-z0-9]+", company_name.lower()):
        if len(word) >= 4 and word not in STOPWORDS:
            terms.add(word)
    return terms


def _matches_ticker(item: dict, ticker: str, company_name: str) -> bool:
    text = f"{item.get('headline', '')} {item.get('snippet', '')}".lower()
    if ticker.upper() in INDEX_TICKERS:
        direct_terms = _company_terms(ticker, company_name)
        has_direct_reference = any(
            re.search(rf"\b{re.escape(term)}\b", text) for term in direct_terms
        )
        return has_direct_reference or any(term in text for term in INDEX_NEWS_TERMS)
    return any(re.search(rf"\b{re.escape(term)}\b", text) for term in _company_terms(ticker, company_name))


def _google_news_url(ticker: str, company_name: str) -> str:
    company = company_name.strip()
    query = f'("{ticker}" OR "{company}") stock when:3d' if company else f'"{ticker}" stock when:3d'
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"


def fetch_ticker_news_rss(ticker: str, company_name: str = "") -> list[dict]:
    """Fetch fresh ticker and macro news from validated public feeds."""
    ticker = ticker.upper().strip()
    items: list[dict] = []

    google_url = _google_news_url(ticker, company_name)
    google_items = _fetch_static_feed("Google News RSS", google_url, "aggregator")
    items.extend(
        dict(item) for item in google_items
        if _matches_ticker(item, ticker, company_name)
    )

    for source, url, source_type in STATIC_FEEDS:
        for item in _fetch_static_feed(source, url, source_type):
            if _matches_ticker(item, ticker, company_name):
                items.append(dict(item))

    return dedupe_news_items(items, limit=MAX_ITEMS_PER_TICKER)


def configured_sources() -> list[str]:
    return ["Google News RSS", *(source for source, _, _ in STATIC_FEEDS)]
