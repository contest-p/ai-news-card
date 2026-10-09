import json
import os
from unittest.mock import patch
from engine.tools.connected_batch import load_gateway
import unittest
from engine.tools.run_batch import log_summary, parse_delivery_at
from datetime import datetime, timezone, timedelta
from engine.tools.delivery_target import resolve_target


class ActionsBatchTests(unittest.TestCase):
    def test_hourly_target_is_fixed_from_workflow_creation_even_across_midnight(self):
        self.assertEqual(resolve_target("hourly", "2026-10-09T22:50:00Z"),
                         "2026-10-09T23:00:00+00:00")
        self.assertEqual(resolve_target("hourly", "2026-10-09T23:55:00Z"),
                         "2026-10-10T00:00:00+00:00")
        self.assertEqual(resolve_target("", ""), "")
        self.assertEqual(resolve_target("2026-10-10T08:00:00+09:00", ""),
                         "2026-10-10T08:00:00+09:00")
        with self.assertRaisesRegex(ValueError, "TIMEZONE_REQUIRED"):
            resolve_target("2026-10-10T08:00:00", "")
    def test_delivery_target_requires_timezone_and_bounds_wait(self):
        now = datetime(2026, 10, 9, 22, 50, tzinfo=timezone.utc)
        target = parse_delivery_at("2026-10-10T08:00:00+09:00", now=now)
        self.assertEqual((target - now).total_seconds(), 600)
        self.assertEqual(parse_delivery_at(target.isoformat(), now=target + timedelta(minutes=5)), target)
        self.assertIsNone(parse_delivery_at("", now=now))
        with self.assertRaisesRegex(ValueError, "TIMEZONE_REQUIRED"):
            parse_delivery_at("2026-10-10T08:00:00", now=now)
        with self.assertRaisesRegex(ValueError, "TOO_FAR"):
            parse_delivery_at("2026-10-11T08:00:00+09:00", now=now)
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
