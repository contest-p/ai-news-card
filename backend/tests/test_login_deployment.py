"""Deployment login checks without real accounts or Firestore writes."""
import importlib
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.tests.test_engine_api import DB


class LoginDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict("os.environ", {"FRONTEND_ORIGINS": "https://ai-news-card-frontend.vercel.app"}), \
             patch("firebase_admin._apps", {"test": object()}), \
             patch("firebase_admin.firestore.client", return_value=DB()):
            cls.main = importlib.reload(importlib.import_module("backend.main"))

    def setUp(self):
        self.main.db = DB()
        self.main.app.dependency_overrides.clear()
        self.client = TestClient(self.main.app)

    def test_login_sync_verifies_firebase_token_and_saves_its_owner(self):
        with patch.object(self.main.auth, "verify_id_token", return_value={
            "uid": "verified-user", "email": "test@example.invalid", "name": "Test"
        }) as verify:
            response = self.client.post("/users/sync", headers={"Authorization": "Bearer test-token"})
        self.assertEqual(response.status_code, 200)
        verify.assert_called_once_with("test-token")
        self.assertEqual(self.main.db.data["users/verified-user"]["uid"], "verified-user")

    def test_missing_or_invalid_token_cannot_save_user(self):
        self.assertEqual(self.client.post("/users/sync").status_code, 401)
        with patch.object(self.main.auth, "verify_id_token", side_effect=ValueError("Invalid token")):
            self.assertEqual(self.client.post("/users/sync", headers={"Authorization": "Bearer bad"}).status_code, 401)
        self.assertEqual(self.main.db.data, {})

    def test_vercel_origin_can_send_bearer_auth_but_other_origin_cannot(self):
        headers = {"Origin": "https://ai-news-card-frontend.vercel.app",
                   "Access-Control-Request-Method": "POST",
                   "Access-Control-Request-Headers": "authorization,content-type"}
        response = self.client.options("/users/sync", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], headers["Origin"])
        headers["Origin"] = "https://untrusted.example.invalid"
        response = self.client.options("/users/sync", headers=headers)
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_health_and_catalog_work_without_user_token(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
        self.assertEqual(self.client.get("/catalog").status_code, 200)
        self.assertEqual(self.client.get("/subscriptions/me").status_code, 401)
