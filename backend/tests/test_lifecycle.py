"""PRD lifecycle regressions: stale identities, KST versions, token visits, deletion retries."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import os
from unittest.mock import patch

from fastapi import HTTPException
from backend.engine_api import EngineService, instant
from backend.privacy import PrivacyService
from backend.user_api import FeedbackBody, FeedbackService
import unittest
from backend.tests import test_frontend_api as frontend_tests
from backend.tests.test_engine_api import transactional


class LifecycleTests(unittest.TestCase):
    setUp = frontend_tests.FrontendApiTests.setUp
    def subscribe(self):
        body = {"categories": ["economy"], "keywords": ["ＡＩ"], "delivery_hour_kst": 9,
                "duration_days": 7, "consent_version": "v1"}
        response = self.client.post("/api/v1/subscriptions", headers=self.headers, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["subscription"]

    def cancel(self, subscription):
        return self.client.post("/api/v1/subscriptions/" + subscription["subscription_id"] + "/cancel",
                                headers=self.headers, json={"confirm": True})

    def test_missing_settings_and_unconfirmed_cancel_are_rejected(self):
        self.assertEqual(self.client.post("/subscriptions/save", headers=self.headers, json={"plan":"basic"}).status_code, 422)
        saved = self.subscribe()
        self.assertEqual(saved["keywords"], ["AI"])
        self.assertIn("consent_at", saved)
        self.assertEqual(self.client.patch("/subscriptions/cancel", headers=self.headers).status_code, 422)
        self.assertEqual(self.client.post("/subscriptions/"+saved["subscription_id"]+"/cancel", headers=self.headers,
                                        json={"confirm":False}).status_code, 422)
        self.assertEqual(self.db.data["subscriptions/test-user"]["status"], "active")

    def test_old_cancel_cannot_cancel_renewed_subscription(self):
        first = self.subscribe()
        self.assertEqual(self.cancel(first).status_code, 200)
        second = self.subscribe()
        self.assertEqual(self.cancel(first).status_code, 404)
        self.assertEqual(self.db.data["subscriptions/test-user"]["subscription_id"], second["subscription_id"])
        self.assertEqual(self.db.data["subscriptions/test-user"]["status"], "active")
        history = self.db.data["subscription_history/" + first["subscription_id"]]
        self.assertEqual(history["status"], "cancelled")
        self.assertIn("cleanup_after", history)
        self.assertFalse(EngineService(self.db).eligibility(first["subscription_id"], datetime.now(timezone.utc))["eligible"])

    def test_settings_apply_tomorrow_and_period_stays_fixed(self):
        first = self.subscribe()
        identity = first["subscription_id"]
        path = "/subscriptions/" + identity + "/settings"
        body = {"categories":["world"], "keywords":[" ＡＩ "], "delivery_hour_kst":18, "expected_settings_version":1}
        # Initial subscribe starts tomorrow; changes before first mail use tomorrow's new settings.
        result = self.client.patch(path, headers=self.headers, json=body)
        self.assertEqual(result.status_code, 200, result.text)
        next_settings = result.json()["subscription"]["next_settings"]
        self.assertEqual((next_settings["settings_version"], next_settings["keywords"]), (2, ["AI"]))
        self.assertEqual(result.json()["subscription"]["end_date_exclusive"], first["end_date_exclusive"])
        self.assertEqual(self.client.patch(path, headers=self.headers, json=body).status_code, 409)
        self.assertEqual(self.client.patch(path, headers=self.headers, json={**body,"duration_days":28}).status_code, 422)
        service = EngineService(self.db)
        day = date.fromisoformat(first["start_date"])
        early = service.list_snapshots(instant(day,9)+timedelta(minutes=1))
        self.assertEqual(early["subscriptions"], [])
        due = service.list_snapshots(instant(day,18)+timedelta(minutes=1))["subscriptions"][0]
        self.assertEqual((due["categories"], due["settings_version"]), (["world"],2))
        self.db.data["subscriptions/test-user"]["settings_versions"][-1]["keywords"] = ["changed"]
        self.assertEqual(service.snapshot(identity,day), due)

    def test_public_categories_fail_closed_and_tokens_do_not_leak_in_errors(self):
        with patch.dict(os.environ, {"PUBLIC_CATEGORIES":""}):
            self.assertEqual(self.client.get("/catalog").json()["categories"], [])
            body = {"engine_settings":{"categories":["economy"],"keywords":[],"delivery_hour_kst":9,
                                        "duration_days":7,"consent_version":"v1"}}
            self.assertEqual(self.client.post("/subscriptions/save",headers=self.headers,json=body).status_code,422)
        secret = "sensitive_token_fixture_with_spaces"
        response = self.client.post("/feedback",json={"token":secret,"rating":"unsupported"})
        self.assertEqual(response.status_code,422)
        self.assertNotIn(secret,response.text)
        self.assertEqual(response.headers["cache-control"],"no-store")

    def seed_feedback(self, now):
        job_id = "c" * 64
        self.db.data["users/test-user"] = {"uid":"test-user"}
        self.db.data["engine_delivery_jobs/"+job_id] = {"user_id":"test-user","subscription_id":"old-sub",
            "mail_kind":"daily_briefing","status":"processing","selected_article_id":"article",
            "scheduled_date_kst":now.astimezone(timezone(timedelta(hours=9))).date().isoformat()}
        with patch.dict(os.environ, {"FEEDBACK_TOKEN_SECRET":"b"*48}):
            token = EngineService(self.db,clock=lambda:now).feedback_token(job_id,job_id)["token"]
        return job_id,token

    def test_feedback_visit_is_read_only_submission_is_single_updatable_record(self):
        now = datetime.now(timezone.utc)
        job_id, token = self.seed_feedback(now)
        before = deepcopy(self.db.data)
        response = self.client.post("/api/v1/feedback/resolve",json={"token":token})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers["cache-control"],"no-store")
        self.assertIsNone(response.json()["current_rating"])
        self.assertEqual(self.db.data,before)
        for rating in ("up","down"):
            response = self.client.post("/feedback",json={"token":token,"rating":rating,"reasons":["other"],"comment":"내용 확인"})
            self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(len([p for p in self.db.data if p.startswith("feedbacks/")]),1)
        self.assertEqual(self.db.data["feedbacks/"+job_id]["rating"],"down")
        self.assertNotIn(token,str(self.db.data))
        self.assertNotIn("email", self.db.data["feedbacks/"+job_id])
        expiry = self.db.data["feedback_tokens/"+job_id]["expires_at"]
        expected = instant(now.astimezone(timezone(timedelta(hours=9))).date()+timedelta(days=30))
        self.assertEqual(expiry,expected)
        service=FeedbackService(self.db,clock=lambda:expiry)
        with self.assertRaises(HTTPException) as error: service.resolve(token)
        self.assertEqual(error.exception.status_code,410)

    def test_expired_job_cannot_mint_new_feedback_token(self):
        now = datetime.now(timezone.utc)
        job_id,_ = self.seed_feedback(now-timedelta(days=31))
        del self.db.data["feedback_tokens/"+job_id]
        with patch.dict(os.environ,{"FEEDBACK_TOKEN_SECRET":"b"*48}), self.assertRaises(HTTPException) as error:
            EngineService(self.db,clock=lambda:now).feedback_token(job_id,job_id)
        self.assertEqual(error.exception.status_code,410)

    def test_deletion_stops_sends_and_retries_auth_failure_without_restoring_data(self):
        saved = self.subscribe()
        now = datetime.now(timezone.utc)
        job_id,token = self.seed_feedback(now)
        archive = "engine_mail_archives/"+job_id
        self.db.data[archive]={"chunks":1}
        self.db.data[archive+"/chunks/0"]={"payload":b"PII"}
        request = self.client.post("/account-deletion-requests",headers=self.headers,json={"confirm":True})
        self.assertEqual(request.status_code,202)
        self.assertTrue(request.json()["sending_stopped"])
        again = self.client.post("/account-deletion-requests",headers=self.headers,json={"confirm":True})
        self.assertEqual(again.json()["request_id"],request.json()["request_id"])
        self.assertEqual(self.client.get("/account-deletion-requests/"+request.json()["request_id"],headers={"Authorization":"Bearer other-user"}).status_code,404)
        self.assertEqual(EngineService(self.db).eligibility(saved["subscription_id"],now)["reason"],"deletion_requested")
        self.assertEqual(self.client.post("/users/sync",headers=self.headers).status_code,410)
        self.assertEqual(self.client.post("/feedback/resolve",json={"token":token}).status_code,410)
        with self.assertRaises(RuntimeError):
            PrivacyService(self.db, delete_auth=lambda uid: (_ for _ in ()).throw(RuntimeError("fixture failure"))).cleanup(now)
        self.assertEqual(self.db.data["users/test-user"]["deletion_status"],"pending")
        self.assertFalse(any(p.startswith(archive) for p in self.db.data))
        deleted_users=[]
        result=PrivacyService(self.db,delete_auth=deleted_users.append).cleanup(now)
        self.assertEqual(result["status"],"completed")
        self.assertEqual(deleted_users,["test-user"])
        self.assertNotIn("users/test-user", self.db.data)
        self.assertTrue(any(p.startswith("privacy_deletions/") for p in self.db.data))
        self.assertEqual(self.client.post("/users/sync",headers=self.headers).status_code,410)
        self.assertEqual(PrivacyService(self.db,delete_auth=deleted_users.append).cleanup(now)["status"],"completed")
        self.assertEqual(len(deleted_users),1)

    def test_old_retention_cleanup_preserves_active_resubscription(self):
        first=self.subscribe()
        self.cancel(first)
        second=self.subscribe()
        old = self.db.data["subscription_history/"+first["subscription_id"]]
        now=datetime.now(timezone.utc)
        old["cleanup_after"]=now-timedelta(seconds=1)
        parent="subscriptions/test-user/engine_snapshots/"
        self.db.data[parent+"old"]={"subscription_id":first["subscription_id"],"recipient_email":"old@example.invalid"}
        self.db.data[parent+"new"]={"subscription_id":second["subscription_id"]}
        self.db.data["feedback_tokens/old"]={"subscription_id":first["subscription_id"],"expires_at":now+timedelta(days=2)}
        result=PrivacyService(self.db,delete_auth=lambda uid:self.fail("active user Auth deletion")).cleanup(now)
        self.assertEqual(result["status"],"completed")
        self.assertNotIn(parent+"old",self.db.data)
        self.assertIn(parent+"new",self.db.data)
        self.assertIn("users/test-user",self.db.data)
        self.assertIn("subscriptions/test-user",self.db.data)
        self.assertNotIn("feedback_tokens/old",self.db.data)

    def test_cleanup_bounds_backlog_and_makes_progress(self):
        now=datetime.now(timezone.utc)
        for i in range(3):
            self.db.data["feedback_tokens/"+str(i)]={"expires_at":now-timedelta(seconds=1)}
        cleanup=PrivacyService(self.db,limit=1)
        for _ in range(2):
            with self.assertRaises(HTTPException) as error: cleanup.cleanup(now)
            self.assertEqual(error.exception.status_code,503)
        self.assertEqual(cleanup.cleanup(now)["status"],"completed")
        self.assertFalse(any(p.startswith("feedback_tokens/") for p in self.db.data))

    def test_cleanup_api_requires_engine_auth_and_rejects_fake_time(self):
        with patch.dict(os.environ, {"ENGINE_API_TOKEN":"x"*48}):
            path="/api/v1/engine/privacy-cleanup"
            now=datetime.now(timezone.utc)
            self.assertEqual(self.client.post(path,json={"now":now.isoformat()}).status_code,401)
            self.assertEqual(self.client.post(path,headers=self.headers,json={"now":now.isoformat()}).status_code,401)
            headers={"Authorization":"Bearer "+"x"*48}
            self.assertEqual(self.client.post(path,headers=headers,json={"now":(now+timedelta(days=31)).isoformat()}).status_code,422)
            self.assertEqual(self.client.post(path,headers=headers,json={"now":now.isoformat()}).json()["status"],"completed")
            with patch("backend.privacy.PrivacyService.cleanup",side_effect=RuntimeError("private database detail")):
                response=self.client.post(path,headers=headers,json={"now":now.isoformat()})
                self.assertEqual(response.status_code,503)
                self.assertNotIn("private database detail",response.text)

    def test_retention_migration_uses_end_exclusive_and_never_fabricates_consent(self):
        from backend.tools.migrate_lifecycle import retention_fields
        data={"start_date":"2026-10-01","end_date_exclusive":"2026-10-08","email":"fixture@example.invalid"}
        result=retention_fields(data,"legacy")
        self.assertEqual(result["cleanup_after"],instant(date(2026,11,6)))
        self.assertEqual(result["subscription_id"],"legacy")
        self.assertNotIn("consent_at",result)
        self.assertNotIn("settings_versions",result)
        data["cleanup_after"]=result["cleanup_after"]
        data["subscription_id"]="legacy"
        self.assertEqual(retention_fields(data,"legacy"),{})

    def test_existing_today_snapshot_survives_multiple_tomorrow_changes(self):
        saved=self.subscribe()
        identity=saved["subscription_id"]
        today=datetime.now(timezone(timedelta(hours=9))).date()
        data=self.db.data["subscriptions/test-user"]
        start=today-timedelta(days=1)
        end=start+timedelta(days=7)
        data.update(start_date=start.isoformat(),end_date_exclusive=end.isoformat(),effective_date=start.isoformat())
        data["settings_versions"][0]["effective_date"]=start.isoformat()
        service=EngineService(self.db)
        original=service.snapshot(identity,today)
        path="/subscriptions/"+identity+"/settings"
        for version,hour in ((1,18),(2,20)):
            response=self.client.patch(path,headers=self.headers,json={"categories":["world"],"keywords":[],
                                       "delivery_hour_kst":hour,"expected_settings_version":version})
            self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(service.snapshot(identity,today),original)
        self.assertEqual(service.snapshot(identity,today+timedelta(days=1))["delivery_hour_kst"],20)
        self.assertEqual(service.snapshot(identity,today+timedelta(days=1))["settings_version"],3)
        self.assertEqual(len(data["settings_versions"]),1)  # original fixture copy remains untouched
        self.assertEqual(len(self.db.data["subscriptions/test-user"]["settings_versions"]),2)

    def test_natural_cleanup_rechecks_concurrent_subscription_replacement(self):
        saved=self.subscribe()
        now=datetime.now(timezone.utc)
        row=self.db.collection("subscriptions").document("test-user").get()
        self.db.data["subscriptions/test-user"]["subscription_id"]="concurrent-new-subscription"
        self.assertFalse(PrivacyService(self.db).expired_subscription(row,now))
        self.assertEqual(self.db.data["subscriptions/test-user"]["subscription_id"],"concurrent-new-subscription")

    def test_failed_child_delete_preserves_job_reference_for_retry(self):
        from backend.tests.test_engine_api import Ref
        now=datetime.now(timezone.utc)
        job_id,_=self.seed_feedback(now)
        archive="engine_mail_archives/"+job_id
        self.db.data[archive]={"chunks":1}
        child=archive+"/chunks/0"
        self.db.data[child]={"payload":b"PII"}
        original=Ref.delete
        def fail_child(ref, **kwargs):
            if ref.path == child:
                raise RuntimeError("fixture write failure")
            return original(ref, **kwargs)
        with patch.object(Ref,"delete",fail_child), self.assertRaises(RuntimeError):
            PrivacyService(self.db).related("user_id","test-user")
        self.assertIn("engine_delivery_jobs/"+job_id,self.db.data)
        self.assertIn(archive,self.db.data)
        self.assertIn(child,self.db.data)
        self.assertTrue(PrivacyService(self.db).related("user_id","test-user"))
        self.assertNotIn(child,self.db.data)
        self.assertNotIn("engine_delivery_jobs/"+job_id,self.db.data)


class ConnectionFixTests(unittest.TestCase):
    setUp = frontend_tests.FrontendApiTests.setUp
    subscribe = LifecycleTests.subscribe

    def test_legacy_document_settings_change_uses_original_reference(self):
        saved = self.subscribe()
        self.db.data["subscriptions/legacy-row"] = self.db.data.pop("subscriptions/test-user")
        for route in ("/subscriptions/me", "/subscriptions/current"):
            response = self.client.get(route, headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["subscription"]["subscription_id"], saved["subscription_id"])
        body = {"categories": ["world"], "keywords": [], "delivery_hour_kst": 18, "expected_settings_version": 1}
        response = self.client.patch("/subscriptions/"+saved["subscription_id"]+"/settings", headers=self.headers, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.db.data["subscriptions/legacy-row"]["settings_version"], 2)
        self.assertNotIn("subscriptions/test-user", self.db.data)
        self.db.data["subscriptions/legacy-row"]["uid"] = "other-user"
        self.assertEqual(self.client.patch("/subscriptions/"+saved["subscription_id"]+"/settings", headers=self.headers, json=body).status_code, 404)

    def test_legacy_document_id_fallback_and_ambiguous_identity(self):
        self.subscribe()
        data = self.db.data.pop("subscriptions/test-user")
        data.pop("subscription_id")
        self.db.data["subscriptions/legacy-row"] = data
        body = {"categories": ["world"], "keywords": [], "delivery_hour_kst": 18, "expected_settings_version": 1}
        response = self.client.patch("/subscriptions/legacy-row/settings", headers=self.headers, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.db.data["subscriptions/duplicate"] = deepcopy(self.db.data["subscriptions/legacy-row"])
        self.assertEqual(self.client.patch("/subscriptions/legacy-row/settings", headers=self.headers, json={**body,"expected_settings_version":2}).status_code, 409)

    def test_test_and_production_jobs_have_separate_tokens_and_feedback(self):
        saved = self.subscribe()
        job_id = "f" * 64
        now = datetime.now(timezone.utc)
        job = {"user_id":"test-user", "subscription_id":saved["subscription_id"], "mail_kind":"daily_briefing",
               "scheduled_date_kst":now.astimezone(timezone(timedelta(hours=9))).date().isoformat(),
               "status":"processing", "selected_article_id":"article"}
        for name in ("engine_delivery_jobs", "engine_test_delivery_jobs"):
            self.db.data[name+"/"+job_id] = deepcopy(job)
        with patch.dict(os.environ, {"FEEDBACK_TOKEN_SECRET":"fixture-secret-"*4}):
            service = EngineService(self.db)
            production = service.feedback_token(job_id,job_id)["token"]
            isolated = service.feedback_token(job_id,job_id,environment="test")["token"]
            self.assertNotEqual(production, isolated)
            self.assertEqual(service.feedback_token(job_id,job_id)["token"],production)
            feedback = FeedbackService(self.db)
            feedback.submit(FeedbackBody(token=production,rating="up"))
            feedback.submit(FeedbackBody(token=isolated,rating="down"))
            self.assertEqual(feedback.resolve(production)["current_rating"],"up")
            self.assertEqual(feedback.resolve(isolated)["current_rating"],"down")
            self.assertEqual(self.db.data["feedbacks/test-"+job_id]["environment"],"test")
            del self.db.data["engine_delivery_jobs/"+job_id]
            with self.assertRaises(HTTPException) as raised:
                service.feedback_token(job_id,job_id)
            self.assertEqual(raised.exception.status_code,404)

if __name__ == "__main__":
    unittest.main()
