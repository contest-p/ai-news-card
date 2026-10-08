import json
import os
from unittest.mock import patch
from engine.tools.connected_batch import load_gateway
import unittest
from engine.tools.run_batch import log_summary


class ActionsBatchTests(unittest.TestCase):
    def test_public_log_excludes_individual_job_results_but_keeps_failure_counts(self):
        source = {"run_id":"private-run", "jobs":{"processed":2,"by_status":{"sent":1,"unknown":1},
                  "results":[{"job_id":"PRIVATE_ID","issues":["PRIVATE_DETAIL"]}]},
                  "errors":["SEND_UNKNOWN"],"delivery_confirmed":False}
        summary = log_summary(source)
        self.assertNotIn("PRIVATE",json.dumps(summary))
        self.assertNotIn("private-run",json.dumps(summary))
        self.assertEqual(summary["jobs"]["by_status"]["unknown"],1)
        self.assertFalse(summary["delivery_confirmed"])
        self.assertEqual(len(source["jobs"]["results"]),1)

    def test_undersized_server_token_is_rejected_without_network(self):
        with patch.dict(os.environ, {"ENGINE_API_BASE_URL":"https://backend.example/api/v1/engine", "ENGINE_API_TOKEN":"short"}, clear=True):
            with self.assertRaisesRegex(ValueError,"ENGINE_API_TOKEN_REQUIRED"):
                load_gateway()
