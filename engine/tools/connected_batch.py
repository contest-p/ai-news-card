"""Wire real services; test sends are restricted to one configured subscription/address."""

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys

from dotenv import load_dotenv

from engine.batch import run_batch, write_summary
from engine.card_builder import GenerationCardBuilder
from engine.card_images import render_with_fallback
from engine.card_render import resolve_font_path
from engine.chat_client import CodysseyChatClient
from engine.firestore_article_store import FirestoreArticleRepository
from engine.firestore_connection import create_client
from engine.firestore_runtime_store import FirestoreGenerationStore, FirestoreJobStore, FirestoreMailArchive
from engine.http_gateway import HttpEngineGateway
from engine.live_collection import collect_live_sources
from engine.mail_assembly import WebMailLinks
from engine.pipeline import PipelineDeps
from engine.settings import load_chat_settings
from engine.smtp_sender import send_message
from engine.tools.db_check import inspect_database
from engine.tools.smtp_test import load_smtp_settings

ROOT = Path(__file__).resolve().parents[2]


class TestGateway:
    def __init__(self, gateway, subscription_id, recipient):
        if not subscription_id:
            raise ValueError("ENGINE_TEST_SUBSCRIPTION_ID_REQUIRED")
        self.gateway, self.subscription_id, self.recipient = gateway, subscription_id, recipient

    def list_due_subscriptions(self, now):
        rows = [row for row in self.gateway.list_due_subscriptions(now)
                if row["subscription_id"] == self.subscription_id]
        if len(rows) > 1 or any(row["recipient_email"] != self.recipient for row in rows):
            raise ValueError("TEST_SUBSCRIPTION_MISMATCH")
        return rows

    def list_expired_subscriptions(self, now):
        return []  # This command tests the daily briefing only.

    def check_delivery_eligibility(self, subscription_id, now):
        if subscription_id != self.subscription_id:
            raise ValueError("TEST_SUBSCRIPTION_MISMATCH")
        return self.gateway.check_delivery_eligibility(subscription_id, now)

    def issue_feedback_token(self, job_id):
        return self.gateway.issue_feedback_token(job_id)


def test_sender(settings):
    def send(message, recipient):
        if recipient != settings.recipient:
            raise ValueError("TEST_RECIPIENT_MISMATCH")
        return send_message(settings, message, recipient=recipient)
    return send


def load_gateway():
    return HttpEngineGateway(os.environ.get("ENGINE_API_BASE_URL", ""), os.environ.get("ENGINE_API_TOKEN", ""))


def preflight(project, credentials=None):
    """Local configuration checks only; no external access or SMTP."""
    load_dotenv(ROOT / "engine" / ".env", override=False)
    missing = []
    for label, checker in (("backend", load_gateway), ("ai", load_chat_settings), ("smtp", load_smtp_settings)):
        try:
            checker()
        except (ValueError, TypeError):
            missing.append(label)
    if not os.environ.get("ENGINE_TEST_SUBSCRIPTION_ID"):
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
    repository = FirestoreArticleRepository(client)
    jobs = FirestoreJobStore(client, collection_name="engine_test_delivery_jobs")
    archive = FirestoreMailArchive(client,
        retention=timedelta(days=int(os.environ["ENGINE_ARCHIVE_RETENTION_DAYS"])),
        collection_name="engine_test_mail_archives")
    generation = FirestoreGenerationStore(client, collection_name="engine_test_generation_jobs")
    source_names = {}
    builder = GenerationCardBuilder(repository=repository, observed_at=datetime.now(timezone.utc),
        publisher_for=lambda key: source_names[key], client=CodysseyChatClient(chat),
        model=chat.model, base_url=chat.base_url, store=generation)

    def collect(*, deadline):
        result = collect_live_sources(max_entries=3, deadline=deadline)
        observed_at = datetime.now(timezone.utc)
        builder.observed_at = observed_at
        for article in result.articles:
            record = repository.save(article, observed_at=observed_at).record
            source_names[record.article.article_id] = result.article_sources[article.article_id]["publisher"]
        return result

    render = (lambda data, job: ((), ["TEST_TEXT_ONLY"])) if text_only else (
        lambda data, job: render_with_fallback(data, ROOT / ".engine-local" / "connected-test" / job.job_id,
                                              font_path=Path(os.environ["CARD_FONT_PATH"]) if os.environ.get("CARD_FONT_PATH") else resolve_font_path()))
    deps = PipelineDeps(gateway=gateway, jobs=jobs, archive=archive, build_cards=builder,
                        render_images=render, send=test_sender(smtp), sender_email=smtp.sender,
                        web_links=WebMailLinks(os.environ["ENGINE_WEB_BASE_URL"]))
    summary = run_batch(deps=deps, collect=collect)
    summary["test_only"] = True
    summary["delivery_confirmed"] = False
    write_summary(summary, ROOT / ".engine-local" / "connected-test" / "runs")
    return summary


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
                          "action": "Check local configuration and latest test job states; do not reset them."}))
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    sys.exit(main())
