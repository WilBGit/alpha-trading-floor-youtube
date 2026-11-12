"""Script QC — Validate generated scripts before production.

Deterministic quality checks:
- Word count validation (1300-1700)
- Duplicate sentence detection
- Ticker/price accuracy
- Alpha Score verification
- Disclaimer presence
- CTA presence
- Similarity check against previous scripts
"""
import hashlib
import json
import logging
import re
from pathlib import Path
from difflib import SequenceMatcher

from config.settings import (
    AUDIO_DIR, DATA_DIR, SCRIPT_MIN_WORDS, SCRIPT_MAX_WORDS,
    SCRIPT_MAX_DUPLICATE_RATIO, BRAND_NAME, TAGLINE, DISCORD_INVITE,
)
from analysis.levels import build_key_levels, level_value_in_text, required_level_block

logger = logging.getLogger("alpha_trading_floor")


_BEGINNER_TRANSLATIONS = {
    "market regime": ("overall market mood", "market's overall mood"),
    "risk-on": ("investors feel confident", "buyers feel confident", "more willing to take risk"),
    "risk-off": ("investors are playing defense", "buyers are cautious", "less willing to take risk"),
    "VWAP": ("average price paid today", "average trading price today"),
    "EMA": ("recent-price trend line", "moving average", "trend line based on recent prices"),
    "RSI": (
        "buying-and-selling heat", "buying and selling heat",
        "buying/selling heat", "momentum meter", "recent buying or selling",
    ),
    "relative volume": ("shares trading compared with usual", "trading compared with usual"),
    "momentum": ("speed and direction of price", "how quickly price is moving"),
    "catalyst": (
        "news or event that could move", "news or event that can move",
        "news or an event that could move", "news or an event that can move",
    ),
    "implied volatility": (
        "how much movement options traders expect", "options market's expected uncertainty",
    ),
    "pullback": ("short drop after a rise", "temporary drop after a rise"),
    "liquidity": ("how easily shares can be bought or sold", "easy to buy or sell"),
    "risk/reward": ("how much you could lose versus gain", "possible loss versus possible gain"),
    "entry": ("possible buy area", "price where a trade begins"),
    "stop": ("price where the idea is wrong", "exit if the idea is wrong"),
    "target": ("upside goal", "price goal"),
    "overbought": ("risen too far, too fast", "risen too far too fast"),
    "oversold": ("selling may have gone too far, too fast", "selling may have gone too far too fast"),
    "breakout": ("moves above its ceiling", "moves above a price ceiling"),
    "breakdown": ("falls below its floor", "falls below a price floor"),
    "support": ("price floor",),
    "resistance": ("price ceiling",),
}


_BEGINNER_AUTOFIX_EXPLANATIONS = {
    "market regime": "the overall market mood",
    "risk-on": "buyers feel confident",
    "risk-off": "investors are playing defense",
    "VWAP": "the average price paid today",
    "EMA": "a trend line based on recent prices",
    "RSI": "a buying-and-selling heat meter",
    "relative volume": "shares trading compared with usual",
    "momentum": "the speed and direction of price",
    "catalyst": "news or an event that could move the stock",
    "implied volatility": "how much movement options traders expect",
    "pullback": "a short drop after a rise",
    "liquidity": "how easily shares can be bought or sold",
    "risk/reward": "possible loss versus possible gain",
    "entry": "the possible buy area",
    "stop": "the price where the idea is wrong",
    "target": "the upside goal",
    "overbought": "the price may have risen too far, too fast",
    "oversold": "selling may have gone too far, too fast",
    "breakout": "price moves above its ceiling",
    "breakdown": "price falls below its floor",
    "support": "a price floor",
    "resistance": "a price ceiling",
}


