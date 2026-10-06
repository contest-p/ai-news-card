from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from engine.chat_client import ChatFailure, CodysseyChatClient, strict_json
from engine.generation import GenerationBusy, LocalGenerationStore, generate_cards
from engine.settings import ChatSettings
from engine.tests import test_cards


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_cards.CardTests()
        self.fixture.setUp()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = LocalGenerationStore(self.temporary.name)
        self.calls = 0

    def complete(self, messages):
        self.calls += 1
        return self.fixture.draft

    def run_generation(self, client=None, **options):
        return generate_cards(self.fixture.current, self.fixture.rag,
                              publishers=self.fixture.publishers, work_date_kst=self.fixture.day,
                              job_id="test-job", model="test-model", base_url="test-url",
                              client=client or self, store=self.store, **options)

    def test_title_over_limit_blocks_before_any_api_call(self):
        from dataclasses import replace
        from engine.article_store import prepare_article
        article = replace(self.fixture.current.article, title="가" * 61)
        self.fixture.current = replace(self.fixture.current, article=article,
                                       content_hash=prepare_article(article)[1])
        output = self.run_generation()
        self.assertEqual((output["status"], output["error_code"]), ("blocked", "TITLE_TOO_LONG"))
        self.assertEqual((output["attempts"], self.calls), (0, 0))
        self.assertFalse(output["api_called_this_run"])

    def test_numeric_format_is_repaired_in_same_run_without_second_call(self):
        body = "병역특례는 53년 된 제도입니다."
        self.fixture.current_body(body)
        self.fixture.draft["card2"] = None
        self.fixture.draft["card1"]["sentences"][0]["numbers"] = [
            {"surface": "53년", "unit": "년", "subject": "병역특례", "as_of": None,
             "source_article_id": "current", "evidence_quote": "53년 된 제도"}]
        output = self.run_generation()
        self.assertEqual((output["status"], output["attempts"], self.calls), ("completed", 1, 1))
        self.assertEqual(output["result"]["card_data"]["card1"]["sentences"][0]["numbers"][0]["surface"], "53")
        self.assertIn("NUMBER_SURFACE_UNIT_SEPARATED", output["format_review"]["changes"])

    def test_explicit_400_recovery_keeps_attempt_count_and_limit(self):
        class Failing:
            def complete(self, messages):
                raise ChatFailure("CHAT_HTTP_400")
        self.assertEqual(self.run_generation(Failing())["attempts"], 1)
        self.assertFalse(self.run_generation()["api_called_this_run"])
        resumed = self.run_generation(retry_blocked=True)
        self.assertEqual((resumed["attempts"], resumed["status"]), (2, "completed"))
        self.assertFalse(self.run_generation(retry_blocked=True)["api_called_this_run"])

    def test_manual_400_recovery_cannot_resume_a_timeout(self):
        class Failing:
            def complete(self, messages):
                raise ChatFailure("CHAT_TIMEOUT")
        self.run_generation(Failing())
        self.assertFalse(self.run_generation(retry_blocked=True)["api_called_this_run"])

    def test_restart_reuses_saved_verified_result_without_another_call(self):
        first = self.run_generation()
        self.store = LocalGenerationStore(self.temporary.name)
        second = self.run_generation()
        self.assertEqual(first["status"], "completed")
        self.assertTrue(second["reused"])
        self.assertFalse(second["api_called_this_run"])
        self.assertEqual(self.calls, 1)

    def test_legacy_cached_fragment_is_rechecked_without_reset_or_api_call(self):
        self.fixture.current_body("은행은 대출금리를 정하므로 정책금리를 확인합니다.")
        first = self.run_generation()
        path = Path(self.temporary.name) / (first["generation_key"] + ".json")
        legacy = json.loads(path.read_text("utf-8"))
        legacy["attempts"] = 2
        legacy.pop("validation_version", None)
        legacy["result"]["card_data"]["card1"]["terms"] = [{
            "term": "대출금리", "definition": "은행은 대출금리를 정하므로",
            "source_article_id": "current", "evidence_quote": "은행은 대출금리를 정하므로"}]
        self.store.save(path, legacy)
        repeated = self.run_generation()
        self.assertEqual(repeated["generation_key"], first["generation_key"])
        self.assertEqual(repeated["attempts"], 2)
        self.assertEqual(repeated["result"]["card_data"]["card1"]["terms"], [])
        self.assertFalse(repeated["api_called_this_run"])
        self.assertEqual(self.calls, 1)
        self.assertIn("TERM_OMITTED:INCOMPLETE_DEFINITION", repeated["result"]["issues"])
        self.assertIn("TERM_OMITTED:INCOMPLETE_DEFINITION", self.run_generation()["result"]["issues"])

    def test_invalid_cached_card_blocks_without_network_retry(self):
        first = self.run_generation()
        path = Path(self.temporary.name) / (first["generation_key"] + ".json")
        state = json.loads(path.read_text("utf-8"))
        state["result"]["card_data"]["card1"]["sentences"][0]["text"] = "원문에 없는 내용"
        self.store.save(path, state)
        result = self.run_generation()
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["api_called_this_run"])
        self.assertIsNone(result["result"]["card_data"])
        self.assertEqual(self.calls, 1)

    def test_two_attempt_limit_survives_restart_and_validation_failure(self):
        self.fixture.draft["card1"]["sentences"][0]["text"] = "원문에 없는 내용"
        first = self.run_generation()
        self.store = LocalGenerationStore(self.temporary.name)
        second = self.run_generation()
        third = self.run_generation()
        self.assertEqual([first["attempts"], second["attempts"], third["attempts"]], [1, 2, 2])
        self.assertEqual(self.calls, 2)
        self.assertFalse(third["api_called_this_run"])

    def test_http_temporary_error_is_not_retried_within_one_run(self):
        class Failing:
            def complete(self, messages):
                raise ChatFailure("CHAT_HTTP_429")
        result = self.run_generation(Failing())
        self.assertEqual((result["attempts"], result["status"]), (1, "retryable"))
        self.assertEqual(self.run_generation()["attempts"], 2)

    def test_timeout_and_authentication_failure_block_automatic_retry(self):
        for code in ["CHAT_TIMEOUT", "CHAT_HTTP_401", "CHAT_NETWORK_ERROR"]:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as root:
                self.store = LocalGenerationStore(root)
                class Failing:
                    def complete(self, messages):
                        raise ChatFailure(code)
                result = self.run_generation(Failing())
                repeated = self.run_generation()
                self.assertEqual(result["status"], "blocked")
                self.assertFalse(repeated["api_called_this_run"])

    def test_count_is_on_disk_before_network_and_crash_is_not_retried(self):
        fixture = self
        class Crashing:
            def complete(self, messages):
                paths = list(Path(fixture.temporary.name).glob("*.json"))
                state = json.loads(paths[0].read_text("utf-8"))
                fixture.assertEqual((state["attempts"], state["status"]), (1, "in_flight"))
                raise RuntimeError("simulated crash")
        with self.assertRaises(RuntimeError):
            self.run_generation(Crashing())
        repeated = self.run_generation()
        self.assertEqual(repeated["status"], "in_flight")
        self.assertFalse(repeated["api_called_this_run"])

    def test_concurrent_calls_have_only_one_completion(self):
        def run(_):
            try:
                return self.run_generation()["status"]
            except GenerationBusy:
                return "busy"
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(run, range(8)))
        self.assertEqual(self.calls, 1)
        self.assertIn("completed", results)

    def test_existing_lock_is_not_automatically_deleted(self):
        key = "a" * 64
        path = Path(self.temporary.name) / (key + ".lock")
        path.touch()
        with self.assertRaises(GenerationBusy), self.store.locked(key):
            self.fail("entered interrupted lock")
        self.assertTrue(path.exists())

    def test_invalid_generation_key_cannot_escape_directory(self):
        with self.assertRaises(ValueError), self.store.locked("../bad"):
            self.fail("invalid key")


