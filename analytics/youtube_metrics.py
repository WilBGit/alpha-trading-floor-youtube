"""YouTube metrics ingestion for Alpha Trading Floor.

Collects:
- YouTube Data API: title, publish date, lifetime views/likes/comments
- YouTube Analytics API: period views, watch minutes, average view duration,
  average viewed percentage, subscribers gained/lost

Thumbnail impressions and impression click-through rate are not exposed by the
YouTube Analytics API used here; those remain YouTube Studio-only metrics.
Deterministic — no LLM calls.
"""
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

from config.settings import GOOGLE_TOKEN_PATH, DATA_DIR

logger = logging.getLogger("alpha_trading_floor")
STUDIO_ONLY_METRICS = ["thumbnail_impressions", "thumbnail_ctr"]
FORMAT_TAG_BY_MODE = {
    "levels": "top10_ranking",
    "advice": "advice_education",
}


def _format_tag_from_run(run: dict) -> str:
    """Return an explicit format tag, or a stable tag derived from run mode."""
    explicit = str(run.get("format_tag", "")).strip()
    if explicit:
        return explicit
    return FORMAT_TAG_BY_MODE.get(str(run.get("mode", "")).strip(), "untagged")


def _get_video_format_tags() -> dict:
    """Map uploaded video IDs to deterministic format tags from DB and run records."""
    tags = {}
    try:
        from database.db import get_connection
        conn = get_connection()
        if conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT youtube_id, mode FROM videos WHERE youtube_id IS NOT NULL "
                "AND youtube_id != ''"
            )
            for video_id, mode in cursor.fetchall():
                tags[video_id] = FORMAT_TAG_BY_MODE.get(mode, "untagged")
            cursor.close()
            conn.close()
    except Exception as e:
        logger.debug(f"DB format-tag lookup failed: {e}")

    if not DATA_DIR.exists():
        return tags
    for path in sorted(DATA_DIR.glob("run_*.json")):
        try:
            run = json.loads(path.read_text())
            video_id = run.get("youtube", {}).get("video_id")
            if video_id:
                tags[video_id] = _format_tag_from_run(run)
        except Exception:
            continue
    return tags


def _load_credentials():
    token_path = Path(GOOGLE_TOKEN_PATH)
    if not token_path.exists():
        raise FileNotFoundError(f"Google token not found: {token_path}")
    creds = Credentials.from_authorized_user_file(str(token_path))
    if creds.expired and creds.refresh_token:
        from google.auth.transport.requests import Request
        creds.refresh(Request())
        token_path.write_text(creds.to_json())
    return creds


def _get_video_ids_from_db(limit: int = 100) -> list:
    video_ids = []
    try:
        from database.db import get_connection
        conn = get_connection()
        if conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT youtube_id FROM videos WHERE youtube_id != '' "
                "ORDER BY run_date DESC LIMIT %s", (limit,)
            )
            video_ids.extend(row[0] for row in cursor.fetchall() if row[0])
            cursor.close()
            conn.close()
    except Exception as e:
        logger.debug(f"DB video ID lookup failed: {e}")

    if not video_ids and DATA_DIR.exists():
        for path in sorted(DATA_DIR.glob("run_*.json"), reverse=True)[:limit]:
            try:
                run = json.loads(path.read_text())
                video_id = run.get("youtube", {}).get("video_id")
                if video_id:
                    video_ids.append(video_id)
            except Exception:
                continue
    return list(dict.fromkeys(video_ids))[:limit]


def fetch_deep_analytics(lookback_days: int = 90) -> dict:
    """Return video-level Analytics API metrics keyed by video ID."""
    creds = _load_credentials()
    service = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=max(1, lookback_days - 1))
    metrics = (
        "views,likes,comments,estimatedMinutesWatched,averageViewDuration,"
        "averageViewPercentage,subscribersGained,subscribersLost"
    )
    response = service.reports().query(
        ids="channel==MINE",
        startDate=start_date.isoformat(),
        endDate=end_date.isoformat(),
        metrics=metrics,
        dimensions="video",
        sort="-views",
        maxResults=200,
    ).execute()
    columns = [column["name"] for column in response.get("columnHeaders", [])]
    results = {}
    for row in response.get("rows", []):
        item = dict(zip(columns, row))
        video_id = item.pop("video")
        results[video_id] = {
            "period_views": int(item.get("views", 0)),
            "period_likes": int(item.get("likes", 0)),
            "period_comments": int(item.get("comments", 0)),
            "estimated_minutes_watched": float(item.get("estimatedMinutesWatched", 0)),
            "avg_view_duration": float(item.get("averageViewDuration", 0)),
            "avg_view_pct": float(item.get("averageViewPercentage", 0)),
            "subscribers_gained": int(item.get("subscribersGained", 0)),
            "subscribers_lost": int(item.get("subscribersLost", 0)),
        }
    logger.info(
        f"Fetched deep analytics for {len(results)} videos "
        f"({start_date} through {end_date})"
    )
    return results


