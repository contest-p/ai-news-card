from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import json
from types import SimpleNamespace
import unittest

from engine.generation import LocalGenerationStore
from engine.demos.live_generate_demo import generate_from_input, prepare_input, write_preview, review_numeric_format, RecordingClient
from engine.selection import Article

NOW = datetime(2026, 10, 5, 6, tzinfo=timezone.utc)


class Client:
    def __init__(self):
        self.calls = 0

    def complete(self, messages):
        self.calls += 1
        return {"card1": {"sentences": [{"text": "국회에서 정책 논의가 열렸습니다.",
                 "source_article_id": "sbs-test", "evidence_quote": "국회에서 정책 논의가 열렸습니다.",
                 "as_of": None, "temporal_role": "current", "numbers": []}], "terms": []}, "card2": None}


def article(*, published=NOW - timedelta(hours=1)):
    return Article("sbs-test", "https://news.sbs.co.kr/news/test", "정책 논의", "국회에서 정책 논의가 열렸습니다.",
                   "politics", published, True, True)


def collector_for(rows):
    def collect(**kwargs):
        return SimpleNamespace(articles=rows, collection_succeeded=True, issues=[],
                               article_sources={row.article_id: {"publisher": "SBS"} for row in rows})
    return collect


class LiveGenerationTests(unittest.TestCase):
    def test_real_article_pipeline_card1_cache_and_export(self):
        payload = prepare_input(now=NOW, collector=collector_for([article()]))
        client = Client()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = LocalGenerationStore(root / "state")
            kwargs = {"client": client, "model": "test", "base_url": "test", "store": store}
            first = generate_from_input(payload, **kwargs)
            second = generate_from_input(payload, **kwargs)
            self.assertEqual(first["status"], "completed")
            self.assertIsNone(first["result"]["card_data"]["card2"])
            self.assertTrue(second["reused"])
            self.assertFalse(second["api_called_this_run"])
            self.assertEqual(client.calls, 1)
            write_preview(root, first)
            self.assertIn("SBS", (root / "preview.md").read_text("utf-8"))
            write_preview(root, {"status": "blocked", "result": None})
            self.assertFalse((root / "card.json").exists())

    def test_stale_article_is_not_selected(self):
        with self.assertRaises(ValueError):
            prepare_input(now=NOW, collector=collector_for([article(published=NOW - timedelta(days=2))]))

    def test_cached_input_is_validated_before_api(self):
        payload = prepare_input(now=NOW, collector=collector_for([article()]))
        payload["article"]["published_at"] = (NOW + timedelta(days=1)).isoformat()
        client = Client()
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):
                generate_from_input(payload, client=client, model="test", base_url="test",
                                    store=LocalGenerationStore(root))
        self.assertEqual(client.calls, 0)

    def test_numeric_format_review_keeps_facts_and_reuses_failed_response(self):
        body = "병역특례는 53년 된 제도입니다. 국회에서 정책 논의가 열렸습니다."
        record = Article("sbs-test", "https://news.sbs.co.kr/news/test", "정책 논의", body,
                         "politics", NOW - timedelta(hours=1), True, True)
        payload = prepare_input(now=NOW, collector=collector_for([record]))
        raw = Client().complete([])
        quote = "병역특례는 53년 된 제도입니다."
        sentence = {"text": quote, "source_article_id": "sbs-test", "evidence_quote": quote,
                    "as_of": None, "temporal_role": "current", "numbers": [
                        {"surface": "53년", "unit": "년", "subject": "병역특례", "as_of": None,
                         "source_article_id": "sbs-test", "evidence_quote": "53년 된 제도"}]}
        raw["card1"]["sentences"].insert(0, sentence)
        client = Client()
        client.complete = lambda messages: raw
        with tempfile.TemporaryDirectory() as directory:
            store = LocalGenerationStore(Path(directory) / "state")
            draft_path = Path(directory) / "draft.json"
            output = generate_from_input(payload, client=RecordingClient(client, draft_path), model="test", base_url="test",
                                         store=store, draft_path=draft_path)
            self.assertEqual(output["status"], "completed")
            self.assertEqual(output["attempts"], 1)
            corrected = output["result"]["card_data"]["card1"]["sentences"][0]
            self.assertEqual(corrected["text"], quote)
            self.assertEqual(corrected["numbers"][0]["surface"], "53")
            self.assertEqual(sentence["numbers"][0]["surface"], "53년")  # 원본 보존
            repeated = generate_from_input(payload, client=client, model="test", base_url="test", store=store)
            self.assertFalse(repeated["api_called_this_run"])

    def test_unverified_numeric_subject_is_omitted_without_guessing(self):
        from engine.article_store import InMemoryArticleRepository
        record = article()
        current = InMemoryArticleRepository().save(record, observed_at=NOW).record
        raw = Client().complete([])
        bad = {"text": "국회에서 정책 논의가 열렸습니다.", "source_article_id": "sbs-test",
               "evidence_quote": record.body, "as_of": None, "temporal_role": "current",
               "numbers": [{"surface": "53", "unit": "년", "subject": "추측한 대상",
                            "as_of": None, "source_article_id": "sbs-test", "evidence_quote": record.body}]}
        raw["card1"]["sentences"].append(bad)
        repaired, changes = review_numeric_format(raw, current)
        self.assertEqual(len(repaired["card1"]["sentences"]), 1)
        self.assertIn("SENTENCE_OMITTED:NUMBER_EVIDENCE_MISMATCH", changes)

    def test_other_input_response_cannot_repair_failed_job(self):
        payload = prepare_input(now=NOW, collector=collector_for([article()]))
        raw = Client().complete([])
        raw["card1"]["sentences"][0]["numbers"] = [
            {"surface": "53", "unit": "년", "subject": "국회", "as_of": None,
             "source_article_id": "sbs-test", "evidence_quote": payload["article"]["body"]}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "draft.json"
            path.write_text(json.dumps({"messages_hash": "different-input", "draft": raw}), encoding="utf-8")
            client = Client()
            client.complete = lambda messages: raw
            output = generate_from_input(payload, client=client, model="test", base_url="test",
                                         store=LocalGenerationStore(Path(directory) / "state"), draft_path=path)
            self.assertEqual(output["status"], "retryable")
            self.assertIsNone(output["result"]["card_data"])

    def test_omitting_all_invalid_sentences_does_not_create_empty_success(self):
        payload = prepare_input(now=NOW, collector=collector_for([article()]))
        raw = Client().complete([])
        raw["card1"]["sentences"][0]["numbers"] = [
            {"surface": "53", "unit": "년", "subject": "국회", "as_of": None,
             "source_article_id": "sbs-test", "evidence_quote": payload["article"]["body"]}]
        with tempfile.TemporaryDirectory() as directory:
            client = Client()
            client.complete = lambda messages: raw
            path = Path(directory) / "draft.json"
            output = generate_from_input(payload, client=RecordingClient(client, path), model="test", base_url="test",
                                         store=LocalGenerationStore(Path(directory) / "state"), draft_path=path)
            self.assertEqual(output["status"], "retryable")
            self.assertIsNone(output["result"]["card_data"])


if __name__ == "__main__":
    unittest.main()
