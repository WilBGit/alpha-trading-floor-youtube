"""Daily Digest — Rule-based content performance analysis.

Reads from content_performance table (or JSON fallback) and generates
a structured feedback JSON that the script generator consumes on the
NEXT run to adjust content strategy.

NO LLM CALLS — pure Python rule-based analysis.
This is the self-learning core: it identifies patterns in what performs
well and feeds them back into the content pipeline.

Output: data/content_feedback.json
"""
import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

from config.settings import DATA_DIR

logger = logging.getLogger("alpha_trading_floor")


def generate_digest(lookback_days: int = 30) -> dict:
    """Generate a daily content performance digest.

    Args:
        lookback_days: How many days of history to analyze.

    Returns:
        Structured dict with insights, recommendations, and data quality flags.
    """
    logger.info(f"Generating daily digest (lookback: {lookback_days} days)")

    # Pull all performance data
    records = _fetch_performance_records(lookback_days)

    if not records:
        logger.info("No performance data available yet — returning empty digest")
        return _empty_digest()

    # Analyze
    digest = {
        "generated_at": datetime.now().isoformat(),
        "period_days": lookback_days,
        "total_videos": len(records),
        "data_quality_warning": False,
        "analytics_note": (
            "YouTube API metrics include views, engagement, watch time, average "
            "view duration, retention percentage, and subscriber gains. Thumbnail "
            "impressions and CTR remain YouTube Studio-only."
        ),
    }

    # Top performer
    digest["top_performer"] = _analyze_top_performer(records)

    # Ticker resonance — which tickers in titles correlate with views
    digest["ticker_insights"] = _analyze_ticker_resonance(records)

    # Format performance — levels vs advice mode
    digest["format_insights"] = _analyze_format_performance(records)

    # Engagement quality — likes per view, comments per view
    digest["engagement_insights"] = _analyze_engagement(records)

    # Retention and watch-time quality
    digest["retention_insights"] = _analyze_retention(records)

    # View trend — are views growing or declining over time?
    digest["view_trend"] = _analyze_view_trend(records)

    # Time-based patterns — which day of week performs best?
    digest["timing_insights"] = _analyze_timing(records)

    # Concrete recommendation for next script
    digest["recommendation"] = _generate_recommendation(digest)

    # Save to file
    out_path = DATA_DIR / "content_feedback.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(digest, f, indent=2, default=str)

    logger.info(f"Daily digest saved to {out_path}")
    logger.info(f"  Top performer: {digest['top_performer'].get('title', 'N/A')}")
    logger.info(f"  Recommendation: {digest['recommendation']}")

    return digest


def _fetch_performance_records(days: int) -> list:
    """Fetch performance records from DB or JSON fallback."""
    records = []

    # Try MySQL
    try:
        from database.db import get_connection
        conn = get_connection()
        if conn:
            cursor = conn.cursor(dictionary=True)
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            cursor.execute("""
                SELECT cp.platform, cp.content_id, cp.title, cp.publish_date,
                       COALESCE(
                           NULLIF(cp.format_tag, ''),
                           CASE
                               WHEN v.mode = 'levels' THEN 'top10_ranking'
                               WHEN v.mode = 'advice' THEN 'advice_education'
                               ELSE 'untagged'
                           END
                       ) AS format_tag,
                       cp.views, cp.likes, cp.comments, cp.shares,
                       cp.subscribers_gained, cp.impressions, cp.ctr,
                       cp.estimated_minutes_watched, cp.avg_view_duration,
                       cp.avg_view_pct
                FROM content_performance cp
                INNER JOIN videos v ON v.youtube_id = cp.content_id
                INNER JOIN (
                    SELECT content_id, MAX(fetched_at) AS latest_fetch
                    FROM content_performance
                    WHERE platform = 'youtube'
                    GROUP BY content_id
                ) latest
                  ON cp.content_id = latest.content_id
                 AND cp.fetched_at = latest.latest_fetch
                WHERE cp.platform = 'youtube' AND cp.publish_date >= %s
                ORDER BY cp.publish_date DESC
            """, (cutoff,))
            records = cursor.fetchall()
            cursor.close()
            conn.close()
            if records:
                return records
    except Exception as e:
        logger.debug(f"MySQL fetch failed: {e}")

    # Fallback: scan JSON run files for basic data
    data_dir = DATA_DIR
    if data_dir.exists():
        cutoff = datetime.now() - timedelta(days=days)
        for f in sorted(data_dir.glob("run_*.json"), reverse=True):
            try:
                run = json.loads(f.read_text())
                run_date_str = run.get("started_at", "")[:10]
                if not run_date_str:
                    continue
                run_date = datetime.fromisoformat(run_date_str)
                if run_date < cutoff:
                    break
                yt = run.get("youtube", {})
                if yt.get("video_id"):
                    records.append({
                        "platform": "youtube",
                        "content_id": yt["video_id"],
                        "title": run.get("script_title", run.get("selected_title", "")),
                        "publish_date": run_date_str,
                        "format_tag": run.get("format_tag") or (
                            "top10_ranking" if run.get("mode") == "levels"
                            else "advice_education" if run.get("mode") == "advice"
                            else "untagged"
                        ),
                        "views": 0,  # Unknown without API pull
                        "likes": 0,
                        "comments": 0,
                    })
            except Exception:
                continue

    return records


