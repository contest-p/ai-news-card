"""Engine API auth, schedule, snapshots and token idempotency without real DB."""
from copy import deepcopy
from datetime import date, timedelta
import os
from threading import RLock
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.engine_api import EngineService, SubscriptionSettings, create_router, instant


class Doc:
    def __init__(self, ref, data):
        self.reference, self.id, self.data = ref, ref.path.rsplit("/",1)[-1], deepcopy(data)
        self.exists = data is not None
    def to_dict(self):
        return deepcopy(self.data)


class Ref:
    def __init__(self, db, path): self.db, self.path = db, path
    def get(self, **kwargs): return Doc(self, self.db.data.get(self.path))
    def collection(self, name): return Query(self.db, self.path+"/"+name)
    def delete(self, **kwargs): self.db.data.pop(self.path, None)
    def collections(self, **kwargs):
        prefix = self.path + "/"
        names = {p[len(prefix):].split("/")[0] for p in self.db.data if p.startswith(prefix)}
        return iter([self.collection(name) for name in names])
    def set(self, data, merge=False):
        self.db.data[self.path] = {**self.db.data.get(self.path,{}), **deepcopy(data)} if merge else deepcopy(data)


class Query:
    def __init__(self, db, path, filters=(), limit=None):
        self.db, self.path, self.filters, self.count = db, path, filters, limit
        self.id = path.rsplit("/", 1)[-1]
    def document(self, key): return Ref(self.db, self.path+"/"+key)
    def list_documents(self, **kwargs):
        prefix = self.path + "/"
        names = {p[len(prefix):].split("/")[0] for p in self.db.data if p.startswith(prefix)}
        return iter([self.document(name) for name in names])
    def where(self, *, filter): return Query(self.db, self.path, self.filters+(filter,), self.count)
    def limit(self, count): return Query(self.db, self.path, self.filters, count)
    def stream(self, **kwargs):
        return iter([Doc(Ref(self.db,path), data) for path,data in self.db.data.items()
            if path.rsplit("/",1)[0] == self.path and all(
                (data.get(f.field_path)==f.value if f.op_string=="==" else (data.get(f.field_path) is not None and data[f.field_path] <= f.value) if f.op_string=="<=" else data.get(f.field_path) in f.value)
                for f in self.filters)][:self.count])


class Tx:
    def __init__(self, db): self.db, self.writes = db, []
    def set(self, ref, data): self.writes.append((ref.path, deepcopy(data)))
    def create(self, ref, data):
        if ref.path in self.db.data: raise ValueError("EXISTS")
        self.writes.append((ref.path, deepcopy(data)))
    def delete(self, ref): self.writes.append((ref.path, None))
    def commit(self):
        for path, data in self.writes:
            if data is None:
                self.db.data.pop(path, None)
            else:
                self.db.data[path] = data


class DB:
    def __init__(self): self.data, self.lock = {}, RLock()
    def collection(self, name): return Query(self, name)
    def transaction(self): return Tx(self)
    def recursive_delete(self, ref):
        for path in list(self.data):
            if path == ref.path or path.startswith(ref.path + "/"):
                del self.data[path]


def transactional(callback):
    def run(tx):
        with tx.db.lock:
            value = callback(tx)
            tx.commit()
            return value
    return run


