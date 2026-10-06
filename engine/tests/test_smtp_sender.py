from email.message import EmailMessage
import smtplib
import unittest

from engine.smtp_sender import SmtpAccount, send_message

ACCOUNT = SmtpAccount("smtp.example.invalid", 465, "ssl", "user", "secret-password", "sender@example.com")


def message(to="reader@example.com"):
    mail = EmailMessage()
    mail["From"], mail["To"], mail["Subject"] = "sender@example.com", to, "브리핑"
    mail.set_content("본문")
    return mail


class FakeSmtp:
    def __init__(self, error=None, refused=None):
        self.error, self.refused, self.sent, self.closed = error, refused or {}, [], False

    def send_message(self, mail, from_addr, to_addrs):
        if self.error:
            raise self.error
        self.sent.append((mail, from_addr, to_addrs))
        return self.refused

    def close(self):
        self.closed = True


class SmtpSenderTests(unittest.TestCase):
    def send(self, smtp=None, connector=None, mail=None):
        smtp = smtp or FakeSmtp()
        return send_message(ACCOUNT, mail or message(), recipient="reader@example.com",
                            connector=connector or (lambda account: smtp)), smtp

    def test_accepted_uses_only_the_job_recipient_and_adds_date(self):
        outcome, smtp = self.send()
        self.assertEqual((outcome.status, outcome.retryable), ("accepted", False))
        mail, sender, recipients = smtp.sent[0]
        self.assertEqual((sender, recipients), ("sender@example.com", ["reader@example.com"]))
        self.assertIn("Date", mail)
        self.assertTrue(smtp.closed)

    def test_envelope_mismatch_is_rejected_before_connecting(self):
        connected = []
        for mail in (message(to="other@example.com"), message()):
            if mail["To"] == "reader@example.com":
                mail["Bcc"] = "hidden@example.com"
            with self.subTest(to=mail["To"]), self.assertRaises(ValueError):
                send_message(ACCOUNT, mail, recipient="reader@example.com",
                             connector=lambda account: connected.append(1))
        self.assertEqual(connected, [])

    def test_auth_failure_is_permanent_account_failure(self):
        def fail(account):
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")
        outcome, _ = self.send(connector=fail)
        self.assertEqual((outcome.status, outcome.retryable, outcome.error_code),
                         ("failed", False, "SMTP_AUTH_FAILED"))

    def test_connection_failure_before_submit_is_retryable(self):
        def fail(account):
            raise OSError("connection refused")
        outcome, _ = self.send(connector=fail)
        self.assertEqual((outcome.status, outcome.retryable), ("failed", True))

    def test_explicit_4xx_reply_is_temporary_and_5xx_is_permanent(self):
        cases = [
            (smtplib.SMTPRecipientsRefused({"reader@example.com": (450, b"mailbox busy")}), True),
            (smtplib.SMTPRecipientsRefused({"reader@example.com": (550, b"no such user")}), False),
            (smtplib.SMTPDataError(421, b"try later"), True),
            (smtplib.SMTPDataError(554, b"rejected"), False),
            (smtplib.SMTPSenderRefused(451, b"later", "sender@example.com"), True),
        ]
        for error, retryable in cases:
            with self.subTest(error=type(error).__name__, retryable=retryable):
                outcome, smtp = self.send(FakeSmtp(error))
                self.assertEqual((outcome.status, outcome.retryable), ("failed", retryable))
                self.assertTrue(smtp.closed)

    def test_disconnect_or_timeout_after_submit_is_unknown(self):
        for error in (smtplib.SMTPServerDisconnected("lost"), TimeoutError("timed out"), OSError("reset")):
            with self.subTest(error=type(error).__name__):
                outcome, _ = self.send(FakeSmtp(error))
                self.assertEqual((outcome.status, outcome.retryable, outcome.error_code),
                                 ("unknown", False, "SMTP_RESULT_UNCERTAIN"))

    def test_outcome_and_account_do_not_expose_secrets(self):
        self.assertNotIn("secret-password", repr(ACCOUNT))
        outcome, _ = self.send(FakeSmtp(smtplib.SMTPDataError(554, b"secret-password reflected")))
        self.assertNotIn("secret-password", repr(outcome))


if __name__ == "__main__":
    unittest.main()
