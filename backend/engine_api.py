"""Server-authenticated engine endpoints; no browser Firebase tokens accepted."""

from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
import base64
import hashlib
import hmac
import os
import re
import unicodedata
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from pydantic import BaseModel, Field, field_validator

KST = timezone(timedelta(hours=9))
CATEGORIES = {"economy", "it_science", "politics", "society", "world", "culture"}


class SubscriptionSettings(BaseModel):
    categories: list[str] = Field(min_length=1, max_length=6)
    keywords: list[str] = Field(default_factory=list, max_length=5)
    delivery_hour_kst: int = Field(ge=0, le=23, strict=True)
    duration_days: int = Field(strict=True)
    consent_version: str = Field(min_length=1, max_length=100)

    @field_validator("categories")
    @classmethod
    def valid_categories(cls, values):
        if any(v not in CATEGORIES for v in values) or len(set(values)) != len(values):
            raise ValueError("CATEGORIES_INVALID")
        return values

    @field_validator("keywords")
    @classmethod
    def valid_keywords(cls, values):
        values = [unicodedata.normalize("NFKC", v).strip() for v in values]
        if any(not 1 <= len(v) <= 20 for v in values):
            raise ValueError("KEYWORDS_INVALID")
        return list(dict.fromkeys(values))

    @field_validator("duration_days")
    @classmethod
    def valid_duration(cls, value):
        if value not in {7, 14, 28}:
            raise ValueError("DURATION_INVALID")
        return value


def utc(value):
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(422, "TIMEZONE_REQUIRED")
    return value.astimezone(timezone.utc)


def instant(day, hour=0):
    return datetime.combine(day, time(hour), KST).astimezone(timezone.utc)


def dates(data):
    start = date.fromisoformat(data["start_date"])
    end = date.fromisoformat(data["end_date_exclusive"])
    if (end-start).days not in {7, 14, 28}:
        raise ValueError("PERIOD_INVALID")
    return start, end


def deleted(data):
    return bool(data.get("deletion_requested_at") or data.get("deletion_requested") or data.get("deleted_at"))


def account_fence(db, uid, transaction=None):
    if not isinstance(uid, str) or not uid or "/" in uid:
        raise HTTPException(401, "AUTH_REQUIRED")
    marker = db.collection("privacy_deletions").document(hashlib.sha256(uid.encode()).hexdigest())
    if marker.get(transaction=transaction).exists:
        raise HTTPException(410, "ACCOUNT_DELETED")


def effective_settings(data, day):
    base = {"settings_version": 1, "effective_date": data.get("start_date"), **data}
    versions = [v for v in data.get("settings_versions", []) if v["effective_date"] <= day.isoformat()]
    return {**base, **max(versions, key=lambda v: (v["effective_date"], v["settings_version"]))} if versions else base


def public_categories():
    return [v for v in sorted(CATEGORIES) if v in os.getenv("PUBLIC_CATEGORIES", "").split(",")]


