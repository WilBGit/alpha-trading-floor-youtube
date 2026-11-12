"""YFinance Data Collector.

Fetches comprehensive stock data including:
- Price data (OHLCV, 52W range, change percentages)
- Technical indicators (SMA, EMA, RSI, ATR, VWAP)
- Market regime indicators (SPY, QQQ, IWM, VIX)
- Volume analysis (relative volume, 5d/20d changes)

Deterministic — no LLM calls.
"""
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import yfinance as yf
import numpy as np

from config.settings import (
    CHARTS_DIR, MARKET_INDEX_TICKERS, DEFAULT_TICKERS
)

logger = logging.getLogger("alpha_trading_floor")


def fetch_ticker_data(ticker: str, period: str = "6mo") -> dict:
    """Fetch comprehensive data for a single ticker.

    Includes price data, technical indicators, volume analysis,
    and support/resistance levels.
    """
    logger.info(f"Fetching data for {ticker}")

    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period=period)

        if hist.empty:
            logger.error(f"No data returned for {ticker}")
            return {}

        latest = hist.iloc[-1]
        prev_close = hist.iloc[-2]["Close"] if len(hist) > 1 else latest["Close"]

        # Key levels
        high_52w = hist["High"].max()
        low_52w = hist["Low"].min()

        # Moving averages
        sma_20 = hist["Close"].rolling(20).mean().iloc[-1] if len(hist) >= 20 else None
        sma_50 = hist["Close"].rolling(50).mean().iloc[-1] if len(hist) >= 50 else None
        sma_200 = hist["Close"].rolling(200).mean().iloc[-1] if len(hist) >= 200 else None

        # Exponential Moving Averages
        ema_9 = hist["Close"].ewm(span=9, adjust=False).mean().iloc[-1] if len(hist) >= 9 else None
        ema_21 = hist["Close"].ewm(span=21, adjust=False).mean().iloc[-1] if len(hist) >= 21 else None
        ema_50 = hist["Close"].ewm(span=50, adjust=False).mean().iloc[-1] if len(hist) >= 50 else None

        # RSI 14
        delta = hist["Close"].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        rsi_14 = (100 - (100 / (1 + rs))).iloc[-1]

        # ATR 14
        high_low = hist["High"] - hist["Low"]
        high_close = (hist["High"] - hist["Close"].shift(1)).abs()
        low_close = (hist["Low"] - hist["Close"].shift(1)).abs()
        tr = high_low.to_frame("HL").join(high_close.rename("HC")).join(low_close.rename("LC")).max(axis=1)
        atr_14 = tr.rolling(14).mean().iloc[-1]

        # Volume analysis
        avg_vol_20 = hist["Volume"].rolling(20).mean().iloc[-1] if len(hist) >= 20 else None
        current_vol = int(latest["Volume"])
        relative_volume = current_vol / avg_vol_20 if avg_vol_20 and avg_vol_20 > 0 else 1.0

        # VWAP calculation (typical price * volume / cumulative volume)
        typical_price = (hist["High"] + hist["Low"] + hist["Close"]) / 3
        if len(hist) >= 1:
            cum_tp_vol = (typical_price * hist["Volume"]).cumsum().iloc[-1]
            cum_vol = hist["Volume"].cumsum().iloc[-1]
            vwap = float(cum_tp_vol / cum_vol) if cum_vol > 0 else float(latest["Close"])
        else:
            vwap = float(latest["Close"])

        # Price changes
        current_price = float(latest["Close"])
        daily_change_pct = float((latest["Close"] - prev_close) / prev_close * 100)

        # 5-day and 20-day price changes
        change_5d = None
        change_20d = None
        if len(hist) >= 6:
            price_5d_ago = float(hist["Close"].iloc[-6])
            change_5d = float((current_price - price_5d_ago) / price_5d_ago * 100)
        if len(hist) >= 21:
            price_20d_ago = float(hist["Close"].iloc[-21])
            change_20d = float((current_price - price_20d_ago) / price_20d_ago * 100)

        # Recent support/resistance (pivot points from last 30 days)
        recent = hist.tail(30)
        supports = []
        resistances = []
        for i in range(2, len(recent) - 2):
            row = recent.iloc[i]
            if recent.iloc[i]["Low"] <= recent.iloc[i-2:i]["Low"].min() and \
               recent.iloc[i]["Low"] <= recent.iloc[i+1:i+3]["Low"].min():
                supports.append(round(float(recent.iloc[i]["Low"]), 2))
            if recent.iloc[i]["High"] >= recent.iloc[i-2:i]["High"].max() and \
               recent.iloc[i]["High"] >= recent.iloc[i+1:i+3]["High"].max():
                resistances.append(round(float(recent.iloc[i]["High"]), 2))

        # Get company info
        info = {}
        try:
            info = stock.info or {}
        except Exception:
            pass

        data = {
            "ticker": ticker,
            "company_name": info.get("longName", info.get("shortName", ticker)),
            "current_price": round(current_price, 2),
            "previous_close": round(float(prev_close), 2),
            "change_pct": round(daily_change_pct, 2),
            "change_5d_pct": round(change_5d, 2) if change_5d is not None else None,
            "change_20d_pct": round(change_20d, 2) if change_20d is not None else None,
            "day_open": round(float(latest["Open"]), 2),
            "day_high": round(float(latest["High"]), 2),
            "day_low": round(float(latest["Low"]), 2),
            "volume": current_vol,
            "avg_volume_20": int(avg_vol_20) if avg_vol_20 else None,
            "relative_volume": round(relative_volume, 2),
            "high_52w": round(float(high_52w), 2),
            "low_52w": round(float(low_52w), 2),
            "sma_20": round(float(sma_20), 2) if sma_20 else None,
            "sma_50": round(float(sma_50), 2) if sma_50 else None,
            "sma_200": round(float(sma_200), 2) if sma_200 else None,
            "ema_9": round(float(ema_9), 2) if ema_9 else None,
            "ema_21": round(float(ema_21), 2) if ema_21 else None,
            "ema_50": round(float(ema_50), 2) if ema_50 else None,
            "vwap": round(vwap, 2),
            "rsi_14": round(float(rsi_14), 2),
            "atr_14": round(float(atr_14), 2),
            "supports": sorted(set(supports), reverse=True)[:5],
            "resistances": sorted(set(resistances))[:5],
            "history_dates": [d.strftime("%Y-%m-%d") for d in hist.index[-120:]],
            "history_opens": [round(float(v), 2) for v in hist["Open"].tail(120)],
            "history_closes": [round(float(v), 2) for v in hist["Close"].tail(120)],
            "history_volumes": [int(v) for v in hist["Volume"].tail(120)],
            "history_highs": [round(float(v), 2) for v in hist["High"].tail(120)],
            "history_lows": [round(float(v), 2) for v in hist["Low"].tail(120)],
            "fetched_at": datetime.now().isoformat(),
        }

        logger.info(f"  {ticker}: ${data['current_price']} ({data['change_pct']:+.2f}%) "
                     f"RSI={data['rsi_14']} RelVol={data['relative_volume']}")
        return data

    except Exception as e:
        logger.error(f"Error fetching {ticker}: {e}")
        return {}


