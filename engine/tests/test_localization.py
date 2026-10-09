import copy
from datetime import timedelta
import tempfile
import unittest
from unittest.mock import patch

from engine.article_store import InMemoryArticleRepository
from engine.card_builder import GenerationCardBuilder
from engine.card_render import validate_render_data
from engine.delivery import new_job
from engine.generation import LocalGenerationStore
from engine.localization import TranslationFailure, korean_card
from engine.mail_assembly import NewsMailData, assemble_mail
from engine.selection import Article
from engine.tests.test_pipeline import NOW, SNAPSHOT

BODY = "An ICE agent shot a man in New York City. The man was alive at a local hospital."
TITLE = "ICE agent shoots man in New York City"
DEFINITION = "US Immigration and Customs Enforcement"
ARTICLE = Article("current", "https://example.com/current", TITLE, BODY + " " + DEFINITION,
                  "world", NOW - timedelta(hours=1), True, True)
DRAFT = {"card1": {"sentences": [{"text": BODY, "source_article_id": "current",
    "evidence_quote": BODY, "as_of": None, "temporal_role": "current", "numbers": []}],
    "terms": [{"term": "ICE", "definition": DEFINITION, "source_article_id": "current",
               "evidence_quote": ARTICLE.body}]}, "card2": None}
TRANSLATED = {"translations": ["미 이민세관단속국 요원, 뉴욕에서 남성에게 총격",
    "미 이민세관단속국 요원이 뉴욕에서 한 남성에게 총격을 가했습니다. 남성은 생존한 상태로 현지 병원에 있었습니다.",
    "미국 이민세관단속국"]}


class Client:
    def __init__(self):
        self.calls = 0
        self.translation = TRANSLATED

    def complete(self, messages):
        self.calls += 1
        return copy.deepcopy(self.translation if '"max_chars"' in messages[1]["content"] else DRAFT)


class LocalizationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = LocalGenerationStore(temporary.name)
        self.client = Client()
        self.builder = GenerationCardBuilder(InMemoryArticleRepository(), NOW, lambda _: "BBC 뉴스",
            self.client, "test-model", "https://api.example.invalid", self.store)
        self.job = new_job(SNAPSHOT, "subscription_preview")

    def test_english_title_text_and_term_reach_mail_in_korean_and_cache_is_reused(self):
        outcome = self.builder(self.job, ARTICLE)
        self.assertEqual(outcome.status, "ready")
        data = outcome.card_data
        self.assertEqual(data["title"], TRANSLATED["translations"][0])
        self.assertEqual(data["card1"]["sentences"][0]["text"], TRANSLATED["translations"][1])
        self.assertEqual(data["card1"]["terms"][0]["definition"], TRANSLATED["translations"][2])
        self.assertEqual(data["card1"]["sentences"][0]["evidence_quote"], BODY)
        self.assertEqual(data["card1"]["terms"][0]["term"], "ICE")
        self.assertEqual(data["sources"][0]["url"], ARTICLE.url)
        validate_render_data(data)
        message = assemble_mail(NewsMailData(job_id=self.job.job_id,
            recipient_email="reader@example.com", sender_email="sender@example.com",
            scheduled_date_kst=NOW.date(), card_data=data,
            selection_reason={"type": "category", "label": "관심 분야의 최신 기사"}, preview=True))[1]
        self.assertIn(TRANSLATED["translations"][1], message.get_body(preferencelist=("plain",)).get_content())
        self.assertIn(TRANSLATED["translations"][2], message.get_body(preferencelist=("html",)).get_content())
        self.assertNotIn(BODY, message.get_body(preferencelist=("plain",)).get_content())
        again = self.builder(self.job, ARTICLE)
        self.assertEqual(again.card_data, data)
        self.assertEqual(self.client.calls, 2)  # 추출 1회 + 번역 1회, 재실행은 0회.

    def test_invalid_translation_does_not_send_english_and_retry_reuses_extraction(self):
        self.client.translation = {"translations": [TITLE, BODY, DEFINITION]}
        first = self.builder(self.job, ARTICLE)
        self.assertEqual((first.status, first.retryable), ("failed", True))
        self.assertIsNone(first.card_data)
        self.client.translation = TRANSLATED
        second = self.builder(self.job, ARTICLE)
        self.assertEqual(second.status, "ready")
        self.assertEqual(self.client.calls, 3)

    def test_translation_attempts_are_bounded_and_failed_result_never_becomes_ready(self):
        self.client.translation = {"translations": []}
        self.builder(self.job, ARTICLE)
        second = self.builder(self.job, ARTICLE)
        self.assertEqual((second.status, second.retryable), ("failed", False))
        self.builder(self.job, ARTICLE)
        self.assertEqual(self.client.calls, 3)

    def test_numbers_cannot_be_changed_or_added_by_translation(self):
        data = self.builder(self.job, ARTICLE).card_data
        data["title"] = "Hospital treated 12 people"
        class NumericClient:
            def complete(self, messages):
                return {"translations": ["병원에서 13명을 치료했습니다"]}
        with self.assertRaises(TranslationFailure):
            korean_card(data, source_key="numeric", client=NumericClient(), store=self.store,
                        model="test", base_url="test")

    def test_korean_cards_skip_translation_api(self):
        data = self.builder(self.job, ARTICLE).card_data
        class Never:
            def complete(self, messages):
                raise AssertionError("한국어 카드는 추가 번역 호출 금지")
        output, translated = korean_card(data, source_key="korean", client=Never(), store=self.store,
                                         model="test", base_url="test")
        self.assertFalse(translated)
        self.assertEqual(output, data)

    def test_past_card_is_translated_without_changing_its_dates_or_evidence(self):
        data = self.builder(self.job, ARTICLE).card_data
        data["sources"].append({**data["sources"][0], "article_id": "past",
            "url": "https://example.com/past", "published_at": "2026-10-01T00:00:00+00:00"})
        data["card2"] = {"sentences": [{"text": "Officials reported 12 cases.",
            "source_article_id": "past", "evidence_quote": "Officials reported 12 cases.",
            "as_of": None, "temporal_role": "past", "numbers": []}], "terms": []}
        class PastClient:
            def complete(self, messages):
                return {"translations": ["당국은 12건을 보고했습니다."]}
        result, _ = korean_card(data, source_key="past", client=PastClient(), store=self.store,
                                model="test", base_url="test")
        before, after = data["card2"]["sentences"][0], result["card2"]["sentences"][0]
        self.assertEqual({k:v for k,v in before.items() if k != "text"},
                         {k:v for k,v in after.items() if k != "text"})

    def test_interrupted_request_is_not_automatically_called_again(self):
        data = self.builder(self.job, ARTICLE).card_data
        data["title"] = TITLE
        class Interrupted:
            calls = 0
            def complete(self, messages):
                self.calls += 1
                raise RuntimeError("interrupted")
        client = Interrupted()
        options = dict(source_key="interrupted", client=client, store=self.store,
                       model="test", base_url="test")
        with self.assertRaises(RuntimeError):
            korean_card(data, **options)
        with self.assertRaises(TranslationFailure):
            korean_card(data, **options)
        self.assertEqual(client.calls, 1)

    def test_translation_persists_and_reuses_with_firestore_store(self):
        from engine.firestore_runtime_store import FirestoreGenerationStore
        from engine.tests.test_connected_stores import FakeClient, fake_transactional
        db = FakeClient()
        with patch("google.cloud.firestore.transactional", side_effect=fake_transactional):
            self.builder.store = FirestoreGenerationStore(db, clock=lambda: NOW)
            first = self.builder(self.job, ARTICLE)
            self.builder.store = FirestoreGenerationStore(db, clock=lambda: NOW)
            second = self.builder(self.job, ARTICLE)
        self.assertEqual(first.status, "ready")
        self.assertEqual(first.card_data, second.card_data)
        self.assertEqual(self.client.calls, 2)


if __name__ == "__main__":
    unittest.main()