def _empty_digest() -> dict:
    return {
        "generated_at": datetime.now().isoformat(),
        "total_videos": 0,
        "data_quality_warning": True,
        "analytics_note": "No performance data available yet. Start collecting metrics.",
        "top_performer": {},
        "ticker_insights": [],
        "format_insights": {},
        "engagement_insights": {},
        "view_trend": "unknown",
        "timing_insights": {},
        "recommendation": "No data yet — focus on producing varied content to build a baseline.",
    }


def _analyze_top_performer(records: list) -> dict:
    """Find the best-performing video by views."""
    if not records:
        return {}

    # Sort by views (descending)
    sorted_by_views = sorted(records, key=lambda r: r.get("views", 0), reverse=True)
    top = sorted_by_views[0] if sorted_by_views else {}

    if not top or top.get("views", 0) == 0:
        return {"note": "No view data available yet — metrics not pulled or zero views."}

    return {
        "title": top.get("title", ""),
        "video_id": top.get("content_id", ""),
        "views": top.get("views", 0),
        "likes": top.get("likes", 0),
        "comments": top.get("comments", 0),
        "engagement_rate": _engagement_rate(top),
    }


def _analyze_ticker_resonance(records: list) -> list:
    """Which tickers mentioned in titles correlate with higher views?"""
    if not records:
        return []

    # Common tickers to look for
    ticker_pattern = re.compile(r'\b(NVDA|TSLA|AAPL|META|AMD|PLTR|AMZN|GOOG|MSFT|SPY|SPCX|ORCL)\b')

    ticker_stats = defaultdict(lambda: {"total_views": 0, "count": 0, "titles": []})

    for r in records:
        title = r.get("title", "").upper()
        views = r.get("views", 0)
        tickers = ticker_pattern.findall(title)
        for t in tickers:
            ticker_stats[t]["total_views"] += views
            ticker_stats[t]["count"] += 1
            if len(ticker_stats[t]["titles"]) < 3:
                ticker_stats[t]["titles"].append(r.get("title", ""))

    # Sort by average views per mention
    insights = []
    for ticker, stats in ticker_stats.items():
        avg_views = stats["total_views"] / max(stats["count"], 1)
        insights.append({
            "ticker": ticker,
            "mentions": stats["count"],
            "avg_views": round(avg_views, 1),
            "sample_titles": stats["titles"],
        })

    insights.sort(key=lambda x: x["avg_views"], reverse=True)
    return insights[:5]


