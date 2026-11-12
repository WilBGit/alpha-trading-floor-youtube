"""Earnings Calendar — Fetch upcoming earnings dates via yfinance.

Populates the upcoming_events table so the script generator can
include real, verified events in the "What's Coming Next" section.

Deterministic — no LLM calls.
"""
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import yfinance as yf

from config.settings import DATA_DIR, DEFAULT_TICKERS

logger = logging.getLogger("alpha_trading_floor")


def fetch_earnings_calendar(tickers: list = None, lookforward_days: int = 30) -> list:
    """Fetch upcoming earnings dates for a list of tickers.

    Args:
        tickers: List of ticker symbols. Defaults to DEFAULT_TICKERS.
        lookforward_days: How many days ahead to look.

    Returns:
        List of event dicts: ticker, event_type, event_date, source, details
    """
    if tickers is None:
        tickers = DEFAULT_TICKERS

    events = []
    cutoff = datetime.now().date() + timedelta(days=lookforward_days)

    for ticker in tickers:
        try:
            stock = yf.Ticker(ticker)

            # Method 1: earnings_dates (newer yfinance API)
            try:
                ed = stock.earnings_dates
                if ed is not None and not ed.empty:
                    for idx, row in ed.iterrows():
                        try:
                            if hasattr(idx, 'date'):
                                event_date = idx.date() if hasattr(idx, 'date') else idx
                            else:
                                event_date = datetime.strptime(str(idx)[:10], "%Y-%m-%d").date()

                            if isinstance(event_date, datetime):
                                event_date = event_date.date()

                            if datetime.now().date() <= event_date <= cutoff:
                                eps_est = row.get("Earnings Estimate", None)
                                events.append({
                                    "ticker": ticker,
                                    "event_type": "earnings",
                                    "event_date": event_date.isoformat(),
                                    "source": "yfinance",
                                    "confidence": 0.9,
                                    "details": f"EPS estimate: {eps_est}" if eps_est else "Earnings report",
                                })
                        except Exception:
                            continue
            except Exception as e:
                logger.debug(f"earnings_dates failed for {ticker}: {e}")

            # Method 2: calendar (older API)
            if not any(e["ticker"] == ticker for e in events):
                try:
                    cal = stock.calendar
                    if cal and isinstance(cal, dict):
                        earnings_date = cal.get("Earnings Date")
                        if earnings_date:
                            if isinstance(earnings_date, list) and earnings_date:
                                ed = earnings_date[0]
                                if isinstance(ed, datetime):
                                    ed = ed.date()
                                if datetime.now().date() <= ed <= cutoff:
                                    events.append({
                                        "ticker": ticker,
                                        "event_type": "earnings",
                                        "event_date": ed.isoformat(),
                                        "source": "yfinance_calendar",
                                        "confidence": 0.8,
                                        "details": "Earnings report (from calendar)",
                                    })
                except Exception as e:
                    logger.debug(f"calendar failed for {ticker}: {e}")

        except Exception as e:
            logger.debug(f"Earnings fetch failed for {ticker}: {e}")

    # Sort by date
    events.sort(key=lambda e: e["event_date"])

    logger.info(f"Found {len(events)} upcoming earnings events in next {lookforward_days} days")
    for e in events:
        logger.info(f"  {e['ticker']}: {e['event_date']} ({e['event_type']})")

    # Save to JSON
    out_path = DATA_DIR / "earnings_calendar.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({
            "fetched_at": datetime.now().isoformat(),
            "lookforward_days": lookforward_days,
            "events": events,
        }, f, indent=2, default=str)

    return events


def save_events_to_db(events: list):
    """Save upcoming events to the database."""
    if not events:
        return

    try:
        from database.db import get_connection
        conn = get_connection()
        if not conn:
            return

        cursor = conn.cursor()

        # Clear old events (past dates)
        cursor.execute("DELETE FROM upcoming_events WHERE event_date < CURDATE()")

        for e in events:
            cursor.execute("""
                INSERT INTO upcoming_events
                    (ticker, event_type, event_date, source, confidence, details)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    details=VALUES(details), confidence=VALUES(confidence)
            """, (
                e.get("ticker", ""),
                e.get("event_type", ""),
                e.get("event_date"),
                e.get("source", ""),
                e.get("confidence", 1.0),
                e.get("details", ""),
            ))

        cursor.close()
        conn.close()
        logger.info(f"Saved {len(events)} events to upcoming_events table")
    except Exception as e:
        logger.warning(f"Failed to save events to DB: {e}")


def get_upcoming_events(days_ahead: int = 14) -> list:
    """Get upcoming events from DB or JSON for script generation."""
    # Try DB first
    try:
        from database.db import get_connection
        conn = get_connection()
        if conn:
            cursor = conn.cursor(dictionary=True)
            cutoff = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
            today = datetime.now().strftime("%Y-%m-%d")
            cursor.execute("""
                SELECT ticker, event_type, event_date, details, source
                FROM upcoming_events
                WHERE event_date >= %s AND event_date <= %s
                ORDER BY event_date ASC
            """, (today, cutoff))
            events = cursor.fetchall()
            cursor.close()
            conn.close()
            if events:
                return events
    except Exception:
        pass

    # Fallback: JSON file
    json_path = DATA_DIR / "earnings_calendar.json"
    if json_path.exists():
        try:
            data = json.loads(json_path.read_text())
            return data.get("events", [])
        except Exception:
            pass

    return []


def run_earnings_calendar():
    """Main entry point for the pipeline."""
    logger.info("=" * 60)
    logger.info("EARNINGS CALENDAR: Fetching upcoming events")
    logger.info("=" * 60)

    try:
        events = fetch_earnings_calendar()
        save_events_to_db(events)
        return {"status": "ok", "events_found": len(events)}
    except Exception as e:
        logger.warning(f"Earnings calendar failed (non-fatal): {e}")
        return {"status": "error", "error": str(e)}
