"""Browser APIs: versioned settings, token feedback and confirmed account deletion."""
from datetime import date, datetime, timedelta, timezone
import hashlib
import hmac
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from pydantic import BaseModel, ConfigDict, Field, field_validator

try:
    from .engine_api import KST, SubscriptionSettings, deleted, effective_settings, public_categories, utc, account_fence
except ImportError:
    from engine_api import KST, SubscriptionSettings, deleted, effective_settings, public_categories, utc, account_fence


class SettingsChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    categories: list[str] = Field(min_length=1, max_length=6)
    keywords: list[str] = Field(default_factory=list, max_length=5)
    delivery_hour_kst: int = Field(strict=True, ge=0, le=23)
    expected_settings_version: int = Field(strict=True, ge=1)

    @field_validator("categories")
    @classmethod
    def categories_valid(cls, values):
        return SubscriptionSettings.valid_categories(values)

    @field_validator("keywords")
    @classmethod
    def keywords_valid(cls, values):
        return SubscriptionSettings.valid_keywords(values)


class Confirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool = Field(strict=True)


class TokenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=20, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")


class FeedbackBody(TokenBody):
    rating: Literal["up", "down"]
    reasons: list[str] = Field(default_factory=list, max_length=5)
    comment: str = Field(default="", max_length=500)

    @field_validator("reasons")
    @classmethod
    def reasons_valid(cls, values):
        # Codes remain stable across Korean labels in the web UI.
        allowed = {"relevant", "clear", "useful", "irrelevant", "inaccurate", "too_long", "other"}
        if any(v not in allowed for v in values) or len(set(values)) != len(values):
            raise ValueError("REASONS_INVALID")
        return values


class FeedbackService:
    def __init__(self, db, clock=lambda: datetime.now(timezone.utc)):
        self.db, self.clock = db, clock

    def resolve_token(self, raw, tx):
        digest = hashlib.sha256(raw.encode()).hexdigest()
        matches = list(self.db.collection("feedback_tokens").where(
            filter=FieldFilter("token_hash", "==", digest)).limit(2).stream(transaction=tx))
        if len(matches) != 1:
            raise HTTPException(400, "TOKEN_INVALID")
        token = matches[0].to_dict()
        if not hmac.compare_digest(token["token_hash"], digest):
            raise HTTPException(400, "TOKEN_INVALID")
        if utc(token["expires_at"]) <= utc(self.clock()):
            raise HTTPException(410, "TOKEN_EXPIRED")
        owner = self.db.collection("users").document(token["user_id"]).get(transaction=tx)
        if not owner.exists or deleted(owner.to_dict()):
            raise HTTPException(410, "TOKEN_EXPIRED")
        ref = self.db.collection("feedbacks").document(token.get("feedback_id", token["job_id"]))
        existing = ref.get(transaction=tx)
        return token, ref, existing.to_dict() if existing.exists else {}

    def resolve(self, raw):
        @firestore.transactional
        def read(tx):
            _, _, existing = self.resolve_token(raw, tx)
            return {"valid": True, "current_rating": existing.get("rating"),
                    "reasons": existing.get("reasons", []), "comment": existing.get("comment", "")}
        return read(self.db.transaction())

    def submit(self, body):
        @firestore.transactional
        def save(tx):
            token, ref, existing = self.resolve_token(body.token, tx)
            now = utc(self.clock())
            data = {"job_id": token["job_id"], "environment": token.get("environment", "production"), "subscription_id": token["subscription_id"],
                    "user_id": token["user_id"], "rating": body.rating, "reasons": body.reasons,
                    "comment": body.comment.strip(), "created_at": existing.get("created_at", now),
                    "updated_at": now}
            tx.set(ref, data)
            return {"saved": True, "current_rating": body.rating}
        return save(self.db.transaction())


def owned_subscription(db, uid, subscription_id=None, transaction=None):
    fixed = db.collection("subscriptions").document(uid).get(transaction=transaction)
    if subscription_id is None and fixed.exists and fixed.to_dict().get("uid") == uid:
        return fixed
    if subscription_id is None:
        query = db.collection("subscriptions").where(filter=FieldFilter("uid", "==", uid))
    else:
        query = db.collection("subscriptions").where(filter=FieldFilter("subscription_id", "==", subscription_id))
    rows = list(query.limit(101).stream(transaction=transaction))
    if len(rows) > 100:
        raise HTTPException(503, "SUBSCRIPTION_QUERY_LIMIT_EXCEEDED")
    matches = [row for row in rows if row.to_dict().get("uid") == uid]
    if subscription_id is not None:
        # Legacy documents may still identify their subscription by document ID.
        direct = db.collection("subscriptions").document(subscription_id).get(transaction=transaction)
        if direct.exists and direct.to_dict().get("uid") == uid and direct.to_dict().get("subscription_id", direct.id) == subscription_id:
            matches = [row for row in matches if row.reference.path != direct.reference.path] + [direct]
        if len(matches) > 1:
            raise HTTPException(409, "SUBSCRIPTION_NOT_UNIQUE")
        return matches[0] if matches else None
    return max(matches, key=lambda row: (row.to_dict().get("status") == "active", str(row.to_dict().get("created_at", "")))) if matches else None