def _analyze_format_performance(records: list) -> dict:
    """Compare tagged formats without declaring a winner from tiny samples."""
    min_videos_per_format = 3
    min_views_per_format = 30
    min_retention_lift_points = 5.0
    grouped = defaultdict(list)
    for record in records:
        grouped[record.get("format_tag") or "untagged"].append(record)

    formats = []
    for format_tag, items in grouped.items():
        total_views = sum(int(item.get("views", 0) or 0) for item in items)
        measured = [item for item in items if item.get("avg_view_pct") is not None]
        measured_views = sum(int(item.get("views", 0) or 0) for item in measured)
        weighted_retention = None
        weighted_duration = None
        if measured:
            weights = [max(int(item.get("views", 0) or 0), 1) for item in measured]
            weight_total = sum(weights)
            weighted_retention = round(sum(
                float(item.get("avg_view_pct", 0) or 0) * weight
                for item, weight in zip(measured, weights)
            ) / weight_total, 2)
            weighted_duration = round(sum(
                float(item.get("avg_view_duration", 0) or 0) * weight
                for item, weight in zip(measured, weights)
            ) / weight_total, 1)
        subscribers = sum(int(item.get("subscribers_gained", 0) or 0) for item in items)
        formats.append({
            "format_tag": format_tag,
            "videos": len(items),
            "total_views": total_views,
            "measured_views": measured_views,
            "avg_views": round(total_views / max(len(items), 1), 1),
            "weighted_avg_view_pct": weighted_retention,
            "weighted_avg_view_duration_seconds": weighted_duration,
            "subscribers_gained": subscribers,
            "qualified": (
                len(items) >= min_videos_per_format
                and total_views >= min_views_per_format
                and weighted_retention is not None
            ),
        })

    formats.sort(
        key=lambda item: item.get("weighted_avg_view_pct")
        if item.get("weighted_avg_view_pct") is not None else -1,
        reverse=True,
    )
    qualified = [item for item in formats if item["qualified"]]
    result = {
        "status": "collecting_baseline",
        "winner": None,
        "formats": formats,
        "minimum_evidence": {
            "videos_per_format": min_videos_per_format,
            "views_per_format": min_views_per_format,
            "retention_lift_points": min_retention_lift_points,
            "qualified_formats_required": 2,
        },
    }
    if len(qualified) < 2:
        result["note"] = (
            "No format winner yet. Compare at least two formats with enough videos, "
            "views, and retention data."
        )
        return result

    leader, runner_up = qualified[:2]
    lift = round(
        leader["weighted_avg_view_pct"] - runner_up["weighted_avg_view_pct"], 2
    )
    if lift < min_retention_lift_points:
        result["status"] = "inconclusive"
        result["note"] = f"Top formats are only {lift:.2f} retention points apart."
        return result

    result["status"] = "candidate_detected"
    result["winner"] = {**leader, "retention_lift_points": lift}
    result["note"] = (
        "Candidate only—not an automatic production change. Confirm with CTR and "
        "additional uploads before adopting it."
    )
    return result


def _analyze_engagement(records: list) -> dict:
    """Analyze likes-per-view and comments-per-view ratios."""
    if not records:
        return {}

    total_views = sum(r.get("views", 0) for r in records)
    total_likes = sum(r.get("likes", 0) for r in records)
    total_comments = sum(r.get("comments", 0) for r in records)

    if total_views == 0:
        return {"note": "No view data available for engagement analysis."}

    return {
        "avg_likes_per_view": round(total_likes / total_views, 4),
        "avg_comments_per_view": round(total_comments / total_views, 4),
        "total_views": total_views,
        "total_likes": total_likes,
        "total_comments": total_comments,
    }


def _analyze_retention(records: list) -> dict:
    """Summarize watch time, retention, and subscriber conversion."""
    measured = [r for r in records if r.get("avg_view_pct") is not None]
    if not measured:
        return {"note": "No retention data available yet."}

    total_views = sum(r.get("views", 0) or 0 for r in measured)
    weighted_retention = sum(
        (r.get("avg_view_pct", 0) or 0) * (r.get("views", 0) or 0)
        for r in measured
    ) / max(total_views, 1)
    total_watch_minutes = sum(
        r.get("estimated_minutes_watched", 0) or 0 for r in measured
    )
    subscribers = sum(r.get("subscribers_gained", 0) or 0 for r in measured)
    best = max(measured, key=lambda r: r.get("avg_view_pct", 0) or 0)

    return {
        "weighted_avg_view_pct": round(weighted_retention, 2),
        "total_watch_minutes": round(total_watch_minutes, 1),
        "subscribers_gained": subscribers,
        "subscriber_conversion_per_100_views": round(
            subscribers / max(total_views, 1) * 100, 2
        ),
        "best_retention_video": {
            "title": best.get("title", ""),
            "video_id": best.get("content_id", ""),
            "avg_view_pct": round(best.get("avg_view_pct", 0) or 0, 2),
            "avg_view_duration_seconds": round(
                best.get("avg_view_duration", 0) or 0, 1
            ),
        },
    }