class EngineApiTests(unittest.TestCase):
    def setUp(self):
        self.db = DB()
        self.now = instant(date(2026,10,8), 9)+timedelta(minutes=7)
        self.data = {"uid":"fixture-user", "email":"fixture@example.com", "status":"active",
            "categories":["economy"], "keywords":[], "delivery_hour_kst":9, "duration_days":7,
            "consent_version":"v1", "start_date":"2026-10-08", "end_date_exclusive":"2026-10-15"}
        self.db.data["subscriptions/fixture-user"] = deepcopy(self.data)
        self.db.data["users/fixture-user"] = {"uid":"fixture-user"}
        self.service = EngineService(self.db, clock=lambda:self.now)
        self.patch = patch("google.cloud.firestore.transactional", side_effect=transactional)
        self.patch.start(); self.addCleanup(self.patch.stop)
        main_transactions = patch("firebase_admin.firestore.transactional", side_effect=transactional)
        main_transactions.start(); self.addCleanup(main_transactions.stop)
        self.env = patch.dict(os.environ, {"ENGINE_API_TOKEN":"a"*48,"FEEDBACK_TOKEN_SECRET":"b"*48,"PUBLIC_CATEGORIES":"economy,it_science,politics,society,world,culture"})
        self.env.start(); self.addCleanup(self.env.stop)
        app = FastAPI(); app.include_router(create_router(lambda:self.db))
        self.client = TestClient(app)
        self.headers={"Authorization":"Bearer "+"a"*48}

    def test_user_token_and_missing_auth_cannot_access_engine(self):
        path="/api/v1/engine/subscriptions/due"
        self.assertEqual(self.client.get(path).status_code,401)
        self.assertEqual(self.client.get(path,headers={"Authorization":"Bearer firebase-user-token"}).status_code,401)

    def test_due_endpoint_matches_engine_contract_and_persists_snapshot(self):
        response=self.client.get("/api/v1/engine/subscriptions/due",params={"now":self.now.isoformat()},headers=self.headers)
        self.assertEqual(response.status_code,200)
        row=response.json()["subscriptions"][0]
        self.assertEqual(row["recipient_email"],self.data["email"])
        self.assertEqual(row["deadline_at"],instant(date(2026,10,8),12).isoformat())
        self.assertTrue(any("engine_snapshots" in p for p in self.db.data))

    def test_not_due_before_schedule_or_at_deadline(self):
        for now in (instant(date(2026,10,8),8),instant(date(2026,10,8),12)):
            self.assertEqual(self.service.list_snapshots(now)["subscriptions"],[])

    def test_missing_settings_are_excluded_and_reported(self):
        del self.db.data["subscriptions/fixture-user"]["keywords"]
        result=self.service.list_snapshots(self.now)
        # keywords has an explicit empty-list default; categories is mandatory.
        del self.db.data["subscriptions/fixture-user"]["categories"]
        result=self.service.list_snapshots(self.now)
        self.assertEqual(result["subscriptions"],[])
        self.assertTrue(result["skipped_counts"])

    def test_snapshot_frozen_but_cancellation_checked_live(self):
        first=self.service.snapshot("fixture-user",date(2026,10,8))
        self.db.data["subscriptions/fixture-user"]["categories"]=["world"]
        self.assertEqual(self.service.snapshot("fixture-user",date(2026,10,8)),first)
        self.db.data["subscriptions/fixture-user"]["status"]="cancelled"
        self.assertEqual(self.service.eligibility("fixture-user",self.now)["reason"],"cancelled")

    def test_expiry_window_and_deletion(self):
        now=instant(date(2026,10,15))+timedelta(hours=1)
        self.assertEqual(self.service.eligibility("fixture-user",now)["reason"],"expired")
        self.assertEqual(len(self.service.list_snapshots(now,expired=True)["subscriptions"]),1)
        self.assertEqual(self.service.list_snapshots(now+timedelta(days=1),expired=True)["subscriptions"],[])
        self.db.data["users/fixture-user"]["deletion_requested_at"]=self.now
        self.assertEqual(self.service.eligibility("fixture-user",self.now)["reason"],"deletion_requested")
        self.assertEqual(self.service.list_snapshots(now,expired=True)["subscriptions"],[])

    def test_naive_time_rejected(self):
        result=self.client.get("/api/v1/engine/subscriptions/due",params={"now":"2026-10-08T09:00:00"},headers=self.headers)
        self.assertEqual(result.status_code,422)

    def test_feedback_idempotent_hash_only_and_expiry(self):
        job_id="c"*64
        self.db.data["engine_delivery_jobs/"+job_id]={"user_id":"fixture-user","subscription_id":"fixture-user",
            "mail_kind":"daily_briefing","scheduled_date_kst":"2026-10-08","status":"processing","selected_article_id":"fixture-article"}
        first=self.service.feedback_token(job_id,job_id)
        self.assertEqual(self.service.feedback_token(job_id,job_id),first)
        doc=self.db.data["feedback_tokens/"+job_id]
        self.assertNotIn(first["token"],str(doc))
        self.service.clock=lambda:self.now+timedelta(days=31)
        with self.assertRaises(HTTPException) as error:self.service.feedback_token(job_id,job_id)
        self.assertEqual(error.exception.status_code,410)

    def test_feedback_api_selects_environment_and_rejects_unknown_values(self):
        job_id = "b"*64
        job = {"user_id":"fixture-user", "subscription_id":"fixture-user", "mail_kind":"daily_briefing",
               "scheduled_date_kst":"2026-10-08", "status":"processing", "selected_article_id":"article"}
        for name in ("engine_delivery_jobs", "engine_test_delivery_jobs"):
            self.db.data[name+"/"+job_id] = deepcopy(job)
        headers = {**self.headers, "Idempotency-Key":job_id}
        # The API clock uses real UTC; pin it to the fixture day for deterministic expiry.
        with patch("backend.engine_api.EngineService", return_value=self.service):
            prod = self.client.post("/api/v1/engine/feedback-tokens", headers=headers, json={"job_id":job_id})
            isolated = self.client.post("/api/v1/engine/feedback-tokens", headers=headers, json={"job_id":job_id,"environment":"test"})
        self.assertEqual(prod.status_code,200,prod.text)
        self.assertEqual(isolated.status_code,200,isolated.text)
        self.assertNotEqual(prod.json()["token"],isolated.json()["token"])
        self.assertEqual(self.client.post("/api/v1/engine/feedback-tokens", headers=headers,
            json={"job_id":job_id,"environment":"invalid"}).status_code,422)

    def test_token_cannot_be_issued_without_job_or_wrong_idempotency(self):
        for key,status in (("wrong",409),("d"*64,404)):
            with self.assertRaises(HTTPException) as error:self.service.feedback_token("d"*64,key)
            self.assertEqual(error.exception.status_code,status)

    def test_resubscription_old_identity_becomes_unavailable(self):
        first = self.service.list_snapshots(self.now)["subscriptions"][0]
        self.assertEqual(first["subscription_id"], "fixture-user")
        self.db.data["subscriptions/fixture-user"]["subscription_id"]="new-subscription"
        self.assertEqual(self.service.eligibility("fixture-user",self.now)["reason"],"not_found")
        self.assertTrue(self.service.eligibility("new-subscription",self.now)["eligible"])
        self.assertEqual(self.service.list_snapshots(self.now)["subscriptions"][0]["subscription_id"],"new-subscription")

    def test_due_query_only_freezes_the_requested_test_subscription(self):
        self.db.data["subscriptions/fixture-user"]["subscription_id"] = "chosen-test"
        self.db.data["subscriptions/another-user"] = {**self.data, "uid": "another-user",
                                                  "subscription_id": "another-test"}
        self.db.data["users/another-user"] = {"uid": "another-user"}
        response = self.client.get("/api/v1/engine/subscriptions/due", params={
            "now": self.now.isoformat(), "subscription_id": "chosen-test"}, headers=self.headers)
        self.assertEqual([r["subscription_id"] for r in response.json()["subscriptions"]], ["chosen-test"])
        self.assertFalse(any(path.startswith("subscriptions/another-user/engine_snapshots/") for path in self.db.data))

    def test_deleted_user_cannot_get_new_feedback_token(self):
        job_id="e"*64
        self.db.data["engine_delivery_jobs/"+job_id]={"user_id":"fixture-user","subscription_id":"fixture-user",
            "mail_kind":"daily_briefing","scheduled_date_kst":"2026-10-08","status":"processing","selected_article_id":"fixture-article"}
        self.db.data["users/fixture-user"]["deletion_requested_at"]=self.now
        with self.assertRaises(HTTPException) as error:self.service.feedback_token(job_id,job_id)
        self.assertEqual(error.exception.status_code,410)
        self.assertNotIn("feedback_tokens/"+job_id,self.db.data)

    def test_save_route_keeps_legacy_requests_and_adds_valid_engine_settings(self):
        import importlib
        with patch("firebase_admin._apps", {"test":object()}), patch("firebase_admin.firestore.client", return_value=self.db):
            main=importlib.import_module("backend.main")
        main.db=self.db
        main.app.dependency_overrides[main.get_current_user]=lambda:{"uid":"new-user","email":"new@example.com","name":"가상 사용자"}
        self.addCleanup(main.app.dependency_overrides.clear)
        client=TestClient(main.app)
        self.assertEqual(client.post("/subscriptions/save",json={"plan":"basic"}).status_code,422)
        settings={"categories":["economy"],"keywords":[],"delivery_hour_kst":9,"duration_days":7,"consent_version":"v1"}
        response=client.post("/subscriptions/save",json={"plan":"basic","engine_settings":settings})
        self.assertEqual(response.status_code,200)
        saved=response.json()["subscription"]
        self.assertEqual((date.fromisoformat(saved["end_date_exclusive"])-date.fromisoformat(saved["start_date"])).days,7)
        self.assertTrue(saved["subscription_id"])
        self.assertEqual(client.post("/subscriptions/save",json={"plan":"basic","engine_settings":settings}).status_code,409)
        self.assertEqual(client.post("/subscriptions/save",json={"plan":"basic","engine_settings":{**settings,"duration_days":8}}).status_code,422)


if __name__=="__main__":unittest.main()
