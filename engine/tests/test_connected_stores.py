"""Persistence/ownership tests with an atomic in-process Firestore double, no network."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
from threading import RLock
import unittest
from unittest.mock import patch

from engine.delivery import ClaimLost, new_job
from engine.firestore_runtime_store import (FirestoreGenerationStore, FirestoreJobStore,
                                           FirestoreMailArchive)
from engine.generation import GenerationBusy, generate_cards
from engine.selection import parse_timestamp
from engine.tests import test_cards
from engine.batch import run_batch
from engine.card_builder import GenerationCardBuilder
from engine.demos.batch_demo import FixtureChatClient
from engine.firestore_article_store import FirestoreArticleRepository
from engine.http_gateway import HttpEngineGateway
from engine.live_collection import LiveCollectionResult
from engine.mail_assembly import WebMailLinks
from engine.pipeline import PipelineDeps
from engine.selection import Article
from engine.smtp_sender import SmtpOutcome
from engine.tests.test_http_gateway import Response
from unittest.mock import Mock
from urllib.parse import urlsplit


class Snapshot:
    def __init__(self, data):
        self.data, self.exists = deepcopy(data), data is not None
    def to_dict(self):
        return deepcopy(self.data)


class Reference:
    def __init__(self, client, path):
        self.client, self.path = client, path
    def get(self, **kwargs):
        return Snapshot(self.client.data.get(self.path))
    def collection(self, name):
        return Query(self.client, self.path + "/" + name)


class Query:
    def __init__(self, client, path, filters=(), count=None):
        self.client, self.path, self.filters, self.count = client, path, filters, count
    def document(self, key):
        return Reference(self.client, self.path + "/" + key)
    def where(self, *, filter):
        return Query(self.client, self.path, self.filters + (filter,), self.count)
    def limit(self, count):
        return Query(self.client, self.path, self.filters, count)
    def stream(self, **kwargs):
        rows = []
        for path, data in self.client.data.items():
            if path.rsplit("/", 1)[0] != self.path:
                continue
            if all((data.get(f.field_path) == f.value if f.op_string == "==" else
                    data.get(f.field_path) in f.value) for f in self.filters):
                rows.append(Snapshot(data))
        return iter(rows[:self.count])


class Transaction:
    def __init__(self, client):
        self.client, self.writes = client, []
    def create(self, ref, data):
        if ref.path in self.client.data:
            raise ValueError("ALREADY_EXISTS")
        self.writes.append((ref.path, deepcopy(data), False))
    def set(self, ref, data, merge=False):
        self.writes.append((ref.path, deepcopy(data), merge))
    def update(self, ref, data):
        self.set(ref, data, merge=True)
    def commit(self):
        for path, data, merge in self.writes:
            self.client.data[path] = {**self.client.data.get(path, {}), **data} if merge else data


class FakeClient:
    def __init__(self):
        self.data, self.lock = {}, RLock()
    def collection(self, name):
        return Query(self, name)
    def transaction(self):
        return Transaction(self)


def fake_transactional(callback):
    def run(tx):
        with tx.client.lock:
            result = callback(tx)
            tx.commit()
            return result
    return run


class ConnectedStoreTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.patch = patch("google.cloud.firestore.transactional", side_effect=fake_transactional)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        fixture = json.loads((Path(__file__).parents[1] / "samples" / "selection.json").read_text("utf-8"))
        self.now = parse_timestamp(fixture["now"])
        self.job = new_job(fixture["subscription_snapshot"], "daily_briefing")
        self.jobs = FirestoreJobStore(self.client)
        self.jobs.create_if_absent(self.job)

    def test_restart_preserves_snapshot_and_only_one_worker_claims(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            claimed = list(pool.map(lambda i: FirestoreJobStore(self.client).claim(
                self.job.job_id, run_id=str(i), now=self.now), range(8)))
        self.assertEqual(sum(item is not None for item in claimed), 1)
        restored = FirestoreJobStore(self.client).create_if_absent(replace(self.job, settings_snapshot={}))
        self.assertEqual(restored.settings_snapshot, self.job.settings_snapshot)

    def test_reclaimed_processing_rejects_old_owner(self):
        first = self.jobs.claim(self.job.job_id, run_id="one", now=self.now)
        second = self.jobs.claim(self.job.job_id, run_id="two", now=self.now + timedelta(minutes=61))
        self.assertIsNotNone(second)
        with self.assertRaises(ClaimLost):
            self.jobs.transition(first.job_id, claim_token=first.claim_token, to_status="sending", now=self.now)

    def test_interrupted_sending_becomes_unknown_and_blocks_replay(self):
        claimed = self.jobs.claim(self.job.job_id, run_id="one", now=self.now)
        self.jobs.transition(claimed.job_id, claim_token=claimed.claim_token, to_status="sending",
                             now=self.now, smtp_attempts=1, selected_article_url="https://example.com/1")
        self.assertIsNone(self.jobs.claim(claimed.job_id, run_id="two", now=self.now + timedelta(minutes=61)))
        restored = FirestoreJobStore(self.client)
        self.assertEqual(restored.get(claimed.job_id).status, "unknown")
        self.assertEqual(restored.open_jobs(), [])
        self.assertEqual(restored.recent_history(self.job.user_id, since=self.now)[0].status, "unknown")

    def test_scan_limit_fails_instead_of_losing_work(self):
        other = replace(self.job, job_id="f" * 64)
        self.jobs.create_if_absent(other)
        with self.assertRaises(RuntimeError):
            FirestoreJobStore(self.client, max_scan=1).open_jobs()

    def test_archive_round_trip_immutable_and_corruption_detected(self):
        archive = FirestoreMailArchive(self.client, retention=timedelta(days=7), clock=lambda: self.now)
        payload = b"MIME" * 250000
        archive.save(self.job.job_id, payload)
        self.assertEqual(archive.load(self.job.job_id), payload)
        archive.save(self.job.job_id, payload)
        with self.assertRaises(ValueError):
            archive.save(self.job.job_id, b"different")
        path = "engine_mail_archives/" + self.job.job_id + "/chunks/0"
        self.client.data[path]["payload"] = b"corrupt"
        with self.assertRaisesRegex(RuntimeError, "CORRUPT"):
            archive.load(self.job.job_id)

    def test_expired_archive_is_not_treated_as_missing(self):
        archive = FirestoreMailArchive(self.client, retention=timedelta(days=1), clock=lambda: self.now)
        archive.save(self.job.job_id, b"MIME")
        archive.clock = lambda: self.now + timedelta(days=1)
        with self.assertRaisesRegex(RuntimeError, "EXPIRED"):
            archive.load(self.job.job_id)

    def test_generation_lock_counts_and_cache_survive_restart(self):
        fixture = test_cards.CardTests()
        fixture.setUp()
        store = FirestoreGenerationStore(self.client, clock=lambda: self.now)
        calls = []
        class Chat:
            def complete(inner, messages):
                calls.append(messages)
                return fixture.draft
        def generate(store):
            return generate_cards(fixture.current, fixture.rag, publishers=fixture.publishers,
                work_date_kst=fixture.day, job_id=self.job.job_id, model="fixture", base_url="fixture",
                client=Chat(), store=store)
        first = generate(store)
        second = generate(FirestoreGenerationStore(self.client, clock=lambda: self.now))
        self.assertEqual(first["status"], "completed")
        self.assertTrue(second["reused"])
        self.assertEqual(len(calls), 1)
        with store.locked("a" * 64):
            with self.assertRaises(GenerationBusy):
                with FirestoreGenerationStore(self.client, clock=lambda: self.now).locked("a" * 64):
                    self.fail("double ownership")

    def test_generation_expired_owner_cannot_save(self):
        store = FirestoreGenerationStore(self.client, clock=lambda: self.now)
        with store.locked("b" * 64) as handle:
            store.clock = lambda: self.now + timedelta(minutes=11)
            with self.assertRaises(GenerationBusy):
                store.save(handle, {"generation_key": handle.key, "attempts": 1})

    def test_interrupted_ai_call_is_not_reissued_after_lease_recovery(self):
        fixture = test_cards.CardTests()
        fixture.setUp()
        calls = []
        class Interrupted:
            def complete(inner, messages):
                calls.append(messages)
                raise RuntimeError("process interrupted")
        store = FirestoreGenerationStore(self.client, clock=lambda: self.now)
        options = dict(publishers=fixture.publishers, work_date_kst=fixture.day,
                       job_id=self.job.job_id, model="fixture", base_url="fixture",
                       client=Interrupted(), store=store)
        with self.assertRaises(RuntimeError):
            generate_cards(fixture.current, fixture.rag, **options)
        # Even after release/restart, the persisted in_flight record blocks AI replay.
        result = generate_cards(fixture.current, fixture.rag, **options)
        self.assertEqual(result["status"], "in_flight")
        self.assertFalse(result["api_called_this_run"])
        self.assertEqual(len(calls), 1)

    def test_full_batch_with_http_gateway_durable_stores_and_restart(self):
        fixture = json.loads((Path(__file__).parents[1] / "samples" / "selection.json").read_text("utf-8"))
        snapshot = fixture["subscription_snapshot"]
        opener = Mock()
        def response(request, **kwargs):
            path = urlsplit(request.full_url).path
            if path.endswith("/due"):
                data = {"subscriptions": [snapshot]}
            elif path.endswith("/expired"):
                data = {"subscriptions": []}
            elif path.endswith("/eligibility"):
                data = {"eligible": True, "reason": "active"}
            elif path.endswith("/feedback-tokens"):
                data = {"token": "fixture-only-token"}
            else:
                self.fail("unexpected backend request")
            return Response(json.dumps(data).encode())
        opener.open.side_effect = response
        gateway = HttpEngineGateway("https://backend.example/engine", "fixture-token", opener=opener)
        articles = [Article(**{**row, "published_at": parse_timestamp(row["published_at"])})
                    for row in fixture["articles"] if row["article_id"] != "fixture_already_sent"]
        submitted = []
        def send(message, recipient):
            submitted.append(message.as_bytes())
            return SmtpOutcome("accepted", False)
        def deps():
            return PipelineDeps(gateway=gateway, jobs=FirestoreJobStore(self.client),
                archive=FirestoreMailArchive(self.client, retention=timedelta(days=7), clock=lambda: self.now),
                build_cards=GenerationCardBuilder(repository=FirestoreArticleRepository(self.client),
                    observed_at=self.now, publisher_for=lambda key: "가상 테스트 출처",
                    client=FixtureChatClient(), model="fixture", base_url="https://fixture.invalid",
                    store=FirestoreGenerationStore(self.client, clock=lambda: self.now)),
                render_images=lambda data, job: ((), ["FIXTURE_TEXT_ONLY"]), send=send,
                sender_email="sender@example.com", web_links=WebMailLinks("https://news.example.com"),
                clock=lambda: self.now)
        collect = lambda **kwargs: LiveCollectionResult(articles=articles, successful_sources=1)
        first = run_batch(deps=deps(), collect=collect)
        self.assertEqual(first["errors"], [])
        self.assertEqual(first["jobs"]["by_status"], {"sent": 1})
        self.assertEqual(first["jobs"]["by_content_kind"], {"news_card": 1})
        second = run_batch(deps=deps(), collect=collect)
        self.assertEqual(second["jobs"]["total"], 0)
        self.assertEqual(len(submitted), 1)


if __name__ == "__main__":
    unittest.main()
