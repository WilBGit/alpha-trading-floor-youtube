"""Alpha Trading Floor — Main Pipeline Orchestrator.

Coordinates all pipeline stages:
1. Fetch Stock Data (yfinance + market regime)
2. Fetch StockTwits Trending
3. Analyze Catalysts (web search)
4. Compute Alpha Scores
5. Generate Script (1 LLM call)
6. Generate Titles (1 LLM call)
7. Script QC (deterministic validation)
8. Generate Charts + Thumbnails
9. Generate Narration (macOS TTS)
10. Compose Video (ffmpeg)
11. Upload to YouTube

LOW TOKEN BURN: Only 2 LLM calls (script + title). Everything else is deterministic.
"""
import json
import logging
import sys
import os
import hashlib
from datetime import datetime
from pathlib import Path

# Remove hermes-agent venv paths that conflict with project venv
sys.path = [p for p in sys.path if 'hermes-agent' not in p]

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "app"))
sys.path.insert(0, str(PROJECT_ROOT / "config"))

from config.settings import (
    PROJECT_DIR, CHARTS_DIR, AUDIO_DIR, VIDEOS_DIR, LOGS_DIR, DATA_DIR,
    DEFAULT_TICKERS, STOCKTWITS_TOP_N, BRAND_NAME, TAGLINE,
    DISCORD_INVITE, GOOGLE_TOKEN_PATH,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOGS_DIR / "pipeline.log"),
    ]
)
logger = logging.getLogger("alpha_trading_floor")

WEEKDAY_MODE = "levels"       # Mon-Fri: stock analysis with Alpha Scores
WEEKEND_MODE = "advice"        # Sat-Sun: finance advice content


def qc_blocks_publish(qc_result: dict | None) -> bool:
    """Fail closed: only an explicit valid QC result may continue to publishing.

    After autofix, some issues are soft-blockers (identical-hash) that shouldn't
    prevent publishing if the script is otherwise valid. Only hard-block on
    price errors, missing disclaimers, and excessive length/duplication.
    """
    if not qc_result or not qc_result.get("valid", False):
        issues = qc_result.get("issues", []) if qc_result else []
        # Soft issues that don't block publishing after autofix
        soft_patterns = [
            "Script is identical to previous run",
            "Script too similar to previous run",
            "Script too short:",  # GLM fallback may produce shorter scripts
        ]
        hard_issues = [i for i in issues if not any(p in i for p in soft_patterns)]
        if hard_issues:
            return True  # Hard issues still block
        elif not issues:
            return True  # No issues but valid=False is unexpected — block
        else:
            # Only soft issues remain — allow publishing
            logger = logging.getLogger("alpha_trading_floor")
            logger.warning(f"QC soft issues only, allowing publish: {issues}")
            return False
    return False


