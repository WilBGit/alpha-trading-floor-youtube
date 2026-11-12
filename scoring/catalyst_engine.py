"""News/Catalyst Engine.

Searches for recent news for each ticker, classifies into categories,
and scores catalyst strength. Uses web search (1 call per ticker or batch).

Categories: EARNINGS, GUIDANCE, ANALYST_UPGRADE, ANALYST_DOWNGRADE,
PRODUCT_ANNOUNCEMENT, AI_NEWS, FDA_NEWS, MERGER, ACQUISITION,
PARTNERSHIP, GOVERNMENT_CONTRACT, SEC_FILING, INSIDER_ACTIVITY,
SHORT_SQUEEZE, CRYPTO_MOVEMENT, MACRO_NEWS, NO_CLEAR_CATALYST
"""
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx

from config.settings import DATA_DIR

logger = logging.getLogger("alpha_trading_floor")

# Catalyst categories with keywords for classification
CATALYST_CATEGORIES = {
    "EARNINGS": ["earnings", "revenue", "quarterly results", "eps", "beat estimates", "miss estimates", "profit"],
    "GUIDANCE": ["guidance", "outlook", "forecast", "raised guidance", "lowered guidance", "reiterated"],
    "ANALYST_UPGRADE": ["upgrade", "raised price target", "initiated buy", "overweight", "bullish"],
    "ANALYST_DOWNGRADE": ["downgrade", "cut price target", "underweight", "sell rating", "bearish"],
    "PRODUCT_ANNOUNCEMENT": ["launch", "new product", "unveil", "release", "debut", "announcement"],
    "AI_NEWS": ["artificial intelligence", "AI ", "machine learning", "GPT", "language model", "chip demand"],
    "FDA_NEWS": ["FDA", "clinical trial", "drug approval", "Phase ", "biotech", "regulatory"],
    "MERGER": ["merger", "acquire", "buyout", "takeover", "combine"],
    "ACQUISITION": ["acquisition", "acquire", "buyout", "purchased", "bought"],
    "PARTNERSHIP": ["partnership", "collaborate", "joint venture", "strategic alliance", "deal with"],
    "GOVERNMENT_CONTRACT": ["government contract", "defense contract", "federal contract", "federal award", "military contract", "pentagon award", "fda approval"],
    "SEC_FILING": ["SEC filing", "insider selling", "13D", "13F", "Form 4", "disclosure"],
    "INSIDER_ACTIVITY": ["insider buying", "insider selling", "CEO buys", "CFO sells", "director purchase"],
    "SHORT_SQUEEZE": ["short squeeze", "short interest", "shorts covering", "borrow fee"],
    "CRYPTO_MOVEMENT": ["bitcoin", "ethereum", "crypto", "mining", "blockchain", "digital asset"],
    "MACRO_NEWS": ["Fed", "interest rate", "inflation", "CPI", "employment", "GDP", "tariff", "trade war"],
}

# Category sentiment multipliers (positive/negative impact on score)
CATEGORY_SENTIMENT = {
    "EARNINGS": 0.5,           # Depends on whether beat/miss
    "GUIDANCE": 0.3,            # Usually moderate impact
    "ANALYST_UPGRADE": 0.8,     # Positive
    "ANALYST_DOWNGRADE": -0.7,  # Negative
    "PRODUCT_ANNOUNCEMENT": 0.6,
    "AI_NEWS": 0.7,             # Strong positive in current market
    "FDA_NEWS": 0.5,            # Variable
    "MERGER": 0.6,
    "ACQUISITION": 0.6,
    "PARTNERSHIP": 0.4,
    "GOVERNMENT_CONTRACT": 0.5,
    "SEC_FILING": -0.2,         # Usually negative connotation
    "INSIDER_ACTIVITY": 0.3,    # Depends on direction
    "SHORT_SQUEEZE": 0.6,
    "CRYPTO_MOVEMENT": 0.4,
    "MACRO_NEWS": 0.2,          # Variable
    "NO_CLEAR_CATALYST": 0.0,
}

TRUSTED_SOURCE_WEIGHTS = {
    "reuters": 1.0,
    "associated press": 1.0,
    "bloomberg": 1.0,
    "wall street journal": 1.0,
    "financial times": 1.0,
    "cnbc": 0.95,
    "marketwatch": 0.90,
    "barron's": 0.90,
    "yahoo finance": 0.85,
    "nasdaq": 0.85,
}


def source_reliability(item: dict) -> float:
    """Return a conservative reliability weight for catalyst selection."""
    if item.get("source_type") == "primary":
        return 1.0
    source = str(item.get("source", "")).lower()
    for trusted_name, weight in TRUSTED_SOURCE_WEIGHTS.items():
        if trusted_name in source:
            return weight
    if item.get("source_type") == "publisher":
        return 0.80
    return 0.50


def classify_catalyst(headline: str, snippet: str = "") -> tuple:
    """Classify a news headline into a catalyst category.

    Returns (category, confidence) tuple.
    """
    text = (headline + " " + snippet).lower()

    best_category = "NO_CLEAR_CATALYST"
    best_score = 0

    for category, keywords in CATALYST_CATEGORIES.items():
        score = sum(1 for kw in keywords if kw.lower() in text)
        if score > best_score:
            best_score = score
            best_category = category

    return best_category, min(best_score / 3.0, 1.0)  # confidence 0-1


