from copy import deepcopy
from datetime import timedelta
import unittest
from unittest.mock import Mock

from engine.delivery import InMemoryJobStore, new_job
from engine.preview import PreviewGateway, PreviewJobStore
from engine.tests.test_pipeline import SNAPSHOT, NOW


class PreviewScopeTests(unittest.TestCase):
    def test_preview_execution_only_opens_target_preview_jobs(self):
        identity = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        other = "bbbbbbbb-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        store = InMemoryJobStore()
        target = {**SNAPSHOT, "subscription_id": identity}
        preview = store.create_if_absent(new_job(target, "subscription_preview"))
        store.create_if_absent(new_job(target, "daily_briefing"))
        store.create_if_absent(new_job({**target, "subscription_id": other}, "subscription_preview"))
        scoped = PreviewJobStore(store, identity)
        self.assertEqual([job.job_id for job in scoped.open_jobs()], [preview.job_id])
        with self.assertRaises(ValueError):
            scoped.create_if_absent(new_job(target, "daily_briefing"))
        gateway = Mock()
        gateway.list_preview_subscriptions.return_value = [target]
        wrapped = PreviewGateway(gateway, identity)
        self.assertEqual(wrapped.list_due_subscriptions(NOW), [])
        self.assertEqual(wrapped.list_expired_subscriptions(NOW), [])
        self.assertEqual(wrapped.list_preview_subscriptions(NOW), [target])
        gateway.list_preview_subscriptions.assert_called_once_with(NOW, subscription_id=identity)
        gateway.list_preview_subscriptions.return_value = [{**target, "subscription_id": other}]
        with self.assertRaises(ValueError):
            wrapped.list_preview_subscriptions(NOW)

    def test_preview_deadline_is_24_hours_and_identity_does_not_collide_with_daily(self):
        snapshot = deepcopy(SNAPSHOT)
        snapshot["deadline_at"] = (NOW + timedelta(days=3)).isoformat()
        preview = new_job(snapshot, "subscription_preview")
        regular = new_job(snapshot, "daily_briefing")
        self.assertEqual(preview.deadline_at - preview.scheduled_at, timedelta(hours=24))
        self.assertNotEqual(preview.job_id, regular.job_id)
