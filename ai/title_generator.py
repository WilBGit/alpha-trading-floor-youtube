"""Title Generator — Generate 5 title options and pick the best.

Second LLM call per run. Generates curiosity/search-relevant titles.
"""
import json
import logging
import time
import base64
import os
import re
from pathlib import Path
from datetime import datetime

import httpx

from config.settings import (
    CODEX_BASE_URL, CODEX_CHAT_MODEL, BRAND_NAME,
)
from ai.script_generator import _read_codex_token, _codex_headers
from analysis.levels import format_key_levels

logger = logging.getLogger("alpha_trading_floor")


def _primary_ticker(title: str, tickers: list[str]) -> str | None:
    """Return the first known ticker mentioned in a title."""
    matches = []
    for ticker in tickers:
        match = re.search(
            rf"(?<![A-Z0-9]){re.escape(str(ticker).upper())}(?![A-Z0-9])",
            str(title).upper(),
        )
        if match:
            matches.append((match.start(), ticker))
    return min(matches, default=(None, None))[1]


def _coherent_title_candidates(
    titles: list[str], stock_data: dict, top_ticker: str
) -> list[str]:
    """Keep only titles whose primary subject matches the thumbnail subject."""
    tickers = list(stock_data.keys())
    return [
        title for title in titles
        if _primary_ticker(title, tickers) == top_ticker
    ]


def generate_titles(
    stock_data: dict,
    alpha_scores: dict = None,
    market_regime: dict = None,
    script_data: dict = None,
) -> dict:
    """Generate 5 title options and score them.

    Returns dict with titles, scores, and the selected best title.
    """
    token = _read_codex_token()
    if not token:
        raise RuntimeError("No Codex token. Run `hermes auth codex` first.")

    # Build context
    today = datetime.now().strftime("%b %d")

    # Get top 3 tickers by Alpha Score
    ranked = []
    if alpha_scores:
        ranked = sorted(alpha_scores.keys(), key=lambda t: alpha_scores[t]["alpha_score"], reverse=True)[:3]
    else:
        ranked = list(stock_data.keys())[:3]

    top_ticker = ranked[0] if ranked else "SPY"
    top_score = alpha_scores.get(top_ticker, {}).get("alpha_score", "?") if alpha_scores else "?"

    # Market regime
    regime = market_regime.get("regime", "NEUTRAL") if market_regime else "NEUTRAL"

    # Ticker price changes for context
    ticker_info = []
    for t in ranked[:5]:
        d = stock_data.get(t, {})
        change = d.get("change_pct", 0)
        score = alpha_scores.get(t, {}).get("alpha_score", "?") if alpha_scores else "?"
        ticker_info.append(
            f"{t} ({change:+.1f}%, Alpha={score}; {format_key_levels(t, d)})"
        )

    system_prompt = f"""You are a YouTube title optimizer for a stock market channel called "{BRAND_NAME}".

Generate 5 title options for today's video. Each title must be:
- Under 70 characters
- Name only the lead ticker, {top_ticker}
- Ask one question or promise one explanation
- Use a real supplied price, level, or score
- Sound like a person asking a useful question, not a ranking show

Title shapes to use, one each:
1. How does {top_ticker}'s level actually work?
2. {top_ticker} explained: the one level that matters
3. If {top_ticker} loses the supplied support, what happens next?
4. What {top_ticker}'s score really means
5. Why this {top_ticker} move does not match the market

Do not write "Top 10", "ranked", "Alpha Pick", "you'll disagree", or a list of tickers.
Output ONLY valid JSON: {{"titles": [str, str, str, str, str]}}"""

    user_prompt = f"""Today: {today}
Top tickers: {', '.join(ticker_info)}
Alpha Pick: {top_ticker} (Score: {top_score})
Market regime: {regime}

Generate 5 YouTube title options."""

    headers = _codex_headers(token)
    payload = {
        "model": CODEX_CHAT_MODEL,
        "store": False,
        "input": [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": user_prompt}]}
        ],
        "instructions": system_prompt,
        "stream": True,
    }

    logger.info("Generating title options via Codex...")

    for attempt in range(3):
        try:
            full_text = ""
            saw_delta = False
            with httpx.Client(timeout=60) as client:
                with client.stream("POST", f"{CODEX_BASE_URL}/responses", json=payload, headers=headers) as resp:
                    resp.raise_for_status()
                    for line in resp.iter_lines():
                        if isinstance(line, bytes):
                            line = line.decode("utf-8", errors="replace")
                        line = line.strip()
                        if not line or line.startswith(":"):
                            continue
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str == "[DONE]":
                                continue
                            try:
                                event = json.loads(data_str)
                                if event.get("type") == "response.output_text.delta":
                                    full_text += event.get("delta", "")
                                    saw_delta = True
                                elif event.get("type") == "response.completed" and not saw_delta:
                                    # Some responses omit delta events. Do not append
                                    # the completed payload after streamed deltas.
                                    for item in event.get("response", {}).get("output", []):
                                        if item.get("type") == "message":
                                            for c in item.get("content", []):
                                                if c.get("type") == "output_text":
                                                    full_text += c.get("text", "")
                            except json.JSONDecodeError:
                                continue

            if not full_text:
                logger.error("Empty response from Codex for titles")
                continue

            text = full_text.strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

            result = json.loads(text)
            titles = result.get("titles", [])

            # Keep a deterministic fallback set available for malformed or
            # subject-incoherent LLM output.
            fallback_titles = [
                f"How Does {top_ticker}'s Level Actually Work?",
                f"{top_ticker} Explained: The Level That Matters",
                f"If {top_ticker} Breaks, What Happens Next?",
                f"What {top_ticker}'s {top_score} Score Really Means",
                f"Why {top_ticker} Is the Setup That Matters",
            ]
            if not isinstance(titles, list):
                titles = []
            titles = [str(title).strip() for title in titles if str(title).strip()]
            if len(titles) < 5:
                titles.extend(fallback_titles)
            titles = titles[:5]

            # The thumbnail and script are built around top_ticker. Never
            # publish a title whose primary ticker names a different stock.
            coherent_titles = _coherent_title_candidates(
                titles, stock_data, top_ticker
            )
            if not coherent_titles:
                coherent_titles = _coherent_title_candidates(
                    fallback_titles, stock_data, top_ticker
                )
            if not coherent_titles:
                coherent_titles = [
                    f"How Does {top_ticker}'s Level Actually Work?"
                ]
            if len(coherent_titles) < 5:
                coherent_titles.extend(
                    title for title in fallback_titles
                    if title not in coherent_titles
                    and _primary_ticker(title, list(stock_data.keys())) == top_ticker
                )
            coherent_titles = coherent_titles[:5]

            # Score only coherent titles, so the deterministic ranking cannot
            # resurrect a mismatched LLM choice.
            scored_titles = []
            for title in coherent_titles:
                score = _score_title(title, stock_data, alpha_scores)
                scored_titles.append({"title": title, "score": score})

            # Pick the best
            scored_titles.sort(key=lambda x: x["score"], reverse=True)
            best = scored_titles[0]

            logger.info(f"Best title: '{best['title']}' (score: {best['score']})")
            return {
                "titles": scored_titles,
                "selected_title": best["title"],
                "selected_score": best["score"],
            }

        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse error (attempt {attempt+1}): {e}")
        except httpx.HTTPStatusError as e:
            logger.warning(f"HTTP {e.response.status_code} (attempt {attempt+1})")
            if e.response.status_code == 429:
                time.sleep(30)
        except Exception as e:
            logger.warning(f"Error (attempt {attempt+1}): {e}")
            time.sleep(10)

    # Fallback
    fallback_title = f"How Does {top_ticker}'s Level Actually Work?"
    return {
        "titles": [{"title": fallback_title, "score": 50}],
        "selected_title": fallback_title,
        "selected_score": 50,
    }


