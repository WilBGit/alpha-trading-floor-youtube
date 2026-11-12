"""Script Generator — Generates the Alpha Trading Floor video script.

Single LLM call per run. Target: 1300-1700 words (9-12 min).

Sections:
1. HOOK (0:00-0:20): High-retention opener
2. ALPHA MARKET PULSE (0:20-0:50): SPY/QQQ/IWM/VIX + market mode
3. TOP 10 OVERVIEW (0:50-1:10): Quick list
4. STOCKS #10-#4 (1:10-6:30): 35-45s each with Alpha Score card
5. TOP 3 DEEP DIVE (6:30-9:15): 50-90s each
6. ALPHA PICK OF THE DAY (9:15-9:50): Highest Alpha Score stock
7. ALPHA WATCHLIST (9:50-10:15): Three stocks to watch + SPY levels
8. WHAT'S COMING NEXT (10:15-10:40): Upcoming-events hook
9. CTA (10:40-10:55): Subscribe + tagline
"""
import json
import logging
import os
import time
import base64
from pathlib import Path
from datetime import datetime

import httpx

from config.settings import (
    CHARTS_DIR, AUDIO_DIR, DATA_DIR, CODEX_BASE_URL, CODEX_CHAT_MODEL,
    DISCORD_INVITE, DISCORD_CTA, BRAND_NAME, TAGLINE,
)
from analysis.levels import format_key_levels

logger = logging.getLogger("alpha_trading_floor")


def _read_codex_token() -> str:
    """Read Codex OAuth token."""
    auth_paths = [
        Path(os.path.expanduser("~/.hermes/auth.json")),
    ]
    for auth_path in auth_paths:
        if not auth_path.exists():
            continue
        try:
            with open(auth_path) as f:
                d = json.load(f)
            pool = d.get("credential_pool", {})
            codex = pool.get("openai-codex", {})
            if isinstance(codex, list):
                for entry in codex:
                    if isinstance(entry, dict):
                        at = entry.get("access_token", "")
                        if at:
                            return at
            elif isinstance(codex, dict):
                at = codex.get("access_token", "")
                if at:
                    return at
            providers = d.get("providers", {})
            at = providers.get("openai-codex", {}).get("tokens", {}).get("access_token", "")
            if at:
                return at
        except Exception:
            continue
    return ""


