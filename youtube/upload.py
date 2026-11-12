"""YouTube Upload — Upload video and thumbnail to YouTube.

Generates YouTube description with:
- 2 paragraphs of content
- Ticker list with Alpha Scores
- Alpha Top 3
- Alpha Pick of the Day
- Disclaimer
- Tagline
- Hashtags
"""
import json
import os
import logging
import subprocess
from pathlib import Path
from datetime import datetime

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from PIL import Image

from config.settings import (
    GOOGLE_TOKEN_PATH, GOOGLE_ENV_PATH, DISCORD_INVITE, BRAND_NAME, TAGLINE,
    CHANNEL_ID, THUMBNAILS_DIR,
)
from analysis.levels import format_key_levels

logger = logging.getLogger("alpha_trading_floor")


def validate_thumbnail(path: str | Path) -> bool:
    """Validate a YouTube thumbnail before any video upload starts."""
    try:
        candidate = Path(path)
        if not candidate.exists() or candidate.stat().st_size < 1024:
            return False
        # YouTube's custom thumbnail limit is 2 MB.
        if candidate.stat().st_size > 2 * 1024 * 1024:
            return False
        with Image.open(candidate) as image:
            return image.size == (1280, 720) and image.mode in {"RGB", "RGBA"}
    except Exception:
        return False


def _load_credentials():
    """Load Google OAuth credentials."""
    token_path = Path(GOOGLE_TOKEN_PATH)
    if not token_path.exists():
        raise FileNotFoundError(f"Google token not found: {token_path}")

    creds = Credentials.from_authorized_user_file(str(token_path))
    if creds.expired and creds.refresh_token:
        from google.auth.transport.requests import Request
        creds.refresh(Request())
        token_path.write_text(creds.to_json())

    return creds


def _get_youtube_service():
    """Get authenticated YouTube service."""
    creds = _load_credentials()
    youtube = build("youtube", "v3", credentials=creds)
    return youtube


def generate_description(
    script_data: dict,
    stock_data: dict,
    alpha_scores: dict = None,
    market_regime: dict = None,
) -> str:
    """Generate a rich YouTube description.

    Format:
    - 2 paragraphs of content
    - Ticker list with Alpha Scores
    - Alpha Top 3
    - Alpha Pick of the Day
    - Disclaimer
    - Tagline
    - Hashtags
    """
    title = script_data.get("title", "Daily Stock Analysis")
    script_text = script_data.get("script", "")

    ranked = []
    if alpha_scores:
        ranked = sorted(alpha_scores.keys(), key=lambda t: alpha_scores[t]["alpha_score"], reverse=True)
    else:
        ranked = list(stock_data.keys())
    lead_ticker = ranked[0] if ranked else next(iter(stock_data), "SPY")
    lead_levels = format_key_levels(lead_ticker, stock_data.get(lead_ticker, {}))

    # Paragraph 1: Hook and market context
    p1 = (
        f"The market is at a decision point. In today's {BRAND_NAME} analysis, "
        f"we go straight to {lead_ticker}'s setup and the price levels that could change the story. "
        "We rank the top stocks by Alpha Score, a checklist from 0 to 100 that measures setup quality, not certainty."
    )

    # Paragraph 2: Market regime and content overview
    regime_text = ""
    if market_regime:
        regime = market_regime.get("regime", "NEUTRAL")
        regime_text = f" Market regime: {regime.replace('_', ' ')}."

    p2 = (
        f"The key question is whether {lead_ticker} can hold support or challenge resistance.{regime_text} "
        f"The lead map is {lead_levels}. "
        "We break down the catalyst, the risk, the level to watch, and what would confirm the next move."
    )

    # Ticker list with Alpha Scores
    ticker_lines = []
    if alpha_scores:
        ranked = sorted(alpha_scores.keys(), key=lambda t: alpha_scores[t]["alpha_score"], reverse=True)
        for i, ticker in enumerate(ranked, 1):
            score = alpha_scores[ticker]["alpha_score"]
            classification = alpha_scores[ticker]["classification"]
            d = stock_data.get(ticker, {})
            change = d.get("change_pct", 0)
            price = d.get("current_price", "N/A")
            ticker_lines.append(f"#{i} {ticker} — ${price} ({change:+.2f}%) — Alpha Score: {score:.0f} ({classification})")
    else:
        for ticker, d in stock_data.items():
            change = d.get("change_pct", 0)
            price = d.get("current_price", "N/A")
            ticker_lines.append(f"{ticker} — ${price} ({change:+.2f}%)")

    # Explicit support, resistance, and decision levels for every covered ticker.
    level_lines = [
        format_key_levels(ticker, stock_data.get(ticker, {}))
        for ticker in ranked
    ]

    # Alpha Top 3
    top3_lines = []
    if alpha_scores:
        top3 = sorted(alpha_scores.keys(), key=lambda t: alpha_scores[t]["alpha_score"], reverse=True)[:3]
        top3_lines = [f"🥇 {t} ({alpha_scores[t]['alpha_score']:.0f} - {alpha_scores[t]['classification']})"
                      for t in top3]

    # Alpha Pick
    alpha_pick = ""
    if alpha_scores:
        pick_ticker = max(alpha_scores.keys(), key=lambda t: alpha_scores[t]["alpha_score"])
        pick_score = alpha_scores[pick_ticker]["alpha_score"]
        pick_class = alpha_scores[pick_ticker]["classification"]
        alpha_pick = f"⭐ Alpha Pick of the Day: {pick_ticker} (Alpha Score: {pick_score:.0f} - {pick_class})"

    # Assemble description
    desc_parts = [
        p1,
        "",
        p2,
        "",
        "📊 STOCKS COVERED:",
        *ticker_lines,
        "",
        "🎯 KEY LEVELS TO WATCH:",
        *level_lines,
        "",
        "🏆 ALPHA TOP 3:",
        *top3_lines,
        "",
        alpha_pick,
        "",
        f"⚠️ Not financial advice. Do your own research.",
        "",
        f"💬 {TAGLINE}",
        "",
        f"🔗 Join our Discord: {DISCORD_INVITE}",
        "",
        "#stocks #trading #stockmarket #stockstowatch #options #marketanalysis",
    ]

    return "\n".join(desc_parts)