def _score_title(title: str, stock_data: dict, alpha_scores: dict = None) -> float:
    """Score a title for curiosity, search relevance, and click potential.

    Deterministic scoring — no LLM calls.
    """
    score = 50.0  # Base score

    # Has ticker symbol (search relevance)
    tickers = list(stock_data.keys())
    for t in tickers:
        if t in title.upper():
            score += 8
            break

    lowered = title.lower()
    if lowered.startswith("how ") or "explained" in lowered or lowered.startswith("if ") or lowered.startswith("why ") or lowered.startswith("what "):
        score += 12
    if "top 10" in lowered or "ranked" in lowered or "alpha pick" in lowered:
        score -= 20

    # Has number (specificity)
    import re
    if re.search(r'\d+', title):
        score += 5

    # Has question or disagreement (curiosity)
    if "?" in title or "disagree" in title.lower() or "nobody" in title.lower():
        score += 7

    # Under 60 chars (optimal YouTube title length)
    if len(title) < 60:
        score += 5
    elif len(title) > 70:
        score -= 10  # Over limit

    # Has today/today context (freshness)
    if "today" in title.lower() or datetime.now().strftime("%b") in title:
        score += 3

    # Has market regime keyword (search relevance)
    if any(kw in title.lower() for kw in ["risk", "bullish", "bearish", "breakout", "levels"]):
        score += 4

    return min(max(score, 0), 100)
