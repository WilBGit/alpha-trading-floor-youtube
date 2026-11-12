import unittest

from analysis.levels import format_key_levels, select_key_levels


class KeyLevelTests(unittest.TestCase):
    def test_selects_nearest_support_resistance_and_decision_level(self):
        levels = select_key_levels(
            "AMD",
            {
                "current_price": 100.0,
                "supports": [92.0, 98.0],
                "resistances": [103.0, 110.0],
            },
        )

        self.assertEqual(levels["support"], 98.0)
        self.assertEqual(levels["resistance"], 103.0)
        self.assertEqual(levels["level_to_watch"], 98.0)
        self.assertEqual(levels["level_to_watch_reason"], "support hold")

    def test_format_contains_explicit_labels(self):
        text = format_key_levels(
            "AMD",
            {
                "current_price": 100.0,
                "supports": [98.0],
                "resistances": [103.0],
            },
        )

        self.assertIn("AMD", text)
        self.assertIn("Support: $98.00", text)
        self.assertIn("Resistance: $103.00", text)
        self.assertIn("Level to watch: $98.00", text)

    def test_missing_levels_are_not_invented(self):
        levels = select_key_levels("AMD", {"current_price": 100.0})
        text = format_key_levels("AMD", {"current_price": 100.0})

        self.assertIsNone(levels["support"])
        self.assertIsNone(levels["resistance"])
        self.assertIsNone(levels["level_to_watch"])
        self.assertIn("Support: N/A", text)
        self.assertIn("Resistance: N/A", text)
        self.assertIn("Level to watch: N/A", text)


if __name__ == "__main__":
    unittest.main()
