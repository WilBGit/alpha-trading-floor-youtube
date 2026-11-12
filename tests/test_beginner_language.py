import unittest
from unittest.mock import patch

from ai.script_qc import find_unexplained_jargon, fix_script_issues, validate_script


class BeginnerLanguageTests(unittest.TestCase):
    def test_flags_unexplained_jargon(self):
        text = "NVDA is above VWAP and RSI is overbought. Watch for a breakout."
        findings = find_unexplained_jargon(text)
        self.assertIn("VWAP", findings)
        self.assertIn("RSI", findings)
        self.assertIn("overbought", findings)
        self.assertIn("breakout", findings)

    def test_flags_additional_beginner_blockers(self):
        text = (
            "The market regime is risk-on. EMA and relative volume show momentum. "
            "A catalyst may lift implied volatility after a pullback. "
            "Liquidity and risk/reward matter if price has a breakdown or looks oversold. "
            "The entry is 100, the stop is 95, and the target is 110."
        )
        findings = find_unexplained_jargon(text)
        for term in (
            "market regime", "risk-on", "EMA", "relative volume", "momentum",
            "catalyst", "implied volatility", "pullback", "liquidity",
            "risk/reward", "breakdown", "oversold", "entry", "stop", "target",
        ):
            self.assertIn(term, findings)

    def test_accepts_plain_english_translation(self):
        text = (
            "VWAP is the average price paid today. "
            "RSI is a simple buying-and-selling heat meter. "
            "Overbought means the price may have risen too far, too fast. "
            "A breakout means price moves above its ceiling."
        )
        self.assertEqual(find_unexplained_jargon(text), [])

    @patch("ai.script_qc._check_previous_similarity", return_value="")
    def test_validator_fails_closed_on_unexplained_terms(self, _):
        script = {
            "title": "Simple Market Update",
            "script": (
                "NVDA is above VWAP and could have a breakout. "
                "What's coming next? Subscribe. Not financial advice. Do your own research."
            ),
        }
        result = validate_script(script, {"NVDA": {"current_price": 100.0}})
        self.assertTrue(any("Unexplained finance terms" in issue for issue in result["issues"]))

    def test_auto_fix_translates_first_use_of_jargon(self):
        script = {
            "script": (
                "NVDA is above VWAP. RSI is overbought and may have a breakout. "
                "A catalyst could add interest. "
                "Subscribe. Not financial advice. Do your own research."
            )
        }
        fixed = fix_script_issues(script, ["Unexplained finance terms"])
        self.assertEqual(find_unexplained_jargon(fixed["script"]), [])
        for term in ("VWAP", "RSI", "overbought", "breakout", "catalyst"):
            self.assertIn(term.lower(), fixed["script"].lower())
        self.assertIn("average price paid today", fixed["script"].lower())
        self.assertIn("price moves above its ceiling", fixed["script"].lower())


if __name__ == "__main__":
    unittest.main()
