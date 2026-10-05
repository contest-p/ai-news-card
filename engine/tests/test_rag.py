from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import math
import unittest

from engine.article_store import InMemoryArticleRepository
from engine.embeddings import DIMENSIONS, validate_vector
from engine.rag import evidence_context, index_articles, past_cutoff, search_past_articles
from engine.selection import Article


def vector(score=1.0):
    return (score, math.sqrt(1 - score * score)) + (0.0,) * (DIMENSIONS - 2)


class TestEncoder:
    model_id = "policy-test-model"
    model_revision = "policy-test-revision"

    def encode_query(self, text):
        return vector()

    def encode_passages(self, texts):
        return [vector() for _ in texts]


class RagTests(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryArticleRepository()
        self.now = datetime(2026, 10, 18, tzinfo=timezone.utc)
        self.encoder = TestEncoder()
        self.current = self.save("current", self.now)

    def save(self, identity, published, **changes):
        article = Article(identity, "https://example.com/" + identity, "가상 기사 " + identity,
                          "정책 테스트용 본문입니다.", "economy", published, True, True)
        return self.store.save(replace(article, **changes), observed_at=self.now).record

    def search(self, records, embeddings=None, **options):
        if embeddings is None:
            embeddings = index_articles(records, self.encoder)
        return search_past_articles(self.current, records, embeddings, self.encoder,
                                    work_date_kst=date(2026, 10, 18), **options)

    def test_kst_midnight_is_exclusive_and_future_and_current_are_excluded(self):
        cutoff = past_cutoff(date(2026, 10, 18))
        self.assertEqual(cutoff, datetime(2026, 10, 17, 15, tzinfo=timezone.utc))
        past = self.save("past", cutoff - timedelta(microseconds=1))
        midnight = self.save("midnight", cutoff)
        future = self.save("future", cutoff + timedelta(days=1))
        result = self.search([self.current, past, midnight, future])
        self.assertEqual([hit.record.article.article_id for hit in result.retrieved], ["past"])
        self.assertEqual(dict(result.excluded)["midnight"], "NOT_BEFORE_KST_MIDNIGHT")

    def test_retrieve_five_use_three_and_context_preserves_source_version(self):
        records = [self.save(str(i), self.now - timedelta(days=i + 2)) for i in range(8)]
        result = self.search(records)
        self.assertEqual(len(result.retrieved), 5)
        self.assertEqual(len(result.usable_evidence), 3)
        self.assertEqual([hit.record.article.article_id for hit in result.retrieved], list("01234"))
        source = evidence_context(result)[0]
        self.assertEqual(source["content_hash"], records[0].content_hash)
        self.assertEqual(source["content_version"], 1)
        self.assertEqual(source["temporal_role"], "past")
        self.assertEqual(source["url"], records[0].article.url)

    def test_similarity_ranking_overrides_recency_and_threshold_is_inclusive(self):
        records = [self.save(str(i), self.now - timedelta(days=i + 2)) for i in range(3)]
        embeddings = [replace(e, vector=vector(score)) for e, score in
                      zip(index_articles(records, self.encoder), [0.5, 0.85, 0.95])]
        result = self.search(records, embeddings)
        self.assertEqual([hit.record.article.article_id for hit in result.retrieved], list("210"))
        self.assertEqual([hit.record.article.article_id for hit in result.usable_evidence], list("21"))

    def test_no_past_articles_is_empty_without_calling_query(self):
        self.encoder.encode_query = lambda _: self.fail("빈 후보에서 질의 임베딩을 호출함")
        result = self.search([self.current])
        self.assertEqual(result.status, "no_evidence")
        self.assertEqual(evidence_context(result), [])

    def test_below_threshold_is_no_evidence_not_embedding_failure(self):
        record = self.save("past", self.now - timedelta(days=2))
        embeddings = [replace(index_articles([record], self.encoder)[0], vector=vector(0.5))]
        result = self.search([record], embeddings)
        self.assertEqual(result.status, "no_evidence")
        self.assertEqual(len(result.retrieved), 1)
        self.assertEqual(evidence_context(result), [])

    def test_stale_content_version_hash_and_model_are_rejected(self):
        record = self.save("past", self.now - timedelta(days=2))
        original = index_articles([record], self.encoder)[0]
        variants = [replace(original, content_version=0), replace(original, content_hash="old"),
                    replace(original, model_id="other"), replace(original, model_revision="other"),
                    replace(original, status="failed", vector=None)]
        for embedding in variants:
            with self.subTest(embedding=embedding):
                result = self.search([record], [embedding])
                self.assertEqual(result.status, "embedding_failed")
                self.assertEqual(result.usable_evidence, ())

    def test_invalid_article_hash_body_and_category_are_excluded(self):
        record = self.save("past", self.now - timedelta(days=2))
        variants = [replace(record, content_hash="wrong"),
                    replace(record, article=replace(record.article, body=""))]
        for candidate in variants:
            with self.subTest(candidate=candidate):
                result = self.search([candidate], [])
                self.assertEqual(dict(result.excluded)["past"], "ARTICLE_INVALID")
        other = self.save("other", self.now - timedelta(days=2), category="sports")
        self.assertEqual(dict(self.search([other]).excluded)["other"], "CATEGORY_MISMATCH")

    def test_one_failed_passage_does_not_discard_successful_passage(self):
        records = [self.save("good", self.now - timedelta(days=2)),
                   self.save("bad", self.now - timedelta(days=2))]
        self.encoder.encode_passages = lambda texts: [vector()] if "good" in texts[0] else []
        embeddings = index_articles(records, self.encoder)
        self.assertEqual([e.status for e in embeddings], ["ready", "failed"])
        self.assertEqual(self.search(records, embeddings).status, "ready")

    def test_query_failure_returns_failure_without_evidence(self):
        record = self.save("past", self.now - timedelta(days=2))
        self.encoder.encode_query = lambda _: (_ for _ in ()).throw(RuntimeError("failure"))
        self.assertEqual(self.search([record]).status, "embedding_failed")

    def test_bad_vectors_are_rejected(self):
        record = self.save("past", self.now - timedelta(days=2))
        original = index_articles([record], self.encoder)[0]
        for bad in [(), (0.0,) * 384, (float("nan"),) * 384, (float("inf"),) * 384]:
            with self.subTest(bad=bad[:1]):
                with self.assertRaises(ValueError):
                    validate_vector(bad)
                result = self.search([record], [replace(original, vector=bad)])
                self.assertEqual(dict(result.excluded)["past"], "VECTOR_INVALID")

    def test_duplicate_embedding_and_invalid_threshold_are_rejected(self):
        record = self.save("past", self.now - timedelta(days=2))
        embeddings = index_articles([record], self.encoder)
        with self.assertRaises(ValueError):
            self.search([record], embeddings * 2)
        for threshold in [-0.1, 1.1, float("nan")]:
            with self.assertRaises(ValueError):
                self.search([record], min_score=threshold)


if __name__ == "__main__":
    unittest.main()
