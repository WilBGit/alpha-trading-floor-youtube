"""Technical Analysis Engine.

Upgraded technical analysis with EMAs, VWAP, volume analysis,
5d/20d changes, and signal detection.

Deterministic — no LLM calls.
"""
import logging
from typing import List

from analysis import detect_signals, classify_alpha_score, compute_risk_penalties

logger = logging.getLogger("alpha_trading_floor")


def analyze_ticker(data: dict) -> dict:
    """Perform full technical analysis on a single ticker.

    Returns a dict with signals, technicals summary, and analysis notes.
    """
    ticker = data.get("ticker", "UNKNOWN")
    logger.info(f"Analyzing {ticker}")

    # Detect signals
    signals = detect_signals(data)

    # Build technical summary
    technicals = {
        "ticker": ticker,
        "price": data.get("current_price"),
        "change_pct": data.get("change_pct"),
        "change_5d_pct": data.get("change_5d_pct"),
        "change_20d_pct": data.get("change_20d_pct"),
        "rsi_14": data.get("rsi_14"),
        "ema_9": data.get("ema_9"),
        "ema_21": data.get("ema_21"),
        "ema_50": data.get("ema_50"),
        "vwap": data.get("vwap"),
        "relative_volume": data.get("relative_volume"),
        "sma_20": data.get("sma_20"),
        "sma_50": data.get("sma_50"),
        "volume": data.get("volume"),
        "avg_volume_20": data.get("avg_volume_20"),
        "supports": data.get("supports", [])[:3],
        "resistances": data.get("resistances", [])[:3],
        "signals": signals,
        "signal_summary": ", ".join(signals) if signals else "NEUTRAL",
    }

    # EMA alignment assessment
    ema_9 = data.get("ema_9")
    ema_21 = data.get("ema_21")
    ema_50 = data.get("ema_50")
    price = data.get("current_price", 0)

    if ema_9 and ema_21 and ema_50:
        if ema_9 > ema_21 > ema_50:
            technicals["ema_alignment"] = "BULLISH"
        elif ema_9 < ema_21 < ema_50:
            technicals["ema_alignment"] = "BEARISH"
        else:
            technicals["ema_alignment"] = "MIXED"
    else:
        technicals["ema_alignment"] = "INSUFFICIENT_DATA"

    # VWAP position
    vwap = data.get("vwap", 0)
    if vwap > 0:
        vwap_offset = (price - vwap) / vwap * 100
        if vwap_offset > 2:
            technicals["vwap_position"] = "ABOVE"
        elif vwap_offset < -2:
            technicals["vwap_position"] = "BELOW"
        else:
            technicals["vwap_position"] = "AT_VWAP"
        technicals["vwap_offset_pct"] = round(vwap_offset, 2)
    else:
        technicals["vwap_position"] = "UNKNOWN"

    # Support/resistance proximity
    nearest_support = None
    nearest_resistance = None
    if data.get("supports"):
        nearest_support = data["supports"][-1]  # closest support below
    if data.get("resistances"):
        nearest_resistance = data["resistances"][0]  # closest resistance above

    technicals["nearest_support"] = nearest_support
    technicals["nearest_resistance"] = nearest_resistance

    # Distance to nearest levels
    if nearest_support and price > 0:
        technicals["dist_to_support_pct"] = round((price - nearest_support) / price * 100, 2)
    if nearest_resistance and price > 0:
        technicals["dist_to_resistance_pct"] = round((nearest_resistance - price) / price * 100, 2)

    return technicals


def analyze_all(stock_data: dict) -> dict:
    """Perform technical analysis on all tickers.

    Returns dict of ticker -> technicals.
    """
    results = {}
    for ticker, data in stock_data.items():
        results[ticker] = analyze_ticker(data)

    logger.info(f"Technical analysis complete for {len(results)} tickers")
    return results
