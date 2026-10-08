"""Durable engine state. All external work stays outside transactions."""

from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import uuid

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from engine.delivery import (CLAIM_TTL, DeliveryJob, apply_transition, claim_decision)
from engine.generation import GenerationBusy
from engine.mail_archive import valid_job_id
from engine.selection import DeliveryHistory, aware_utc


def atomic(client, operation):
    return firestore.transactional(operation)(client.transaction())


class FirestoreJobStore:
    def __init__(self, client, *, max_scan=10000, collection_name="engine_delivery_jobs"):
        if max_scan < 1:
            raise ValueError("SCAN_LIMIT_INVALID")
        self.client, self.max_scan = client, max_scan
        self.collection = client.collection(collection_name)

    def reference(self, key):
        return self.collection.document(valid_job_id(key))

    def get(self, job_id):
        doc = self.reference(job_id).get(timeout=20)
        return DeliveryJob(**doc.to_dict()) if doc.exists else None

    def create_if_absent(self, job):
        ref = self.reference(job.job_id)
        def run(tx):
            doc = ref.get(transaction=tx)
            if doc.exists:
                return DeliveryJob(**doc.to_dict())
            tx.create(ref, asdict(job))
            return job
        return atomic(self.client, run)

    def claim(self, job_id, *, run_id, now, claim_ttl=CLAIM_TTL):
        ref, now = self.reference(job_id), aware_utc(now)
        token = uuid.uuid4().hex
        def run(tx):
            doc = ref.get(transaction=tx)
            if not doc.exists:
                raise LookupError("JOB_NOT_FOUND")
            job = DeliveryJob(**doc.to_dict())
            decision = claim_decision(job, now, claim_ttl)
            if decision == "skip":
                return None
            if decision == "claim":
                updated = replace(job, status="processing", claim_token=token,
                                  claimed_at=now, run_id=run_id, updated_at=now)
            else:
                updated = replace(job, status="unknown" if decision == "mark_unknown" else "skipped_late",
                                  claim_token=None, updated_at=now,
                                  error_code="SENDING_INTERRUPTED" if decision == "mark_unknown" else "DEADLINE_PASSED")
            tx.set(ref, asdict(updated))
            return updated if decision == "claim" else None
        return atomic(self.client, run)

    def transition(self, job_id, *, claim_token, to_status, now, **fields):
        ref = self.reference(job_id)
        def run(tx):
            doc = ref.get(transaction=tx)
            if not doc.exists:
                raise LookupError("JOB_NOT_FOUND")
            updated = apply_transition(DeliveryJob(**doc.to_dict()), claim_token=claim_token,
                                       to_status=to_status, now=now, fields=fields)
            tx.set(ref, asdict(updated))
            return updated
        return atomic(self.client, run)

    def bounded(self, query):
        docs = list(query.limit(self.max_scan + 1).stream(timeout=30))
        if len(docs) > self.max_scan:
            raise RuntimeError("JOB_QUERY_LIMIT_EXCEEDED")
        return [DeliveryJob(**doc.to_dict()) for doc in docs]

    def open_jobs(self):
        query = self.collection.where(filter=FieldFilter("status", "in", ["pending", "processing", "sending", "failed"]))
        return [job for job in self.bounded(query) if job.status != "failed" or job.retryable]

    def recent_history(self, user_id, *, since):
        # One equality filter needs no custom composite index. Never silently truncate.
        query = self.collection.where(filter=FieldFilter("user_id", "==", user_id))
        rows = [DeliveryHistory(job.selected_article_url, job.status, job.updated_at or job.scheduled_at)
                for job in self.bounded(query) if job.selected_article_url
                and (job.updated_at or job.scheduled_at) >= aware_utc(since)]
        return sorted(rows, key=lambda row: row.attempted_at)


@dataclass(frozen=True)
class GenerationHandle:
    key: str
    token: str


