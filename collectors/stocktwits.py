"""StockTwits Trending Collector.

Fetches top N trending tickers from StockTwits.
Deterministic — no LLM calls.
"""
import json
import logging
from pathlib import Path

import httpx

from config.settings import STOCKTWITS_TRENDING_URL, STOCKTWITS_TOP_N, CHARTS_DIR

logger = logging.getLogger("alpha_trading_floor")


def fetch_stocktwits_trending(limit: int = None) -> list:
    """Fetch trending tickers from StockTwits.

    Returns a list of dicts: [{symbol, title, watchlist_count}, ...]
    """
    if limit is None:
        limit = STOCKTWITS_TOP_N

    logger.info(f"Fetching StockTwits trending (top {limit})...")

    tickers = []
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(STOCKTWITS_TRENDING_URL, headers={
                "User-Agent": "Mozilla/5.0 (AlphaTradingFloor)",
                "Accept": "application/json",
            })
            resp.raise_for_status()
            data = resp.json()

        symbols = data.get("trends") or data.get("symbols") or data.get("messages") or []
        for item in symbols[:limit]:
            sym = {}
            if isinstance(item, dict):
                sym["symbol"] = item.get("symbol") or item.get("ticker") or ""
                sym["title"] = item.get("title") or item.get("name") or sym["symbol"]
                sym["watchlist_count"] = item.get("watchlist_count", 0)
                # Add sentiment data if available
                sym["bullish_count"] = item.get("bullish_count", 0)
                sym["bearish_count"] = item.get("bearish_count", 0)
                sym["message_count"] = item.get("message_count", 0)
            elif isinstance(item, str):
                sym = {"symbol": item, "title": item, "watchlist_count": 0,
                       "bullish_count": 0, "bearish_count": 0, "message_count": 0}
            if sym.get("symbol"):
                tickers.append(sym)

    except Exception as e:
        logger.warning(f"StockTwits fetch failed: {e}")

    if not tickers:
        logger.info("StockTwits API unavailable; using curated trending list")
        curated = [
            {"symbol": "NVDA", "title": "NVIDIA Corp", "watchlist_count": 0, "bullish_count": 0, "bearish_count": 0, "message_count": 0},
            {"symbol": "TSLA", "title": "Tesla Inc", "watchlist_count": 0, "bullish_count": 0, "bearish_count": 0, "message_count": 0},
            {"symbol": "AAPL", "title": "Apple Inc", "watchlist_count": 0, "bullish_count": 0, "bearish_count": 0, "message_count": 0},
            {"symbol": "META", "title": "Meta Platforms", "watchlist_count": 0, "bullish_count": 0, "bearish_count": 0, "message_count": 0},
            {"symbol": "AMD", "title": "Advanced Micro Devices", "watchlist_count": 0, "bullish_count": 0, "bearish_count": 0, "message_count": 0},
            {"symbol": "PLTR", "title": "Palantir Technologies", "watchlist_count": 0, "bullish_count": 0, "bearish_count": 0, "message_count": 0},
            {"symbol": "AMZN", "title": "Amazon.com Inc", "watchlist_count": 0, "bullish_count": 0, "bearish_count": 0, "message_count": 0},
            {"symbol": "GOOG", "title": "Alphabet Inc", "watchlist_count": 0, "bullish_count": 0, "bearish_count": 0, "message_count": 0},
            {"symbol": "MSFT", "title": "Microsoft Corp", "watchlist_count": 0, "bullish_count": 0, "bearish_count": 0, "message_count": 0},
            {"symbol": "SPY", "title": "SPDR S&P 500 ETF", "watchlist_count": 0, "bullish_count": 0, "bearish_count": 0, "message_count": 0},
        ]
        tickers = curated[:limit]

    # Save for reference
    out = CHARTS_DIR / "stocktwits_trending.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(tickers, indent=2))

    logger.info(f"StockTwits trending: {[t['symbol'] for t in tickers[:5]]}")
    return tickers


def get_top10_ticker_symbols() -> list:
    """Return just the list of top-10 ticker symbols."""
    trending = fetch_stocktwits_trending(limit=10)
    return [t["symbol"] for t in trending]
