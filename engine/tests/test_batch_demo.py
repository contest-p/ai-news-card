from email import policy
from email.parser import BytesParser
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine.demos.batch_demo import run_demo


class BatchDemoTests(unittest.TestCase):
    def test_demo_runs_whole_batch_without_network_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch("socket.create_connection", side_effect=AssertionError("network forbidden")), \
                patch("smtplib.SMTP", side_effect=AssertionError("smtp forbidden")):
            output = Path(directory)
            first = run_demo(output)
            second = run_demo(output, reuse_state=first["state"])
            self.assertEqual(first["summary"]["jobs"]["by_status"], {"sent": 1})
            self.assertEqual(second["summary"]["jobs"]["total"], 0)
            outbox = list((output / "outbox").glob("*.eml"))
            self.assertEqual(len(outbox), 1)
            parsed = BytesParser(policy=policy.default).parsebytes(outbox[0].read_bytes())
            plain = parsed.get_body(preferencelist=("plain",)).get_content()
            self.assertIn("feedback#t=TOKEN_FIXTURE_ONLY&rating=up", plain)
            self.assertNotIn("이미 발송된", plain)
            summary = json.loads((output / "runs" / (first["summary"]["run_id"] + ".json")).read_text("utf-8"))
            self.assertNotIn("tester@example.com", json.dumps(summary))


if __name__ == "__main__":
    unittest.main()
