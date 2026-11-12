import unittest

from analytics.daily_digest import _analyze_format_performance
from analytics.youtube_metrics import _format_tag_from_run


class FormatAnalyticsTests(unittest.TestCase):
    def test_run_mode_maps_to_stable_format_tag(self):
        self.assertEqual(_format_tag_from_run({"mode": "levels"}), "top10_ranking")
        self.assertEqual(_format_tag_from_run({"mode": "advice"}), "advice_education")
        self.assertEqual(
            _format_tag_from_run({"mode": "levels", "format_tag": "three_stock_deep_dive"}),
            "three_stock_deep_dive",
        )

    def test_format_digest_collects_baseline_without_declaring_early_winner(self):
        result = _analyze_format_performance([
            {"format_tag": "top10_ranking", "views": 8, "avg_view_pct": 10.0, "avg_view_duration": 90, "subscribers_gained": 0},
            {"format_tag": "top10_ranking", "views": 5, "avg_view_pct": 5.0, "avg_view_duration": 45, "subscribers_gained": 0},
            {"format_tag": "three_stock_deep_dive", "views": 7, "avg_view_pct": 20.0, "avg_view_duration": 110, "subscribers_gained": 1},
        ])
        self.assertEqual(result["status"], "collecting_baseline")
        self.assertIsNone(result["winner"])
        self.assertEqual({item["format_tag"] for item in result["formats"]}, {"top10_ranking", "three_stock_deep_dive"})

    def test_format_digest_declares_candidate_only_with_two_qualified_formats(self):
        records = []
        for _ in range(3):
            records.append({"format_tag": "top10_ranking", "views": 20, "avg_view_pct": 12.0, "avg_view_duration": 80, "subscribers_gained": 0})
            records.append({"format_tag": "three_stock_deep_dive", "views": 20, "avg_view_pct": 28.0, "avg_view_duration": 130, "subscribers_gained": 1})
        result = _analyze_format_performance(records)
        self.assertEqual(result["status"], "candidate_detected")
        self.assertEqual(result["winner"]["format_tag"], "three_stock_deep_dive")
        self.assertEqual(result["winner"]["weighted_avg_view_pct"], 28.0)


if __name__ == "__main__":
    unittest.main()