def create_user_router(get_db, authenticate, subscription_response, cancel_subscription):
    router = APIRouter()

    @router.get("/subscriptions/current")
    def current(user=Depends(authenticate)):
        doc = owned_subscription(get_db(), user["uid"])
        data = {**doc.to_dict(), "subscription_id": doc.to_dict().get("subscription_id", doc.id)} if doc else None
        return {"subscription": subscription_response(data) if data else None}

    @router.patch("/subscriptions/{subscription_id}/settings")
    def change(subscription_id: str, body: SettingsChange, user=Depends(authenticate)):
        db = get_db()
        if not subscription_id or "/" in subscription_id or len(subscription_id) > 1500:
            raise HTTPException(422, "SUBSCRIPTION_ID_INVALID")
        now = datetime.now(timezone.utc)
        today = now.astimezone(KST).date()
        tomorrow = today + timedelta(days=1)
        @firestore.transactional
        def save(tx):
            doc = owned_subscription(db, user["uid"], subscription_id, tx)
            owner = db.collection("users").document(user["uid"]).get(transaction=tx)
            data = doc.to_dict() if doc else {}
            data.setdefault("subscription_id", doc.id if doc else None)
            if data.get("subscription_id") != subscription_id:
                raise HTTPException(404, "SUBSCRIPTION_NOT_FOUND")
            if deleted(data) or not owner.exists or deleted(owner.to_dict()):
                raise HTTPException(410, "ACCOUNT_DELETION_PENDING")
            if data.get("status") != "active" or tomorrow.isoformat() >= data["end_date_exclusive"]:
                raise HTTPException(409, "SUBSCRIPTION_NOT_CHANGEABLE")
            if data.get("settings_version", 1) != body.expected_settings_version:
                raise HTTPException(409, "SETTINGS_CONFLICT")
            settings = SubscriptionSettings(categories=body.categories, keywords=body.keywords,
                delivery_hour_kst=body.delivery_hour_kst, duration_days=data["duration_days"],
                consent_version=data["consent_version"])
            if any(v not in public_categories() for v in settings.categories):
                raise HTTPException(422, "CATEGORY_NOT_PUBLIC")
            baseline = effective_settings(data, today)
            keys = ("categories", "keywords", "delivery_hour_kst", "duration_days", "consent_version", "settings_version", "effective_date")
            previous = {key: baseline[key] for key in keys}
            version = data.get("settings_version", 1) + 1
            pending = {**settings.model_dump(), "settings_version": version, "effective_date": tomorrow.isoformat()}
            data.update(settings_versions=[previous, pending], settings_version=version, updated_at=now)
            tx.set(doc.reference, data)
            return data
        return {"subscription": subscription_response(save(db.transaction()))}

    @router.post("/subscriptions/{subscription_id}/cancel")
    def cancel(subscription_id: str, body: Confirm, user=Depends(authenticate)):
        # Delegate to the same transactional ownership + identity checks as the compatibility endpoint.
        from types import SimpleNamespace
        return cancel_subscription(SimpleNamespace(subscription_id=subscription_id, confirm=body.confirm), user)

    @router.post("/feedback/resolve")
    def resolve(body: TokenBody, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return FeedbackService(get_db()).resolve(body.token)

    @router.post("/feedback")
    def feedback(body: FeedbackBody, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return FeedbackService(get_db()).submit(body)

    @router.post("/account-deletion-requests", status_code=202)
    def request_deletion(body: Confirm, user=Depends(authenticate)):
        if body.confirm is not True:
            raise HTTPException(422, "CONFIRM_REQUIRED")
        db = get_db()
        ref = db.collection("users").document(user["uid"])
        now = datetime.now(timezone.utc)
        request_id = str(uuid4())
        @firestore.transactional
        def request(tx):
            owner = ref.get(transaction=tx)
            account_fence(db, user["uid"], tx)
            data = owner.to_dict() if owner.exists else {"uid": user["uid"]}
            if data.get("deletion_requested_at"):
                return data
            data.update(deletion_requested_at=now, deletion_request_id=request_id, deletion_status="pending")
            # This owner flag is the live authorization fence for every engine eligibility/token check.
            tx.set(ref, data)
            return data
        data = request(db.transaction())
        return {"request_id": data["deletion_request_id"], "status": data["deletion_status"],
                "sending_stopped": True, "status_available_until_auth_deletion": True}

    @router.get("/account-deletion-requests/{request_id}")
    def deletion_status(request_id: str, user=Depends(authenticate)):
        doc = get_db().collection("users").document(user["uid"]).get()
        if not doc.exists or doc.to_dict().get("deletion_request_id") != request_id:
            raise HTTPException(404, "DELETION_REQUEST_NOT_FOUND")
        return {"request_id": request_id, "status": doc.to_dict()["deletion_status"]}

    return router
