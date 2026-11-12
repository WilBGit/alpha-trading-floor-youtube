"""Scoring — Alpha Score Engine.

Calculates the Alpha Score for each ticker based on 6 sub-scores:
- MOMENTUM (25%): daily change, 5d change, 20d change, RSI
- RELATIVE VOLUME (20%): current vol / avg vol
- TECHNICAL STRUCTURE (20%): EMA alignment, VWAP position, support/resistance proximity
- CATALYST STRENGTH (15%): news catalyst classification and scoring
- SENTIMENT (10%): StockTwits message volume + bullish/bearish ratio
- RISK QUALITY (10%): inverse of risk factors

Deterministic — no LLM calls.
"""
import logging
from typing import Optional

from analysis import detect_signals, classify_alpha_score, compute_risk_penalties

logger = logging.getLogger("alpha_trading_floor")


def score_momentum(data: dict) -> float:
    """Calculate momentum sub-score (0-100).

    Components:
    - Daily change: |change| -> 0-30 points
    - 5-day change: |change| -> 0-25 points
    - 20-day change: |change| -> 0-20 points
    - RSI quality: distance from 50 -> 0-25 points
    """
    score = 0.0

    # Daily change (absolute magnitude rewarded, direction adds bonus)
    daily_change = data.get("change_pct", 0)
    daily_magnitude = min(abs(daily_change), 5.0) / 5.0 * 30  # Max 30 points
    daily_direction_bonus = 10 if daily_change > 0 else 0  # Up days score higher
    score += daily_magnitude + daily_direction_bonus

    # 5-day change
    change_5d = data.get("change_5d_pct")
    if change_5d is not None:
        score += min(abs(change_5d), 10.0) / 10.0 * 25  # Max 25 points
    else:
        score += 12.5  # Neutral if no data

    # 20-day change
    change_20d = data.get("change_20d_pct")
    if change_20d is not None:
        score += min(abs(change_20d), 20.0) / 20.0 * 20  # Max 20 points
    else:
        score += 10  # Neutral if no data

    # RSI quality (50 is neutral, extremes score lower)
    rsi = data.get("rsi_14", 50)
    if 40 <= rsi <= 60:
        rsi_score = 25  # Perfect range
    elif 30 <= rsi <= 70:
        rsi_score = 15 + 10 * (1 - abs(rsi - 50) / 20)
    else:
        rsi_score = max(0, 10 - abs(rsi - 50) / 3)
    score += rsi_score

    return min(max(score, 0), 100)


def score_relative_volume(data: dict) -> float:
    """Calculate relative volume sub-score (0-100).

    Higher relative volume = more conviction.
    rel_vol 2.0+ = 100, 1.0 = 60, <0.5 = 20
    """
    rel_vol = data.get("relative_volume", 1.0)

    if rel_vol >= 3.0:
        return 100.0
    elif rel_vol >= 2.0:
        return 80 + (rel_vol - 2.0) * 20
    elif rel_vol >= 1.0:
        return 40 + (rel_vol - 1.0) * 40
    elif rel_vol >= 0.5:
        return 20 + (rel_vol - 0.5) * 40
    else:
        return max(5, rel_vol * 40)


def score_technical_structure(data: dict) -> float:
    """Calculate technical structure sub-score (0-100).

    Components:
    - EMA alignment: 0-35 points
    - VWAP position: 0-35 points
    - Support/resistance proximity: 0-30 points
    """
    score = 0.0
    price = data.get("current_price", 0)

    # EMA alignment (0-35)
    ema_9 = data.get("ema_9")
    ema_21 = data.get("ema_21")
    ema_50 = data.get("ema_50")

    if ema_9 and ema_21 and ema_50:
        if ema_9 > ema_21 > ema_50:
            # Bullish alignment + price above EMA9
            if price > ema_9:
                score += 35
            else:
                score += 25
        elif ema_9 < ema_21 < ema_50:
            # Bearish alignment
            if price < ema_9:
                score += 15  # Confirmed downtrend (still low score)
            else:
                score += 20  # Potential reversal
        else:
            # Mixed alignment
            score += 18
    else:
        score += 15  # Insufficient data

    # VWAP position (0-35)
    vwap = data.get("vwap", 0)
    if vwap > 0:
        vwap_offset = (price - vwap) / vwap * 100
        if 0 < vwap_offset < 2:
            score += 35  # Just above VWAP - ideal
        elif 2 <= vwap_offset < 5:
            score += 25  # Above VWAP but not extended
        elif vwap_offset < 0:
            score += 15  # Below VWAP
        elif vwap_offset >= 5:
            score += 10  # Extended above VWAP
        else:
            score += 20
    else:
        score += 17  # Neutral

    # Support/resistance proximity (0-30)
    supports = data.get("supports", [])
    resistances = data.get("resistances", [])

    if supports and resistances:
        # Near support with room to resistance = good setup
        dist_to_support = abs(price - supports[-1]) / price * 100 if price > 0 else 999
        dist_to_resistance = abs(resistances[0] - price) / price * 100 if price > 0 else 999

        if dist_to_support < 2 and dist_to_resistance > 3:
            score += 30  # Near support, far from resistance - ideal
        elif dist_to_support < 3:
            score += 20  # Near support
        elif dist_to_resistance < 2:
            score += 10  # Near resistance
        else:
            score += 15  # Between levels
    else:
        score += 15  # No clear levels

    return min(max(score, 0), 100)