class EngineService:
    def __init__(self, db, *, subscriptions="subscriptions", jobs=("engine_delivery_jobs",),
                 tokens="feedback_tokens", users="users", clock=lambda: datetime.now(timezone.utc)):
        self.db, self.clock = db, clock
        self.subscriptions = db.collection(subscriptions)
        self.jobs, self.tokens, self.users = jobs, db.collection(tokens), db.collection(users)

    def read(self, subscription_id):
        if not subscription_id or "/" in subscription_id or len(subscription_id) > 1500:
            raise HTTPException(422, "SUBSCRIPTION_ID_INVALID")
        ref = self.subscriptions.document(subscription_id)
        doc = ref.get(timeout=20)
        if doc.exists:
            if doc.to_dict().get("subscription_id", subscription_id) != subscription_id:
                raise HTTPException(404, "SUBSCRIPTION_REPLACED")
        else:
            matches = list(self.subscriptions.where(filter=FieldFilter("subscription_id", "==", subscription_id)).limit(2).stream(timeout=20))
            if len(matches) > 1:
                raise HTTPException(409, "SUBSCRIPTION_NOT_UNIQUE")
            if not matches and self.subscriptions.id == "subscriptions":
                archived = self.db.collection("subscription_history").document(subscription_id).get(timeout=20)
                if archived.exists:
                    matches = [archived]
            if len(matches) != 1:
                raise HTTPException(404, "SUBSCRIPTION_NOT_FOUND")
            doc, ref = matches[0], matches[0].reference
        return ref, doc.to_dict()

    def deletion_requested(self, data):
        if deleted(data):
            return True
        uid = data.get("uid")
        if not isinstance(uid, str) or not uid or "/" in uid:
            raise ValueError("SUBSCRIPTION_USER_INVALID")
        user = self.users.document(uid).get(timeout=20)
        # Missing user records are not authorized for delivery.
        return not user.exists or deleted(user.to_dict())

    def eligibility(self, subscription_id, now):
        now = utc(now)
        try:
            _, data = self.read(subscription_id)
        except HTTPException as error:
            if error.status_code == 404:
                return {"eligible": False, "reason": "not_found"}
            raise
        if self.deletion_requested(data):
            return {"eligible": False, "reason": "deletion_requested"}
        if data.get("status") == "cancelled" or data.get("cancelled_at"):
            return {"eligible": False, "reason": "cancelled"}
        try:
            start, end = dates(data)
        except (KeyError, ValueError, TypeError):
            return {"eligible": False, "reason": "not_found"}
        if now >= instant(end):
            return {"eligible": False, "reason": "expired"}
        active = data.get("status") == "active" and now >= instant(start)
        return {"eligible": active, "reason": "active" if active else "not_found"}

    def snapshot(self, subscription_id, day, *, kind="daily_briefing"):
        ref, data = self.read(subscription_id)
        try:
            applied = effective_settings(data, day)
            settings = SubscriptionSettings.model_validate(applied)
            start, end = dates(data)
            if settings.duration_days != (end-start).days or self.deletion_requested(data):
                raise ValueError()
            email = data["email"]
            if not isinstance(email, str) or not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", email):
                raise ValueError()
            if data.get("cancelled_at") or data.get("status") not in {"active", "expired"}:
                raise ValueError()
            if kind == "daily_briefing" and (not start <= day < end or data.get("status") != "active"):
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            raise HTTPException(409, "SUBSCRIPTION_SETTINGS_INCOMPLETE_OR_INELIGIBLE") from None
        scheduled = instant(day, settings.delivery_hour_kst) if kind == "daily_briefing" else instant(end)
        deadline = min(scheduled + timedelta(hours=3), instant(day+timedelta(days=1)), instant(end)) if kind == "daily_briefing" else scheduled+timedelta(hours=24)
        candidate = {"subscription_id": subscription_id, "user_id": data["uid"], "recipient_email": email,
                     "timezone": "Asia/Seoul", "status": "active" if kind == "daily_briefing" else "expired",
                     "categories": settings.categories, "keywords": settings.keywords,
                     "contract_version": "1.0", "settings_version": applied.get("settings_version", 1),
                     "effective_date": applied.get("effective_date", data["start_date"]),
                     "delivery_hour_kst": settings.delivery_hour_kst,
                     "start_date": start.isoformat(), "end_date_exclusive": end.isoformat(),
                     "scheduled_date_kst": day.isoformat(), "scheduled_at": scheduled.isoformat(),
                     "deadline_at": deadline.isoformat()}
        # Freeze the day's settings once they are requested; no SMTP/AI inside callback.
        identity = hashlib.sha256(subscription_id.encode()).hexdigest()[:24]
        snapshot_parent = self.subscriptions.document(data["snapshot_parent_id"]) if data.get("snapshot_parent_id") else ref
        snapshot_ref = snapshot_parent.collection("engine_snapshots").document(identity+"-"+day.isoformat()+"-"+kind)
        @firestore.transactional
        def freeze(tx):
            current = ref.get(transaction=tx)
            existing = snapshot_ref.get(transaction=tx)
            if current.to_dict() != data:
                raise HTTPException(409, "SUBSCRIPTION_CHANGED_RETRY")
            if existing.exists:
                old = existing.to_dict()
                if (old["start_date"], old["end_date_exclusive"]) != (candidate["start_date"], candidate["end_date_exclusive"]):
                    raise HTTPException(409, "SUBSCRIPTION_PERIOD_CHANGED")
                return old
            tx.create(snapshot_ref, candidate)
            return candidate
        return freeze(self.db.transaction())

    def list_snapshots(self, now, *, expired=False, subscription_id=None):
        now = utc(now)
        query = self.subscriptions.where(filter=FieldFilter(
            "subscription_id" if subscription_id else "status",
            "==" if subscription_id else "in",
            subscription_id if subscription_id else ["active", "expired"]))
        docs = list(query.limit(1001).stream(timeout=20))
        if expired and self.subscriptions.id == "subscriptions":
            history = self.db.collection("subscription_history").where(filter=FieldFilter(
                "subscription_id" if subscription_id else "status", "==" if subscription_id else "in",
                subscription_id if subscription_id else ["active", "expired"]))
            docs += list(history.limit(1001).stream(timeout=20))
        if len(docs) > 1000:
            raise HTTPException(503, "SUBSCRIPTION_QUERY_LIMIT_EXCEEDED")
        rows, skipped = [], Counter()
        for doc in docs:
            data = doc.to_dict()
            try:
                _, end = dates(data)
                if expired:
                    if not instant(end) <= now < instant(end)+timedelta(hours=24):
                        continue
                    day, kind = end, "subscription_end"
                else:
                    day, kind = now.astimezone(KST).date(), "daily_briefing"
                    hour = effective_settings(data, day)["delivery_hour_kst"]
                    if type(hour) is not int or not 0 <= hour <= 23:
                        raise ValueError()
                    scheduled = instant(day, hour)
                    if not scheduled <= now < min(scheduled+timedelta(hours=3), instant(day+timedelta(days=1)), instant(end)):
                        continue
                rows.append(self.snapshot(data.get("subscription_id", doc.id), day, kind=kind))
            except (KeyError, ValueError, TypeError):
                skipped["incomplete_settings"] += 1
            except HTTPException as error:
                if error.status_code != 409:
                    raise
                skipped["incomplete_or_changed"] += 1
        return {"subscriptions": rows, "skipped_counts": dict(skipped)}

    def feedback_token(self, job_id, idempotency_key, *, environment="production"):
        if environment not in {"production", "test"}:
            raise HTTPException(422, "DELIVERY_ENVIRONMENT_INVALID")
        if idempotency_key != job_id:
            raise HTTPException(409, "IDEMPOTENCY_KEY_MISMATCH")
        secret = os.environ.get("FEEDBACK_TOKEN_SECRET", "")
        if len(secret) < 32:
            raise HTTPException(503, "FEEDBACK_TOKEN_SECRET_NOT_CONFIGURED")
        names = self.jobs if environment == "production" else ("engine_test_delivery_jobs",)
        refs = [self.db.collection(name).document(job_id) for name in names]
        feedback_id = job_id if environment == "production" else "test-" + job_id
        token_ref = self.tokens.document(feedback_id)
        raw = base64.urlsafe_b64encode(hmac.digest(secret.encode(), ("feedback-v1:"+feedback_id).encode(), "sha256")).decode().rstrip("=")
        digest = hashlib.sha256(raw.encode()).hexdigest()
        @firestore.transactional
        def issue(tx):
            candidates = [ref.get(transaction=tx) for ref in refs]
            existing = token_ref.get(transaction=tx)
            found = [doc.to_dict() for doc in candidates if doc.exists]
            if len(found) != 1:
                raise HTTPException(404 if not found else 409, "DELIVERY_JOB_NOT_UNIQUE")
            job = found[0]
            if job.get("mail_kind") != "daily_briefing" or job.get("status") not in {"processing", "sending", "sent"} or not job.get("selected_article_id"):
                raise HTTPException(409, "DELIVERY_JOB_NOT_READY")
            uid = job.get("user_id")
            if not isinstance(uid, str) or not uid or "/" in uid:
                raise HTTPException(409, "DELIVERY_JOB_USER_INVALID")
            owner = self.users.document(uid).get(transaction=tx)
            if not owner.exists or deleted(owner.to_dict()):
                raise HTTPException(410, "FEEDBACK_USER_UNAVAILABLE")
            now = utc(self.clock())
            if existing.exists:
                previous = existing.to_dict()
                if previous["expires_at"] <= now:
                    raise HTTPException(410, "FEEDBACK_TOKEN_EXPIRED")
                if not hmac.compare_digest(previous["token_hash"], digest):
                    raise HTTPException(409, "FEEDBACK_SECRET_CHANGED")
                return
            try:
                expiry = instant(date.fromisoformat(job["scheduled_date_kst"]) + timedelta(days=30))
            except (KeyError, ValueError, TypeError):
                raise HTTPException(409, "DELIVERY_JOB_DATE_INVALID") from None
            if expiry <= now:
                raise HTTPException(410, "FEEDBACK_TOKEN_EXPIRED")
            tx.create(token_ref, {"token_hash": digest, "job_id": job_id,
                "feedback_id": feedback_id, "environment": environment,
                "user_id": job["user_id"], "subscription_id": job["subscription_id"],
                "created_at": now, "expires_at": expiry})
        issue(self.db.transaction())
        return {"token": raw}


