import unittest

from ai.title_generator import _coherent_title_candidates, _primary_ticker


class TitleCoherenceTests(unittest.TestCase):
    def setUp(self):
        self.stock_data = {"TSLA": {}, "META": {}, "AMZN": {}}

    def test_primary_ticker_uses_title_order(self):
        self.assertEqual(
            _primary_ticker("TSLA vs META: Which Setup Matters?", list(self.stock_data)),
            "TSLA",
        )

    def test_mismatched_title_is_rejected(self):
        titles = [
            "AMZN: The Stock Nobody's Talking About",
            "TSLA Leads Today's Alpha Score Ranking",
        ]
        self.assertEqual(
            _coherent_title_candidates(titles, self.stock_data, "TSLA"),
            ["TSLA Leads Today's Alpha Score Ranking"],
        )

    def test_generic_title_does_not_override_thumbnail_subject(self):
        titles = [
            "Top 10 Stocks Ranked by Alpha Score",
            "META Scores 64 Today",
        ]
        self.assertEqual(
            _coherent_title_candidates(titles, self.stock_data, "TSLA"),
            [],
        )


if __name__ == "__main__":
    unittest.main()