def _analyze_view_trend(records: list) -> str:
    """Determine if views are growing, flat, or declining."""
    if len(records) < 3:
        return "insufficient_data"

    # Sort by publish date
    sorted_records = sorted(records, key=lambda r: r.get("publish_date", ""), reverse=False)

    # Split into first half and second half
    mid = len(sorted_records) // 2
    first_half_avg = sum(r.get("views", 0) for r in sorted_records[:mid]) / max(mid, 1)
    second_half_avg = sum(r.get("views", 0) for r in sorted_records[mid:]) / max(len(sorted_records) - mid, 1)

    if first_half_avg == 0:
        return "unknown"
    change_pct = (second_half_avg - first_half_avg) / first_half_avg * 100

    if change_pct > 10:
        return f"growing (+{change_pct:.0f}%)"
    elif change_pct < -10:
        return f"declining ({change_pct:.0f}%)"
    else:
        return f"flat ({change_pct:+.0f}%)"


def _analyze_timing(records: list) -> dict:
    """Which day of week performs best?"""
    if not records:
        return {}

    day_stats = defaultdict(lambda: {"views": 0, "count": 0})

    for r in records:
        pub_date = r.get("publish_date")
        if not pub_date:
            continue
        try:
            dt = datetime.fromisoformat(str(pub_date))
            day_name = dt.strftime("%A")
            day_stats[day_name]["views"] += r.get("views", 0)
            day_stats[day_name]["count"] += 1
        except Exception:
            continue

    insights = {}
    for day, stats in day_stats.items():
        avg = stats["views"] / max(stats["count"], 1)
        insights[day] = {"avg_views": round(avg, 1), "count": stats["count"]}

    return insights


def _generate_recommendation(digest: dict) -> str:
    """Generate one concrete recommendation based on all insights."""
    if digest.get("total_videos", 0) == 0:
        return "No data yet — produce varied content to build a baseline."

    retention = digest.get("retention_insights", {})
    avg_view_pct = retention.get("weighted_avg_view_pct")
    if avg_view_pct is not None and avg_view_pct < 25:
        return (
            f"Average viewer retention is only {avg_view_pct:.1f}%. Shorten the next "
            "video, reveal the main conclusion in the first 20 seconds, and remove the "
            "generic Top-10 preamble before testing more ticker variations."
        )

    ticker_insights = digest.get("ticker_insights", [])
    if ticker_insights and ticker_insights[0].get("avg_views", 0) > 0:
        top_ticker = ticker_insights[0]["ticker"]
        return f"Videos mentioning {top_ticker} average the most views. Consider leading with {top_ticker} in the title."

    view_trend = digest.get("view_trend", "unknown")
    if "declining" in view_trend:
        return "Views are declining — try a different content format (educational, case study, or contrarian take)."

    if "growing" in view_trend:
        return "Views are growing — keep the current format and double down on what's working."

    return "Insufficient data for a strong recommendation. Continue producing varied content."


def _engagement_rate(record: dict) -> float:
    """Calculate engagement rate: (likes + comments) / views."""
    views = record.get("views", 0)
    if views == 0:
        return 0.0
    return round((record.get("likes", 0) + record.get("comments", 0)) / views, 4)


def run_daily_digest():
    """Main entry point for the pipeline."""
    logger.info("=" * 60)
    logger.info("DAILY DIGEST: Content Performance Analysis")
    logger.info("=" * 60)

    try:
        digest = generate_digest()
        return {"status": "ok", "digest": digest}
    except Exception as e:
        logger.warning(f"Daily digest failed (non-fatal): {e}")
        return {"status": "error", "error": str(e)}
