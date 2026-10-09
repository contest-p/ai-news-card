"""한 발송 작업의 선별→카드→이미지→메일→재확인→SMTP 흐름. 외부 서비스 없이 가짜 의존성 사용."""

from datetime import date, datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
import json
from pathlib import Path
import unittest

from engine.delivery import InMemoryJobStore, new_job
from engine.gateway import Eligibility
from engine.mail_archive import InMemoryMailArchive
from engine.mail_assembly import WebMailLinks
from engine.pipeline import CardOutcome, DailyContext, PipelineDeps, process_job
from engine.selection import Article, parse_timestamp, KST
from engine.smtp_sender import SmtpOutcome
from engine.tests import test_cards

SAMPLES = Path(__file__).parents[1] / "samples"
FIXTURE = json.loads((SAMPLES / "selection.json").read_text("utf-8"))
SNAPSHOT = FIXTURE["subscription_snapshot"]
NOW = parse_timestamp(SNAPSHOT["scheduled_at"]) + timedelta(minutes=7)
ARTICLES = [Article(**{**row, "published_at": parse_timestamp(row["published_at"])}) for row in FIXTURE["articles"]]


class FakeGateway:
    def __init__(self):
        self.reason = "active"
        self.tokens = 0

    def check_delivery_eligibility(self, subscription_id, now):
        return Eligibility(self.reason in {"active"} or self.reason == "expired_ok", self.reason)

    def issue_feedback_token(self, job_id):
        self.tokens += 1
        return f"TOKEN_FIXTURE_{self.tokens}"


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def card_data_for(article):
    fixture = test_cards.CardTests()
    fixture.setUp()
    fixture.draft["card2"] = None
    from engine.cards import assemble_cards
    return assemble_cards(fixture.draft, fixture.current, fixture.rag, publishers=fixture.publishers,
                          work_date_kst=fixture.day).card_data