def fetch_video_metrics(video_ids: list, max_ids_per_call: int = 50) -> list:
    """Fetch lifetime public statistics and metadata for video IDs."""
    if not video_ids:
        return []
    youtube = build("youtube", "v3", credentials=_load_credentials(), cache_discovery=False)
    results = []
    for start in range(0, len(video_ids), max_ids_per_call):
        batch = video_ids[start:start + max_ids_per_call]
        response = youtube.videos().list(
            part="statistics,snippet", id=",".join(batch)
        ).execute()
        for item in response.get("items", []):
            stats = item.get("statistics", {})
            snippet = item.get("snippet", {})
            results.append({
                "video_id": item["id"],
                "title": snippet.get("title", ""),
                "published_at": snippet.get("publishedAt", ""),
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
                "comments": int(stats.get("commentCount", 0)),
            })
    logger.info(f"Fetched public metrics for {len(results)} videos")
    return results


def _merge_metrics(public_metrics: list, deep_metrics: dict) -> list:
    merged = []
    for item in public_metrics:
        deep = deep_metrics.get(item["video_id"], {})
        item.update(deep)
        item["analytics_available"] = bool(deep)
        merged.append(item)
    return merged


def save_metrics_to_db(metrics: list):
    if not metrics:
        return
    try:
        from database.db import get_connection
        conn = get_connection()
        if not conn:
            _save_metrics_to_json(metrics)
            return
        cursor = conn.cursor()
        fetched_at = datetime.now()
        for item in metrics:
            publish_date = None
            if item.get("published_at"):
                try:
                    publish_date = datetime.fromisoformat(
                        item["published_at"].replace("Z", "+00:00")
                    ).date()
                except Exception:
                    pass
            cursor.execute("""
                INSERT INTO content_performance
                    (platform, content_id, content_url, title, publish_date,
                     format_tag, views, likes, comments, subscribers_gained,
                     estimated_minutes_watched, avg_view_duration, avg_view_pct,
                     fetched_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                "youtube", item["video_id"],
                f"https://www.youtube.com/watch?v={item['video_id']}",
                item.get("title", ""), publish_date,
                item.get("format_tag", "untagged"),
                item.get("views", 0), item.get("likes", 0),
                item.get("comments", 0), item.get("subscribers_gained"),
                item.get("estimated_minutes_watched"),
                item.get("avg_view_duration"), item.get("avg_view_pct"),
                fetched_at,
            ))
        cursor.close()
        conn.close()
        logger.info(f"Saved {len(metrics)} analytics snapshots to the database")
    except Exception as e:
        logger.warning(f"Failed to save metrics to DB: {e}")
        _save_metrics_to_json(metrics)


def _save_metrics_to_json(metrics: list):
    out_path = DATA_DIR / "youtube_metrics_latest.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "fetched_at": datetime.now().isoformat(),
        "analytics_available": True,
        "studio_only_metrics": STUDIO_ONLY_METRICS,
        "metrics": metrics,
    }, indent=2))
    logger.info(f"Metrics saved to {out_path}")


def run_analytics_ingest():
    """Fetch and persist all available YouTube performance metrics."""
    logger.info("=" * 60)
    logger.info("ANALYTICS INGEST: YouTube Metrics")
    logger.info("=" * 60)
    try:
        deep = fetch_deep_analytics()
        video_ids = _get_video_ids_from_db()
        if not video_ids:
            return {"status": "no_videos", "analytics_available": True}
        public = fetch_video_metrics(video_ids)
        metrics = _merge_metrics(public, deep)
        format_tags = _get_video_format_tags()
        for item in metrics:
            item["format_tag"] = format_tags.get(item["video_id"], "untagged")
        save_metrics_to_db(metrics)
        _save_metrics_to_json(metrics)
        return {
            "status": "ok",
            "videos_fetched": len(metrics),
            "videos_with_deep_analytics": sum(
                1 for item in metrics if item.get("analytics_available")
            ),
            "analytics_available": True,
            "studio_only_metrics": STUDIO_ONLY_METRICS,
        }
    except Exception as e:
        logger.warning(f"Analytics ingest failed (non-fatal): {e}")
        return {"status": "error", "error": str(e), "analytics_available": False}