class ChatClientTests(unittest.TestCase):
    def setUp(self):
        self.settings = ChatSettings("test-secret-never-log", "https://example.com/v1", "gpt-5.5")
        self.client = CodysseyChatClient(self.settings)

    def test_worker_timeout_is_hard_60_seconds_and_secret_is_not_command_argument(self):
        with patch("engine.chat_client.subprocess.run", side_effect=subprocess.TimeoutExpired("worker", 60)) as run:
            with self.assertRaises(ChatFailure) as error:
                self.client.complete([])
        self.assertEqual(str(error.exception), "CHAT_TIMEOUT")
        self.assertEqual(run.call_args.kwargs["timeout"], 60)
        self.assertNotIn(self.settings.api_key, str(run.call_args.args))
        self.assertEqual(json.loads(run.call_args.kwargs["input"])["payload"]["model"], "gpt-5.5")

    def test_bad_worker_stderr_is_never_exposed(self):
        process = subprocess.CompletedProcess([], 1, stdout="bad", stderr="secret server error")
        with patch("engine.chat_client.subprocess.run", return_value=process):
            with self.assertRaises(ChatFailure) as error:
                self.client.complete([])
        self.assertEqual(str(error.exception), "CHAT_RESPONSE_INVALID")

    def test_valid_worker_draft_and_http_error(self):
        for payload in [{"draft": {"card1": {}, "card2": None}}, {"error": "CHAT_HTTP_401"}]:
            process = subprocess.CompletedProcess([], 0, stdout=json.dumps(payload))
            with patch("engine.chat_client.subprocess.run", return_value=process):
                if "draft" in payload:
                    self.assertEqual(self.client.complete([]), payload["draft"])
                else:
                    with self.assertRaisesRegex(ChatFailure, "CHAT_HTTP_401"):
                        self.client.complete([])

    def test_duplicate_fields_and_nonfinite_json_are_rejected(self):
        for text in ['{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}']:
            with self.assertRaises(ValueError):
                strict_json(text)

    def test_default_uses_verified_relay_format_and_json_mode_is_optional(self):
        response = subprocess.CompletedProcess([], 0, stdout='{"draft":{}}')
        for compatibility in [True, False]:
            with patch("engine.chat_client.subprocess.run", return_value=response) as run:
                CodysseyChatClient(self.settings, compatible_request=compatibility).complete([])
            payload = json.loads(run.call_args.kwargs["input"])["payload"]
            self.assertEqual("max_tokens" in payload, compatibility)
            self.assertEqual("response_format" in payload, not compatibility)


