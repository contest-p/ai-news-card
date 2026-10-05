from email.message import EmailMessage
from pathlib import Path
import smtplib
import tempfile
import unittest

from engine.generation import LocalGenerationStore
from engine.smtp_test import SmtpSettings, send_once


class FakeSmtp:
    def __init__(self, *, uncertain=False):
        self.calls = 0
        self.uncertain = uncertain
        self.envelope = None

    def send_message(self, message, **envelope):
        self.calls += 1
        self.envelope = envelope
        if self.uncertain:
            raise smtplib.SMTPServerDisconnected()
        return {}

    def close(self):
        pass


class SmtpTestTests(unittest.TestCase):
    def setUp(self):
        self.settings = SmtpSettings("smtp.example.com", 465, "ssl", "user", "secret", "from@example.com", "to@example.com")
        self.message = EmailMessage()
        self.message["From"] = self.settings.sender
        self.message["To"] = self.settings.recipient
        self.message.set_content("테스트")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = LocalGenerationStore(Path(self.temp.name))

    def test_exact_test_envelope_and_no_duplicate_submission(self):
        smtp = FakeSmtp()
        options = dict(store=self.store, key="a" * 64, connector=lambda settings: smtp)
        first = send_once(self.settings, self.message, **options)
        again = send_once(self.settings, self.message, **options)
        self.assertEqual(first["status"], "smtp_accepted")
        self.assertFalse(again["smtp_called_this_run"])
        self.assertEqual(smtp.calls, 1)
        self.assertEqual(smtp.envelope["to_addrs"], [self.settings.recipient])

    def test_uncertain_submission_is_not_retried(self):
        smtp = FakeSmtp(uncertain=True)
        options = dict(store=self.store, key="b" * 64, connector=lambda settings: smtp)
        first = send_once(self.settings, self.message, **options)
        again = send_once(self.settings, self.message, **options)
        self.assertEqual(first["status"], "unknown")
        self.assertFalse(again["smtp_called_this_run"])
        self.assertEqual(smtp.calls, 1)

    def test_auth_failure_never_submits_and_limits_attempts(self):
        def fail(settings):
            raise smtplib.SMTPAuthenticationError(535, b"private response")
        options = dict(store=self.store, key="c" * 64, connector=fail)
        for _ in range(3):
            result = send_once(self.settings, self.message, **options)
        self.assertEqual(result["status"], "failed_before_send")
        self.assertEqual(result["attempts"], 2)
        self.assertFalse(result["smtp_called_this_run"])
        self.assertNotIn("private", str(result))
        self.assertNotIn("secret", repr(self.settings))

    def test_other_recipient_or_bcc_cannot_be_sent(self):
        self.message["Bcc"] = "other@example.com"
        with self.assertRaises(ValueError):
            send_once(self.settings, self.message, store=self.store, key="d" * 64)


if __name__ == "__main__":
    unittest.main()