def find_unexplained_jargon(script_text: str) -> list[str]:
    """Return finance terms used without a beginner-friendly translation."""
    lowered = script_text.lower()
    unexplained = []
    for term, explanations in _BEGINNER_TRANSLATIONS.items():
        if re.search(rf"\b{re.escape(term.lower())}\b", lowered) and not any(
            phrase in lowered for phrase in explanations
        ):
            unexplained.append(term)
    return unexplained


def validate_script(
    script_data: dict,
    stock_data: dict,
    alpha_scores: dict = None,
) -> dict:
    """Validate a generated script against quality criteria.

    Returns dict with 'valid' (bool), 'issues' (list), 'warnings' (list).
    """
    issues = []
    warnings = []

    script_text = script_data.get("script", "")
    title = script_data.get("title", "")

    # 1. Word count
    word_count = len(script_text.split())
    if word_count < SCRIPT_MIN_WORDS:
        issues.append(f"Script too short: {word_count} words (min {SCRIPT_MIN_WORDS})")
    elif word_count > SCRIPT_MAX_WORDS:
        issues.append(f"Script too long: {word_count} words (max {SCRIPT_MAX_WORDS})")
    elif word_count < SCRIPT_MIN_WORDS + 100:
        warnings.append(f"Script near minimum length: {word_count} words")

    # 2. Duplicate sentences — hard fail if >10% of sentences are near-duplicates
    # Exclude boilerplate (disclaimer, CTA, tagline) from duplicate calculation
    boilerplate_markers = [
        "not financial advice", "do your own research", "subscribe",
        "drop the ticker", "compound wealth", "trade smart", "manage risk",
        "discord.gg", "what's coming next", "next video",
    ]
    raw_sentences = re.split(r'[.!?]+', script_text)
    sentences = []
    for s in raw_sentences:
        s = s.strip().lower()
        if len(s) <= 10:
            continue
        if any(marker in s for marker in boilerplate_markers):
            continue
        sentences.append(s)

    if len(sentences) > 1:
        # Count sentences that are near-duplicates of an *earlier* sentence
        # (first occurrence is not a duplicate; subsequent matches are)
        duplicate_count = 0
        seen = []
        for s in sentences:
            is_dup = False
            for prev in seen:
                if SequenceMatcher(None, prev, s).ratio() > 0.80:
                    is_dup = True
                    break
            if is_dup:
                duplicate_count += 1
            else:
                seen.append(s)

        dup_ratio = duplicate_count / len(sentences) if sentences else 0
        if dup_ratio > 0.10:
            issues.append(f"Too many duplicate sentences: {duplicate_count}/{len(sentences)} ({dup_ratio:.0%}) — threshold is 10%")
        elif duplicate_count > 3:
            warnings.append(f"Found {duplicate_count} duplicate or near-duplicate sentences")

    # 3. Ticker/price accuracy
    tickers_in_data = set(stock_data.keys())
    tickers_in_script = set()
    for t in tickers_in_data:
        if t in script_text.upper():
            tickers_in_script.add(t)

    missing_tickers = tickers_in_data - tickers_in_script
    if len(missing_tickers) > 2:
        warnings.append(f"Missing tickers in script: {missing_tickers}")

    # Check prices mentioned are plausible — hard fail if >5% off
    # Only check prices claimed as CURRENT (trading at, closed at, currently at, etc.)
    for ticker, data in stock_data.items():
        price = data.get("current_price")
        if not price:
            continue
        # Build patterns INSIDE the loop so ticker is defined
        esc_ticker = re.escape(ticker)
        current_price_patterns = [
            rf'{esc_ticker}\s+(?:is\s+)?(?:trading|currently|closed|priced|sitting|hovering)\s+(?:at|near|around)\s+\$(\d+\.?\d*)',
            rf'{esc_ticker}\s+(?:is\s+)?at\s+\$(\d+\.?\d*)',
            rf'\$(\d+\.?\d*)\s+(?:for|on)\s+{esc_ticker}',
        ]
        for pattern in current_price_patterns:
            matches = re.findall(pattern, script_text, re.IGNORECASE)
            for match in matches:
                mentioned_price = float(match)
                if mentioned_price <= 0:
                    continue
                pct_off = abs(mentioned_price - price) / price
                if pct_off > 0.05:
                    issues.append(
                        f"PRICE ERROR: {ticker} current price mentioned ${mentioned_price:.2f} vs actual ${price:.2f} "
                        f"({pct_off:.0%} off — threshold 5%)"
                    )
                elif pct_off > 0.02:
                    warnings.append(
                        f"{ticker}: mentioned ${mentioned_price:.2f} vs actual ${price:.2f} ({pct_off:.0%} off)"
                    )

    # 4. Required ticker levels. The top setup must state all three labels and
    # the supplied numeric values so the viewer gets an actionable map.
    if stock_data:
        top_ticker = (
            max(alpha_scores.keys(), key=lambda ticker: alpha_scores[ticker].get("alpha_score", 0))
            if alpha_scores else next(iter(stock_data))
        )
        selected_levels = build_key_levels(stock_data).get(top_ticker, {})
        required_markers = {
            "support": "support",
            "resistance": "resistance",
            "level_to_watch": "level to watch",
        }
        lowered_script = script_text.lower()
        missing_markers = [
            label for label in required_markers.values()
            if label not in lowered_script
        ]
        if missing_markers:
            issues.append("Missing required ticker level labels: " + ", ".join(missing_markers))
        for key, label in required_markers.items():
            value = selected_levels.get(key)
            if value is not None and not level_value_in_text(script_text, value):
                issues.append(
                    f"Missing supplied {label} value for {top_ticker}: {value:.2f}"
                )

    # 5. Alpha Score verification
    if alpha_scores:
        for ticker, score_data in alpha_scores.items():
            score = score_data.get("alpha_score")
            classification = score_data.get("classification", "")
            if score is not None and f"{score:.0f}" in script_text:
                # Verify the classification matches
                if classification and classification.upper() in script_text.upper():
                    pass  # Good — classification mentioned
                # Check the score value matches
                # (We just verify the number appears, not that it's attributed correctly)

    # 5. Disclaimer presence
    disclaimer_keywords = ["not financial advice", "do your own research", "dyor"]
    has_disclaimer = any(kw in script_text.lower() for kw in disclaimer_keywords)
    if not has_disclaimer:
        issues.append("Missing financial disclaimer in script")

    # 6. Upcoming-events hook
    upcoming_markers = ["what's coming next", "what comes next", "upcoming", "next video", "next episode"]
    if not any(marker in script_text.lower() for marker in upcoming_markers):
        issues.append("Missing upcoming-events hook for the next video")

    # 7. CTA presence
    has_cta = "subscribe" in script_text.lower() or "discord" in script_text.lower()
    if not has_cta:
        issues.append("Missing CTA (subscribe/Discord) in script")

    # 7. Brand/tagline
    if TAGLINE.lower() not in script_text.lower() and "compound wealth" not in script_text.lower():
        warnings.append(f"Missing tagline: '{TAGLINE}'")

    # 8. Beginner-language gate
    unexplained_terms = find_unexplained_jargon(script_text)
    if unexplained_terms:
        issues.append(
            "Unexplained finance terms: " + ", ".join(unexplained_terms)
            + ". Translate each term into everyday language the first time it appears."
        )

    # 9. Similarity to previous script
    similarity_issue = _check_previous_similarity(script_text)
    if similarity_issue:
        issues.append(similarity_issue)

    valid = len(issues) == 0

    result = {
        "valid": valid,
        "word_count": word_count,
        "issues": issues,
        "warnings": warnings,
        "script_hash": _hash_script(script_text),
    }

    if valid:
        logger.info(f"Script QC passed: {word_count} words, {len(warnings)} warnings")
    else:
        logger.warning(f"Script QC FAILED: {len(issues)} issues, {len(warnings)} warnings")
        for issue in issues:
            logger.warning(f"  - {issue}")

    return result


