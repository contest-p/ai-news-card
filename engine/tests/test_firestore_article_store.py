"""SDK 문서·쓰기 요청 구성만 검증한다. 실제 Firebase에는 연결하지 않는다."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from google.auth.credentials import AnonymousCredentials
from google.cloud import firestore
from google.cloud.firestore_v1._helpers import decode_dict, encode_dict

from engine.article_store import ObservationConflict, StoredArticle, prepare_article
from engine.firestore_article_store import (
    FirestoreArticleRepository, decode_record, document_key, record_document,
)
from engine.selection import Article


class FirestoreArticleStoreTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 18, 0, 7, tzinfo=timezone.utc)
        self.article = Article("fixture-firestore", "https://example.com/news/1", "가상 제목",
                               "가상 기사 본문", "economy", self.now - timedelta(hours=1), True, True)
        self.client = firestore.Client(project="demo-ai-news-card-test", credentials=AnonymousCredentials())
        self.addCleanup(self.client.close)
        self.store = FirestoreArticleRepository(self.client)
        self.reference = self.store.articles.document(document_key(self.article.url))
        self.record = StoredArticle(self.article, prepare_article(self.article)[1], 1,
                                    self.now, self.now, self.now)

    def snapshot(self, reference, data):
        return firestore.DocumentSnapshot(reference, data, data is not None, self.now, self.now, self.now)

    def test_sdk_serialization_preserves_korean_and_utc(self):
        document = record_document(self.record)
        decoded = decode_dict(encode_dict(document), self.client)
        self.assertEqual(decode_record(decoded), self.record)

    def test_insert_constructs_atomic_current_identity_and_revision_writes(self):
        transaction = self.client.transaction()

        def get(reference, *, transaction):
            self.assertEqual(len(transaction._write_pbs), 0, "reads must precede writes")
            return self.snapshot(reference, None)

        with patch.object(firestore.DocumentReference, "get", new=get):
            result = self.store._save_transaction(transaction, self.article,
                                                  prepare_article(self.article)[1], self.now)
        self.assertEqual(result.action, "inserted")
        self.assertEqual(len(transaction._write_pbs), 3)
        paths = [write.update.name for write in transaction._write_pbs]
        self.assertTrue(any(path.endswith("/versions/1") for path in paths))
        self.assertTrue(all(write.current_document.exists is False for write in transaction._write_pbs))

    def test_same_url_changed_input_id_is_not_duplicate(self):
        transaction = self.client.transaction()
        incoming, digest = prepare_article(replace(self.article, article_id="incoming-different-id",
                                                   url="HTTPS://EXAMPLE.COM:443/news/1#top"))
        with patch.object(firestore.DocumentReference, "get",
                          return_value=self.snapshot(self.reference, record_document(self.record))):
            result = self.store._save_transaction(transaction, incoming, digest,
                                                  self.now + timedelta(seconds=1))
        self.assertEqual(result.action, "unchanged")
        self.assertEqual(result.record.article.article_id, self.article.article_id)
        self.assertEqual(len(transaction._write_pbs), 1)

    def test_change_constructs_new_revision_without_updating_old_one(self):
        transaction = self.client.transaction()
        incoming, digest = prepare_article(replace(self.article, body="변경된 본문"))
        with patch.object(firestore.DocumentReference, "get",
                          return_value=self.snapshot(self.reference, record_document(self.record))):
            result = self.store._save_transaction(transaction, incoming, digest,
                                                  self.now + timedelta(seconds=1))
        self.assertEqual(result.record.content_version, 2)
        self.assertEqual(len(transaction._write_pbs), 2)
        self.assertTrue(transaction._write_pbs[-1].update.name.endswith("/versions/2"))
        self.assertFalse(any(write.update.name.endswith("/versions/1") for write in transaction._write_pbs))

    def test_stale_and_conflicting_observations_do_not_write(self):
        for delta in (-1, 0):
            transaction = self.client.transaction()
            incoming, digest = prepare_article(replace(self.article, body="다른 본문"))
            with patch.object(firestore.DocumentReference, "get",
                              return_value=self.snapshot(self.reference, record_document(self.record))):
                if delta == 0:
                    with self.assertRaises(ObservationConflict):
                        self.store._save_transaction(transaction, incoming, digest, self.now)
                else:
                    result = self.store._save_transaction(transaction, incoming, digest,
                                                          self.now + timedelta(seconds=delta))
                    self.assertEqual(result.action, "stale")
            self.assertEqual(transaction._write_pbs, [])

    def test_registered_id_for_other_url_is_rejected_without_writes(self):
        transaction = self.client.transaction()
        identity_reference = self.store.identities.document(document_key(self.article.article_id))
        with patch.object(firestore.DocumentReference, "get", side_effect=[
            self.snapshot(self.reference, None),
            self.snapshot(identity_reference, {"article_id": self.article.article_id, "document_key": "other"}),
        ]):
            with self.assertRaises(ValueError):
                self.store._save_transaction(transaction, self.article, prepare_article(self.article)[1], self.now)
        self.assertEqual(transaction._write_pbs, [])

    def test_transaction_failure_is_not_reported_as_saved(self):
        def fail_decorator(callback):
            def fail(transaction):
                raise RuntimeError("TEST_ONLY_COMMIT_FAILURE")
            return fail

        with patch("google.cloud.firestore.transactional", side_effect=fail_decorator):
            with self.assertRaisesRegex(RuntimeError, "TEST_ONLY_COMMIT_FAILURE"):
                self.store.save(self.article, observed_at=self.now)

    def test_inspection_scan_limit_does_not_silently_truncate(self):
        self.store.max_scan = 1
        documents = [self.snapshot(self.reference, record_document(self.record))] * 2
        with patch("google.cloud.firestore_v1.query.Query.stream", return_value=iter(documents)):
            with self.assertRaises(RuntimeError):
                self.store.list_current()


if __name__ == "__main__":
    unittest.main()