def fetch_market_regime() -> dict:
    """Fetch market regime indicators: SPY, QQQ, IWM, VIX.

    Returns regime classification: RISK_ON, NEUTRAL, or RISK_OFF.
    Deterministic — no LLM calls.
    """
    from config.settings import REGIME_THRESHOLDS

    logger.info("Fetching market regime indicators...")
    regime_data = {}

    for ticker in MARKET_INDEX_TICKERS:
        data = fetch_ticker_data(ticker, period="3mo")
        if data:
            regime_data[ticker] = data

    # Calculate regime
    regime = "NEUTRAL"
    confidence = 50
    signals = []

    spy = regime_data.get("SPY", {})
    qqq = regime_data.get("QQQ", {})
    iwm = regime_data.get("IWM", {})
    vix = regime_data.get("^VIX", {})

    # SPY vs SMA50
    if spy and spy.get("sma_50"):
        spy_vs_sma = (spy["current_price"] - spy["sma_50"]) / spy["sma_50"] * 100
        if spy_vs_sma > REGIME_THRESHOLDS["spy_sma_offset_pct"]:
            signals.append("SPY_ABOVE_SMA50")
        elif spy_vs_sma < -REGIME_THRESHOLDS["spy_sma_offset_pct"]:
            signals.append("SPY_BELOW_SMA50")

    # VIX level
    vix_price = vix.get("current_price", 20)
    if vix_price > REGIME_THRESHOLDS["vix_fear"]:
        signals.append("VIX_FEAR")
    elif vix_price < REGIME_THRESHOLDS["vix_complacent"]:
        signals.append("VIX_COMPLACENT")

    # QQQ/IWM ratio (growth vs value rotation)
    if qqq and iwm:
        qqq_price = qqq.get("current_price", 0)
        iwm_price = iwm.get("current_price", 1)
        ratio = qqq_price / iwm_price
        # Simple regime detection based on ratio trend
        if qqq.get("change_pct", 0) > iwm.get("change_pct", 0) + 0.5:
            signals.append("GROWTH_LEADING")
        elif iwm.get("change_pct", 0) > qqq.get("change_pct", 0) + 0.5:
            signals.append("VALUE_LEADING")

    # Determine regime from signals
    risk_on_count = sum(1 for s in signals if s in [
        "SPY_ABOVE_SMA50", "VIX_COMPLACENT", "GROWTH_LEADING"
    ])
    risk_off_count = sum(1 for s in signals if s in [
        "SPY_BELOW_SMA50", "VIX_FEAR", "VALUE_LEADING"
    ])

    if risk_on_count > risk_off_count + 1:
        regime = "RISK_ON"
        confidence = 60 + risk_on_count * 10
    elif risk_off_count > risk_on_count + 1:
        regime = "RISK_OFF"
        confidence = 60 + risk_off_count * 10
    else:
        regime = "NEUTRAL"
        confidence = 50

    confidence = min(confidence, 95)

    result = {
        "regime": regime,
        "confidence": confidence,
        "signals": signals,
        "spy": {k: v for k, v in spy.items() if k in [
            "current_price", "change_pct", "sma_50", "rsi_14"
        ]} if spy else {},
        "qqq": {k: v for k, v in qqq.items() if k in [
            "current_price", "change_pct"
        ]} if qqq else {},
        "iwm": {k: v for k, v in iwm.items() if k in [
            "current_price", "change_pct"
        ]} if iwm else {},
        "vix_price": vix.get("current_price") if vix else None,
        "fetched_at": datetime.now().isoformat(),
    }

    logger.info(f"Market regime: {regime} (confidence: {confidence}%) signals: {signals}")
    return result


def fetch_all(tickers: list = None) -> dict:
    """Fetch data for all tickers plus market regime."""
    if tickers is None:
        from config.settings import DEFAULT_TICKERS
        tickers = DEFAULT_TICKERS

    results = {}
    for t in tickers:
        data = fetch_ticker_data(t)
        if data:
            results[t] = data

    # Save to file
    output_path = CHARTS_DIR / "stock_data.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    logger.info(f"Saved data for {len(results)} tickers to {output_path}")

    return results
