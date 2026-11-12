"""Finviz Scanner — Secondary stock screener for unusual activity.

Scrapes Finviz screener for:
- Unusual volume (relative volume > 2)
- Gap up/down > 3%
- Top gainers/losers

Caches results with a 15-minute TTL. Falls back gracefully if
Finviz blocks the request — never crashes the pipeline.

WARNING: Finviz has no public API. HTML scraping may break when
their layout changes. This module is defensive: timeout, cache,
fallback. Treat Finviz as a secondary signal, not authoritative.

Deterministic — no LLM calls.
"""
import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from config.settings import DATA_DIR, DEFAULT_TICKERS

logger = logging.getLogger("alpha_trading_floor")

CACHE_PATH = DATA_DIR / "finviz_cache.json"
CACHE_TTL_MINUTES = 15

FINVIZ_URLS = {
    "top_gainers": "https://finviz.com/screener.ashx?v=111&s=ta_topgainers&f=cap_midover&o=-change",
    "top_losers": "https://finviz.com/screener.ashx?v=111&s=ta_toplosers&f=cap_midover&o=-change",
    "unusual_volume": "https://finviz.com/screener.ashx?v=111&s=ta_unusualvolume&f=cap_midover&o=-volume",
    "gap_up": "https://finviz.com/screener.ashx?v=111&s=ta_gapup&f=cap_midover&o=-change",
    "gap_down": "https://finviz.com/screener.ashx?v=111&s=ta_gapdown&f=cap_midover&o=-change",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


def _is_cache_valid() -> bool:
    """Check if cached Finviz data is still fresh."""
    if not CACHE_PATH.exists():
        return False
    try:
        data = json.loads(CACHE_PATH.read_text())
        cached_at = datetime.fromisoformat(data.get("cached_at", ""))
        age = datetime.now() - cached_at
        return age < timedelta(minutes=CACHE_TTL_MINUTES)
    except Exception:
        return False


def _load_cache() -> dict:
    """Load cached Finviz data."""
    try:
        return json.loads(CACHE_PATH.read_text())
    except Exception:
        return {}


def _save_cache(data: dict):
    """Save Finviz data to cache."""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    data["cached_at"] = datetime.now().isoformat()
    with open(CACHE_PATH, "w") as f:
        json.dump(data, f, indent=2)


def _parse_finviz_table(html: str) -> list:
    """Parse Finviz screener HTML to extract ticker data.

    Finviz screener results are in a table with class 'screener-table'.
    Each row has cells with ticker, price, change, volume, etc.
    """
    results = []

    # Finviz uses a table with specific structure
    # Look for ticker links: <a href="quote.ashx?t=TICKER&...
    ticker_pattern = re.compile(
        r'href="quote\.ashx\?t=([A-Z]+)[^"]*"[^>]*class="screener-link[^"]*"[^>]*>([A-Z]+)</a>'
    )

    # Also try the newer Finviz layout
    # Look for data in table rows
    row_pattern = re.compile(
        r'<tr[^>]*class="screener-[^"]*"[^>]*>(.*?)</tr>',
        re.DOTALL
    )

    rows = row_pattern.findall(html)

    for row_html in rows:
        # Extract cells
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row_html, re.DOTALL)
        if len(cells) < 5:
            continue

        # Clean cell text
        def clean(cell):
            return re.sub(r'<[^>]+>', '', cell).strip()

        cells = [clean(c) for c in cells]

        # Finviz screener columns vary by view, but typically:
        # [No, Ticker, Company, Sector, Industry, Country, Market Cap, P/E, Price, Change, Volume]
        # We look for the ticker (usually 2nd column) and price/change

        ticker = ""
        for i, cell in enumerate(cells):
            if re.match(r'^[A-Z]{1,5}$', cell) and i > 0:
                ticker = cell
                break

        if not ticker:
            continue

        # Try to find price and change
        price = None
        change_pct = None
        volume = None

        for cell in cells:
            # Price pattern: $XXX.XX or XXX.XX
            if not price and re.match(r'^\$?\d+\.\d{2}$', cell):
                price = float(cell.replace("$", ""))
            # Change pattern: +X.XX% or -X.XX%
            if not change_pct and re.match(r'^[+-]?\d+\.\d{2}%$', cell):
                change_pct = float(cell.replace("%", ""))
            # Volume pattern: XX.XM or XX.XB or plain number
            if not volume and re.match(r'^\d+\.?\d*[MBK]?$', cell) and cell not in [ticker]:
                pass  # Skip — too ambiguous

        if ticker and price:
            results.append({
                "ticker": ticker,
                "price": price,
                "change_pct": change_pct,
            })

    return results


