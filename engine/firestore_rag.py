"""Persistent E5 embeddings and bounded, pre-filtered Firestore cosine search."""

from datetime import datetime, timezone
import math

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
from google.cloud.firestore_v1.vector import Vector

from engine.article_store import prepare_article
from engine.embeddings import validate_vector
from engine.firestore_article_store import decode_record, document_key
from engine.rag import RagHit, RagResult, past_cutoff
from engine.selection import aware_utc


class FirestoreRagStore:
    def __init__(self, repository, encoder, *, min_score=0.85, clock=lambda: datetime.now(timezone.utc)):
        if not 0 <= min_score <= 1:
            raise ValueError("RAG_SCORE_INVALID")
        self.repository, self.encoder = repository, encoder
        self.min_score, self.clock = min_score, clock

    def index(self, record):
        """Compute outside transaction; only commit to the same article version."""
        reference = self.repository.articles.document(document_key(record.article.url))
        existing = reference.get(timeout=20)
        data = existing.to_dict() if existing.exists else {}
        if (data.get("embedding_status") == "ready" and
                data.get("embedding_content_hash") == record.content_hash and
                data.get("embedding_model") == self.encoder.model_id and
                data.get("embedding_revision") == self.encoder.model_revision):
            try:
                validate_vector(data["embedding"])
                return "reused"
            except (KeyError, ValueError, TypeError):
                pass
        try:
            _, digest = prepare_article(record.article)
            if digest != record.content_hash:
                raise ValueError("ARTICLE_HASH_INVALID")
            vectors = self.encoder.encode_passages([record.article.title + "\n" + record.article.body])
            if len(vectors) != 1:
                raise ValueError("EMBEDDING_COUNT_INVALID")
            vector = validate_vector(vectors[0])
            state, error = "ready", None
        except Exception:
            vector, state, error = None, "failed", "EMBEDDING_FAILED"
        @firestore.transactional
        def commit(tx):
            current = reference.get(transaction=tx)
            latest = current.to_dict() if current.exists else {}
            if (latest.get("content_hash"), latest.get("content_version")) != (record.content_hash, record.content_version):
                return "stale"
            tx.update(reference, {"embedding_status": state, "embedding": Vector(vector) if vector else None,
                "embedding_content_hash": record.content_hash, "embedding_model": self.encoder.model_id,
                "embedding_revision": self.encoder.model_revision, "embedding_error": error,
                "embedding_updated_at": aware_utc(self.clock())})
            return state
        return commit(self.repository.client.transaction())

    def reindex_pending(self, *, limit=50, deadline=None, monotonic=None):
        """Bounded retries for pending/failed records, oldest attempts first."""
        import time
        monotonic = monotonic or time.monotonic
        if not 1 <= limit <= 500:
            raise ValueError("REINDEX_LIMIT_INVALID")
        counts, seen = {}, set()
        for status in ("pending", "failed"):
            query = self.repository.articles.where(filter=FieldFilter("embedding_status", "==", status))
            query = query.order_by("embedding_updated_at" if status == "failed" else "first_seen_at").limit(limit)
            for doc in query.stream(timeout=30):
                if deadline is not None and monotonic() >= deadline:
                    counts["budget_exceeded"] = 1
                    return counts
                record = decode_record(doc.to_dict())
                if record.article.article_id in seen:
                    continue
                seen.add(record.article.article_id)
                outcome = self.index(record)
                counts[outcome] = counts.get(outcome, 0) + 1
        return counts

    def __call__(self, current, day):
        cutoff = past_cutoff(day)
        try:
            vector = validate_vector(self.encoder.encode_query(current.article.title + "\n" + current.article.body))
        except Exception:
            return RagResult("embedding_failed", cutoff, (), (), (), self.encoder.model_id, self.encoder.model_revision)
        query = self.repository.articles
        filters = (("category", "==", current.article.category), ("published_at", "<", cutoff),
                   ("source_verified", "==", True), ("body_valid", "==", True),
                   ("embedding_status", "==", "ready"), ("embedding_model", "==", self.encoder.model_id),
                   ("embedding_revision", "==", self.encoder.model_revision))
        for field, operator, value in filters:
            query = query.where(filter=FieldFilter(field, operator, value))
        query = query.find_nearest(vector_field="embedding", query_vector=Vector(vector),
            distance_measure=DistanceMeasure.COSINE, limit=5, distance_result_field="rag_distance")
        hits, excluded = [], []
        for doc in query.stream(timeout=30):
            data = doc.to_dict()
            identity = data.get("article_id", "invalid")
            try:
                record = decode_record(data)
                _, digest = prepare_article(record.article)
                validate_vector(data["embedding"])
                distance = float(data["rag_distance"])
                if (not math.isfinite(distance) or not 0 <= distance <= 2 or digest != record.content_hash
                        or data.get("embedding_content_hash") != record.content_hash
                        or data.get("embedding_model") != self.encoder.model_id
                        or data.get("embedding_revision") != self.encoder.model_revision
                        or data.get("embedding_status") != "ready"
                        or record.article.published_at >= cutoff or record.article.category != current.article.category
                        or record.article.url == current.article.url or identity == current.article.article_id
                        or not isinstance(data.get("publisher"), str) or not data["publisher"].strip()):
                    raise ValueError("RAG_RECORD_INVALID")
                hits.append(RagHit(record, 1.0 - distance))
            except (KeyError, ValueError, TypeError, AttributeError):
                excluded.append((identity, "RAG_RECORD_INVALID"))
        hits.sort(key=lambda hit: (-hit.score, -hit.record.article.published_at.timestamp(), hit.record.article.article_id))
        retrieved = tuple(hits[:5])
        usable = tuple(hit for hit in retrieved if hit.score >= self.min_score)[:3]
        return RagResult("ready" if usable else "no_evidence", cutoff, retrieved, usable,
                         tuple(excluded), self.encoder.model_id, self.encoder.model_revision)

    def record_search(self, job_id, current, result):
        from engine.mail_archive import valid_job_id
        payload = {"article_id": current.article.article_id, "content_hash": current.content_hash,
                   "status": result.status, "cutoff_utc": result.cutoff_utc,
                   "model_id": result.model_id, "model_revision": result.model_revision,
                   "retrieved": [{"article_id": hit.record.article.article_id, "score": hit.score,
                                  "content_version": hit.record.content_version,
                                  "content_hash": hit.record.content_hash} for hit in result.retrieved],
                   "selected_evidence_ids": [hit.record.article.article_id for hit in result.usable_evidence],
                   "excluded": [{"article_id": identity, "reason": reason} for identity, reason in result.excluded],
                   "updated_at": aware_utc(self.clock())}
        self.repository.client.collection("engine_rag_results").document(valid_job_id(job_id)).set(payload)

    def record_usage(self, job_id, card_data):
        from engine.mail_archive import valid_job_id
        current_id = card_data["article_id"]
        used = [source["article_id"] for source in card_data["sources"] if source["article_id"] != current_id]
        self.repository.client.collection("engine_rag_results").document(valid_job_id(job_id)).set(
            {"used_article_ids": used, "card2_generated": card_data["card2"] is not None}, merge=True)
