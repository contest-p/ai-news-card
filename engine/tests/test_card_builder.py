from datetime import timedelta
import tempfile
import unittest

from engine.article_store import InMemoryArticleRepository
from engine.card_builder import GenerationCardBuilder, no_evidence_search
from engine.chat_client import ChatFailure
from engine.delivery import new_job
from engine.generation import LocalGenerationStore
from engine.selection import Article
from engine.tests.test_pipeline import NOW, SNAPSHOT

ARTICLE = Article("current", "https://example.com/current", "가상 제목", "은행은 대출금리를 정했습니다.",
                  "economy", NOW - timedelta(hours=1), True, True)
DRAFT = {"card1": {"sentences": [{"text": ARTICLE.body, "source_article_id": "current",
                                  "evidence_quote": ARTICLE.body, "as_of": None, "temporal_role": "current",
                                  "numbers": []}], "terms": []}, "card2": None}


class Client:
    def __init__(self, error=None):
        self.error, self.calls = error, 0

    def complete(self, messages):
        self.calls += 1
        if self.error:
            raise self.error
        return DRAFT


class CardBuilderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.job = new_job(SNAPSHOT, "daily_briefing")

    def builder(self, client):
        return GenerationCardBuilder(repository=InMemoryArticleRepository(), observed_at=NOW,
                                     publisher_for=lambda article_id: "가상 출처", client=client,
                                     model="test-model", base_url="https://api.example.invalid",
                                     store=LocalGenerationStore(self.temporary.name))

    def test_completed_generation_becomes_ready_card(self):
        client = Client()
        outcome = self.builder(client)(self.job, ARTICLE)
        self.assertEqual((outcome.status, outcome.retryable), ("ready", False))
        self.assertIsNone(outcome.card_data["card2"])
        self.assertEqual(outcome.card_data["sources"][0]["publisher"], "가상 출처")
        self.assertTrue(outcome.generation_key)
        # 같은 작업은 저장된 결과를 재사용하고 API를 다시 부르지 않는다.
        self.builder(client)(self.job, ARTICLE)
        self.assertEqual(client.calls, 1)

    def test_temporary_api_error_is_retryable_and_auth_error_is_not(self):
        for code, retryable in (("CHAT_HTTP_503", True), ("CHAT_HTTP_401", False)):
            with self.subTest(code=code):
                self.setUp()
                outcome = self.builder(Client(ChatFailure(code)))(self.job, ARTICLE)
                self.assertEqual((outcome.status, outcome.retryable, outcome.error_code), ("failed", retryable, code))

    def test_no_evidence_search_omits_background(self):
        record = InMemoryArticleRepository().save(ARTICLE, observed_at=NOW).record
        result = no_evidence_search(record, NOW.date())
        self.assertEqual((result.status, result.usable_evidence), ("no_evidence", ()))


if __name__ == "__main__":
    unittest.main()