def scan_finviz(category: str = "unusual_volume") -> list:
    """Scan Finviz for a specific category.

    Args:
        category: One of 'top_gainers', 'top_losers', 'unusual_volume',
                  'gap_up', 'gap_down'

    Returns:
        List of dicts with ticker, price, change_pct
    """
    url = FINVIZ_URLS.get(category)
    if not url:
        logger.warning(f"Unknown Finviz category: {category}")
        return []

    try:
        with httpx.Client(timeout=8, follow_redirects=True, headers=HEADERS) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                logger.warning(f"Finviz returned {resp.status_code} for {category}")
                return []

            results = _parse_finviz_table(resp.text)
            logger.info(f"Finviz {category}: found {len(results)} stocks")
            return results

    except httpx.TimeoutException:
        logger.warning(f"Finviz timed out for {category}")
        return []
    except Exception as e:
        logger.warning(f"Finviz scan failed for {category}: {e}")
        return []


def scan_all_categories() -> dict:
    """Scan all Finviz categories and return combined results.

    Uses cache if fresh. Falls back to DEFAULT_TICKERS if all scans fail.
    """
    # Check cache
    if _is_cache_valid():
        cached = _load_cache()
        logger.info(f"Using cached Finviz data (age: {datetime.now() - datetime.fromisoformat(cached['cached_at'])})")
        return cached

    results = {}
    total_found = 0

    for category in FINVIZ_URLS:
        stocks = scan_finviz(category)
        results[category] = stocks
        total_found += len(stocks)

    if total_found == 0:
        logger.warning("All Finviz scans failed — using fallback tickers")
        results["fallback"] = True
        results["fallback_tickers"] = DEFAULT_TICKERS[:10]
    else:
        results["fallback"] = False

    # Merge all tickers found for a combined "interesting stocks" list
    all_tickers = set()
    for cat_stocks in results.values():
        if not isinstance(cat_stocks, list):
            continue
        for s in cat_stocks:
            if isinstance(s, dict):
                t = s.get("ticker", "")
            elif isinstance(s, str) and len(s) <= 5 and s.isalpha():
                t = s
            else:
                continue
            if t:
                all_tickers.add(t)
    results["all_tickers"] = sorted([t for t in all_tickers if t])[:20]
    results["scanned_at"] = datetime.now().isoformat()

    _save_cache(results)
    return results


def get_unusual_activity_tickers() -> list:
    """Get a list of tickers with unusual activity for pipeline use.

    Returns tickers from unusual volume + gap scans, limited to 5
    to supplement (not replace) the default ticker list.
    """
    data = scan_all_categories()

    if data.get("fallback"):
        return []

    tickers = []
    for cat in ["unusual_volume", "gap_up", "gap_down"]:
        stocks = data.get(cat, [])
        if not isinstance(stocks, list):
            continue
        for s in stocks:
            if isinstance(s, dict):
                t = s.get("ticker", "")
            elif isinstance(s, str) and len(s) <= 5 and s.isalpha():
                t = s
            else:
                continue
            if t and t not in tickers:
                tickers.append(t)

    return tickers[:5]


def run_finviz_scan():
    """Main entry point for the pipeline."""
    logger.info("=" * 60)
    logger.info("FINVIZ SCANNER: Scanning for unusual activity")
    logger.info("=" * 60)

    try:
        results = scan_all_categories()
        unusual = get_unusual_activity_tickers()

        return {
            "status": "ok",
            "fallback": results.get("fallback", True),
            "unusual_activity_tickers": unusual,
            "categories_scanned": len(FINVIZ_URLS),
        }
    except Exception as e:
        logger.warning(f"Finviz scan failed (non-fatal): {e}")
        return {"status": "error", "error": str(e), "unusual_activity_tickers": []}
