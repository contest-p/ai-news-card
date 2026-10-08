import contextlib
import io
import os
import unittest
from unittest.mock import Mock, patch
from engine.tools import privacy_cleanup


class PrivacyCleanupTests(unittest.TestCase):
    def invoke(self, mode, gateway):
        output = io.StringIO()
        with patch.object(privacy_cleanup, "load_dotenv"), patch.object(privacy_cleanup, "load_gateway", return_value=gateway), contextlib.redirect_stdout(output):
            code = privacy_cleanup.main([mode])
        return code, output.getvalue()

    def test_check_never_calls_backend(self):
        gateway = Mock()
        code, output = self.invoke("--check", gateway)
        self.assertEqual(code, 0)
        gateway.privacy_cleanup.assert_not_called()
        self.assertIn('"backend_verified": false', output)

    def test_run_needs_no_ai_or_smtp_configuration(self):
        gateway = Mock()
        gateway.privacy_cleanup.return_value = {"status": "completed"}
        with patch.dict(os.environ, {"ENGINE_API_BASE_URL":"https://backend.example/api/v1/engine", "ENGINE_API_TOKEN":"x"*32}, clear=True):
            privacy_cleanup.load_gateway()
            code, output = self.invoke("--run", gateway)
        self.assertEqual(code, 0)
        self.assertIn('"completed"', output)
        now = gateway.privacy_cleanup.call_args.args[0]
        self.assertIsNotNone(now.tzinfo)

    def test_backend_failure_exits_nonzero_without_sensitive_details(self):
        gateway = Mock()
        gateway.privacy_cleanup.side_effect = RuntimeError("PRIVATE_TOKEN_USER_DATA")
        code, output = self.invoke("--run", gateway)
        self.assertEqual(code, 1)
        self.assertNotIn("PRIVATE_TOKEN_USER_DATA", output)
        self.assertIn("PRIVACY_CLEANUP_FAILED", output)

    def test_incomplete_backend_cleanup_is_not_reported_as_success(self):
        gateway = Mock()
        gateway.privacy_cleanup.return_value = {"status":"pending"}
        self.assertEqual(self.invoke("--run", gateway)[0],1)