def _codex_headers(token: str) -> dict:
    """Build headers for Codex API calls."""
    headers = {
        "User-Agent": "codex_cli_rs/0.0.0 (Alpha Trading Floor)",
        "originator": "codex_cli_rs",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        account_id = claims.get("chatgpt_account_id")
        if account_id:
            headers["ChatGPT-Account-ID"] = account_id
    except Exception:
        pass
    return headers


def _build_stock_summary(ticker: str, data: dict, alpha_score: dict = None,
                          technicals: dict = None, catalyst: dict = None) -> str:
    """Build a concise stock summary for the prompt."""
    lines = [
        f"{ticker} ({data.get('company_name', ticker)}):",
        f"  Price: ${data.get('current_price', 'N/A')} ({data.get('change_pct', 0):+.2f}%)",
    ]
    if data.get('change_5d_pct') is not None:
        lines.append(f"  5d Change: {data['change_5d_pct']:+.2f}%")
    if data.get('change_20d_pct') is not None:
        lines.append(f"  20d Change: {data['change_20d_pct']:+.2f}%")
    lines.append(f"  RSI: {data.get('rsi_14', 'N/A')}")
    lines.append(f"  Relative Volume: {data.get('relative_volume', 'N/A')}")
    if data.get('vwap'):
        lines.append(f"  VWAP: ${data['vwap']}")
    if data.get('ema_9') and data.get('ema_21') and data.get('ema_50'):
        lines.append(f"  EMAs: 9=${data['ema_9']}, 21=${data['ema_21']}, 50=${data['ema_50']}")
    lines.append(f"  Key levels: {format_key_levels(ticker, data)}")

    if alpha_score:
        lines.append(f"  **ALPHA SCORE: {alpha_score.get('alpha_score', 'N/A')} ({alpha_score.get('classification', 'N/A')})**")
        sub = alpha_score.get('sub_scores', {})
        if sub:
            lines.append(f"  Sub-scores: Momentum={sub.get('momentum', '?')}, "
                          f"RelVol={sub.get('relative_volume', '?')}, "
                          f"Tech={sub.get('technical_structure', '?')}, "
                          f"Catalyst={sub.get('catalyst_strength', '?')}, "
                          f"Sentiment={sub.get('sentiment', '?')}, "
                          f"Risk={sub.get('risk_quality', '?')}")

    if technicals:
        signals = technicals.get('signals', [])
        if signals:
            lines.append(f"  Signals: {', '.join(signals)}")

    if catalyst and catalyst.get('category') != 'NO_CLEAR_CATALYST':
        lines.append(f"  Catalyst: {catalyst.get('category')} (strength: {catalyst.get('strength', '?')}/10)")
        if catalyst.get('headline'):
            lines.append(f"  News: {catalyst['headline']}")

    return "\n".join(lines)


def generate_script(
    stock_data: dict,
    alpha_scores: dict = None,
    technicals: dict = None,
    catalysts: dict = None,
    market_regime: dict = None,
    trending_tickers: list = None,
    mode: str = "levels",
    upcoming_events: list = None,
) -> dict:
    """Generate a YouTube script for the Alpha Trading Floor video.

    This is the ONLY LLM call for script content per run.
    """
    token = _read_codex_token()
    if not token:
        raise RuntimeError("No Codex token. Run `hermes auth codex` first.")

    # ── Load content performance feedback (self-learning) ──────────────
    feedback_section = ""
    try:
        feedback_path = DATA_DIR / "content_feedback.json"
        if feedback_path.exists():
            fb = json.loads(feedback_path.read_text())
            parts = []
            if fb.get("total_videos", 0) > 0:
                parts.append(f"Videos analyzed: {fb['total_videos']}")
            top = fb.get("top_performer", {})
            if top.get("title"):
                parts.append(f"Top performer: '{top['title']}' ({top.get('views', 0)} views)")
            trend = fb.get("view_trend", "")
            if trend and trend != "unknown":
                parts.append(f"View trend: {trend}")
            ticker_insights = fb.get("ticker_insights", [])
            if ticker_insights:
                top_tickers = [f"{t['ticker']} ({t['avg_views']:.0f} avg views)" for t in ticker_insights[:3]]
                parts.append(f"Best-performing tickers in titles: {', '.join(top_tickers)}")
            retention = fb.get("retention_insights", {})
            if retention.get("weighted_avg_view_pct") is not None:
                parts.append(
                    f"Weighted average retention: {retention['weighted_avg_view_pct']:.1f}%"
                )
                parts.append(
                    f"Subscriber conversion: {retention.get('subscriber_conversion_per_100_views', 0):.2f} per 100 views"
                )
            rec = fb.get("recommendation", "")
            if rec:
                parts.append(f"Recommendation: {rec}")
            if parts:
                feedback_section = f"""

CONTENT PERFORMANCE FEEDBACK (from our analytics — use as guidance, not rules):
{chr(10).join(f'- {p}' for p in parts)}
Use this to guide title hooks and ticker emphasis. Do not repeat it verbatim."""
            else:
                feedback_section = "\n\nCONTENT PERFORMANCE FEEDBACK: No data yet — produce varied content to build a baseline."
    except Exception as e:
        logger.debug(f"Could not load content feedback: {e}")

    # ── Load upcoming events for "What's Coming Next" section ──────────
    events_section = ""
    if upcoming_events:
        event_lines = []
        for ev in upcoming_events[:5]:
            ticker = ev.get("ticker", "")
            ev_date = ev.get("event_date", "")
            details = ev.get("details", "")
            event_lines.append(f"- {ticker} {ev.get('event_type', 'event')} on {ev_date}: {details}")
        events_section = f"""

UPCOMING EVENTS (for "What's Coming Next" section — ONLY mention these, do not invent dates):
{chr(10).join(event_lines)}
If this list is empty, say the next video will track the next market session and whether today's key levels hold."""
    else:
        events_section = "\n\nUPCOMING EVENTS: No verified events found. Say the next video will track the next market session and whether today's key levels hold."

    # Build stock summaries with Alpha Scores
    summaries = []
    for ticker, data in stock_data.items():
        alpha = alpha_scores.get(ticker) if alpha_scores else None
        tech = technicals.get(ticker) if technicals else None
        cat = catalysts.get(ticker) if catalysts else None
        summaries.append(_build_stock_summary(ticker, data, alpha, tech, cat))

    # Build trending section
    trending_section = ""
    if trending_tickers:
        trending_symbols = [t['symbol'] for t in trending_tickers[:10]]
        trending_section = f"""

STOCKTWITS TRENDING (top 10 right now):
{', '.join(trending_symbols)}

Mention 2-3 of these trending tickers in the script if they overlap with our analysis tickers."""

    # Market regime section
    regime_section = ""
    if market_regime:
        regime = market_regime.get("regime", "NEUTRAL")
        confidence = market_regime.get("confidence", 50)
        spy_data = market_regime.get("spy", {})
        regime_section = f"""

MARKET REGIME: {regime} (confidence: {confidence}%)
SPY: ${spy_data.get('current_price', 'N/A')} ({spy_data.get('change_pct', 'N/A'):}%)
VIX: {market_regime.get('vix_price', 'N/A')}
Signals: {', '.join(market_regime.get('signals', []))}"""

    today = datetime.now().strftime("%A, %B %d, %Y")
    hook_angles = (
        "lead with the concrete price level that decides the top setup",
        "lead with the biggest risk that could invalidate the top setup",
        "lead with the catalyst or market event that changes the ranking",
        "lead with a direct question the viewer needs answered",
        "lead with the surprising gap between the top two scores",
    )
    hook_angle = hook_angles[datetime.now().toordinal() % len(hook_angles)]

    # Sort tickers by Alpha Score for ranking
    ranked_tickers = []
    if alpha_scores:
        ranked_tickers = sorted(alpha_scores.keys(), key=lambda t: alpha_scores[t]["alpha_score"], reverse=True)
    else:
        ranked_tickers = list(stock_data.keys())

    # Alpha Pick (highest score)
    alpha_pick = ranked_tickers[0] if ranked_tickers else list(stock_data.keys())[0]
    alpha_pick_score = alpha_scores.get(alpha_pick, {}).get("alpha_score", "?") if alpha_scores else "N/A"

    system_prompt = f"""You are a professional stock market analyst creating scripts for a faceless YouTube channel called "{BRAND_NAME}".

Your voice style: {BRAND_NAME} — CALM, CONFIDENT, CURIOUS, ANALYTICAL, PROFESSIONAL, MEASURED. Like a Bloomberg anchor who actually trades and is genuinely interested in what the next price move will reveal.

RETENTION AND INTEREST:
- Sound genuinely interested in the setup. Use tension, contrast, and a clear payoff instead of flat recitation.
- Put the most surprising verified fact or decision point in the first 10 seconds.
- Make each section answer a question the viewer actually has: what matters, what changes the setup, and what level confirms it.
- Use energetic but measured language. Never use hype, guarantees, or empty engagement bait.

SCRIPT STRUCTURE (MANDATORY — one question, one subject, original analysis only):
Do not copy, paraphrase, or imitate the wording of any other channel's video. Use only the supplied prices, levels, scores, and events. The shape should feel like a clear explainer: one question, then the mechanism, then the answer.

1. HOOK (0:00-0:20): Ask the one question this video answers. Today's required angle: {hook_angle}. Name {alpha_pick} and one supplied fact. No "Welcome back." No "You might disagree." No ranking list.

2. THE QUESTION IN PLAIN ENGLISH (0:20-2:30): Explain what the question means for a first-time viewer. Use SPY only as context, in two sentences. Do not tour the other tickers.

3. HOW THE MECHANISM WORKS (2:30-6:00): Teach the one idea. If the question is a price level, explain support as a price floor, resistance as a price ceiling, and the level to watch. If the question is a score, explain Alpha Score once as "our checklist score from zero to one hundred; it measures setup quality, not certainty." Use only supplied numbers.

4. THE ANSWER FOR {alpha_pick} (6:00-9:30): This is the whole video. State the exact labels "Support:", "Resistance:", and "Level to watch:" with the supplied values for {alpha_pick}. Then say what happens if the level holds and what happens if it breaks. Score: {alpha_pick_score}. One catalyst only if it was supplied.

5. WHAT WOULD CHANGE THE ANSWER (9:30-10:20): One risk, one upcoming supplied event, or the next session. If no dated event is supplied, say the next video will check whether this level holds. Do not invent a date.

6. CTA (10:20-10:40): "Which level are you watching? Drop the ticker below and subscribe for the next explanation." Then: "{TAGLINE}"

You may mention at most two other tickers, in one sentence each, only if they explain the same question. Do not rank ten stocks. Do not say Top 10.

CRITICAL RULES:
- Target 1450-1800 words. Spend them on the one question, not on a list.
- The title in the JSON must be a question or an "explained" title about {alpha_pick} only. Under 70 characters. No "Top 10".
- Assume this is the viewer's first trading video. Write at roughly a sixth-grade reading level without sounding childish.
- Reveal the useful conclusion in the first 10 seconds. Do not open with a long market preamble.
- Never use the phrase "You might disagree" or another repeated template opener. Start with a concrete fact, level, catalyst, risk, or question.
- Use one idea per sentence. Prefer sentences under 18 words. Use common words and active voice.
- Define Alpha Score once as "our checklist score from zero to one hundred; it measures setup quality, not certainty."

STYLE RULES — KEEP IT SIMPLE, NO REPETITION:
- NEVER repeat the same percentage or price change more than once per ticker. Say it ONCE and move on.
- Do NOT recite "up X%, down Y%" on every stock. The viewer can see the chart. Mention direction ONCE if relevant.
- Instead of listing numbers, FOCUS ON LEVELS: "SPY is at 545. Support is 540. If that holds, next target is 552."
- For each stock, give: current price → key support → key resistance → what happens if each breaks. That's it.
- Use if/then logic: "If support holds at $150, the next target is $158. If it breaks below $150, watch $145."
- Do NOT say the same thing three different ways. Say it once clearly and move on.
- Do NOT repeat the Alpha Score number after the first mention in each section.
- The market regime section should be 2-3 sentences MAX. State the regime, SPY level, and one implication. Done.

TECHNICAL TERMS — keep all, but explain on first use only:
- Keep all standard technical trading terms. Never remove, rename, or replace them.
- The FIRST time each term appears, say the term then immediately explain it in everyday language. After that, just use the term.
  * support = price floor; resistance = price ceiling
  * VWAP = average price paid today
  * EMA = a trend line based on recent prices
  * RSI = a buying-and-selling heat meter
  * relative volume = shares trading compared with usual
  * breakout = price moves above its ceiling; breakdown = price falls below its floor
- Do NOT explain the same term twice. One explanation per term, ever.
- Do NOT say "Alpha Score" and then repeat the number. Say it once per section.

STRUCTURE PER STOCK (concise, no repetition):
1. Name, Alpha Score, and one-line classification (e.g., "PLTR, Alpha Score 64, Neutral.")
2. Current price, Support, Resistance, and Level to watch. Use the exact supplied values.
3. If/then: "If support holds, target the next level. If it breaks, watch the next support."
4. Beginner takeaway: One sentence. Done.

- Never say "not financial advice" more than once, at the very end.
- Use only supplied prices, levels, dates, and events. Never invent a market figure.
- Include simple on-screen cue suggestions in brackets: [SHOW SUPPORT], [SHOW RESISTANCE], [SHOW VWAP].
- Include the Discord CTA: "{DISCORD_CTA}"
- Include disclaimer at end: "Not financial advice. Do your own research."

WEEKEND MODE ('advice'): Instead of stock analysis, create a FINANCIAL ADVISER educational video about personal finance principles. Still use the same 9-section structure, explain each concept like a friendly teacher, and focus on PRINCIPLES, not entries/exits.

Output ONLY valid JSON with these keys:
- title: YouTube video title (max 70 chars, include date context)
- hook: 1-2 sentence attention-grabbing opener (15-25 words)
- script: The full narration script (1450-1800 words) answering one question about {alpha_pick}
- alpha_pick: The ticker with highest Alpha Score
- tags: list of 15-20 YouTube tags
- description: Full YouTube description (400-800 chars) including Discord link and disclaimer"""

    mode_instruction = ""
    if mode == "advice":
        mode_instruction = "\n\nMODE: FINANCIAL ADVISER (weekend content). Focus on personal finance principles, portfolio strategy, risk management — not stock entries/exits. Use tickers as teaching examples."
    else:
        mode_instruction = "\n\nMODE: ONE-QUESTION EXPLAINER. Answer one question about {alpha_pick} with the supplied levels. Do not rank the full list."

    user_prompt = f"""Today is {today}. Here is the stock data:

{chr(10).join(summaries)}
{regime_section}
{trending_section}
{feedback_section}
{events_section}

Ranked by Alpha Score (highest to lowest): {', '.join(ranked_tickers)}
Alpha Pick of the Day: {alpha_pick} (Score: {alpha_pick_score})
{mode_instruction}

Create the one-question explainer. Include the exact Support, Resistance, and Level to watch values for {alpha_pick}, a plain-English explanation, the upcoming-events line, and the disclaimer. Target 1450-1800 words. Do not write a top-10 ranking."""

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

    logger.info("Generating script via Codex (Alpha Trading Floor)...")

    for attempt in range(3):
        try:
            full_text = ""
            saw_delta = False
            with httpx.Client(timeout=120) as client:
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
                                    delta = event.get("delta", "")
                                    full_text += delta
                                    saw_delta = True
                                elif event.get("type") == "response.completed" and not saw_delta:
                                    # Some responses omit delta events. Only use the
                                    # completed payload when no deltas were received;
                                    # otherwise it duplicates the streamed JSON.
                                    for item in event.get("response", {}).get("output", []):
                                        if item.get("type") == "message":
                                            for c in item.get("content", []):
                                                if c.get("type") == "output_text":
                                                    full_text += c.get("text", "")
                            except json.JSONDecodeError:
                                continue

            if not full_text:
                logger.error("Empty response from Codex")
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
            logger.info(f"Script generated: {result.get('title', 'Untitled')}")
            return result

        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse error (attempt {attempt+1}): {e}")
            logger.debug(f"Raw text: {text[:500]}")
        except httpx.HTTPStatusError as e:
            logger.warning(f"HTTP {e.response.status_code} (attempt {attempt+1})")
            if e.response.status_code == 429:
                time.sleep(30)
        except Exception as e:
            logger.warning(f"Error (attempt {attempt+1}): {e}")
            time.sleep(10)

    # ── Fallback: Grok through Hermes OAuth ───────────────────────────────
    logger.warning("Codex exhausted retries. Falling back to Grok via Hermes...")
    try:
        hermes_result = _generate_via_hermes(system_prompt, user_prompt)
        if hermes_result:
            logger.info("Script generated via Grok/Hermes fallback")
            return hermes_result
    except Exception as hermes_err:
        logger.error(f"Grok/Hermes fallback also failed: {hermes_err}")

    raise RuntimeError("Failed to generate script after 3 Codex attempts and Grok/Hermes fallback")


def _generate_via_hermes(system_prompt: str, user_prompt: str) -> dict:
    """Generate a fallback script through Hermes' authenticated Grok route."""
    import shutil
    import subprocess

    hermes_bin = os.environ.get("HERMES_BIN", "").strip() or shutil.which("hermes") or "hermes"
    model = os.environ.get("ATF_HERMES_MODEL", "grok-4.6")
    provider = os.environ.get("ATF_HERMES_PROVIDER", "xai-oauth")

    # Prepend anti-repetition instructions for models prone to duplication
    anti_repeat = (
        "CRITICAL: Do NOT repeat the same sentence, fact, or percentage twice. "
        "Each idea appears ONCE then moves on. Focus on price levels (support, resistance, VWAP) "
        "not percentage changes. Keep it concise — say it once clearly and move on.\n\n"
    )

    combined_prompt = f"{anti_repeat}{system_prompt}\n\n---\n\n{user_prompt}"

    combined_prompt += "\n\nReturn only valid JSON. Do not use markdown fences or commentary."
    result = subprocess.run(
        [
            hermes_bin,
            "chat",
            "-Q",
            "--ignore-rules",
            "-t",
            "safe",
            "-q",
            combined_prompt,
            "-m",
            model,
            "--provider",
            provider,
            "--reasoning",
            "medium",
            "--max-turns",
            "1",
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "provider process failed").strip()
        raise RuntimeError(f"Hermes provider exited {result.returncode}: {detail[:300]}")

    text = (result.stdout or "").strip()
    if "\nsession_id:" in text:
        text = text.split("\nsession_id:", 1)[0].rstrip()
    if not text:
        raise RuntimeError("Empty response from Hermes")

    # Strip markdown code fences
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    result = json.loads(text)

    # Ensure required keys exist
    for key in ("title", "hook", "script", "alpha_pick", "tags", "description"):
        if key not in result:
            if key == "tags":
                result[key] = ["Alpha Trading Floor", "stock analysis", "SPY"]
            elif key == "description":
                result[key] = f"{result.get('title', 'Daily analysis')}. Not financial advice. Do your own research."
            elif key == "hook":
                result[key] = result.get("title", "Today's analysis")
            elif key == "alpha_pick":
                result[key] = ""

    return result


def save_script(script_data: dict, stock_data: dict, alpha_scores: dict = None,
                 technicals: dict = None, catalysts: dict = None,
                 market_regime: dict = None) -> Path:
    """Save the generated script to a JSON file."""
    today = datetime.now().strftime("%Y-%m-%d")
    output_path = AUDIO_DIR / f"script_{today}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    combined = {
        "date": today,
        "brand": BRAND_NAME,
        "stock_data": stock_data,
        "alpha_scores": alpha_scores,
        "technicals": technicals,
        "catalysts": catalysts,
        "market_regime": market_regime,
        "script": script_data,
    }

    with open(output_path, "w") as f:
        json.dump(combined, f, indent=2)

    logger.info(f"Script saved to {output_path}")
    return output_path