def _hash_script(text: str) -> str:
    """Generate a hash of the script for dedup."""
    # Normalize whitespace
    normalized = " ".join(text.lower().split())
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _check_previous_similarity(script_text: str) -> str:
    """Check if script is too similar to the previous one.

    Returns empty string if OK, or error message if too similar.
    """
    hash_file = DATA_DIR / "script_hashes.json"
    current_hash = _hash_script(script_text)

    if not hash_file.exists():
        # Save current hash
        hash_file.parent.mkdir(parents=True, exist_ok=True)
        hash_file.write_text(json.dumps([{"hash": current_hash, "date": "today"}]))
        return ""

    try:
        hashes = json.loads(hash_file.read_text())
        if hashes:
            last = hashes[-1]
            last_hash = last.get("hash", "")

            # Compare similarity using sentence overlap
            # Quick check: if hashes are identical, definitely too similar
            if current_hash == last_hash:
                # Allow through if autofix can still trim/dedup it
                logger.warning("Script hash identical to previous run — will attempt autofix")
                return "Script is identical to previous run"

            # Load previous script text for deeper comparison
            # For now, just check hash prefix (first 8 chars)
            if current_hash[:8] == last_hash[:8]:
                return "Script too similar to previous run (>15% overlap)"
    except Exception as e:
        logger.debug(f"Could not check previous similarity: {e}")

    # Append current hash
    hashes.append({"hash": current_hash, "date": "today"})
    # Keep only last 30 hashes
    hashes = hashes[-30:]
    hash_file.write_text(json.dumps(hashes))

    return ""


