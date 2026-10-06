"""발송 작업 ID·기한·선점·상태 전이. DB 없이 메모리 저장소로 Firestore 계약을 검증한다."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import unittest

from engine.delivery import (
    CLAIM_TTL, ClaimLost, InMemoryJobStore, InvalidTransition, kst_midnight, make_job_id,
    new_job, news_deadline,
)

SNAPSHOT = json.loads((Path(__file__).parents[1] / "samples" / "selection.json").read_text("utf-8"))["subscription_snapshot"]
SCHEDULED = datetime(2026, 10, 17, 23, tzinfo=timezone.utc)  # 2026-10-18 08:00 KST


class JobIdentityTests(unittest.TestCase):
    def test_job_id_is_deterministic_and_separates_fields(self):
        first = make_job_id("sub", date(2026, 10, 18), "daily_briefing")
        self.assertEqual(first, make_job_id("sub", date(2026, 10, 18), "daily_briefing"))
        self.assertNotEqual(first, make_job_id("sub", date(2026, 10, 18), "subscription_end"))
        self.assertNotEqual(first, make_job_id("sub", date(2026, 10, 19), "daily_briefing"))
        # 구분자를 포함한 값이 다른 조합과 충돌하지 않는다.
        self.assertNotEqual(make_job_id("a|2026-10-18", date(2026, 10, 18), "daily_briefing"),
                            make_job_id("a", date(2026, 10, 18), "daily_briefing"))
        with self.assertRaises(ValueError):
            make_job_id("sub", date(2026, 10, 18), "other")

    def test_news_deadline_is_earliest_of_three_hours_midnight_and_expiry(self):
        self.assertEqual(news_deadline(SCHEDULED, date(2026, 10, 18), date(2026, 11, 15)),
                         SCHEDULED + timedelta(hours=3))
        late = datetime(2026, 10, 18, 13, tzinfo=timezone.utc)  # 22:00 KST
        self.assertEqual(news_deadline(late, date(2026, 10, 18), date(2026, 11, 15)),
                         kst_midnight(date(2026, 10, 19)))
        self.assertEqual(kst_midnight(date(2026, 10, 19)), datetime(2026, 10, 18, 15, tzinfo=timezone.utc))

    def test_new_job_uses_earlier_of_backend_and_engine_deadline(self):
        job = new_job({**SNAPSHOT, "deadline_at": "2026-10-18T05:00:00Z"}, "daily_briefing")
        self.assertEqual(job.deadline_at, SCHEDULED + timedelta(hours=3))
        self.assertEqual(job.status, "pending")
        self.assertEqual(job.settings_snapshot["keywords"], ["금리"])
        self.assertNotIn("recipient_email", repr(job))

    def test_end_notice_window_starts_at_expiry_for_24_hours(self):
        job = new_job({**SNAPSHOT, "status": "expired", "start_date": "2026-10-18",
                       "end_date_exclusive": "2026-10-25"}, "subscription_end")
        self.assertEqual(job.scheduled_at, kst_midnight(date(2026, 10, 25)))
        self.assertEqual(job.deadline_at, kst_midnight(date(2026, 10, 25)) + timedelta(hours=24))
        self.assertEqual(job.scheduled_date_kst, "2026-10-25")


class JobStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryJobStore()
        self.job = self.store.create_if_absent(new_job(SNAPSHOT, "daily_briefing"))
        self.now = SCHEDULED + timedelta(minutes=7)

    def claim(self, now=None, run_id="run-1"):
        return self.store.claim(self.job.job_id, run_id=run_id, now=now or self.now)

    def test_create_if_absent_keeps_existing_job(self):
        claimed = self.claim()
        again = self.store.create_if_absent(new_job(SNAPSHOT, "daily_briefing"))
        self.assertEqual(again.status, "processing")
        self.assertEqual(again.claim_token, claimed.claim_token)

    def test_only_one_concurrent_claim_wins(self):
        with ThreadPoolExecutor(8) as pool:
            results = list(pool.map(lambda index: self.claim(run_id=f"run-{index}"), range(16)))
        self.assertEqual(sum(result is not None for result in results), 1)

    def test_not_claimed_before_scheduled_time(self):
        self.assertIsNone(self.claim(now=SCHEDULED - timedelta(minutes=1)))

    def test_deadline_passed_marks_skipped_late(self):
        self.assertIsNone(self.claim(now=self.job.deadline_at))
        self.assertEqual(self.store.get(self.job.job_id).status, "skipped_late")

    def test_wrong_claim_token_cannot_change_state(self):
        claimed = self.claim()
        with self.assertRaises(ClaimLost):
            self.store.transition(self.job.job_id, claim_token="other", to_status="sending", now=self.now)
        self.store.transition(self.job.job_id, claim_token=claimed.claim_token, to_status="sending",
                              now=self.now, smtp_attempts=1)
        with self.assertRaises(InvalidTransition):
            self.store.transition(self.job.job_id, claim_token=claimed.claim_token,
                                  to_status="cancelled", now=self.now)

    def test_sent_is_never_claimed_again(self):
        claimed = self.claim()
        token = claimed.claim_token
        self.store.transition(self.job.job_id, claim_token=token, to_status="sending", now=self.now, smtp_attempts=1)
        self.store.transition(self.job.job_id, claim_token=token, to_status="sent", now=self.now, sent_at=self.now)
        self.assertIsNone(self.claim(now=self.now + timedelta(minutes=30), run_id="run-2"))
        self.assertEqual(self.store.get(self.job.job_id).status, "sent")

    def test_stale_processing_is_reclaimed_with_new_token(self):
        first = self.claim()
        self.assertIsNone(self.claim(now=self.now + timedelta(minutes=10), run_id="run-2"))
        second = self.claim(now=self.now + CLAIM_TTL, run_id="run-2")
        self.assertNotEqual(first.claim_token, second.claim_token)
        with self.assertRaises(ClaimLost):
            self.store.transition(self.job.job_id, claim_token=first.claim_token, to_status="sending", now=self.now)

    def test_stale_sending_becomes_unknown_and_is_not_resent(self):
        claimed = self.claim()
        self.store.transition(self.job.job_id, claim_token=claimed.claim_token, to_status="sending",
                              now=self.now, smtp_attempts=1)
        self.assertIsNone(self.claim(now=self.now + CLAIM_TTL, run_id="run-2"))
        job = self.store.get(self.job.job_id)
        self.assertEqual((job.status, job.error_code), ("unknown", "SENDING_INTERRUPTED"))

    def test_retryable_failure_is_reclaimed_only_within_attempts_and_time(self):
        claimed = self.claim()
        token = claimed.claim_token
        self.store.transition(self.job.job_id, claim_token=token, to_status="sending", now=self.now, smtp_attempts=1)
        self.store.transition(self.job.job_id, claim_token=token, to_status="failed", now=self.now,
                              retryable=True, next_retry_at=self.now + timedelta(minutes=10),
                              error_code="SMTP_TEMPORARY_FAILURE")
        self.assertIsNone(self.claim(now=self.now + timedelta(minutes=5), run_id="run-2"))
        retried = self.claim(now=self.now + timedelta(minutes=10), run_id="run-2")
        self.assertEqual((retried.status, retried.smtp_attempts), ("processing", 1))

    def test_attempt_limit_and_permanent_failure_stop_retries(self):
        for attempts, retryable in ((3, True), (1, False)):
            with self.subTest(attempts=attempts, retryable=retryable):
                store = InMemoryJobStore()
                job = store.create_if_absent(new_job(SNAPSHOT, "daily_briefing"))
                token = store.claim(job.job_id, run_id="run-1", now=self.now).claim_token
                store.transition(job.job_id, claim_token=token, to_status="sending", now=self.now,
                                 smtp_attempts=attempts)
                store.transition(job.job_id, claim_token=token, to_status="failed", now=self.now,
                                 retryable=retryable, next_retry_at=self.now)
                self.assertIsNone(store.claim(job.job_id, run_id="run-2", now=self.now + timedelta(minutes=1)))

    def test_recent_history_uses_sent_and_unknown_urls_for_the_user(self):
        claimed = self.claim()
        self.store.transition(self.job.job_id, claim_token=claimed.claim_token, to_status="processing",
                              now=self.now, selected_article_id="a1", selected_article_url="https://example.com/a1")
        self.store.transition(self.job.job_id, claim_token=claimed.claim_token, to_status="sending",
                              now=self.now, smtp_attempts=1)
        self.store.transition(self.job.job_id, claim_token=claimed.claim_token, to_status="unknown",
                              now=self.now, error_code="SMTP_RESULT_UNCERTAIN")
        history = self.store.recent_history(SNAPSHOT["user_id"], since=self.now - timedelta(days=7))
        self.assertEqual([(row.url, row.status) for row in history], [("https://example.com/a1", "unknown")])
        self.assertEqual(self.store.recent_history("other-user", since=self.now - timedelta(days=7)), [])

    def test_open_jobs_lists_recoverable_work(self):
        self.assertEqual([job.job_id for job in self.store.open_jobs()], [self.job.job_id])
        self.claim(now=self.job.deadline_at)
        self.assertEqual(self.store.open_jobs(), [])


if __name__ == "__main__":
    unittest.main()
