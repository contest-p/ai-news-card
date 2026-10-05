import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine.settings import load_chat_settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env_path = Path(self.temp.name) / ".env"
        self.env_path.write_text(
            "OPENAI_API_KEY=TEST_PLACEHOLDER_ONLY\n"
            "OPENAI_BASE_URL=https://example.com/v1\n"
            "OPENAI_MODEL=gpt-5.5\n", encoding="utf-8")

    def test_load_without_network_or_key_in_repr(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = load_chat_settings(self.env_path)
        self.assertEqual(settings.model, "gpt-5.5")
        self.assertNotIn("TEST_PLACEHOLDER_ONLY", repr(settings))

    def test_deployment_secrets_override_file(self):
        with patch.dict(os.environ, {"OPENAI_MODEL": "test-model"}, clear=True):
            settings = load_chat_settings(self.env_path)
        self.assertEqual(settings.model, "test-model")

    def test_empty_key_and_insecure_urls_rejected(self):
        for overrides in ({"OPENAI_API_KEY": ""},
                          {"OPENAI_BASE_URL": "http://example.com/v1"},
                          {"OPENAI_BASE_URL": "https://name:secret@example.com/v1"}):
            with patch.dict(os.environ, overrides, clear=True), self.assertRaises(ValueError):
                load_chat_settings(self.env_path)


if __name__ == "__main__":
    unittest.main()