def score_catalyst(catalyst_data: Optional[dict]) -> float:
    """Calculate catalyst strength sub-score (0-100).

    Based on catalyst classification and strength score.
    """
    if not catalyst_data or catalyst_data.get("category") == "NO_CLEAR_CATALYST":
        return 20.0  # Low score for no catalyst

    strength = catalyst_data.get("strength", 5)  # 0-10 scale
    category = catalyst_data.get("category", "NO_CLEAR_CATALYST")

    # Base score from strength (0-10 -> 0-70)
    base_score = (strength / 10.0) * 70

    # Category bonuses (high-impact catalysts get bonus)
    high_impact = ["EARNINGS", "ANALYST_UPGRADE", "PRODUCT_ANNOUNCEMENT",
                   "AI_NEWS", "FDA_NEWS", "MERGER", "ACQUISITION"]
    medium_impact = ["GUIDANCE", "PARTNERSHIP", "GOVERNMENT_CONTRACT",
                     "SHORT_SQUEEZE", "CRYPTO_MOVEMENT"]

    if category in high_impact:
        base_score += 30
    elif category in medium_impact:
        base_score += 15
    else:
        base_score += 5

    return min(max(base_score, 0), 100)


def score_sentiment(stocktwits_data: Optional[dict], data: dict) -> float:
    """Calculate sentiment sub-score (0-100).

    Based on StockTwits message volume + bullish/bearish ratio.
    Falls back to neutral if no StockTwits data.
    """
    if not stocktwits_data:
        return 50.0  # Neutral

    msg_count = stocktwits_data.get("message_count", 0)
    bullish = stocktwits_data.get("bullish_count", 0)
    bearish = stocktwits_data.get("bearish_count", 0)

    # Message volume score (higher = more interest)
    if msg_count > 500:
        vol_score = 40
    elif msg_count > 100:
        vol_score = 30
    elif msg_count > 20:
        vol_score = 20
    else:
        vol_score = 10

    # Bullish/bearish ratio score
    total = bullish + bearish
    if total > 0:
        bull_ratio = bullish / total
        # Slightly bullish is good (60-70%), extreme is risky
        if 0.55 <= bull_ratio <= 0.70:
            ratio_score = 60
        elif 0.45 <= bull_ratio <= 0.80:
            ratio_score = 40
        elif bull_ratio > 0.80:
            ratio_score = 30  # Too bullish = contrarian warning
        else:
            ratio_score = 20
    else:
        ratio_score = 30  # No sentiment data

    return min(max(vol_score + ratio_score, 0), 100)


def score_risk_quality(data: dict, catalyst_data: Optional[dict] = None) -> float:
    """Calculate risk quality sub-score (0-100).

    Inverse of risk factors — higher = less risky.
    """
    risk_factors = 0

    # High RSI = risk
    rsi = data.get("rsi_14", 50)
    if rsi > 80:
        risk_factors += 3
    elif rsi > 70:
        risk_factors += 1

    # Extended above VWAP
    price = data.get("current_price", 0)
    vwap = data.get("vwap", price)
    if vwap > 0 and price > vwap * 1.08:
        risk_factors += 3

    # Low relative volume = less conviction
    rel_vol = data.get("relative_volume", 1.0)
    if rel_vol < 0.5:
        risk_factors += 2
    elif rel_vol < 0.7:
        risk_factors += 1

    # Near resistance = risk
    resistances = data.get("resistances", [])
    for r in resistances[:2]:
        if abs(price - r) / r < 0.01:
            risk_factors += 2
            break

    # Negative catalyst
    if catalyst_data and catalyst_data.get("category") in [
        "ANALYST_DOWNGRADE", "SEC_FILING"
    ]:
        risk_factors += 3

    # Convert to score (max risk_factors ~12, score = 100 - risk*8)
    score = 100 - (risk_factors * 8)
    return min(max(score, 0), 100)


