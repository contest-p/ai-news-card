import unittest

from engine.tools.run_batch import log_summary


class SafeValidationLogTests(unittest.TestCase):
    def test_validation_codes_are_counted_without_raw_text_or_identifiers(self):
        result = {"run_id": "private-run", "jobs": {"total": 2, "results": [
            {"job_id": "private-job", "status": "failed", "error_code": "CARD_VALIDATION_FAILED",
             "issues": ["CARD1:QUOTE_NOT_IN_BODY", "reader@example.com", "PRIVATE_TEXT"]},
            {"status": "failed", "error_code": "TRANSLATION_VALIDATION_FAILED",
             "issues": ["TRANSLATION:TRANSLATION_NUMBERS_CHANGED"]}]}}
        summary = log_summary(result)
        self.assertEqual(summary["jobs"]["validation_issue_counts"], {
            "CARD1:QUOTE_NOT_IN_BODY": 1, "TRANSLATION:TRANSLATION_NUMBERS_CHANGED": 1})
        self.assertNotIn("private", str(summary))
        self.assertNotIn("reader@", str(summary))
        self.assertNotIn("PRIVATE_TEXT", str(summary))


if __name__ == "__main__":
    unittest.main()
