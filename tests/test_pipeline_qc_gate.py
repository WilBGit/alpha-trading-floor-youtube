import unittest

from app.main import qc_blocks_publish


class PipelineQcGateTests(unittest.TestCase):
    def test_qc_blocks_publish_when_result_is_missing(self):
        self.assertTrue(qc_blocks_publish(None))

    def test_qc_blocks_publish_when_validation_failed(self):
        self.assertTrue(qc_blocks_publish({"valid": False, "issues": ["too repetitive"]}))

    def test_qc_allows_publish_when_validation_passed(self):
        self.assertFalse(qc_blocks_publish({"valid": True, "issues": []}))


if __name__ == "__main__":
    unittest.main()
