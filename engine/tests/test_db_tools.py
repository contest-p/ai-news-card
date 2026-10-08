"""Read-only inspection, credential isolation and durable article behavior."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from engine.article_store import InMemoryArticleRepository
from engine.collection import collect_local_samples
from engine.firestore_connection import create_client
from engine.tools.collect_to_firestore import save_collection
from engine.tools.db_check import inspect_database


class DatabaseToolsTests(unittest.TestCase):
    def test_inspection_bounds_reads_and_never_outputs_identifiers(self):
        client = Mock()
        rows = [Mock(), Mock(), Mock()]
        for row in rows:
            row.to_dict.return_value = {"uid": "PRIVATE_UID", "email": "private@example.com",
                                        "status": "active", "plan": "basic"}
        client.collection.return_value.limit.return_value.stream.return_value = rows
        report = inspect_database(client, limit=2)
        self.assertEqual(report["collections"]["subscriptions"]["status_counts"], {"active": 2})
        self.assertEqual(report["collections"]["subscriptions"]["missing_setting_counts"]["categories"], 2)
        self.assertTrue(report["collections"]["users"]["truncated"])
        self.assertFalse(report["automatic_delivery_enabled"])
        self.assertNotIn("PRIVATE_UID", json.dumps(report))
        self.assertNotIn("private@example.com", json.dumps(report))
        client.collection.return_value.limit.assert_called_with(3)
        client.collection.return_value.limit.return_value.stream.assert_called_with(timeout=20, retry=None)

    def test_db_failure_does_not_become_empty_success(self):
        client = Mock()
        client.collection.return_value.limit.return_value.stream.side_effect = RuntimeError("offline")
        with self.assertRaises(RuntimeError):
            inspect_database(client)

    def test_invalid_limit_is_rejected_before_access(self):
        client = Mock()
        with self.assertRaises(ValueError):
            inspect_database(client, 0)
        client.collection.assert_not_called()

    def test_wrong_project_is_rejected_before_client_creation(self):
        with patch("engine.firestore_connection.Path.is_file", return_value=True):
            with patch("google.oauth2.service_account.Credentials.from_service_account_file",
                       return_value=SimpleNamespace(project_id="other-project")), \
                 patch("google.cloud.firestore.Client") as constructor:
                with self.assertRaisesRegex(ValueError, "CREDENTIAL_PROJECT_MISMATCH"):
                    create_client("team-project", "unused-test-credential.json")
                constructor.assert_not_called()

    def test_actions_inline_json_uses_matching_project_without_key_file(self):
        credentials = SimpleNamespace(project_id="team-project")
        with patch.dict(os.environ, {"FIREBASE_SERVICE_ACCOUNT_JSON":'{"type":"service_account"}'}, clear=True), \
             patch("google.oauth2.service_account.Credentials.from_service_account_info", return_value=credentials) as loader, \
             patch("google.cloud.firestore.Client") as client, patch("dotenv.load_dotenv"):
            create_client("team-project")
            loader.assert_called_once_with({"type":"service_account"})
            client.assert_called_once_with(project="team-project",credentials=credentials)
            with self.assertRaisesRegex(ValueError,"CREDENTIAL_PROJECT_MISMATCH"):
                create_client("wrong-project")
            self.assertEqual(client.call_count,1)

    def test_invalid_actions_credential_does_not_leak_secret_or_use_adc(self):
        for value in ('PRIVATE_INVALID_JSON', '{}'):
            with patch.dict(os.environ, {"FIREBASE_SERVICE_ACCOUNT_JSON":value}, clear=True), \
                 patch("google.oauth2.service_account.Credentials.from_service_account_info", side_effect=ValueError("PRIVATE_KEY")), \
                 patch("google.cloud.firestore.Client") as client, patch("dotenv.load_dotenv"):
                with self.assertRaisesRegex(ValueError,"^CREDENTIAL_JSON_INVALID$"):
                    create_client("team-project")
                client.assert_not_called()

    def test_collection_replay_is_idempotent_and_failure_propagates(self):
        result = collect_local_samples(Path(__file__).resolve().parents[1] / "samples" / "collection")
        store = InMemoryArticleRepository()
        now = datetime.now(timezone.utc)
        count = len(result.articles)
        self.assertGreater(count, 0)
        self.assertEqual(save_collection(store, result, now), {"inserted": count})
        self.assertEqual(save_collection(store, result, now), {"unchanged": count})
        self.assertEqual(len(store.list_current()), count)
        broken = Mock()
        broken.save.side_effect = RuntimeError("write failed")
        with self.assertRaises(RuntimeError):
            save_collection(broken, result, now)


if __name__ == "__main__":
    unittest.main()