class FirestoreGenerationStore:
    def __init__(self, client, *, clock=lambda: datetime.now(timezone.utc), lease=timedelta(minutes=10),
                 collection_name="engine_generation_jobs"):
        self.client, self.clock, self.lease = client, clock, lease
        self.collection = client.collection(collection_name)

    def reference(self, key):
        return self.collection.document(valid_job_id(key))

    @contextmanager
    def locked(self, key):
        ref, token = self.reference(key), uuid.uuid4().hex
        def acquire(tx):
            data = ref.get(transaction=tx).to_dict() or {}
            now = aware_utc(self.clock())
            if data.get("lock_token") and data["lock_until"] > now:
                raise GenerationBusy("GENERATION_BUSY_OR_INTERRUPTED")
            # Expired ownership may be reclaimed, but generate_cards blocks in_flight
            # before issuing another AI request and preserves the attempt count.
            tx.set(ref, {"lock_token": token, "lock_until": now + self.lease}, merge=True)
        atomic(self.client, acquire)
        try:
            yield GenerationHandle(key, token)
        finally:
            def release(tx):
                data = ref.get(transaction=tx).to_dict() or {}
                if data.get("lock_token") == token:
                    tx.update(ref, {"lock_token": None, "lock_until": None})
            atomic(self.client, release)

    def owned(self, tx, handle):
        ref = self.reference(handle.key)
        data = ref.get(transaction=tx).to_dict() or {}
        if data.get("lock_token") != handle.token or data["lock_until"] <= aware_utc(self.clock()):
            raise GenerationBusy("GENERATION_CLAIM_LOST")
        return ref, data

    def load(self, handle):
        def run(tx):
            _, data = self.owned(tx, handle)
            return json.loads(data["state_json"]) if data.get("state_json") else None
        return atomic(self.client, run)

    def save(self, handle, state):
        payload = json.dumps(state, ensure_ascii=False, allow_nan=False)
        if len(payload.encode("utf-8")) > 900000:
            raise ValueError("GENERATION_DOCUMENT_TOO_LARGE")
        def run(tx):
            ref, old = self.owned(tx, handle)
            previous = json.loads(old["state_json"]) if old.get("state_json") else None
            if state.get("generation_key") != handle.key or (previous and state["attempts"] < previous["attempts"]):
                raise ValueError("GENERATION_STATE_REGRESSION")
            tx.update(ref, {"state_json": payload, "updated_at": aware_utc(self.clock())})
        atomic(self.client, run)


class FirestoreMailArchive:
    """Immutable MIME chunks under 1 MiB; expiry enforced on reads, cleanup explicit."""
    CHUNK_SIZE = 400000
    MAX_BYTES = 7000000

    def __init__(self, client, *, retention, clock=lambda: datetime.now(timezone.utc),
                 collection_name="engine_mail_archives"):
        if not timedelta(days=1) <= retention <= timedelta(days=30):
            raise ValueError("ARCHIVE_RETENTION_INVALID")
        self.client, self.retention, self.clock = client, retention, clock
        self.collection = client.collection(collection_name)

    def load(self, job_id):
        ref = self.collection.document(valid_job_id(job_id))
        doc = ref.get(timeout=20)
        if not doc.exists:
            return None
        data = doc.to_dict()
        if aware_utc(self.clock()) >= data["expires_at"]:
            raise RuntimeError("MAIL_ARCHIVE_EXPIRED")
        pieces = [ref.collection("chunks").document(str(i)).get(timeout=20) for i in range(data["chunks"])]
        if any(not item.exists for item in pieces):
            raise RuntimeError("MAIL_ARCHIVE_INCOMPLETE")
        payload = b"".join(item.to_dict()["payload"] for item in pieces)
        if hashlib.sha256(payload).hexdigest() != data["sha256"]:
            raise RuntimeError("MAIL_ARCHIVE_CORRUPT")
        return payload

    def save(self, job_id, message_bytes):
        ref = self.collection.document(valid_job_id(job_id))
        payload = bytes(message_bytes)
        if not payload or len(payload) > self.MAX_BYTES:
            raise ValueError("MAIL_ARCHIVE_SIZE_INVALID")
        digest = hashlib.sha256(payload).hexdigest()
        chunks = [payload[i:i+self.CHUNK_SIZE] for i in range(0, len(payload), self.CHUNK_SIZE)]
        def run(tx):
            doc = ref.get(transaction=tx)
            if doc.exists:
                if doc.to_dict()["sha256"] != digest:
                    raise ValueError("MAIL_ARCHIVE_CONTENT_CONFLICT")
                return
            now = aware_utc(self.clock())
            tx.create(ref, {"sha256": digest, "chunks": len(chunks), "created_at": now,
                            "expires_at": now + self.retention})
            for i, chunk in enumerate(chunks):
                tx.create(ref.collection("chunks").document(str(i)), {"payload": chunk})
        atomic(self.client, run)

    def delete_expired(self, now, *, limit=100):
        """Delete MIME children and parent together; expiry alone is not deletion."""
        if not 1 <= limit <= 500:
            raise ValueError("CLEANUP_LIMIT_INVALID")
        rows = self.collection.where(filter=FieldFilter("expires_at", "<=", aware_utc(now))).limit(limit)
        count = 0
        for doc in rows.stream(timeout=30):
            ref = doc.reference
            pieces = list(ref.collection("chunks").limit(21).stream(timeout=20))
            if len(pieces) > 20:
                raise RuntimeError("MAIL_ARCHIVE_CHUNKS_INVALID")
            batch = self.client.batch()
            for piece in pieces:
                batch.delete(piece.reference)
            batch.delete(ref)
            batch.commit()
            count += 1
        return count
