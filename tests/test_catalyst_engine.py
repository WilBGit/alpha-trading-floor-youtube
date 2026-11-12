import unittest

from scoring.catalyst_engine import classify_catalyst, source_reliability


class CatalystClassificationTests(unittest.TestCase):
    def test_federal_reserve_policy_is_macro_not_contract(self):
        category, confidence = classify_catalyst(
            "Federal Reserve holds interest rates steady after policy meeting"
        )
        self.assertEqual(category, "MACRO_NEWS")
        self.assertGreater(confidence, 0)

    def test_explicit_federal_contract_remains_government_contract(self):
        category, confidence = classify_catalyst(
            "Defense company wins new federal contract from the Pentagon"
        )
        self.assertEqual(category, "GOVERNMENT_CONTRACT")
        self.assertGreater(confidence, 0)

    def test_primary_and_reputable_sources_outrank_unknown_publishers(self):
        self.assertEqual(source_reliability({"source": "Federal Reserve", "source_type": "primary"}), 1.0)
        self.assertEqual(source_reliability({"source": "Reuters"}), 1.0)
        self.assertGreater(
            source_reliability({"source": "CNBC Markets"}),
            source_reliability({"source": "unknown.example"}),
        )


if __name__ == "__main__":
    unittest.main()