class WorkerTests(unittest.TestCase):
    def test_refusal_truncation_and_invalid_json_do_not_become_cards(self):
        from engine.chat_worker import request_draft
        config = {"base_url": "https://example.com/v1", "api_key": "not-real", "payload": {}}
        for choice in [
            {"finish_reason": "length", "message": {"content": "{}"}},
            {"finish_reason": "stop", "message": {"content": "{}", "refusal": "refused"}},
            {"finish_reason": "stop", "message": {"content": '```json\n{}\n```'}},
            {"finish_reason": "stop", "message": {"content": '{"card1":{},"card1":{}}'}},
        ]:
            with self.subTest(choice=choice):
                with patch("engine.chat_worker.build_opener") as opener:
                    opener.return_value.open.return_value.__enter__.return_value.read.return_value = json.dumps({"choices": [choice]}).encode()
                    with self.assertRaises(ValueError):
                        request_draft(config)

    def test_valid_worker_json_is_parsed_and_redirects_are_blocked(self):
        from engine.chat_worker import NoRedirect, request_draft
        config = {"base_url": "https://example.com/v1", "api_key": "not-real", "payload": {}}
        draft = {"card1": {}, "card2": None}
        body = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(draft)}}]}
        with patch("engine.chat_worker.build_opener") as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = json.dumps(body).encode()
            self.assertEqual(request_draft(config), draft)
            self.assertEqual(opener.return_value.open.call_args.kwargs["timeout"], 55)
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example"))


if __name__ == "__main__":
    unittest.main()