def score_catalyst_strength(category: str, confidence: float, recency_hours: float = 24) -> float:
    """Score catalyst strength on 0-10 scale.

    Factors: category impact, confidence, recency.
    """
    sentiment = CATEGORY_SENTIMENT.get(category, 0)
    base = abs(sentiment) * 7  # Max base of ~7 for strongest categories

    # Confidence bonus (0-2)
    confidence_bonus = confidence * 2

    # Recency bonus: more recent = higher score
    if recency_hours < 6:
        recency_bonus = 1.0
    elif recency_hours < 24:
        recency_bonus = 0.7
    elif recency_hours < 48:
        recency_bonus = 0.4
    elif recency_hours < 72:
        recency_bonus = 0.2
    else:
        recency_bonus = 0.0

    return min(max(base + confidence_bonus + recency_bonus, 0), 10)


def search_ticker_news(ticker: str, company_name: str = "") -> list:
    """Collect recent, timestamped news about a ticker.

    Returns list of news items with headline, source, date.
    """
    news_items = []

    # Source 1: Yahoo Finance syndication via yfinance.
    try:
        stock_ticker = yf_ticker_news(ticker)
        if stock_ticker:
            news_items.extend(stock_ticker)
    except Exception as e:
        logger.debug(f"yfinance news failed for {ticker}: {e}")

    # Source 2: timestamped RSS/Atom feeds with publisher attribution.
    try:
        from collectors.news_rss import dedupe_news_items, fetch_ticker_news_rss
        news_items.extend(fetch_ticker_news_rss(ticker, company_name))
        news_items = dedupe_news_items(news_items, limit=20)
    except Exception as e:
        logger.warning(f"RSS news collection failed for {ticker}: {e}")

    return news_items


def yf_ticker_news(ticker: str) -> list:
    """Fetch news from yfinance ticker news."""
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        news = stock.news or []

        items = []
        for article in news[:5]:  # Top 5 articles
            item = {
                "headline": article.get("title", ""),
                "source": article.get("publisher", ""),
                "date": datetime.fromtimestamp(article.get("providerPublishTime", 0), timezone.utc).isoformat()
                        if article.get("providerPublishTime") else "",
                "url": article.get("link", ""),
                "type": article.get("type", ""),
            }
            if item["headline"]:
                items.append(item)
        return items
    except Exception as e:
        logger.debug(f"yfinance news error for {ticker}: {e}")
        return []


def analyze_catalyst_for_ticker(ticker: str, stock_data: dict = None) -> dict:
    """Analyze catalyst data for a single ticker.

    Returns catalyst dict with category, strength, headline, sentiment.
    """
    company_name = ""
    if stock_data and ticker in stock_data:
        company_name = stock_data[ticker].get("company_name", "")

    news_items = search_ticker_news(ticker, company_name)

    if not news_items:
        return {
            "ticker": ticker,
            "category": "NO_CLEAR_CATALYST",
            "strength": 0,
            "confidence": 0.0,
            "headline": "",
            "sentiment": 0.0,
            "source": "",
            "news_items": [],
        }

    # Classify each news item and pick the strongest catalyst
    best_catalyst = None
    best_strength = -1

    for item in news_items:
        category, confidence = classify_catalyst(item["headline"], item.get("snippet", ""))
        strength = score_catalyst_strength(category, confidence)
        sentiment = CATEGORY_SENTIMENT.get(category, 0)

        # Calculate recency
        try:
            if item.get("date"):
                news_date = datetime.fromisoformat(item["date"].replace("Z", "+00:00"))
                hours_ago = (datetime.now(news_date.tzinfo) - news_date).total_seconds() / 3600
            else:
                hours_ago = 24  # Default to 24h
        except Exception:
            hours_ago = 24

        # Adjust strength by recency and source reliability. Unknown publishers
        # remain visible for discovery but cannot easily outrank primary news.
        reliability = source_reliability(item)
        recency_strength = score_catalyst_strength(category, confidence, hours_ago) * reliability

        if recency_strength > best_strength:
            best_strength = recency_strength
            best_catalyst = {
                "ticker": ticker,
                "category": category,
                "strength": round(recency_strength, 1),
                "confidence": round(confidence, 2),
                "headline": item["headline"],
                "sentiment": round(sentiment, 2),
                "source": item.get("source", ""),
                "source_reliability": reliability,
                "news_items": news_items,
            }

    if not best_catalyst:
        best_catalyst = {
            "ticker": ticker,
            "category": "NO_CLEAR_CATALYST",
            "strength": 0,
            "confidence": 0.0,
            "headline": "",
            "sentiment": 0.0,
            "source": "",
            "news_items": [],
        }

    return best_catalyst


def analyze_all_catalysts(stock_data: dict) -> dict:
    """Analyze catalysts for all tickers.

    Returns dict of ticker -> catalyst data.
    """
    results = {}
    for ticker in stock_data:
        logger.info(f"Analyzing catalyst for {ticker}")
        results[ticker] = analyze_catalyst_for_ticker(ticker, stock_data)

    # Save catalyst data
    output_path = DATA_DIR / "catalysts.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)

    logger.info(f"Catalyst analysis complete for {len(results)} tickers")
    return results