class FeedbackTokenRequest(BaseModel):
    environment: Literal["production", "test"] = "production"
    job_id: str = Field(pattern=r"^[0-9a-f]{64}$")


class CleanupRequest(BaseModel):
    now: datetime


def create_router(get_db):
    bearer = HTTPBearer(auto_error=False, scheme_name="EngineServiceBearer")
    def authenticate(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        expected = os.environ.get("ENGINE_API_TOKEN", "")
        if len(expected) < 32:
            raise HTTPException(503, "ENGINE_AUTH_NOT_CONFIGURED")
        if credentials is None or credentials.scheme.lower() != "bearer" or not hmac.compare_digest(credentials.credentials.encode(), expected.encode()):
            raise HTTPException(401, "ENGINE_AUTH_REQUIRED", headers={"WWW-Authenticate": "Bearer"})
    router = APIRouter(prefix="/api/v1/engine", tags=["engine"], dependencies=[Depends(authenticate)])
    def service():
        return EngineService(get_db(), subscriptions=os.environ.get("ENGINE_SUBSCRIPTIONS_COLLECTION", "subscriptions"))
    @router.get("/subscriptions/due")
    def due(now: datetime | None = None, subscription_id: str | None = None):
        return service().list_snapshots(now or datetime.now(timezone.utc), subscription_id=subscription_id)
    @router.get("/subscriptions/expired")
    def expired(now: datetime | None = None):
        return service().list_snapshots(now or datetime.now(timezone.utc), expired=True)
    @router.get("/subscriptions/{subscription_id}/snapshot")
    def snapshot(subscription_id: str, scheduled_date_kst: date):
        return {"subscription": service().snapshot(subscription_id, scheduled_date_kst)}
    @router.get("/subscriptions/{subscription_id}/eligibility")
    def eligibility(subscription_id: str, now: datetime | None = None):
        return service().eligibility(subscription_id, now or datetime.now(timezone.utc))
    @router.post("/feedback-tokens")
    def token(body: FeedbackTokenRequest, idempotency_key: str = Header(default="")):
        return service().feedback_token(body.job_id, idempotency_key, environment=body.environment)
    @router.post("/privacy-cleanup")
    def cleanup(body: CleanupRequest):
        try:
            from .privacy import PrivacyService
        except ImportError:
            from privacy import PrivacyService
        now = utc(body.now)
        if abs((now - datetime.now(timezone.utc)).total_seconds()) > 3600:
            raise HTTPException(422, "CLEANUP_TIME_INVALID")
        try:
            return PrivacyService(get_db()).cleanup(now)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "PRIVACY_CLEANUP_FAILED_RETRY_REQUIRED") from None
    return router
