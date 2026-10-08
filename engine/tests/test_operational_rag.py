"""Engine RAG and service assembly regression tests; no external services."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from unittest.mock import Mock, patch
import unittest

from google.auth.credentials import AnonymousCredentials
from google.cloud import firestore
from google.cloud.firestore_v1.vector import Vector
from google.cloud.firestore_v1.vector_query import VectorQuery

from engine.article_store import InMemoryArticleRepository
from engine.card_builder import GenerationCardBuilder
from engine.firestore_article_store import FirestoreArticleRepository, record_document
from engine.firestore_rag import FirestoreRagStore
from engine.firestore_runtime_store import FirestoreMailArchive
from engine.http_gateway import HttpEngineGateway, GatewayUnavailable
from engine.rag import RagResult, RagHit, past_cutoff
from engine.tests.test_card_builder import ARTICLE, Client
from engine.tests.test_connected_stores import FakeClient, fake_transactional
from engine.tests.test_pipeline import NOW, SNAPSHOT
from engine.selection import KST
from engine.tests.test_rag import TestEncoder, vector
from engine.delivery import new_job
from engine.generation import LocalGenerationStore
import tempfile


class PersistentRagTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.tx = patch("google.cloud.firestore.transactional", side_effect=fake_transactional)
        self.tx.start()
        self.addCleanup(self.tx.stop)
        self.repository = FirestoreArticleRepository(self.client)
        self.record = self.repository.save(ARTICLE, observed_at=NOW).record
        self.encoder = TestEncoder()
        self.rag = FirestoreRagStore(self.repository, self.encoder, clock=lambda: NOW)

    def test_embedding_reuse_and_source_survive_restart_then_change_invalidates_vector(self):
        self.repository.set_publisher(self.record, "검증 출처")
        with patch.object(self.encoder, "encode_passages", wraps=self.encoder.encode_passages) as encode:
            self.assertEqual(self.rag.index(self.record), "ready")
            self.assertEqual(FirestoreRagStore(self.repository, self.encoder).index(self.record), "reused")
            self.assertEqual(encode.call_count, 1)
        changed = self.repository.save(replace(ARTICLE, body="은행 정책이 달라졌습니다."),
                                       observed_at=NOW + timedelta(seconds=1)).record
        self.assertEqual(self.repository.publisher_for(ARTICLE.article_id), "검증 출처")
        self.assertEqual(self.rag.index(changed), "ready")
        # A late result from the old version cannot replace the new vector.
        with patch.object(self.encoder, "encode_passages", return_value=[vector(0.8)]):
            self.assertEqual(self.rag.index(self.record), "stale")

    def test_failed_embedding_is_persistent_and_can_be_retried(self):
        with patch.object(self.encoder, "encode_passages", side_effect=RuntimeError("secret")):
            self.assertEqual(self.rag.index(self.record), "failed")
        self.assertEqual(self.rag.index(self.record), "ready")

    def test_embedding_content_race_never_marks_a_new_article_ready(self):
        def mutate(texts):
            self.repository.save(replace(ARTICLE, body="다른 내용"), observed_at=NOW + timedelta(seconds=1))
            return [vector()]
        with patch.object(self.encoder, "encode_passages", side_effect=mutate):
            self.assertEqual(self.rag.index(self.record), "stale")
        self.assertEqual(self.repository.list_current()[0].embedding_status, "pending")

    def test_missing_publisher_is_not_invented(self):
        with self.assertRaises(LookupError):
            self.repository.publisher_for(ARTICLE.article_id)


    def test_pending_retry_has_a_time_budget(self):
        with patch("engine.tests.test_connected_stores.Query.order_by", new=lambda query, field: query, create=True):
            self.assertEqual(self.rag.reindex_pending(deadline=1, monotonic=lambda: 2), {"budget_exceeded": 1})


class VectorQueryTests(unittest.TestCase):
    def setUp(self):
        self.client = firestore.Client(project="demo-engine-rag", credentials=AnonymousCredentials())
        self.addCleanup(self.client.close)
        self.repository = FirestoreArticleRepository(self.client)
        self.rag = FirestoreRagStore(self.repository, TestEncoder())
        self.day = NOW.astimezone(KST).date()
        self.cutoff = past_cutoff(self.day)
        self.records = InMemoryArticleRepository()
        self.current = self.records.save(ARTICLE, observed_at=NOW).record

    def document(self, identity, *, score=0.95, **changes):
        article = replace(ARTICLE, article_id=identity, url="https://example.com/"+identity,
                          published_at=self.cutoff-timedelta(days=1))
        record = self.records.save(article, observed_at=NOW).record
        data = {**record_document(record), "publisher": "검증 출처", "embedding": Vector(vector()),
                "embedding_status": "ready", "embedding_content_hash": record.content_hash,
                "embedding_model": self.rag.encoder.model_id, "embedding_revision": self.rag.encoder.model_revision,
                "rag_distance": 1-score, **changes}
        return Mock(to_dict=lambda: deepcopy(data))

    def test_sdk_query_filters_before_top_five_and_uses_cosine(self):
        captured = []
        def stream(query, **kwargs):
            captured.append(query._to_protobuf())
            return iter([self.document("past")])
        with patch.object(VectorQuery, "stream", new=stream):
            result = self.rag(self.current, self.day)
        request = captured[0]
        self.assertEqual(request.find_nearest.limit, 5)
        self.assertEqual(request.find_nearest.distance_measure.name, "COSINE")
        filters = {f.field_filter.field.field_path for f in request.where.composite_filter.filters}
        self.assertTrue({"category", "published_at", "embedding_status", "embedding_model", "embedding_revision"} <= filters)
        self.assertEqual(result.status, "ready")
        self.assertAlmostEqual(result.usable_evidence[0].score, 0.95)

    def test_retrieve_five_use_three_and_exclude_stale_or_current_records(self):
        docs = [self.document("past"+str(i), score=.95-i*.01) for i in range(5)]
        with patch.object(VectorQuery, "stream", return_value=iter(docs)):
            result = self.rag(self.current, self.day)
        self.assertEqual((len(result.retrieved), len(result.usable_evidence)), (5, 3))
        invalid = [self.document("stale", embedding_content_hash="old"),
                   self.document("future", published_at=self.cutoff), self.document("missing", publisher=None)]
        with patch.object(VectorQuery, "stream", return_value=iter(invalid)):
            result = self.rag(self.current, self.day)
        self.assertEqual(result.status, "no_evidence")
        self.assertEqual(len(result.excluded), 3)

    def test_empty_or_low_score_omits_background_and_query_failure_propagates(self):
        with patch.object(VectorQuery, "stream", return_value=iter([self.document("weak", score=.2)])):
            self.assertEqual(self.rag(self.current, self.day).status, "no_evidence")
        with patch.object(VectorQuery, "stream", side_effect=RuntimeError("index missing")):
            with self.assertRaises(RuntimeError):
                self.rag(self.current, self.day)

    def test_query_encoder_failure_is_distinct_from_no_evidence(self):
        with patch.object(self.rag.encoder, "encode_query", side_effect=RuntimeError()):
            self.assertEqual(self.rag(self.current, self.day).status, "embedding_failed")

    def test_recovery_query_is_date_bounded_and_refuses_truncation(self):
        with patch("google.cloud.firestore_v1.query.Query.stream", return_value=iter([self.document('one')]*2)):
            with self.assertRaisesRegex(RuntimeError, "LIMIT"):
                self.repository.recent_articles(since=NOW-timedelta(hours=27), before=NOW, limit=1)


class LazyEncoderTests(unittest.TestCase):
    def test_model_is_loaded_once_only_on_first_use(self):
        from engine.embeddings import LazyE5Encoder
        with patch("engine.embeddings.E5Encoder") as factory:
            encoder = LazyE5Encoder()
            factory.assert_not_called()
            encoder.encode_query("기사")
            encoder.encode_passages(["본문"])
            factory.assert_called_once_with()

    def test_missing_local_model_is_not_reloaded_for_each_subscriber(self):
        from engine.embeddings import LazyE5Encoder
        with patch("engine.embeddings.E5Encoder", side_effect=RuntimeError("missing cache")) as factory:
            encoder = LazyE5Encoder()
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError, "EMBEDDING_MODEL_UNAVAILABLE"):
                    encoder.encode_query("기사")
            self.assertEqual(factory.call_count, 1)


class ReindexToolTests(unittest.TestCase):
    def test_unrecognized_source_is_not_invented_or_indexed(self):
        from engine.tools.reindex_articles import reindex_records
        repository, rag = Mock(), Mock()
        repository.publisher_for.side_effect = LookupError()
        record = InMemoryArticleRepository().save(ARTICLE, observed_at=NOW).record
        self.assertEqual(reindex_records(repository, rag, [record], []), {"publisher_missing": 1})
        rag.index.assert_not_called()

    def test_existing_publisher_and_failed_index_are_reported(self):
        from engine.tools.reindex_articles import reindex_records
        repository, rag = Mock(), Mock()
        rag.index.return_value = "failed"
        record = InMemoryArticleRepository().save(ARTICLE, observed_at=NOW).record
        self.assertEqual(reindex_records(repository, rag, [record], []), {"failed": 1})
        repository.set_publisher.assert_not_called()


class BuilderAndCleanupTests(unittest.TestCase):
    def test_search_failure_is_recorded_and_only_verified_current_card_is_used(self):
        with tempfile.TemporaryDirectory() as root:
            client = Client()
            record = Mock()
            builder = GenerationCardBuilder(InMemoryArticleRepository(), NOW, lambda key: "출처", client,
                "fixture", "fixture", LocalGenerationStore(root),
                search_evidence=Mock(side_effect=RuntimeError("index missing")), record_search=record)
            result = builder(new_job(SNAPSHOT, "daily_briefing"), ARTICLE)
            self.assertEqual(result.status, "ready")
            self.assertIsNone(result.card_data["card2"])
            self.assertIn("RAG_SEARCH_FAILED", result.issues)
            self.assertEqual(record.call_args.args[2].status, "search_failed")

    def test_audit_failure_blocks_ai_call_and_preserves_retry(self):
        with tempfile.TemporaryDirectory() as root:
            client = Client()
            builder = GenerationCardBuilder(InMemoryArticleRepository(), NOW, lambda key: "출처", client,
                "fixture", "fixture", LocalGenerationStore(root), record_search=Mock(side_effect=RuntimeError()))
            outcome = builder(new_job(SNAPSHOT, "daily_briefing"), ARTICLE)
            self.assertEqual((outcome.status, outcome.retryable, outcome.error_code), ("failed", True, "RAG_RECORD_FAILED"))
            self.assertEqual(client.calls, 0)

    def test_expired_archive_deletes_chunks_and_parent_in_one_batch(self):
        client = Mock()
        parent, child = Mock(), Mock()
        parent.reference.collection.return_value.limit.return_value.stream.return_value = [child]
        client.collection.return_value.where.return_value.limit.return_value.stream.return_value = [parent]
        archive = FirestoreMailArchive(client, retention=timedelta(days=7))
        self.assertEqual(archive.delete_expired(NOW), 1)
        batch = client.batch.return_value
        self.assertEqual([c.args[0] for c in batch.delete.call_args_list], [child.reference, parent.reference])
        batch.commit.assert_called_once()

    def test_cleanup_endpoint_failure_is_not_success(self):
        gateway = HttpEngineGateway("https://backend.example/engine", "fixture")
        with patch.object(gateway, "request", return_value={"status": "pending"}):
            with self.assertRaises(GatewayUnavailable):
                gateway.privacy_cleanup(NOW)
        with patch.object(gateway, "request", return_value={"status": "completed"}) as request:
            gateway.privacy_cleanup(NOW)
            self.assertTrue(request.call_args.kwargs['idempotency_key'].startswith('privacy-'))


if __name__ == "__main__":
    unittest.main()