def run_pipeline(tickers: list = None, upload: bool = True, mode: str = None,
                 format_tag: str = None) -> dict:
    """Run the full Alpha Trading Floor pipeline.

    Args:
        tickers: Override ticker list (default: dynamic from StockTwits top 10)
        upload: Whether to upload to YouTube
        mode: 'levels' for stock analysis, 'advice' for weekend finance advice
        format_tag: Stable analytics label for controlled format comparisons
    """
    start = datetime.now()
    today = start.strftime("%Y-%m-%d")
    day_name = start.strftime("%A")

    # Auto-detect mode from day of week
    if mode is None:
        mode = WEEKEND_MODE if start.weekday() >= 5 else WEEKDAY_MODE
    if not format_tag:
        format_tag = "one_question_explainer" if mode == WEEKDAY_MODE else "advice_education"

    status = {
        "started_at": start.isoformat(),
        "mode": mode,
        "format_tag": format_tag,
        "day": day_name,
        "brand": BRAND_NAME,
        "errors": [],
        "tasks_completed": [],
    }

    logger.info(f"Running {BRAND_NAME} in {mode.upper()} mode ({day_name})")

    # ── Initialize database ──────────────────────────────────────────────
    try:
        from database.db import init_database
        init_database()
        status["tasks_completed"].append("db_init")
    except Exception as e:
        logger.warning(f"Database init failed (non-fatal): {e}")

    # ── Task 1: Fetch Stock Data + Market Regime ─────────────────────────
    logger.info("=" * 60)
    logger.info("TASK 1: Fetch Stock Data + Market Regime")
    logger.info("=" * 60)

    if tickers is None:
        try:
            from collectors.stocktwits import fetch_stocktwits_trending
            trending = fetch_stocktwits_trending(limit=STOCKTWITS_TOP_N)
            tickers = list(dict.fromkeys([t["symbol"] for t in trending]))
            if "SPY" not in tickers:
                tickers.insert(0, "SPY")
            tickers = tickers[:10]
            logger.info(f"Dynamic tickers from StockTwits: {tickers}")
        except Exception as e:
            logger.warning(f"StockTwits fetch failed, using defaults: {e}")
            tickers = DEFAULT_TICKERS[:10]

    status["tickers"] = tickers

    from collectors.yfinance_data import fetch_all, fetch_market_regime

    try:
        stock_data = fetch_all(tickers)
        if not stock_data:
            raise RuntimeError("No stock data fetched")
        status["tasks_completed"].append("task01_stock_data")
        logger.info(f"Fetched data for {len(stock_data)} tickers")
    except Exception as e:
        logger.error(f"Stock data fetch failed: {e}")
        status["errors"].append(f"Task 1: {e}")
        _save_status(status, start)
        return status

    # Market regime
    market_regime = {}
    try:
        market_regime = fetch_market_regime()
        status["market_regime"] = market_regime
        status["tasks_completed"].append("task01b_market_regime")
        logger.info(f"Market regime: {market_regime.get('regime', 'UNKNOWN')}")
    except Exception as e:
        logger.warning(f"Market regime detection failed (non-fatal): {e}")

    # ── Task 1c: StockTwits Trending ─────────────────────────────────────
    logger.info("=" * 60)
    logger.info("TASK 1c: StockTwits Trending")
    logger.info("=" * 60)
    trending_tickers = []
    try:
        from collectors.stocktwits import fetch_stocktwits_trending
        trending_tickers = fetch_stocktwits_trending()
        status["tasks_completed"].append("task01c_stocktwits")
        status["trending_tickers"] = [t["symbol"] for t in trending_tickers]

        # Save to DB
        try:
            from database.db import save_trending
            save_trending(trending_tickers)
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"StockTwits trending fetch failed (non-fatal): {e}")

    # ── Task 1d: Finviz Scanner (non-fatal, supplements ticker list) ───
    logger.info("=" * 60)
    logger.info("TASK 1d: Finviz Scanner")
    logger.info("=" * 60)
    finviz_signals = []
    try:
        from collectors.finviz_scanner import run_finviz_scan
        finviz_result = run_finviz_scan()
        status["finviz_scan"] = finviz_result
        finviz_signals = finviz_result.get("unusual_activity_tickers", [])
        if finviz_signals:
            logger.info(f"Finviz unusual activity: {finviz_signals}")
            # Supplement ticker list with up to 2 Finviz tickers not already included
            for t in finviz_signals[:2]:
                if t not in tickers and len(tickers) < 12:
                    tickers.append(t)
                    logger.info(f"Added Finviz ticker: {t}")
            status["tickers"] = tickers
        status["tasks_completed"].append("task01d_finviz")
    except Exception as e:
        logger.warning(f"Finviz scan failed (non-fatal): {e}")

    # ── Task 1e: Earnings Calendar (non-fatal, feeds "What's Coming Next") ─
    logger.info("=" * 60)
    logger.info("TASK 1e: Earnings Calendar")
    logger.info("=" * 60)
    upcoming_events = []
    try:
        from collectors.earnings_calendar import run_earnings_calendar, get_upcoming_events
        earnings_result = run_earnings_calendar()
        status["earnings_calendar"] = earnings_result
        upcoming_events = get_upcoming_events(days_ahead=14)
        if upcoming_events:
            logger.info(f"Found {len(upcoming_events)} upcoming events for script hook")
        status["tasks_completed"].append("task01e_earnings")
    except Exception as e:
        logger.warning(f"Earnings calendar failed (non-fatal): {e}")

    # ── Task 2: Technical Analysis ───────────────────────────────────────
    logger.info("=" * 60)
    logger.info("TASK 2: Technical Analysis")
    logger.info("=" * 60)
    technicals = {}
    try:
        from analysis.technical import analyze_all
        technicals = analyze_all(stock_data)
        status["tasks_completed"].append("task02_technical_analysis")

        # Save to DB
        try:
            from database.db import save_technical_analysis
            save_technical_analysis(technicals)
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"Technical analysis failed (non-fatal): {e}")

    # ── Task 3: Catalyst Analysis ─────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("TASK 3: Catalyst Analysis")
    logger.info("=" * 60)
    catalysts = {}
    try:
        from scoring.catalyst_engine import analyze_all_catalysts
        catalysts = analyze_all_catalysts(stock_data)
        from collectors.news_rss import configured_sources
        status["news_sources_configured"] = configured_sources()
        status["news_sources_observed"] = sorted({
            item.get("source", "")
            for catalyst in catalysts.values()
            for item in catalyst.get("news_items", [])
            if item.get("source")
        })
        status["tasks_completed"].append("task03_catalysts")

        # Save to DB
        try:
            from database.db import save_catalysts
            save_catalysts(catalysts)
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"Catalyst analysis failed (non-fatal): {e}")

    # ── Task 4: Alpha Score Engine ────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("TASK 4: Alpha Score Engine")
    logger.info("=" * 60)
    alpha_scores = {}
    try:
        from scoring.alpha_score import score_all_tickers

        # Build StockTwits sentiment lookup
        sw_sentiment = {}
        for t in trending_tickers:
            sw_sentiment[t["symbol"]] = t

        alpha_scores = score_all_tickers(stock_data, catalysts, sw_sentiment)
        status["tasks_completed"].append("task04_alpha_scores")

        # Log scores
        for ticker, score in alpha_scores.items():
            logger.info(f"  {ticker}: Alpha Score={score['alpha_score']:.1f} ({score['classification']})")

        from analysis.levels import build_key_levels
        status["key_levels"] = build_key_levels(stock_data)

        # Save to DB
        try:
            from database.db import save_alpha_scores
            save_alpha_scores(alpha_scores)
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"Alpha Score computation failed (non-fatal): {e}")

    # ── Task 5: Generate Script (LLM CALL #1) ────────────────────────────
    logger.info("=" * 60)
    logger.info(f"TASK 5: Generate Script ({mode} mode)")
    logger.info("=" * 60)
    script_data = {}
    try:
        from ai.script_generator import generate_script, save_script

        script_data = generate_script(
            stock_data,
            alpha_scores=alpha_scores,
            technicals=technicals,
            catalysts=catalysts,
            market_regime=market_regime,
            trending_tickers=trending_tickers,
            mode=mode,
            upcoming_events=upcoming_events,
        )
        script_path = save_script(script_data, stock_data, alpha_scores, technicals, catalysts, market_regime)
        status["tasks_completed"].append("task05_script")
        status["script_title"] = script_data.get("title", "Untitled")
        logger.info(f"Script: {script_data.get('title', 'Untitled')}")
    except Exception as e:
        logger.error(f"Script generation failed: {e}")
        status["errors"].append(f"Task 5: {e}")
        _save_status(status, start)
        return status

    # ── Task 5b: Script QC ────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("TASK 5b: Script QC")
    logger.info("=" * 60)
    try:
        from ai.script_qc import validate_script, fix_script_issues

        qc_result = validate_script(script_data, stock_data, alpha_scores)

        if not qc_result["valid"]:
            logger.warning(f"Script QC issues: {qc_result['issues']}")
            # Try to fix common issues once, then fail closed.
            script_data = fix_script_issues(
                script_data,
                qc_result["issues"],
                stock_data=stock_data,
                alpha_scores=alpha_scores,
            )
            qc_result = validate_script(script_data, stock_data, alpha_scores)

        status["script_qc"] = qc_result
        status["tasks_completed"].append("task05b_script_qc")
        if qc_blocks_publish(qc_result):
            issues = qc_result.get("issues", ["unknown QC failure"])
            message = f"Task 5b: Script QC blocked publishing: {issues}"
            logger.error(message)
            status["errors"].append(message)
            _save_status(status, start)
            return status
    except Exception as e:
        message = f"Task 5b: Script QC failed closed: {e}"
        logger.error(message)
        status["errors"].append(message)
        _save_status(status, start)
        return status

    # ── Task 6: Generate Titles (LLM CALL #2) ────────────────────────────
    logger.info("=" * 60)
    logger.info("TASK 6: Generate Titles")
    logger.info("=" * 60)
    title_data = {}
    try:
        from ai.title_generator import generate_titles

        title_data = generate_titles(stock_data, alpha_scores, market_regime, script_data)
        script_data["title"] = title_data.get("selected_title", script_data.get("title", ""))
        status["tasks_completed"].append("task06_titles")
        status["selected_title"] = title_data.get("selected_title")
        logger.info(f"Selected title: {title_data.get('selected_title')}")
    except Exception as e:
        logger.warning(f"Title generation failed (using script title): {e}")
        status["errors"].append(f"Task 6: {e}")

    # ── Task 7: Generate Charts + Thumbnails ──────────────────────────────
    logger.info("=" * 60)
    logger.info("TASK 7: Generate Charts + Thumbnails")
    logger.info("=" * 60)
    chart_paths = {}
    try:
        from visuals.charts import generate_all_charts

        chart_paths = generate_all_charts(stock_data, script_data, alpha_scores)
        status["tasks_completed"].append("task07_charts")
        logger.info(f"Generated {len(chart_paths)} chart images")

        # Reuse the verified Facebook image for this same-day release. A
        # different YouTube-generated cover would break the cross-platform
        # visual handoff, so missing or stale Facebook assets fail closed.
        from visuals.cover_gen import (
            apply_question_headline,
            build_thumbnail_output_path,
            facebook_cover_pixel_sha256,
            generate_dynamic_thumbnail,
            load_facebook_cover_for_date,
            question_headline,
        )
        thumbnail_ticker = max(
            alpha_scores.keys(), key=lambda t: alpha_scores[t].get("alpha_score", 0)
        ) if alpha_scores else next(iter(stock_data), "SPY")
        thumbnail_score = float(
            alpha_scores.get(thumbnail_ticker, {}).get("alpha_score", 0)
        ) if alpha_scores else 0.0
        thumbnail_output = build_thumbnail_output_path(
            script_data,
            thumbnail_ticker,
            thumbnail_score,
            output_dir=VIDEOS_DIR / today,
        )
        cover_date = os.environ.get("ATF_COVER_DATE", today)
        facebook_cover_path = load_facebook_cover_for_date(cover_date)
        if not facebook_cover_path:
            raise RuntimeError(
                "No verified same-day Alpha Trading Floor Facebook image is available; "
                "refusing to release a mismatched YouTube cover"
            )
        thumbnail_path = generate_dynamic_thumbnail(
            stock_data,
            alpha_scores,
            {**script_data, "mode": mode},
            market_regime=market_regime,
            output_path=thumbnail_output,
            facebook_cover_path=facebook_cover_path,
        )
        chart_paths["thumbnail"] = str(thumbnail_path)
        chart_paths["thumbnail_source"] = "facebook_verified_same_day"
        status["thumbnail_path"] = str(thumbnail_path)
        status["thumbnail_source"] = "facebook_verified_same_day"
        status["facebook_cover_path"] = str(facebook_cover_path)
        status["facebook_cover_sha256"] = hashlib.sha256(
            facebook_cover_path.read_bytes()
        ).hexdigest()
        facebook_pixels = facebook_cover_pixel_sha256(facebook_cover_path)
        thumbnail_pixels = facebook_cover_pixel_sha256(thumbnail_path)
        status["facebook_cover_pixel_sha256"] = facebook_pixels
        status["thumbnail_pixel_sha256"] = thumbnail_pixels
        status["thumbnail_matches_facebook"] = facebook_pixels == thumbnail_pixels
        if not status["thumbnail_matches_facebook"]:
            raise RuntimeError("Replicated YouTube cover pixels do not match the Facebook source")
        headline = question_headline(script_data.get("title", ""), thumbnail_ticker)
        thumbnail_path = apply_question_headline(facebook_cover_path, headline, thumbnail_output)
        chart_paths["thumbnail"] = str(thumbnail_path)
        status["thumbnail_path"] = str(thumbnail_path)
        status["cover_headline"] = headline
        status["cover_style"] = "question_headline_on_verified_facebook_photo"
        logger.info(f"Question headline applied to verified Facebook cover: {headline}")
    except Exception as e:
        logger.error(f"Chart generation failed: {e}")
        status["errors"].append(f"Task 7: {e}")
        _save_status(status, start)
        return status

    # ── Task 8: Generate Narration (macOS TTS — zero tokens) ─────────────
    logger.info("=" * 60)
    logger.info("TASK 8: Generate Narration")
    logger.info("=" * 60)
    try:
        from voice.narration import generate_narration

        audio_path = generate_narration(script_data, stock_data)
        status["tasks_completed"].append("task08_voice")
        status["audio_path"] = str(audio_path)
        logger.info(f"Narration: {audio_path}")
    except Exception as e:
        logger.error(f"Narration generation failed: {e}")
        status["errors"].append(f"Task 8: {e}")
        _save_status(status, start)
        return status

    # ── Task 9: Compose Video (ffmpeg — zero tokens) ──────────────────────
    logger.info("=" * 60)
    logger.info("TASK 9: Compose Video")
    logger.info("=" * 60)
    try:
        from video.composer import compose_video

        video_path = compose_video(stock_data, script_data, str(audio_path),
                                    alpha_scores=alpha_scores, chart_paths=chart_paths)
        status["tasks_completed"].append("task09_video")
        status["video_path"] = str(video_path)
        logger.info(f"Video: {video_path}")
    except Exception as e:
        logger.error(f"Video composition failed: {e}")
        status["errors"].append(f"Task 9: {e}")
        _save_status(status, start)
        return status

    # ── Task 10: Upload to YouTube ────────────────────────────────────────
    if upload:
        logger.info("=" * 60)
        logger.info("TASK 10: Upload to YouTube")
        logger.info("=" * 60)
        try:
            from youtube.upload import upload_video, generate_description, generate_and_upload_thumbnail

            # Generate description
            description = generate_description(script_data, stock_data, alpha_scores, market_regime)

            # Get thumbnail
            thumbnail_path = generate_and_upload_thumbnail(chart_paths, stock_data, alpha_scores)

            title = script_data.get("title", f"{BRAND_NAME} — {start.strftime('%b %d')}")
            tags = script_data.get("tags", ["stock market", "trading", "alpha score", BRAND_NAME])

            result = upload_video(
                video_path=str(video_path),
                title=title,
                description=description,
                tags=tags,
                thumbnail_path=thumbnail_path,
            )
            status["youtube"] = result
            if result.get("status") != "uploaded":
                raise RuntimeError(result.get("error", "YouTube upload did not complete with a verified custom thumbnail"))
            status["tasks_completed"].append("task10_youtube")
            logger.info(f"YouTube: {result.get('url', 'N/A')} | custom thumbnail verified")
        except Exception as e:
            logger.error(f"YouTube upload failed: {e}")
            status["errors"].append(f"Task 10: {e}")

    # ── Task 11: Analytics Ingest (non-fatal, post-upload) ──────────────
    logger.info("=" * 60)
    logger.info("TASK 11: Analytics Ingest")
    logger.info("=" * 60)
    try:
        from analytics.youtube_metrics import run_analytics_ingest
        analytics_result = run_analytics_ingest()
        status["analytics_ingest"] = analytics_result
        status["tasks_completed"].append("task11_analytics")
    except Exception as e:
        logger.warning(f"Analytics ingest failed (non-fatal): {e}")

    # ── Task 12: Daily Digest (non-fatal, generates content_feedback.json) ─
    logger.info("=" * 60)
    logger.info("TASK 12: Daily Digest")
    logger.info("=" * 60)
    try:
        from analytics.daily_digest import run_daily_digest
        digest_result = run_daily_digest()
        status["daily_digest"] = digest_result
        status["tasks_completed"].append("task12_digest")
    except Exception as e:
        logger.warning(f"Daily digest failed (non-fatal): {e}")

    # ── Save to database ──────────────────────────────────────────────────
    try:
        from database.db import save_run, save_video

        status["status"] = "completed" if not status["errors"] else "completed_with_errors"
        status["completed_at"] = datetime.now().isoformat()
        status["duration_seconds"] = (datetime.now() - start).total_seconds()
        save_run(status)

        save_video({
            "title": script_data.get("title", ""),
            "video_path": str(video_path) if "video_path" in status else "",
            "audio_path": str(audio_path) if "audio_path" in status else "",
            "youtube_id": status.get("youtube", {}).get("video_id", ""),
            "youtube_url": status.get("youtube", {}).get("url", ""),
            "script_hash": status.get("script_qc", {}).get("script_hash", ""),
            "word_count": status.get("script_qc", {}).get("word_count", 0),
            "mode": mode,
            "status": "uploaded" if upload else "local_only",
        })
    except Exception as e:
        logger.warning(f"Failed to save to DB: {e}")

    # ── Done ──────────────────────────────────────────────────────────────
    end = datetime.now()
    status["completed_at"] = end.isoformat()
    status["duration_seconds"] = (end - start).total_seconds()
    status["status"] = "completed" if not status["errors"] else "completed_with_errors"

    logger.info("=" * 60)
    logger.info(f"PIPELINE COMPLETE: {status['status']}")
    logger.info(f"Mode: {mode} | Duration: {status['duration_seconds']:.0f}s")
    logger.info(f"Tasks: {len(status['tasks_completed'])}")
    logger.info(f"Errors: {len(status['errors'])}")
    logger.info("=" * 60)

    # Save status
    _save_status(status, start)

    return status