class PipelineTests(unittest.TestCase):
    def test_subscription_preview_sends_before_regular_start_and_is_not_resent(self):
        tomorrow = self.clock.now.astimezone(KST).date() + timedelta(days=1)
        snapshot = {**SNAPSHOT, "start_date": tomorrow.isoformat(),
                    "end_date_exclusive": (tomorrow + timedelta(days=7)).isoformat(),
                    "scheduled_at": self.clock.now.isoformat(),
                    "deadline_at": (self.clock.now + timedelta(hours=24)).isoformat(),
                    "delivery_hour_kst": 8}
        job = self.jobs.create_if_absent(new_job(snapshot, "subscription_preview"))
        self.gateway.check_preview_eligibility = self.gateway.check_delivery_eligibility
        report = self.run_job(job)
        self.assertEqual(report["status"], "sent")
        message, _ = self.sent[0]
        plain, html = self.bodies(message)
        self.assertIn("[구독 완료]", str(message["Subject"]))
        self.assertIn("오전 8시", plain)
        self.assertIn("첫 미리보기", html)
        self.assertEqual(self.jobs.get(job.job_id).mail_kind, "subscription_preview")
        self.assertEqual(self.run_job(job)["status"], "skipped")
        self.assertEqual(len(self.sent), 1)

    def setUp(self):
        self.gateway = FakeGateway()
        self.jobs = InMemoryJobStore()
        self.archive = InMemoryMailArchive()
        self.clock = Clock(NOW)
        self.sent = []
        self.outcomes = []
        self.card_outcome = None
        self.render_issues = ["CARD_IMAGE_FAILED_TEXT_ONLY"]
        self.deps = PipelineDeps(
            gateway=self.gateway, jobs=self.jobs, archive=self.archive,
            build_cards=self.build_cards, render_images=lambda data, job: ((), list(self.render_issues)),
            send=self.send, sender_email="sender@example.com",
            web_links=WebMailLinks("https://news.example.com"), clock=self.clock)
        self.context = DailyContext(ARTICLES, True)
        self.job = self.jobs.create_if_absent(new_job(SNAPSHOT, "daily_briefing"))

    def build_cards(self, job, article):
        return self.card_outcome or CardOutcome("ready", card_data_for(article), False, None, "gen-key")

    def send(self, message, recipient):
        self.sent.append((message, recipient))
        return self.outcomes.pop(0) if self.outcomes else SmtpOutcome("accepted", False)

    def run_job(self, job=None, context=None, run_id="run-1"):
        return process_job((job or self.job).job_id, context=context or self.context, deps=self.deps, run_id=run_id)

    def bodies(self, message):
        parsed = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
        return (parsed.get_body(preferencelist=("plain",)).get_content(),
                parsed.get_body(preferencelist=("html",)).get_content())

    def test_news_job_is_sent_once_with_feedback_and_without_preview_notice(self):
        report = self.run_job()
        self.assertEqual((report["status"], report["content_kind"]), ("sent", "news_card"))
        message, recipient = self.sent[0]
        self.assertEqual(recipient, SNAPSHOT["recipient_email"])
        plain, html = self.bodies(message)
        self.assertIn("/feedback#t=TOKEN_FIXTURE_1&rating=up", plain)
        self.assertNotIn("로컬 검수용", plain)
        self.assertIn("CARD_IMAGE_FAILED_TEXT_ONLY", report["issues"])
        job = self.jobs.get(self.job.job_id)
        self.assertEqual(job.smtp_attempts, 1)
        self.assertIn(job.selected_article_url, {article.url for article in ARTICLES})
        self.assertIsNotNone(job.sent_at)
        # 같은 작업 재실행은 다시 보내지 않는다.
        self.clock.now += timedelta(minutes=30)
        self.assertEqual(self.run_job(run_id="run-2")["status"], "skipped")
        self.assertEqual(len(self.sent), 1)

    def test_renderer_setup_failure_still_sends_verified_text(self):
        def unavailable(data, job):
            raise FileNotFoundError("FONT_OR_BROWSER_NOT_FOUND")
        self.deps.render_images = unavailable
        report = self.run_job()
        self.assertEqual(report["status"], "sent")
        self.assertIn("CARD_IMAGE_FAILED_TEXT_ONLY", report["issues"])
        self.assertEqual(len(self.sent), 1)
        plain, html = self.bodies(self.sent[0][0])
        self.assertIn("핵심 뉴스", html)
        self.assertNotIn("cid:", html)
        self.assertNotIn("FONT_OR_BROWSER_NOT_FOUND", plain)

    def test_temporary_smtp_failure_resends_same_archived_message_up_to_three_attempts(self):
        temporary = SmtpOutcome("failed", True, "SMTP_REJECTED_TEMPORARY", 421)
        self.outcomes = [temporary, temporary, temporary]
        for index in range(4):
            self.run_job(run_id=f"run-{index}")
            self.clock.now += timedelta(minutes=15)
        self.assertEqual(len(self.sent), 3)  # P-15: 최초 포함 최대 3회
        self.assertEqual(len({str(message["Message-ID"]) for message, _ in self.sent}), 1)
        self.assertEqual(self.gateway.tokens, 1)  # 재시도에서 토큰을 재발급하지 않는다.
        job = self.jobs.get(self.job.job_id)
        self.assertEqual((job.status, job.smtp_attempts), ("failed", 3))

    def test_sent_and_unknown_urls_are_not_selected_again_within_seven_days(self):
        self.run_job()
        first_url = self.jobs.get(self.job.job_id).selected_article_url
        next_day = {**SNAPSHOT, "scheduled_date_kst": "2026-10-19", "scheduled_at": "2026-10-18T23:00:00Z",
                    "deadline_at": "2026-10-19T02:00:00Z"}
        job = self.jobs.create_if_absent(new_job(next_day, "daily_briefing"))
        self.clock.now = job.scheduled_at + timedelta(minutes=7)
        moved = [Article(**{**vars(article), "published_at": article.published_at + timedelta(days=1)})
                 for article in ARTICLES]
        self.run_job(job=job, context=DailyContext(moved, True))
        self.assertNotEqual(self.jobs.get(job.job_id).selected_article_url, first_url)

    def test_unknown_result_is_never_resent(self):
        self.outcomes = [SmtpOutcome("unknown", False, "SMTP_RESULT_UNCERTAIN")]
        self.assertEqual(self.run_job()["status"], "unknown")
        self.clock.now += timedelta(minutes=30)
        self.run_job(run_id="run-2")
        self.assertEqual(len(self.sent), 1)

    def test_cancellation_before_smtp_stops_delivery(self):
        for reason, expected in (("cancelled", "cancelled"), ("deletion_requested", "cancelled"),
                                 ("expired", "skipped_late")):
            with self.subTest(reason=reason):
                self.setUp()
                self.gateway.reason = reason
                self.assertEqual(self.run_job()["status"], expected)
                self.assertEqual(self.sent, [])

    def test_collection_failure_is_not_reported_as_no_news(self):
        report = self.run_job(context=DailyContext([], False))
        self.assertEqual((report["status"], report["error_code"]), ("failed", "COLLECTION_UNAVAILABLE"))
        self.assertTrue(self.jobs.get(self.job.job_id).retryable)
        self.assertEqual(self.sent, [])

    def test_successful_collection_without_candidates_sends_no_news_once(self):
        report = self.run_job(context=DailyContext([], True))
        self.assertEqual((report["status"], report["content_kind"]), ("sent", "no_news"))
        plain, _ = self.bodies(self.sent[0][0])
        self.assertIn("오늘은 새 브리핑이 없습니다", plain)
        self.assertNotIn("feedback", plain)
        self.assertEqual(self.gateway.tokens, 0)

    def test_card_failure_fails_job_without_mail(self):
        self.card_outcome = CardOutcome("failed", None, True, "CARD_VALIDATION_FAILED", "gen-key")
        report = self.run_job()
        self.assertEqual((report["status"], report["error_code"]), ("failed", "CARD_VALIDATION_FAILED"))
        self.assertEqual(self.sent, [])

    def test_deadline_passed_while_processing_skips_before_smtp(self):
        def slow_build(job, article):
            self.clock.now = job.deadline_at
            return CardOutcome("ready", card_data_for(article), False, None, "gen-key")
        self.deps.build_cards = slow_build
        self.assertEqual(self.run_job()["status"], "skipped_late")
        self.assertEqual(self.sent, [])

    def test_batch_budget_expiring_during_render_or_eligibility_prevents_smtp(self):
        for stage in ("render", "eligibility"):
            with self.subTest(stage=stage):
                self.setUp()
                timer = [0.0]
                if stage == "render":
                    def render(data, job):
                        timer[0] = 60.0
                        return (), []
                    self.deps.render_images = render
                else:
                    calls = []
                    def eligibility(subscription_id, now):
                        calls.append(1)
                        if len(calls) == 2:
                            timer[0] = 60.0
                        return Eligibility(True, "active")
                    self.gateway.check_delivery_eligibility = eligibility
                report = process_job(self.job.job_id, context=self.context, deps=self.deps,
                                     run_id="budget", batch_deadline=60.0,
                                     monotonic=lambda: timer[0])
                self.assertEqual(report["error_code"], "BATCH_BUDGET_EXCEEDED")
                self.assertEqual(self.jobs.get(self.job.job_id).smtp_attempts, 0)
                self.assertEqual(self.sent, [])
                if stage == "render":
                    self.assertEqual(self.gateway.tokens, 0)

    def test_archive_save_interruption_restores_kind_and_reuses_mail_for_all_types(self):
        for kind in ("news_card", "no_news", "end_notice"):
            with self.subTest(kind=kind):
                self.setUp()
                job = self.job
                context = self.context if kind == "news_card" else DailyContext([], True)
                if kind == "end_notice":
                    snapshot = {**SNAPSHOT, "status": "expired", "start_date": "2026-10-11",
                                "end_date_exclusive": "2026-10-18"}
                    job = self.jobs.create_if_absent(new_job(snapshot, "subscription_end"))
                    self.gateway.reason = "expired"
                    self.clock.now = job.scheduled_at + timedelta(minutes=7)
                save = self.archive.save
                def interrupted_save(job_id, message_bytes):
                    save(job_id, message_bytes)
                    raise RuntimeError("interrupted after durable save")
                self.archive.save = interrupted_save
                with self.assertRaises(RuntimeError):
                    self.run_job(job=job, context=context)
                self.assertIsNone(self.jobs.get(job.job_id).content_kind)
                archived = self.archive.load(job.job_id)
                tokens = self.gateway.tokens
                self.archive.save = save
                self.clock.now += timedelta(minutes=60)
                report = self.run_job(job=job, context=context, run_id="recovery")
                self.assertEqual((report["status"], report["content_kind"]), ("sent", kind))
                self.assertEqual(self.jobs.get(job.job_id).content_kind, kind)
                self.assertEqual(self.sent[0][0].as_bytes(), archived)
                self.assertEqual(self.gateway.tokens, tokens)

    def test_legacy_archived_mail_without_kind_header_is_restored(self):
        save = self.archive.save
        def legacy_save(job_id, message_bytes):
            message = BytesParser(policy=policy.SMTP).parsebytes(message_bytes)
            del message["X-Briefing-Content-Kind"]
            save(job_id, message.as_bytes())
            raise RuntimeError("legacy interrupted save")
        self.archive.save = legacy_save
        with self.assertRaises(RuntimeError):
            self.run_job(context=DailyContext([], True))
        self.clock.now += timedelta(minutes=60)
        report = self.run_job(context=DailyContext([], True), run_id="recovery")
        self.assertEqual(report["content_kind"], "no_news")
        self.assertEqual(self.jobs.get(self.job.job_id).content_kind, "no_news")

    def test_invalid_archived_kind_is_not_sent(self):
        self.outcomes = [SmtpOutcome("failed", True, "SMTP_TEMPORARY_FAILURE")]
        self.run_job(context=DailyContext([], True))
        message = BytesParser(policy=policy.SMTP).parsebytes(self.archive.load(self.job.job_id))
        message.replace_header("X-Briefing-Content-Kind", "end_notice")
        self.deps.archive = InMemoryMailArchive()
        self.deps.archive.save(self.job.job_id, message.as_bytes())
        self.clock.now += timedelta(minutes=15)
        report = self.run_job(run_id="recovery")
        self.assertEqual(report["error_code"], "ARCHIVED_CONTENT_KIND_INVALID")
        self.assertEqual(len(self.sent), 1)
        self.assertFalse(self.jobs.get(self.job.job_id).retryable)

    def test_end_notice_only_for_natural_expiry(self):
        snapshot = {**SNAPSHOT, "status": "expired", "start_date": "2026-10-11", "end_date_exclusive": "2026-10-18"}
        for reason, expected in (("expired", "sent"), ("cancelled", "cancelled"), ("not_found", "cancelled")):
            with self.subTest(reason=reason):
                self.setUp()
                self.gateway.reason = reason
                job = self.jobs.create_if_absent(new_job(snapshot, "subscription_end"))
                self.clock.now = job.scheduled_at + timedelta(minutes=7)
                report = self.run_job(job=job)
                self.assertEqual(report["status"], expected)
                if expected == "sent":
                    plain, _ = self.bodies(self.sent[0][0])
                    self.assertIn("구독이 종료되었습니다", plain)
                    self.assertEqual(report["content_kind"], "end_notice")
                else:
                    self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
