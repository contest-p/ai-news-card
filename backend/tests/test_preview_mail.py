"""신청 당일 미리보기, 중복 dispatch 및 장애 시 구독 보존."""

from copy import deepcopy
from datetime import date, timedelta
import json
import os
import unittest
from unittest.mock import Mock, MagicMock, patch
from fastapi import HTTPException

from backend.engine_api import EngineService, instant
from backend.preview_mail import dispatch_preview
from backend.tests.test_engine_api import DB, transactional


class PreviewMailTests(unittest.TestCase):
    def setUp(self):
        self.now = instant(date(2026, 10, 9), 11)
        self.db = DB()
        self.identity = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        settings = {"categories": ["it_science"], "keywords": [], "delivery_hour_kst": 8,
                    "duration_days": 7, "consent_version": "v1"}
        self.data = {**settings, "subscription_id": self.identity, "uid": "user", "email": "user@example.com",
                     "status": "active", "start_date": "2026-10-10", "end_date_exclusive": "2026-10-17",
                     "preview_requested_at": self.now, "preview_settings": settings,
                     "preview_dispatch_status": "pending"}
        self.db.data["subscriptions/user"] = deepcopy(self.data)
        self.db.data["users/user"] = {"uid": "user"}
        self.ref = self.db.collection("subscriptions").document("user")
        self.service = EngineService(self.db)
        p = patch("backend.preview_mail.firestore.transactional", transactional)
        p.start(); self.addCleanup(p.stop)

    def test_preview_is_eligible_before_first_regular_day_and_preserves_settings(self):
        self.assertFalse(self.service.eligibility(self.identity, self.now)["eligible"])
        self.assertTrue(self.service.eligibility(self.identity, self.now, preview=True)["eligible"])
        self.assertEqual(self.service.list_snapshots(self.now)["subscriptions"], [])
        row = self.service.list_previews(self.now, subscription_id=self.identity)["subscriptions"][0]
        self.assertEqual(row["categories"], ["it_science"])
        self.assertEqual(row["start_date"], "2026-10-10")
        self.assertEqual(row["scheduled_date_kst"], "2026-10-09")
        self.assertEqual(self.service.list_previews(self.now, subscription_id="other")["subscriptions"], [])

    def test_preview_expires_and_cancelled_or_deleted_accounts_never_send(self):
        self.assertEqual(self.service.list_previews(self.now + timedelta(hours=24))["subscriptions"], [])
        self.db.data["subscriptions/user"]["status"] = "cancelled"
        with self.assertRaises(HTTPException):
            self.service.preview_snapshot(self.identity, self.now)
        self.db.data["subscriptions/user"]["status"] = "active"
        self.db.data["users/user"]["deletion_requested_at"] = self.now
        self.assertEqual(self.service.list_previews(self.now)["subscriptions"], [])

    def test_dispatch_is_targeted_and_idempotent(self):
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.status = 204
        with patch.dict(os.environ, {"GITHUB_PREVIEW_DISPATCH_TOKEN": "fixture-token"}):
            first = dispatch_preview(self.db, self.ref, self.identity, opener=opener, clock=lambda: self.now)
            second = dispatch_preview(self.db, self.ref, self.identity, opener=opener, clock=lambda: self.now)
        self.assertEqual((first, second), ("accepted", "accepted"))
        self.assertEqual(opener.open.call_count, 1)
        request = opener.open.call_args.args[0]
        inputs = json.loads(request.data)["inputs"]
        self.assertEqual(inputs, {"mode": "run", "delivery_at": "", "preview_subscription_id": self.identity})
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 8)

    def test_dispatch_failure_keeps_subscription_and_preview_for_recovery(self):
        opener = Mock()
        opener.open.side_effect = OSError("private-details")
        with patch.dict(os.environ, {"GITHUB_PREVIEW_DISPATCH_TOKEN": "fixture-token"}):
            state = dispatch_preview(self.db, self.ref, self.identity, opener=opener, clock=lambda: self.now)
        self.assertEqual(state, "failed")
        self.assertEqual(self.db.data["subscriptions/user"]["status"], "active")
        self.assertEqual(len(self.service.list_previews(self.now)["subscriptions"]), 1)

    def test_missing_configuration_has_no_network_or_db_writes(self):
        before = deepcopy(self.db.data)
        with patch.dict(os.environ, {"GITHUB_PREVIEW_DISPATCH_TOKEN": ""}):
            self.assertEqual(dispatch_preview(self.db, self.ref, self.identity), "not_configured")
        self.assertEqual(self.db.data, before)
