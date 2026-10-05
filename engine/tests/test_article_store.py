from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from engine.article_store import InMemoryArticleRepository, ObservationConflict, prepare_article
from engine.selection import Article


class ArticleStoreTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 18, 0, 7, tzinfo=timezone.utc)
        self.article = Article("fixture-store", "https://example.com/news/1", "가상 제목",
                               "실제 뉴스가 아닌 저장 테스트용 본문입니다.", "economy",
                               self.now - timedelta(hours=1), True, True)
        self.store = InMemoryArticleRepository()

    def test_repeat_same_url_has_one_record_and_one_version(self):
        first = self.store.save(self.article, observed_at=self.now)
        repeat = self.store.save(self.article, observed_at=self.now + timedelta(seconds=1))
        self.assertEqual((first.action, repeat.action), ("inserted", "unchanged"))
        self.assertEqual(len(self.store.list_current()), 1)
        self.assertEqual(repeat.record.content_version, 1)
        self.assertEqual(repeat.record.updated_at, self.now)
        self.assertEqual(repeat.record.last_seen_at, self.now + timedelta(seconds=1))
        self.assertIsNone(self.store.get_revision(self.article.article_id, 2))

    def test_normalized_url_and_changed_incoming_id_keep_original_identity(self):
        self.store.save(self.article, observed_at=self.now)
        incoming = replace(self.article, article_id="other-incoming-id",
                           url="HTTPS://EXAMPLE.COM:443/news/1#top")
        result = self.store.save(incoming, observed_at=self.now + timedelta(seconds=1))
        self.assertEqual(result.action, "unchanged")
        self.assertEqual(result.record.article.article_id, self.article.article_id)
        self.assertEqual(len(self.store.list_current()), 1)

    def test_content_changes_preserve_old_evidence_and_identity(self):
        self.store.save(self.article, observed_at=self.now)
        incoming = replace(self.article, article_id="new-incoming-id", body="수정된 가상 본문입니다.")
        result = self.store.save(incoming, observed_at=self.now + timedelta(seconds=1))
        self.assertEqual(result.action, "updated")
        self.assertEqual(result.record.content_version, 2)
        self.assertEqual(result.record.article.article_id, self.article.article_id)
        self.assertEqual(result.record.first_seen_at, self.now)
        self.assertEqual(result.record.embedding_status, "pending")
        self.assertEqual(self.store.get_revision(self.article.article_id, 1).article.body, self.article.body)
        self.assertEqual(self.store.get_revision(self.article.article_id, 2).article.body, incoming.body)

    def test_reverted_content_still_gets_new_monotonic_version(self):
        self.store.save(self.article, observed_at=self.now)
        self.store.save(replace(self.article, title="다른 제목"), observed_at=self.now + timedelta(seconds=1))
        reverted = self.store.save(self.article, observed_at=self.now + timedelta(seconds=2))
        self.assertEqual(reverted.record.content_version, 3)
        self.assertEqual(reverted.record.content_hash,
                         self.store.get_revision(self.article.article_id, 1).content_hash)

    def test_late_observation_cannot_overwrite_newer_content(self):
        self.store.save(self.article, observed_at=self.now)
        changed = replace(self.article, body="수정된 기사")
        self.store.save(changed, observed_at=self.now + timedelta(seconds=2))
        late = self.store.save(self.article, observed_at=self.now + timedelta(seconds=1))
        self.assertEqual(late.action, "stale")
        self.assertEqual(late.record.article.body, changed.body)
        self.assertEqual(late.record.content_version, 2)

    def test_same_observation_time_conflict_does_not_mutate_data(self):
        self.store.save(self.article, observed_at=self.now)
        with self.assertRaises(ObservationConflict):
            self.store.save(replace(self.article, title="충돌 제목"), observed_at=self.now)
        self.assertEqual(self.store.list_current()[0].article, self.article)

    def test_different_urls_are_not_merged_by_title_or_content(self):
        self.store.save(self.article, observed_at=self.now)
        self.store.save(replace(self.article, article_id="second", url="https://example.com/news/2"),
                        observed_at=self.now)
        self.assertEqual(len(self.store.list_current()), 2)

    def test_same_id_for_different_urls_is_rejected(self):
        self.store.save(self.article, observed_at=self.now)
        with self.assertRaises(ValueError):
            self.store.save(replace(self.article, url="https://example.com/news/2"), observed_at=self.now)
        self.assertEqual(len(self.store.list_current()), 1)

    def test_timezone_and_invalid_input_fail_before_mutation(self):
        for article in (replace(self.article, body=" "), replace(self.article, source_verified=False),
                        replace(self.article, body_valid=False), replace(self.article, url="javascript:alert(1)"),
                        replace(self.article, published_at=datetime(2026, 10, 18))):
            with self.subTest(article=article), self.assertRaises(ValueError):
                self.store.save(article, observed_at=self.now)
        with self.assertRaises(ValueError):
            self.store.save(self.article, observed_at=datetime(2026, 10, 18))
        self.assertEqual(self.store.list_current(), [])

    def test_utc_equivalent_publication_has_same_fingerprint(self):
        offset = replace(self.article, published_at=self.article.published_at.astimezone(
            timezone(timedelta(hours=9))))
        self.assertEqual(prepare_article(self.article)[1], prepare_article(offset)[1])

    def test_parallel_duplicate_writes_have_one_insert(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.store.save(self.article, observed_at=self.now), range(32)))
        self.assertEqual(sum(result.action == "inserted" for result in results), 1)
        self.assertEqual(len(self.store.list_current()), 1)
        self.assertEqual(self.store.list_current()[0].content_version, 1)


if __name__ == "__main__":
    unittest.main()