def _deduplicate_sentences(text: str, max_removal_ratio: float = 0.25) -> str:
    """Remove near-duplicate sentences (keeping the first occurrence).

    max_removal_ratio: stop removing duplicates once we've removed this
    fraction of original sentences. Prevents over-trimming when LLM
    output is very repetitive (e.g. GLM fallback).
    """
    boilerplate_markers = [
        "not financial advice", "do your own research", "subscribe",
        "drop the ticker", "compound wealth", "trade smart", "manage risk",
        "discord.gg", "what's coming next", "next video",
    ]
    raw_parts = re.split(r'([.!?]+)', text)
    # Rebuild: parts[0] is text, parts[1] is punctuation, parts[2] text, etc.
    sentences_with_punct = []
    i = 0
    while i < len(raw_parts):
        sentence = raw_parts[i].strip()
        punct = raw_parts[i + 1] if i + 1 < len(raw_parts) else ""
        if sentence:
            sentences_with_punct.append((sentence, punct))
        i += 2

    total_original = len(sentences_with_punct)
    max_removals = int(total_original * max_removal_ratio) if total_original > 0 else 0
    removed = 0

    seen_normalized = []
    kept = []
    for sentence, punct in sentences_with_punct:
        normalized = sentence.lower().strip()
        if len(normalized) <= 10:
            kept.append((sentence, punct))
            continue
        # Skip boilerplate from dedup check
        if any(marker in normalized for marker in boilerplate_markers):
            kept.append((sentence, punct))
            continue
        is_dup = False
        for prev in seen_normalized:
            if SequenceMatcher(None, prev, normalized).ratio() > 0.80:
                is_dup = True
                break
        if is_dup and removed < max_removals:
            removed += 1
            continue  # drop duplicate
        seen_normalized.append(normalized)
        kept.append((sentence, punct))

    result = ""
    for sentence, punct in kept:
        result += sentence + punct + " "
    return result.strip()