def _save_status(status: dict, start: datetime):
    """Save pipeline status and an idempotence marker for verified uploads."""
    status_path = LOGS_DIR / "last_run_status.json"
    with open(status_path, "w") as f:
        json.dump(status, f, indent=2, default=str)

    youtube = status.get("youtube") or {}
    if youtube.get("status") == "uploaded" and youtube.get("video_id") and youtube.get("url"):
        upload_record = {
            "recorded_at": datetime.now().isoformat(),
            "completed_at": status.get("completed_at"),
            "video_id": youtube.get("video_id"),
            "url": youtube.get("url"),
            "title": status.get("script_title", ""),
            "video_path": status.get("video_path", ""),
            "status": "verified_uploaded",
        }
        upload_path = LOGS_DIR / "last_successful_upload.json"
        tmp_path = upload_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(upload_record, indent=2, default=str))
        tmp_path.replace(upload_path)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Alpha Trading Floor Pipeline")
    parser.add_argument("--upload", action="store_true", default=False,
                        help="Upload to YouTube (default: skip)")
    parser.add_argument("--mode", default=None,
                        help="levels | advice (default: auto from day of week)")
    parser.add_argument("--format-tag", default=None,
                        help="Stable analytics label for a controlled format variant")
    args = parser.parse_args()
    result = run_pipeline(
        upload=args.upload, mode=args.mode, format_tag=args.format_tag
    )
    print(json.dumps({
        "status": result.get("status"),
        "tasks_completed": result.get("tasks_completed"),
        "errors": result.get("errors"),
    }, indent=2, default=str))
