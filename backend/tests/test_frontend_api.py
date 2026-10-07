"""Browser API flow and ownership checks with an isolated in-memory DB."""
import importlib
from datetime import date, datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.tests.test_engine_api import DB, transactional
from backend.engine_api import KST, EngineService, instant


class FrontendApiTests(unittest.TestCase):
    def setUp(self):
        self.db = DB()
        with patch("firebase_admin._apps", {"test": object()}), patch("firebase_admin.firestore.client", return_value=self.db):
            self.main = importlib.import_module("backend.main")
        self.main.db = self.db
        self.main.app.dependency_overrides.clear()
        self.client = TestClient(self.main.app)
        self.addCleanup(self.main.app.dependency_overrides.clear)
        self.verify = patch("backend.main.auth.verify_id_token", side_effect=lambda token: {
            "uid": token, "email": token + "@example.invalid", "name": "테스트 사용자"})
        self.verify.start()
        self.addCleanup(self.verify.stop)
        self.headers = {"Authorization": "Bearer test-user"}

    def test_catalog_is_public_and_missing_subscription_is_404(self):
        catalog = self.client.get("/catalog")
        self.assertEqual(catalog.status_code, 200)
        self.assertEqual(len(catalog.json()["categories"]), 6)
        self.assertFalse(catalog.json()["capabilities"]["settings_change"])
        self.assertEqual(self.client.get("/subscriptions/me").status_code, 401)
        self.assertEqual(self.client.get("/subscriptions/me", headers=self.headers).status_code, 404)

    def test_login_save_read_cancel_resubscribe_and_ownership(self):
        self.assertEqual(self.client.post("/users/sync", headers=self.headers).status_code, 200)
        settings = {"categories": ["economy", "it_science"], "keywords": ["AI"],
                    "delivery_hour_kst": 9, "duration_days": 14, "consent_version": "v1"}
        body = {"plan": "basic", "engine_settings": settings}
        saved = self.client.post("/subscriptions/save", headers=self.headers, json=body).json()["subscription"]
        today = datetime.now(KST).date()
        self.assertEqual(saved["start_date"], (today + timedelta(days=1)).isoformat())
        self.assertEqual(saved["end_date_exclusive"], (today + timedelta(days=15)).isoformat())
        self.assertEqual(saved["categories"], settings["categories"])
        read = self.client.get("/subscriptions/me", headers=self.headers).json()["subscription"]
        self.assertEqual(read["subscription_id"], saved["subscription_id"])
        with patch("backend.engine_api.firestore.transactional", transactional):
            due = EngineService(self.db).list_snapshots(instant(date.fromisoformat(saved["start_date"]), 9) + timedelta(minutes=1))
        self.assertEqual(due["subscriptions"][0]["subscription_id"], saved["subscription_id"])
        self.assertEqual(due["subscriptions"][0]["recipient_email"], "test-user@example.invalid")
        other = {"Authorization": "Bearer other-user"}
        self.assertEqual(self.client.get("/subscriptions/me", headers=other).status_code, 404)
        self.assertEqual(self.client.patch("/subscriptions/cancel", headers=other).status_code, 404)
        cancelled = self.client.patch("/subscriptions/cancel", headers=self.headers)
        self.assertEqual(cancelled.json()["subscription"]["status"], "cancelled")
        renewed = self.client.post("/subscriptions/save", headers=self.headers, json=body)
        self.assertEqual(renewed.status_code, 200)
        self.assertNotEqual(renewed.json()["subscription"]["subscription_id"], saved["subscription_id"])

    def test_naturally_expired_subscription_can_be_renewed(self):
        self.db.data["subscriptions/test-user"] = {"status": "active", "start_date": "2025-01-01",
            "end_date_exclusive": "2025-01-08", "expires_at": datetime(2025, 1, 8, tzinfo=timezone.utc)}
        read = self.client.get("/subscriptions/me", headers=self.headers)
        self.assertEqual(read.json()["subscription"]["status"], "expired")
        body = {"plan": "basic", "engine_settings": {"categories": ["economy"], "keywords": [],
            "delivery_hour_kst": 9, "duration_days": 7, "consent_version": "v1"}}
        self.assertEqual(self.client.post("/subscriptions/save", headers=self.headers, json=body).status_code, 200)

    def test_browser_cors_allows_bearer_and_patch(self):
        response = self.client.options("/subscriptions/cancel", headers={
            "Origin": "http://localhost:5500", "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "authorization,content-type,idempotency-key"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], "http://localhost:5500")

    def test_stale_consent_cannot_save_subscription(self):
        body = {"plan": "basic", "engine_settings": {"categories": ["economy"], "keywords": [],
            "delivery_hour_kst": 9, "duration_days": 7, "consent_version": "old-version"}}
        response = self.client.post("/subscriptions/save", headers=self.headers, json=body)
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("subscriptions/test-user", self.db.data)


if __name__ == "__main__":
    unittest.main()