def compute_alpha_score(
    data: dict,
    catalyst_data: Optional[dict] = None,
    stocktwits_data: Optional[dict] = None,
) -> dict:
    """Compute the full Alpha Score for a ticker.

    Returns dict with sub-scores, raw score, penalties, final score, classification.
    """
    from config.settings import ALPHA_SCORE_WEIGHTS

    ticker = data.get("ticker", "UNKNOWN")

    # Calculate sub-scores
    momentum = score_momentum(data)
    rel_volume = score_relative_volume(data)
    technical = score_technical_structure(data)
    catalyst = score_catalyst(catalyst_data)
    sentiment = score_sentiment(stocktwits_data, data)
    risk_quality = score_risk_quality(data, catalyst_data)

    # Weighted sum
    raw_score = (
        momentum * ALPHA_SCORE_WEIGHTS["momentum"] +
        rel_volume * ALPHA_SCORE_WEIGHTS["relative_volume"] +
        technical * ALPHA_SCORE_WEIGHTS["technical_structure"] +
        catalyst * ALPHA_SCORE_WEIGHTS["catalyst_strength"] +
        sentiment * ALPHA_SCORE_WEIGHTS["sentiment"] +
        risk_quality * ALPHA_SCORE_WEIGHTS["risk_quality"]
    )

    # Risk penalties
    penalties = compute_risk_penalties(data, catalyst_data)

    # Final score
    final_score = max(0, min(100, raw_score - penalties))
    classification = classify_alpha_score(final_score)

    # Detect signals
    signals = detect_signals(data)

    result = {
        "ticker": ticker,
        "sub_scores": {
            "momentum": round(momentum, 1),
            "relative_volume": round(rel_volume, 1),
            "technical_structure": round(technical, 1),
            "catalyst_strength": round(catalyst, 1),
            "sentiment": round(sentiment, 1),
            "risk_quality": round(risk_quality, 1),
        },
        "raw_score": round(raw_score, 1),
        "risk_penalties": round(penalties, 1),
        "alpha_score": round(final_score, 1),
        "classification": classification,
        "signals": signals,
        "penalty_details": _penalty_details(data, catalyst_data),
    }

    logger.info(f"  {ticker}: Alpha Score={final_score:.1f} ({classification}) "
                 f"Raw={raw_score:.1f} Penalties={penalties:.1f} "
                 f"Signals={signals}")
    return result


def _penalty_details(data: dict, catalyst_data: Optional[dict] = None) -> list:
    """Return a list of which penalties were applied."""
    from config.settings import RISK_PENALTIES
    details = []
    price = data.get("current_price", 0)
    rsi = data.get("rsi_14", 50)
    vwap = data.get("vwap", price)
    rel_vol = data.get("relative_volume", 1.0)
    resistances = data.get("resistances", [])

    if rsi > 80:
        details.append(f"RSI>{80} (-{RISK_PENALTIES['rsi_overbought']})")
    if vwap > 0 and price > vwap * 1.08:
        details.append(f"Price>8% above VWAP (-{RISK_PENALTIES['extended_above_vwap']})")
    if rel_vol < 0.70:
        details.append(f"RelVol<{0.70} (-{RISK_PENALTIES['low_relative_volume']})")
    if catalyst_data is None or catalyst_data.get("category") == "NO_CLEAR_CATALYST":
        details.append(f"No catalyst (-{RISK_PENALTIES['no_catalyst']})")
    for r in resistances[:2]:
        if abs(price - r) / r < 0.01:
            details.append(f"Near resistance (-{RISK_PENALTIES['near_resistance']})")
            break
    if catalyst_data and catalyst_data.get("category") in ["ANALYST_DOWNGRADE", "SEC_FILING"]:
        if catalyst_data.get("sentiment", 0) < 0:
            details.append(f"Negative catalyst (-{RISK_PENALTIES['negative_catalyst']})")

    return details


def score_all_tickers(
    stock_data: dict,
    catalyst_data: dict = None,
    stocktwits_data: dict = None,
) -> dict:
    """Compute Alpha Scores for all tickers.

    Args:
        stock_data: Dict of ticker -> price data from yfinance
        catalyst_data: Dict of ticker -> catalyst info (from news engine)
        stocktwits_data: Dict of ticker -> StockTwits sentiment data

    Returns:
        Dict of ticker -> alpha score result, sorted by score descending
    """
    if catalyst_data is None:
        catalyst_data = {}
    if stocktwits_data is None:
        stocktwits_data = {}

    results = {}
    for ticker, data in stock_data.items():
        cat = catalyst_data.get(ticker)
        sw = stocktwits_data.get(ticker)
        results[ticker] = compute_alpha_score(data, cat, sw)

    # Sort by alpha score descending
    results = dict(sorted(results.items(), key=lambda x: x[1]["alpha_score"], reverse=True))
    return results