def upload_video(
    video_path: str,
    title: str,
    description: str,
    tags: list = None,
    thumbnail_path: str = "",
) -> dict:
    """Upload video to YouTube."""
    if not Path(video_path).exists():
        return {"video_id": "", "url": "", "status": "failed", "error": "Video file not found"}
    if not validate_thumbnail(thumbnail_path):
        return {
            "video_id": "",
            "url": "",
            "status": "failed",
            "error": "Valid custom thumbnail required before YouTube upload",
        }

    youtube = _get_youtube_service()

    tags = tags or ["stock market", "trading", "alpha score", "daily analysis", BRAND_NAME]

    # Ensure brand and finance tags
    all_tags = list(set(tags + [
        BRAND_NAME, "stock analysis", "day trading",
        "stocks to watch", "market analysis",
        "alpha score", "technical analysis",
    ]))

    # Ensure Discord link in description
    if DISCORD_INVITE not in description:
        description = description.rstrip() + f"\n\nJoin our Discord: {DISCORD_INVITE}"

    privacy_status = os.getenv("YOUTUBE_PRIVACY_STATUS", "private").strip().lower()
    if privacy_status not in {"private", "unlisted", "public"}:
        raise ValueError("YOUTUBE_PRIVACY_STATUS must be private, unlisted, or public")

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": all_tags,
            "categoryId": "25",  # News & Politics
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
            "madeForKids": False,
            "embeddable": True,
        },
    }

    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = request.execute()
    video_id = response["id"]
    url = f"https://www.youtube.com/watch?v={video_id}"

    logger.info(f"Video uploaded: {url}")

    # Upload thumbnail. A video without its custom cover is not a successful run.
    thumbnail_error = None
    try:
        suffix = Path(thumbnail_path).suffix.lower()
        mimetype = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
        response = youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(thumbnail_path, mimetype=mimetype, resumable=True)
        ).execute()
        if not response:
            raise RuntimeError("YouTube returned an empty thumbnail response")
        logger.info("Custom thumbnail uploaded and acknowledged by YouTube")
    except Exception as e:
        # Retry the same current-run asset as JPEG, never a shared/stale file.
        try:
            source = Path(thumbnail_path)
            jpg_path = source.with_name(f"{source.stem}_720p.jpg")
            subprocess.run([
                "ffmpeg", "-y", "-i", str(source),
                "-vf", "scale=1280:720", "-q:v", "5", str(jpg_path)
            ], capture_output=True, check=True, timeout=30)
            if not validate_thumbnail(jpg_path):
                raise RuntimeError(f"JPEG fallback failed validation: {jpg_path}")
            response = youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(jpg_path), mimetype="image/jpeg", resumable=True)
            ).execute()
            if not response:
                raise RuntimeError("YouTube returned an empty JPEG thumbnail response")
            thumbnail_path = str(jpg_path)
            logger.info("Custom thumbnail uploaded and acknowledged by YouTube (JPEG)")
        except Exception as fallback_error:
            thumbnail_error = f"PNG upload: {e}; JPEG fallback: {fallback_error}"
            logger.error(f"Custom thumbnail upload failed: {thumbnail_error}")

    if thumbnail_error:
        return {
            "video_id": video_id,
            "url": url,
            "status": "uploaded_thumbnail_failed",
            "thumbnail_path": str(thumbnail_path),
            "error": thumbnail_error,
        }

    return {
        "video_id": video_id,
        "url": url,
        "status": "uploaded",
        "thumbnail_path": str(thumbnail_path),
        "thumbnail_status": "uploaded",
    }


def generate_and_upload_thumbnail(chart_paths: dict, stock_data: dict,
                                  alpha_scores: dict = None) -> str:
    """Return the current run's validated thumbnail path for upload.

    Never select the legacy shared ``thumbnails/thumbnail.png`` file: it can
    belong to a different episode after a failed or partial pipeline run.
    """
    current_thumbnail = chart_paths.get("thumbnail", "")
    thumbnail_source = chart_paths.get("thumbnail_source", "")
    if current_thumbnail and validate_thumbnail(current_thumbnail):
        return str(current_thumbnail)
    if current_thumbnail:
        logger.error(f"Current-run thumbnail failed validation: {current_thumbnail}")
    if thumbnail_source == "facebook_verified_same_day":
        raise FileNotFoundError(
            "Verified Facebook-derived YouTube cover is unavailable or invalid; refusing fallback"
        )

    # Fallback: resize this run's title card, still using a unique asset.
    title_card = chart_paths.get("title_card", "")
    if title_card and Path(title_card).exists():
        out_path = THUMBNAILS_DIR / f"thumbnail_from_title_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.png"
        cmd = [
            "ffmpeg", "-y", "-i", title_card,
            "-vf", "scale=1280:720",
            str(out_path)
        ]
        subprocess.run(cmd, capture_output=True, check=True, timeout=30)
        if validate_thumbnail(out_path):
            return str(out_path)

    raise FileNotFoundError("No valid current-run custom thumbnail is available")
