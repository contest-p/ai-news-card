"""Resumable server-only privacy cleanup; no success until data and Auth deletion finish."""
from datetime import datetime, timedelta, timezone
import hashlib
from google.cloud import firestore
from fastapi import HTTPException
from firebase_admin import auth
from google.cloud.firestore_v1.base_query import FieldFilter

try:
    from .engine_api import instant, utc
except ImportError:
    from engine_api import instant, utc


class PrivacyService:
    def __init__(self, db, delete_auth=None, limit=100):
        self.db, self.delete_auth, self.limit = db, delete_auth or auth.delete_user, limit
        self.delete_budget = 2000

    def rows(self, collection, field, value):
        return list(self.db.collection(collection).where(
            filter=FieldFilter(field, "==", value)).limit(self.limit + 1).stream(timeout=20))

    def erase(self, ref):
        # Delete children before their retry reference. BulkWriter may report partial
        # failures without raising; synchronous deletes must all succeed before parent removal.
        for collection in ref.collections(timeout=20):
            for child in collection.list_documents(page_size=100, timeout=20):
                self.erase(child)
        if self.delete_budget <= 0:
            raise HTTPException(503, "PRIVACY_CLEANUP_RETRY_REQUIRED")
        ref.delete(timeout=20)
        self.delete_budget -= 1

    def related(self, field, value):
        remaining = False
        for name in ("engine_delivery_jobs", "engine_test_delivery_jobs"):
            rows = self.rows(name, field, value)
            for row in rows[:self.limit]:
                prefix = "engine_test_" if name.startswith("engine_test_") else "engine_"
                for collection in (prefix + "mail_archives", prefix + "rag_results"):
                    self.erase(self.db.collection(collection).document(row.id))
                # Delete the job last; a failed archive deletion must retain the retry reference.
                self.erase(row.reference)
            remaining |= len(rows) > self.limit
        for name in ("feedback_tokens", "feedbacks"):
            rows = self.rows(name, field, value)
            for row in rows[:self.limit]:
                self.erase(row.reference)
            remaining |= len(rows) > self.limit
        return not remaining

    def delete_account(self, uid):
        # The users/{uid} deletion fence remains present throughout retries.
        if not self.related("user_id", uid):
            return False
        # Legacy feedback used uid instead of user_id.
        for row in self.rows("feedbacks", "uid", uid)[:self.limit]:
            self.erase(row.reference)
        remaining = False
        for name in ("subscriptions", "subscription_history", "engine_test_subscriptions"):
            rows = self.rows(name, "uid", uid)
            for row in rows[:self.limit]:
                # Legacy jobs may have lacked the user_id field.
                identity = row.to_dict().get("subscription_id", row.id)
                if not self.related("subscription_id", identity):
                    remaining = True
                    continue
                self.erase(row.reference)
            remaining |= len(rows) > self.limit
        if remaining or self.rows("feedbacks", "uid", uid):
            return False
        try:
            self.delete_auth(uid)
        except auth.UserNotFoundError:
            pass  # Auth may have succeeded before a Firestore failure in the previous attempt.
        @firestore.transactional
        def finish(tx):
            marker = self.db.collection("privacy_deletions").document(hashlib.sha256(uid.encode()).hexdigest())
            tx.set(marker, {"deleted_at": datetime.now(timezone.utc)})
            tx.delete(self.db.collection("users").document(uid))
        finish(self.db.transaction())
        return True

    def expired_subscription(self, row, now):
        data = row.to_dict()
        @firestore.transactional
        def claim(tx):
            current = row.reference.get(transaction=tx)
            if not current.exists or current.to_dict() != data:
                return False
            tx.set(row.reference, {**data, "status": "privacy_cleaning"})
            return True
        if not claim(self.db.transaction()):
            return False
        identity = data.get("subscription_id", row.id)
        if not self.related("subscription_id", identity):
            return False
        if row.reference.path.startswith("subscription_history/"):
            # Archived subscriptions' snapshots lived below users' current subscription document.
            parent = self.db.collection("subscriptions").document(data["uid"])
            query = parent.collection("engine_snapshots").where(filter=FieldFilter("subscription_id", "==", identity))
            snapshots = list(query.limit(self.limit + 1).stream(timeout=20))
            for snapshot in snapshots[:self.limit]:
                self.erase(snapshot.reference)
            if len(snapshots) > self.limit:
                return False
        self.erase(row.reference)
        uid = data.get("uid")
        if uid:
            @firestore.transactional
            def prune_user(tx):
                retained = []
                for name in ("subscriptions", "subscription_history"):
                    retained += list(self.db.collection(name).where(filter=FieldFilter("uid", "==", uid))
                                     .limit(1).stream(transaction=tx))
                ref = self.db.collection("users").document(uid)
                user = ref.get(transaction=tx)
                if not retained and user.exists and not user.to_dict().get("deletion_requested_at"):
                    tx.delete(ref)
            prune_user(self.db.transaction())
        return True

    def cleanup(self, now):
        now = utc(now)
        count, pending = 0, False
        requests = self.rows("users", "deletion_status", "pending")
        for row in requests[:self.limit]:
            if self.delete_account(row.id):
                count += 1
            else:
                pending = True
        pending |= len(requests) > self.limit
        for name in ("subscriptions", "subscription_history"):
            rows = list(self.db.collection(name).where(filter=FieldFilter("cleanup_after", "<=", now))
                        .limit(self.limit + 1).stream(timeout=20))
            for row in rows[:self.limit]:
                if self.expired_subscription(row, now):
                    count += 1
                else:
                    pending = True
            pending |= len(rows) > self.limit
        rows = list(self.db.collection("feedback_tokens").where(filter=FieldFilter("expires_at", "<=", now))
                    .limit(self.limit + 1).stream(timeout=20))
        for row in rows[:self.limit]:
            self.erase(row.reference)
        pending |= len(rows) > self.limit
        if pending:
            raise HTTPException(503, "PRIVACY_CLEANUP_RETRY_REQUIRED")
        return {"status": "completed", "deleted_records": count}
