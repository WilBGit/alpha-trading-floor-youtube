import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ai.script_generator import _build_stock_summary
from ai.script_qc import fix_script_issues, validate_script


class ScriptLevelRequirementTests(unittest.TestCase):
    def setUp(self):
        self.stock_data = {
            "AMD": {
                "current_price": 100.0,
                "change_pct": 1.2,
                "supports": [98.0],
                "resistances": [103.0],
            }
        }
        self.alpha_scores = {"AMD": {"alpha_score": 71.8, "classification": "CONSTRUCTIVE"}}

    def test_stock_summary_labels_all_required_levels(self):
        summary = _build_stock_summary("AMD", self.stock_data["AMD"], self.alpha_scores["AMD"])
        self.assertIn("Support: $98.00", summary)
        self.assertIn("Resistance: $103.00", summary)
        self.assertIn("Level to watch: $98.00", summary)

    def test_qc_requires_explicit_level_language_for_top_ticker(self):
        with TemporaryDirectory() as tmp:
            with patch("ai.script_qc.DATA_DIR", Path(tmp)):
                result = validate_script(
                    {
                        "title": "AMD analysis",
                        "script": (
                            "AMD is trading at $100.00. Support is a price floor and resistance is a price ceiling. "
                            "What's coming next is important. Subscribe for the next update. "
                            "Not financial advice. Do your own research."
                        ),
                    },
                    self.stock_data,
                    self.alpha_scores,
                )
        self.assertTrue(any("level" in issue.lower() for issue in result["issues"]))

    def test_autofix_adds_exact_level_block(self):
        fixed = fix_script_issues(
            {
                "script": "AMD is the focus. Not financial advice. Do your own research.",
            },
            [],
            stock_data=self.stock_data,
            alpha_scores=self.alpha_scores,
        )
        text = fixed["script"]
        self.assertIn("Support: $98.00", text)
        self.assertIn("Resistance: $103.00", text)
        self.assertIn("Level to watch: $98.00", text)


if __name__ == "__main__":
    unittest.main()
