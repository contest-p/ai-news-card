"""Verify real Firestore writes in isolated engine_test collections; no SMTP/AI."""
from datetime import datetime, timedelta
import hashlib
import json
import uuid

from backend.main import db
from backend.engine_api import EngineService, KST, instant
from google.cloud import firestore


def main():
    identity = "api-probe-"+uuid.uuid4().hex
    job_id = hashlib.sha256(identity.encode()).hexdigest()
    day = datetime.now(KST).date()
    now = instant(day, 9)+timedelta(minutes=7)
    user = db.collection("engine_test_users").document(identity)
    sub = db.collection("engine_test_subscriptions").document(identity)
    job = db.collection("engine_test_api_jobs").document(job_id)
    token = db.collection("engine_test_feedback_tokens").document(job_id)
    frozen = sub.collection("engine_snapshots").document(day.isoformat()+"-daily_briefing")
    seeded = False
    try:
        @firestore.transactional
        def seed(tx):
            tx.create(user, {"uid":identity})
            tx.create(sub, {"uid":identity,"email":"probe@example.invalid","status":"active",
                "categories":["economy"],"keywords":[],"delivery_hour_kst":9,"duration_days":7,
                "consent_version":"test-only","start_date":day.isoformat(),
                "end_date_exclusive":(day+timedelta(days=7)).isoformat()})
            tx.create(job, {"job_id":job_id,"user_id":identity,"subscription_id":identity,
                "mail_kind":"daily_briefing","scheduled_date_kst":day.isoformat(),"status":"processing","selected_article_id":"probe-only"})
        seed(db.transaction())
        seeded = True
        service = EngineService(db, subscriptions="engine_test_subscriptions", users="engine_test_users",
                                jobs=("engine_test_api_jobs",), tokens="engine_test_feedback_tokens", clock=lambda:now)
        rows = service.list_snapshots(now)["subscriptions"]
        snapshot = next(row for row in rows if row["subscription_id"] == identity)
        assert service.snapshot(identity,day) == snapshot
        assert service.eligibility(identity,now)["eligible"]
        first = service.feedback_token(job_id,job_id)
        assert service.feedback_token(job_id,job_id) == first
        stored = token.get().to_dict()
        assert stored["token_hash"] == hashlib.sha256(first["token"].encode()).hexdigest()
        assert first["token"] not in str(stored)
        report = {"real_firestore_verified":True,"snapshot_persisted":True,
                  "feedback_idempotency_verified":True,"token_hash_only":True,"mail_sent":False}
    finally:
        if seeded:
            # Exact documents created by this invocation only; chunks/other users untouched.
            for ref in (frozen, token, job, sub, user):
                ref.delete()
    report["test_documents_cleaned"] = True
    print(json.dumps(report))


if __name__=="__main__":main()
