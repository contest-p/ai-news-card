"""Wire real services; test sends are restricted to one configured subscription/address."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from dotenv import load_dotenv

from engine.firestore_connection import create_client
from engine.http_gateway import HttpEngineGateway
from engine.mail_assembly import WebMailLinks
from engine.settings import load_chat_settings
from engine.smtp_sender import send_message, load_smtp_account
from engine.tools.db_check import inspect_database
from engine.tools.smtp_test import load_smtp_settings

ROOT = Path(__file__).resolve().parents[2]


class TestGateway:
    def __init__(self, gateway, subscription_id, recipient):
        if not subscription_id:
            raise ValueError("ENGINE_TEST_SUBSCRIPTION_ID_REQUIRED")
        self.gateway, self.subscription_id, self.recipient = gateway, subscription_id, recipient

    def list_due_subscriptions(self, now):
        rows = self.gateway.list_due_subscriptions(now, subscription_id=self.subscription_id)
        if len(rows) > 1 or any(row["subscription_id"] != self.subscription_id or
                                row["recipient_email"] != self.recipient for row in rows):
            raise ValueError("TEST_SUBSCRIPTION_MISMATCH")
        return rows

    def list_expired_subscriptions(self, now):
        return []  # This command tests the daily briefing only.

    def check_delivery_eligibility(self, subscription_id, now):
        if subscription_id != self.subscription_id:
            raise ValueError("TEST_SUBSCRIPTION_MISMATCH")
        return self.gateway.check_delivery_eligibility(subscription_id, now)

    def issue_feedback_token(self, job_id):
        return self.gateway.issue_feedback_token(job_id, environment="test")


def test_sender(settings):
    def send(message, recipient):
        if recipient != settings.recipient:
            raise ValueError("TEST_RECIPIENT_MISMATCH")
        return send_message(settings, message, recipient=recipient)
    return send


def load_gateway():
    return HttpEngineGateway(os.environ.get("ENGINE_API_BASE_URL", ""), os.environ.get("ENGINE_API_TOKEN", ""))


def preflight(project, credentials=None, *, test=True):
    """Local configuration checks only; no external access or SMTP."""
    load_dotenv(ROOT / "engine" / ".env", override=False)
    missing = []
    for label, checker in (("backend", load_gateway), ("ai", load_chat_settings), ("smtp", load_smtp_settings if test else load_smtp_account)):
        try:
            checker()
        except (ValueError, TypeError):
            missing.append(label)
    if test and not os.environ.get("ENGINE_TEST_SUBSCRIPTION_ID"):
        missing.append("test_subscription_id")
    try:
        WebMailLinks(os.environ.get("ENGINE_WEB_BASE_URL", ""))
    except ValueError:
        missing.append("web_base_url")
    try:
        retention = int(os.environ.get("ENGINE_ARCHIVE_RETENTION_DAYS", "0"))
        if not 1 <= retention <= 30:
            raise ValueError()
    except ValueError:
        missing.append("archive_retention_days")
    # Authentication resolution is verified separately with db_check.
    return {"project": project, "missing_configuration": missing,
            "database_verified": False, "backend_verified": False, "mail_sent": False}


def run_test(client, *, text_only=False):
    smtp, chat = load_smtp_settings(), load_chat_settings()
    gateway = TestGateway(load_gateway(), os.environ["ENGINE_TEST_SUBSCRIPTION_ID"], smtp.recipient)
    if not gateway.list_due_subscriptions(datetime.now(timezone.utc)):
        raise ValueError("TEST_SUBSCRIPTION_NOT_DUE")
    from engine.runtime import run_connected
    return run_connected(client, gateway=gateway, smtp=smtp, sender=test_sender(smtp),
        text_only=text_only, test=True,
        jobs_wrapper=lambda store: TestJobStore(store, gateway.subscription_id, smtp.recipient))



class TestJobStore:
    """Delegate persistence while only opening jobs for this test identity/address."""
    def __init__(self, store, subscription_id, recipient):
        self.store, self.subscription_id, self.recipient = store, subscription_id, recipient

    def __getattr__(self, name):
        return getattr(self.store, name)

    def open_jobs(self):
        return [job for job in self.store.open_jobs()
                if job.subscription_id == self.subscription_id and
                   job.settings_snapshot.get("recipient_email") == self.recipient]


def main():
    parser = argparse.ArgumentParser(description="연결 설정 확인 또는 실제 테스트 구독 1개 배치 실행")
    parser.add_argument("--project", default="ai-news-card")
    parser.add_argument("--credentials")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="로컬 설정 확인만")
    group.add_argument("--check-db", action="store_true", help="기존 DB 읽기 확인만")
    group.add_argument("--send-test", action="store_true", help="DB·API·AI·SMTP 실제 테스트")
    parser.add_argument("--text-only", action="store_true")
    args = parser.parse_args()
    client = None
    try:
        report = preflight(args.project, args.credentials)
        if args.check:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1 if report["missing_configuration"] else 0
        if args.send_test and report["missing_configuration"]:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1
        client = create_client(args.project, args.credentials)
        if args.check_db:
            result = inspect_database(client)
        else:
            result = run_test(client, text_only=args.text_only)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.send_test:
            jobs = result["jobs"]
            return 0 if not result["errors"] and jobs["by_status"].get("sent", 0) == 1 else 1
        return 0
    except Exception as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__,
                          "error_code": "TEST_SUBSCRIPTION_NOT_DUE" if str(error) == "TEST_SUBSCRIPTION_NOT_DUE" else "CONNECTED_TEST_FAILED",
                          "action": "Check local configuration and latest test job states; do not reset them."}))
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    sys.exit(main())