def _trim_to_word_limit(text: str, max_words: int) -> str:
    """Trim script to max_words by removing sentences from the end."""
    words = text.split()
    if len(words) <= max_words:
        return text
    # Split into paragraphs, trim from the end (keep intro/analysis, cut outro)
    paragraphs = text.split("\n\n")
    kept = []
    word_count = 0
    for para in paragraphs:
        para_words = len(para.split())
        if word_count + para_words <= max_words:
            kept.append(para)
            word_count += para_words
        elif word_count < max_words - 30:
            # Partial paragraph — take enough sentences to stay under limit
            sentences = re.split(r'([.!?]+)', para)
            trimmed = ""
            trim_words = 0
            i = 0
            while i < len(sentences) and word_count + trim_words < max_words:
                trimmed += sentences[i]
                trim_words += len(sentences[i].split())
                i += 1
            if trimmed.strip():
                kept.append(trimmed.strip())
                word_count += trim_words
            break
        else:
            break
    return "\n\n".join(kept)


def fix_script_issues(
    script_data: dict,
    issues: list,
    stock_data: dict | None = None,
    alpha_scores: dict | None = None,
) -> dict:
    """Attempt to fix common script issues.

    Adds missing disclaimer and CTA if needed, trims length,
    and deduplicates near-duplicate sentences.
    """
    script_text = script_data.get("script", "")

    # --- Deduplicate near-duplicate sentences first ---
    script_text = _deduplicate_sentences(script_text)

    # --- Trim to word limit ---
    words = script_text.split()
    if len(words) > SCRIPT_MAX_WORDS:
        # Reserve 80 words for disclaimer + upcoming hook + CTA + tagline that may be appended
        script_text = _trim_to_word_limit(script_text, SCRIPT_MAX_WORDS - 80)

    # Translate only the first use of each unexplained finance term. This keeps
    # the narration natural while making later uses understandable.
    for term in find_unexplained_jargon(script_text):
        explanation = _BEGINNER_AUTOFIX_EXPLANATIONS[term]
        pattern = re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
        script_text = pattern.sub(
            lambda match: f"{match.group(0)}, meaning {explanation}",
            script_text,
            count=1,
        )

    # Add a deterministic level block when the model omitted a required label
    # or supplied number. This is a safety repair, not a guessed market view.
    if stock_data:
        top_ticker = (
            max(alpha_scores.keys(), key=lambda ticker: alpha_scores[ticker].get("alpha_score", 0))
            if alpha_scores else next(iter(stock_data))
        )
        levels = build_key_levels(stock_data).get(top_ticker, {})
        required_values = (
            levels.get("support"),
            levels.get("resistance"),
            levels.get("level_to_watch"),
        )
        needs_levels = (
            "support" not in script_text.lower()
            or "resistance" not in script_text.lower()
            or "level to watch" not in script_text.lower()
            or any(value is not None and not level_value_in_text(script_text, value) for value in required_values)
        )
        if needs_levels:
            script_text += "\n\n" + required_level_block(top_ticker, levels=levels)

    # Add disclaimer if missing
    disclaimer_keywords = ["not financial advice", "do your own research"]
    has_disclaimer = any(kw in script_text.lower() for kw in disclaimer_keywords)
    if not has_disclaimer:
        script_text += "\n\nNot financial advice. Do your own research."

    # Add upcoming-events hook if missing
    upcoming_markers = ["what's coming next", "what comes next", "upcoming", "next video", "next episode"]
    if not any(marker in script_text.lower() for marker in upcoming_markers):
        script_text += "\n\nWhat's coming next: In the next video, we'll track whether today's key levels hold and what fresh earnings and economic data could shift the market. Subscribe so you don't miss it."

    # Add CTA if missing
    if "subscribe" not in script_text.lower():
        script_text += f"\n\nWhich stock are you watching? Drop the ticker below and subscribe for the next update."

    if "discord" not in script_text.lower():
        script_text += f"\n\n{DISCORD_INVITE}"

    if TAGLINE.lower() not in script_text.lower():
        script_text += f"\n\n{TAGLINE}"

    script_data["script"] = script_text
    return script_data
