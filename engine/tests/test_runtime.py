"""Real engine assembly with fake IO; covers RAG -> background -> mail -> restart."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
from contextlib import ExitStack
import tempfile
from pathlib import Path
import unittest

from engine.runtime import run_connected
from engine.firestore_article_store import FirestoreArticleRepository
from engine.firestore_rag import FirestoreRagStore
from engine.gateway import Eligibility
from engine.live_collection import LiveCollectionResult
from engine.smtp_sender import SmtpOutcome
from engine.tests.test_connected_stores import FakeClient, Reference, Query, Snapshot, fake_transactional
from engine.tests.test_card_builder import ARTICLE
from engine.tests.test_pipeline import NOW, SNAPSHOT
from engine.tests.test_rag import TestEncoder


class Ref(Reference):
    def set(self, data, merge=False):
        self.client.data[self.path] = {**self.client.data.get(self.path, {}), **deepcopy(data)} if merge else deepcopy(data)


class Rows(Query):
    def document(self, key):
        return Ref(self.client, self.path + '/' + key)
    def where(self, *, filter):
        return Rows(self.client, self.path, self.filters + (filter,), self.count)
    def limit(self, count):
        return Rows(self.client, self.path, self.filters, count)
    def order_by(self, field):
        return self
    def find_nearest(self, **kwargs):
        self.count = kwargs['limit']
        self.vector_query = True
        return self
    def stream(self, **kwargs):
        def matches(data, f):
            value = data.get(f.field_path)
            if f.op_string == '==': return value == f.value
            if f.op_string == 'in': return value in f.value
            if value is None: return False
            if f.op_string == '<': return value < f.value
            if f.op_string == '>=': return value >= f.value
            if f.op_string == '<=': return value <= f.value
            raise AssertionError(f.op_string)
        result = []
        for path, data in self.client.data.items():
            if path.rsplit('/', 1)[0] == self.path and all(matches(data, f) for f in self.filters):
                row = Snapshot({**data, **({'rag_distance': .05} if getattr(self, 'vector_query', False) else {})})
                row.reference = Ref(self.client, path)
                result.append(row)
        return iter(result[:self.count])


class MemoryDB(FakeClient):
    def collection(self, name):
        return Rows(self, name)


class RuntimeTests(unittest.TestCase):
    @patch("google.cloud.firestore.transactional", side_effect=fake_transactional)
    def test_full_assembly_sends_background_and_expiry_only_once_and_reports_cleanup_failure(self, transactional):
        db, encoder = MemoryDB(), TestEncoder()
        repository = FirestoreArticleRepository(db)
        past = replace(ARTICLE, article_id='past', url='https://example.com/past',
                       body='과거에는 은행 조달 비용이 달라졌습니다.', published_at=NOW-timedelta(days=2))
        stored = repository.save(past, observed_at=NOW).record
        repository.set_publisher(stored, '과거 검증 출처')
        expired = {**SNAPSHOT, 'subscription_id': 'expired-fixture', 'status': 'expired',
                   'start_date': '2026-10-11', 'end_date_exclusive': '2026-10-18'}
        gateway = Mock()
        gateway.list_due_subscriptions.return_value = [SNAPSHOT]
        gateway.list_expired_subscriptions.return_value = [expired]
        gateway.check_delivery_eligibility.side_effect = lambda identity, now: Eligibility(
            identity != 'expired-fixture', 'expired' if identity == 'expired-fixture' else 'active')
        gateway.issue_feedback_token.return_value = 'FIXTURE_TOKEN_ONLY'
        gateway.privacy_cleanup.side_effect = RuntimeError('backend not implemented')
        collected = LiveCollectionResult(articles=[ARTICLE], successful_sources=1,
            article_sources={ARTICLE.article_id: {'publisher': '현재 검증 출처'}})
        def card(article, role):
            return {'sentences': [{'text': article.body, 'source_article_id': article.article_id,
                'evidence_quote': article.body, 'as_of': None, 'temporal_role': role, 'numbers': []}], 'terms': []}
        chat = Mock()
        chat.complete.return_value = {'card1': card(ARTICLE, 'current'), 'card2': card(past, 'past')}
        submitted = []
        def send(message, recipient):
            submitted.append(message)
            return SmtpOutcome('accepted', False)
        settings = SimpleNamespace(sender='sender@example.com')
        env = {'ENGINE_WEB_BASE_URL': 'https://news.example.com', 'ENGINE_ARCHIVE_RETENTION_DAYS': '7'}
        with tempfile.TemporaryDirectory() as output, ExitStack() as stack:
            stack.enter_context(patch.dict('os.environ', env))
            stack.enter_context(patch('google.cloud.firestore.transactional', side_effect=fake_transactional))
            stack.enter_context(patch('engine.runtime.ROOT', Path(output)))
            stack.enter_context(patch('engine.runtime.load_chat_settings', return_value=SimpleNamespace(model='fixture', base_url='fixture')))
            stack.enter_context(patch('engine.runtime.CodysseyChatClient', return_value=chat))
            stack.enter_context(patch('engine.runtime.collect_live_sources', side_effect=lambda **kw: deepcopy(collected)))
            stack.enter_context(patch('engine.runtime.FirestoreMailArchive.delete_expired', return_value=0))
            options = dict(gateway=gateway, sender=send, smtp=settings, encoder=encoder, text_only=True, clock=lambda: NOW)
            first = run_connected(db, **options)
            self.assertEqual(first['jobs']['by_status'], {'sent': 2})
            self.assertEqual(first['jobs']['by_content_kind'], {'news_card': 1, 'end_notice': 1})
            self.assertEqual(first['privacy_cleanup'], 'failed')
            self.assertIn('PRIVACY_CLEANUP_FAILED', first['errors'])
            news = next(message for message in submitted if message['X-Briefing-Content-Kind'] == 'news_card')
            self.assertIn(past.body, news.get_body(preferencelist=('plain',)).get_content())
            self.assertIn('과거 검증 출처', news.get_body(preferencelist=('plain',)).get_content())
            second = run_connected(db, **options)
            self.assertEqual(second['jobs']['total'], 0)
            self.assertEqual(len(submitted), 2)
            self.assertEqual(chat.complete.call_count, 1)
            audits = [data for path, data in db.data.items() if path.startswith('engine_rag_results/')]
            self.assertEqual(audits[0]['used_article_ids'], ['past'])


if __name__ == '__main__':
    unittest.main()
