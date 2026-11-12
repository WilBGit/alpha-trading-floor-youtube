"""Analysis — Technical analysis engine.

Detects technical signals from stock data.
Deterministic — no LLM calls.
"""
import logging
from typing import List

from config.settings import (
    ALPHA_CLASSIFICATIONS, ALPHA_SCORE_WEIGHTS, RISK_PENALTIES
)

logger = logging.getLogger("alpha_trading_floor")


def detect_signals(data: dict) -> List[str]:
    """Detect technical signals from stock data.

    Returns a list of signal strings from:
    BREAKOUT, BREAKDOWN, ABOVE_VWAP, BELOW_VWAP, EMA_BULLISH, EMA_BEARISH,
    OVERBOUGHT, OVERSOLD, VOLUME_EXPANSION, VOLUME_CONTRACTION,
    NEAR_SUPPORT, NEAR_RESISTANCE, EXTENDED_PRICE, MOMENTUM_REVERSAL
    """
    signals = []

    price = data.get("current_price", 0)
    rsi = data.get("rsi_14", 50)
    rel_vol = data.get("relative_volume", 1.0)
    vwap = data.get("vwap", price)
    ema_9 = data.get("ema_9")
    ema_21 = data.get("ema_21")
    ema_50 = data.get("ema_50")
    sma_20 = data.get("sma_20")
    change_pct = data.get("change_pct", 0)
    change_5d = data.get("change_5d_pct")
    supports = data.get("supports", [])
    resistances = data.get("resistances", [])

    # RSI signals
    if rsi > 70:
        signals.append("OVERBOUGHT")
    elif rsi < 30:
        signals.append("OVERSOLD")

    # VWAP signals
    if price > vwap * 1.005:
        signals.append("ABOVE_VWAP")
    elif price < vwap * 0.995:
        signals.append("BELOW_VWAP")

    # EMA alignment signals
    if ema_9 and ema_21 and ema_50:
        if ema_9 > ema_21 > ema_50:
            signals.append("EMA_BULLISH")
        elif ema_9 < ema_21 < ema_50:
            signals.append("EMA_BEARISH")

    # Volume signals
    if rel_vol > 1.5:
        signals.append("VOLUME_EXPANSION")
    elif rel_vol < 0.6:
        signals.append("VOLUME_CONTRACTION")

    # Breakout / Breakdown (price above/below key levels)
    if resistances and price > resistances[0] * 1.005:
        signals.append("BREAKOUT")
    elif supports and price < supports[-1] * 0.995:
        signals.append("BREAKDOWN")

    # Near support/resistance
    for r in resistances[:2]:
        if abs(price - r) / r < 0.01:
            signals.append("NEAR_RESISTANCE")
            break

    for s in supports[:2]:
        if abs(price - s) / s < 0.01:
            signals.append("NEAR_SUPPORT")
            break

    # Extended price (> 8% above VWAP)
    if vwap > 0 and price > vwap * 1.08:
        signals.append("EXTENDED_PRICE")

    # Momentum reversal (positive daily but negative 5d, or vice versa)
    if change_5d is not None:
        if change_pct > 1.0 and change_5d < -1.0:
            signals.append("MOMENTUM_REVERSAL")
        elif change_pct < -1.0 and change_5d > 1.0:
            signals.append("MOMENTUM_REVERSAL")

    return signals


def classify_alpha_score(score: float) -> str:
    """Classify an Alpha Score into a rating category."""
    for (low, high), label in ALPHA_CLASSIFICATIONS.items():
        if low <= score <= high:
            return label
    return "HIGH RISK"


def compute_risk_penalties(data: dict, catalyst_data: dict = None) -> float:
    """Compute risk penalties based on technical and catalyst data."""
    penalties = 0.0

    price = data.get("current_price", 0)
    rsi = data.get("rsi_14", 50)
    vwap = data.get("vwap", price)
    rel_vol = data.get("relative_volume", 1.0)
    resistances = data.get("resistances", [])

    # RSI > 80 penalty
    if rsi > 80:
        penalties += RISK_PENALTIES["rsi_overbought"]

    # Price > 8% above VWAP
    if vwap > 0 and price > vwap * 1.08:
        penalties += RISK_PENALTIES["extended_above_vwap"]

    # Relative volume < 0.70
    if rel_vol < 0.70:
        penalties += RISK_PENALTIES["low_relative_volume"]

    # No catalyst
    if catalyst_data is None or catalyst_data.get("category") == "NO_CLEAR_CATALYST":
        penalties += RISK_PENALTIES["no_catalyst"]

    # Extreme bullish + RSI > 75
    sentiment = data.get("sentiment_bullish_pct", 50)
    if sentiment > 75 and rsi > 75:
        penalties += RISK_PENALTIES["extreme_bullish_overbought"]

    # Resistance within 1%
    for r in resistances[:2]:
        if abs(price - r) / r < 0.01:
            penalties += RISK_PENALTIES["near_resistance"]
            break

    # Negative catalyst
    if catalyst_data and catalyst_data.get("category") in [
        "ANALYST_DOWNGRADE", "SEC_FILING"
    ]:
        if catalyst_data.get("sentiment", 0) < 0:
            penalties += RISK_PENALTIES["negative_catalyst"]

    return penalties
