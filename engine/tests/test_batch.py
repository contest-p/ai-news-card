"""배치 1회 실행: 수집 예산·작업 생성·복구·전체 예산·정리 호출·실행 요약."""

from datetime import timedelta
import json
import unittest

from engine.batch import run_batch
from engine.collection import CollectionIssue
from engine.delivery import InMemoryJobStore, new_job
from engine.live_collection import LiveCollectionResult
from engine.mail_archive import InMemoryMailArchive
from engine.mail_assembly import WebMailLinks
from engine.pipeline import PipelineDeps
from engine.smtp_sender import SmtpOutcome
from engine.tests import test_pipeline
from engine.tests.test_pipeline import ARTICLES, NOW, SNAPSHOT, Clock, FakeGateway


class BatchGateway(FakeGateway):
    def __init__(self, due=None, expired=None):
        super().__init__()
        self.due = [SNAPSHOT] if due is None else due
        self.expired = expired or []

    def list_due_subscriptions(self, now):
        return [dict(row) for row in self.due]

    def list_expired_subscriptions(self, now):
        return [dict(row) for row in self.expired]


class Monotonic:
    def __init__(self, step=0.0):
        self.value, self.step = 0.0, step

    def __call__(self):
        self.value += self.step
        return self.value


def collected(*, succeeded=True):
    result = LiveCollectionResult(articles=list(ARTICLES))
    if succeeded:
        result.successful_sources = 1
    else:
        result.failed_sources = 1
        result.issues.append(CollectionIssue("test", None, "FEED_UNREACHABLE"))
    return result


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.gateway = BatchGateway()
        self.jobs = InMemoryJobStore()
        self.sent = []
        builder = test_pipeline.PipelineTests()
        builder.card_outcome = None
        self.deps = PipelineDeps(
            gateway=self.gateway, jobs=self.jobs, archive=InMemoryMailArchive(),
            build_cards=builder.build_cards, render_images=lambda data, job: ((), []),
            send=lambda message, recipient: self.sent.append(recipient) or SmtpOutcome("accepted", False),
            sender_email="sender@example.com", web_links=WebMailLinks("https://news.example.com"),
            clock=Clock(NOW))
        self.deadlines = []

    def collect(self, *, deadline):
        self.deadlines.append(deadline)
        return collected()

    def test_runs_due_job_and_writes_summary_without_personal_data(self):
        cleaned = []
        summary = run_batch(deps=self.deps, collect=self.collect, run_id="run-1",
                            privacy_cleanup=lambda now: cleaned.append(now) or {"deleted": 0},
                            monotonic=Monotonic())
        self.assertEqual(self.sent, [SNAPSHOT["recipient_email"]])
        self.assertEqual(summary["jobs"]["by_status"], {"sent": 1})
        self.assertEqual(summary["jobs"]["by_content_kind"], {"news_card": 1})
        self.assertTrue(summary["collection"]["succeeded"])
        self.assertEqual(summary["privacy_cleanup"], "completed")
        self.assertEqual(len(cleaned), 1)
        text = json.dumps(summary, ensure_ascii=False)
        self.assertNotIn(SNAPSHOT["recipient_email"], text)
        self.assertNotIn("TOKEN_FIXTURE", text)

    def test_collection_receives_fifteen_minute_deadline(self):
        run_batch(deps=self.deps, collect=self.collect, run_id="run-1", monotonic=Monotonic())
        self.assertEqual(self.deadlines, [15 * 60.0])

    def test_rerun_does_not_send_again(self):
        run_batch(deps=self.deps, collect=self.collect, run_id="run-1", monotonic=Monotonic())
        self.deps.clock.now += timedelta(minutes=60)
        summary = run_batch(deps=self.deps, collect=self.collect, run_id="run-2", monotonic=Monotonic())
        self.assertEqual(len(self.sent), 1)
        # 이미 sent인 작업은 미완료 목록에 없어 다시 처리하지 않는다.
        self.assertEqual((summary["jobs"]["total"], summary["jobs"]["by_status"]), (0, {}))

    def test_open_job_from_earlier_run_is_recovered_even_if_backend_list_is_empty(self):
        self.jobs.create_if_absent(new_job(SNAPSHOT, "daily_briefing"))
        self.gateway.due = []
        summary = run_batch(deps=self.deps, collect=self.collect, run_id="run-2", monotonic=Monotonic())
        self.assertEqual(summary["jobs"]["by_status"], {"sent": 1})

    def test_backend_list_failure_is_recorded_and_open_jobs_continue(self):
        self.jobs.create_if_absent(new_job(SNAPSHOT, "daily_briefing"))

        def broken(now):
            raise ConnectionError("backend down")
        self.gateway.list_due_subscriptions = broken
        summary = run_batch(deps=self.deps, collect=self.collect, run_id="run-1", monotonic=Monotonic())
        self.assertIn("DUE_SUBSCRIPTIONS_UNAVAILABLE", summary["errors"])
        self.assertEqual(summary["jobs"]["by_status"], {"sent": 1})

    def test_collection_crash_does_not_become_no_news(self):
        def crash(*, deadline):
            raise RuntimeError("collector crashed")
        summary = run_batch(deps=self.deps, collect=crash, run_id="run-1", monotonic=Monotonic())
        self.assertIn("COLLECTION_CRASHED", summary["errors"])
        self.assertEqual(summary["jobs"]["by_status"], {"failed": 1})
        self.assertEqual(self.sent, [])

    def test_batch_budget_stops_remaining_jobs(self):
        other = {**SNAPSHOT, "subscription_id": "00000000-0000-4000-8000-000000000009",
                 "recipient_email": "second@example.com"}
        self.gateway.due = [SNAPSHOT, other]
        summary = run_batch(deps=self.deps, collect=self.collect, run_id="run-1",
                            monotonic=Monotonic(step=30 * 60), budget=timedelta(minutes=45))
        self.assertTrue(summary["budget_exceeded"])
        self.assertEqual(summary["jobs"]["unprocessed"], 1)
        self.assertEqual(len(self.sent), 1)

    def test_job_crash_and_cleanup_failure_are_isolated(self):
        def crash(job, article):
            raise RuntimeError("unexpected")
        self.deps.build_cards = crash

        def cleanup(now):
            raise RuntimeError("cleanup failed")
        summary = run_batch(deps=self.deps, collect=self.collect, run_id="run-1",
                            privacy_cleanup=cleanup, monotonic=Monotonic())
        self.assertEqual(summary["jobs"]["by_status"], {"crashed": 1})
        self.assertIn("JOB_CRASHED", summary["errors"])
        self.assertEqual(summary["privacy_cleanup"], "failed")

    def test_expired_subscription_creates_end_notice_job(self):
        expired = {**SNAPSHOT, "status": "expired", "start_date": "2026-10-11", "end_date_exclusive": "2026-10-18"}
        self.gateway.due = []
        self.gateway.expired = [expired]
        self.gateway.reason = "expired"
        summary = run_batch(deps=self.deps, collect=self.collect, run_id="run-1", monotonic=Monotonic())
        self.assertEqual(summary["jobs"]["by_content_kind"], {"end_notice": 1})


if __name__ == "__main__":
    unittest.main()
