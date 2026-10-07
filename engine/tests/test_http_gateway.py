from copy import deepcopy
from datetime import date
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError

from engine.http_gateway import GatewayUnavailable, HttpEngineGateway, NoRedirect
from engine.tools.connected_batch import TestGateway, TestJobStore, test_sender
from engine.selection import parse_timestamp

FIXTURE = json.loads((Path(__file__).parents[1] / "samples" / "selection.json").read_text("utf-8"))


class Response(io.BytesIO):
    status = 200


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.opener = Mock()
        self.gateway = HttpEngineGateway("https://backend.example/api/v1/engine", "TEST_SECRET",
                                         opener=self.opener)
        self.snapshot = deepcopy(FIXTURE["subscription_snapshot"])
        self.now = parse_timestamp(FIXTURE["now"])

    def response(self, data):
        self.opener.open.return_value = Response(json.dumps(data).encode())

    def test_due_contract_auth_and_timeout(self):
        self.response({"subscriptions": [self.snapshot]})
        self.assertEqual(self.gateway.list_due_subscriptions(self.now), [self.snapshot])
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer TEST_SECRET")
        self.assertIn("/subscriptions/due?now=", request.full_url)
        self.assertEqual(self.opener.open.call_args.kwargs["timeout"], 20)

    def test_existing_basic_subscription_is_rejected(self):
        self.response({"subscriptions": [{"uid": "fixture", "status": "active", "plan": "basic"}]})
        with self.assertRaises(ValueError):
            self.gateway.list_due_subscriptions(self.now)

    def test_snapshot_identity_cannot_be_swapped(self):
        self.response({"subscription": self.snapshot})
        with self.assertRaises(GatewayUnavailable):
            self.gateway.get_subscription_snapshot("other", date.fromisoformat(self.snapshot["scheduled_date_kst"]))

    def test_backend_failure_never_becomes_empty_list(self):
        self.opener.open.side_effect = URLError("PRIVATE_DETAIL")
        with self.assertRaises(GatewayUnavailable) as raised:
            self.gateway.list_due_subscriptions(self.now)
        self.assertNotIn("PRIVATE_DETAIL", str(raised.exception))

    def test_feedback_issuance_has_idempotency_key(self):
        self.response({"token": "opaque-fixture"})
        self.assertEqual(self.gateway.issue_feedback_token("a" * 64), "opaque-fixture")
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Idempotency-key"), "a" * 64)
        self.assertEqual(request.get_method(), "POST")

    def test_redirect_and_insecure_remote_url_are_rejected(self):
        with self.assertRaises(ValueError):
            HttpEngineGateway("http://remote.example", "token")
        with self.assertRaises(GatewayUnavailable):
            NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example")

    def test_test_gateway_does_not_redirect_real_subscriber_mail(self):
        backend = Mock()
        backend.list_due_subscriptions.return_value = [self.snapshot]
        gateway = TestGateway(backend, self.snapshot["subscription_id"], "different@example.com")
        with self.assertRaises(ValueError):
            gateway.list_due_subscriptions(self.now)
        backend.list_due_subscriptions.assert_called_once_with(self.now, subscription_id=self.snapshot["subscription_id"])

    def test_test_gateway_rejects_a_different_subscription_even_with_same_email(self):
        backend = Mock()
        backend.list_due_subscriptions.return_value = [{**self.snapshot, "subscription_id": "other"}]
        gateway = TestGateway(backend, self.snapshot["subscription_id"], self.snapshot["recipient_email"])
        with self.assertRaises(ValueError):
            gateway.list_due_subscriptions(self.now)

    def test_scoped_http_query_and_pending_jobs_are_limited_to_one_test(self):
        from types import SimpleNamespace
        self.response({"subscriptions": [self.snapshot]})
        self.gateway.list_due_subscriptions(self.now, subscription_id="chosen-test")
        self.assertIn("subscription_id=chosen-test", self.opener.open.call_args.args[0].full_url)
        chosen = SimpleNamespace(subscription_id="chosen-test", settings_snapshot={"recipient_email": "chosen@example.com"})
        other = SimpleNamespace(subscription_id="other", settings_snapshot={"recipient_email": "chosen@example.com"})
        wrong_recipient = SimpleNamespace(subscription_id="chosen-test", settings_snapshot={"recipient_email": "other@example.com"})
        store = Mock()
        store.open_jobs.return_value = [chosen, other, wrong_recipient]
        scoped = TestJobStore(store, "chosen-test", "chosen@example.com")
        self.assertEqual(scoped.open_jobs(), [chosen])

    def test_test_sender_enforces_envelope_and_keyword_argument(self):
        settings = Mock(recipient="test@example.com")
        sender = test_sender(settings)
        with patch("engine.tools.connected_batch.send_message") as smtp:
            with self.assertRaises(ValueError):
                sender(object(), "other@example.com")
            smtp.assert_not_called()
            message = object()
            sender(message, settings.recipient)
            smtp.assert_called_once_with(settings, message, recipient=settings.recipient)


if __name__ == "__main__":
    unittest.main()
