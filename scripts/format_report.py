#!/usr/bin/env python3
"""Refresh and report Alpha Trading Floor format-performance evidence."""
from analytics.youtube_metrics import run_analytics_ingest
from analytics.daily_digest import run_daily_digest


def main():
    ingest = run_analytics_ingest()
    if ingest.get("status") != "ok":
        print(f"Alpha Trading Floor format report failed: {ingest.get('error', ingest.get('status'))}")
        raise SystemExit(1)

    digest_result = run_daily_digest()
    if digest_result.get("status") != "ok":
        print(f"Alpha Trading Floor format report failed: {digest_result.get('error', digest_result.get('status'))}")
        raise SystemExit(1)

    digest = digest_result["digest"]
    insight = digest.get("format_insights", {})
    winner = insight.get("winner")
    print("Alpha Trading Floor — weekly viewer-format check")
    for item in insight.get("formats", []):
        retention = item.get("weighted_avg_view_pct")
        retention_text = "not available" if retention is None else f"{retention:.2f}%"
        print(
            f"• {item['format_tag']}: {item['videos']} videos, "
            f"{item['total_views']} views, {retention_text} retention"
        )
    if winner:
        print(
            f"Candidate format: {winner['format_tag']} "
            f"(+{winner['retention_lift_points']:.2f} retention points)."
        )
        print("Review CTR in YouTube Studio before making it the permanent format.")
    else:
        print("Result: No reliable format winner yet.")
        print(insight.get("note", "Continue collecting controlled comparison data."))


if __name__ == "__main__":
    main()
