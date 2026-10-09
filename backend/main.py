
import os
import json
import hashlib
import hmac
from uuid import uuid4
from datetime import datetime, timezone, timedelta, date
from typing import Optional, Any, List, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field, field_validator, ValidationError

import firebase_admin
from firebase_admin import credentials, auth, firestore
from zoneinfo import ZoneInfo
from pathlib import Path
from google.cloud.firestore_v1.base_query import FieldFilter

try:
    from .engine_api import SubscriptionSettings, create_router, instant, deleted, effective_settings, public_categories, account_fence
    from .preview_mail import dispatch_preview
except ImportError:
    from engine_api import SubscriptionSettings, create_router, instant, deleted, effective_settings, public_categories, account_fence
    from preview_mail import dispatch_preview

# --------------------------------------------------
# 환경변수 로드
# --------------------------------------------------
load_dotenv(Path(__file__).with_name(".env"), override=False)


# --------------------------------------------------
# FastAPI 앱 생성
# --------------------------------------------------
app = FastAPI(
    title="AI News Card API",
    description="Firebase Auth 기반 뉴스 구독/피드백 API",
    version="1.0.0",
)


# --------------------------------------------------
# CORS 설정
# --------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "http://127.0.0.1:5501",
        "http://localhost:5501",
        *[origin.strip().rstrip("/") for origin in os.getenv("FRONTEND_ORIGINS", "").split(",") if origin.strip()],
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------
# Firebase Admin 초기화
# --------------------------------------------------
def get_firebase_cred_path():
    # 1. 환경변수에서 먼저 찾기
    env_path = (
        os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        or os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY")
        or os.getenv("FIREBASE_CREDENTIALS")
        or os.getenv("FIREBASE_CREDENTIALS_PATH")
    )

    if env_path:
        if not os.path.isfile(env_path):
            raise FileNotFoundError("설정한 Firebase 서비스 계정 파일을 찾을 수 없습니다.")
        return env_path

    # 2. backend 폴더와 프로젝트 루트 둘 다 확인
    backend_dir = Path(__file__).resolve().parent
    root_dir = backend_dir.parent

    candidates = [
        backend_dir / "firebase-service-account.json",
        root_dir / "firebase-service-account.json",
        backend_dir / "ai-news-card-firebase-adminsdk.json",
        root_dir / "ai-news-card-firebase-adminsdk.json",
    ]

    for path in candidates:
        if path.exists():
            return str(path)

    # 3. 실제 긴 파일명도 자동 탐색
    for folder in [root_dir, backend_dir]:
        matches = list(folder.glob("ai-news-card-firebase*.json"))
        if matches:
            return str(matches[0])

    return None  # Use Application Default Credentials when no key file is set.


if not firebase_admin._apps:
    service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    if service_account_json:
        cred = credentials.Certificate(json.loads(service_account_json))
    else:
        cred_path = get_firebase_cred_path()
        cred = credentials.Certificate(cred_path) if cred_path else credentials.ApplicationDefault()
    firebase_admin.initialize_app(cred, {"projectId": os.getenv("FIREBASE_PROJECT_ID", "ai-news-card")})

db = firestore.client()


# --------------------------------------------------
# 시간대 설정
# --------------------------------------------------
KST = ZoneInfo("Asia/Seoul")


# --------------------------------------------------
# 공통 직렬화 함수
# Firestore datetime 값을 JSON 응답 가능 형태로 변환
# --------------------------------------------------
def to_iso(value: Any):
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def serialize_value(value: Any):
    if isinstance(value, dict):
        return {k: serialize_value(v) for k, v in value.items()}

    if isinstance(value, list):
        return [serialize_value(v) for v in value]

    return to_iso(value)


def serialize_dict(data: dict):
    return {k: serialize_value(v) for k, v in data.items()}


# --------------------------------------------------
# Bearer 인증 설정
# Firebase 사용자 인증과 엔진 인증 모두 Bearer 사용
# --------------------------------------------------
security = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
):
    """
    프론트엔드 사용자용 Firebase ID Token 검증
    Authorization: Bearer Firebase_ID_TOKEN
    """

    if not credentials:
        raise HTTPException(status_code=401, detail="Authorization 헤더가 없습니다.")

    if credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Bearer 토큰 형식이 아닙니다.")

    token = credentials.credentials

    try:
        decoded_token = auth.verify_id_token(token, check_revoked=True)

        account_fence(db, decoded_token.get("uid"))
        return {
            "uid": decoded_token.get("uid"),
            "email": decoded_token.get("email"),

        }

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="AUTH_REQUIRED")


def get_engine_auth(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
):
    """
    AI 뉴스 발송 엔진 전용 인증
    Authorization: Bearer ENGINE_API_TOKEN
    """

    engine_token = os.getenv("ENGINE_API_TOKEN")

    if not engine_token or len(engine_token) < 32:
        raise HTTPException(
            status_code=500,
            detail="서버에 ENGINE_API_TOKEN 환경변수가 설정되어 있지 않습니다.",
        )

    if not credentials:
        raise HTTPException(status_code=401, detail="엔진 인증 토큰이 없습니다.")

    if credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Bearer 토큰 형식이 아닙니다.")

    if not hmac.compare_digest(credentials.credentials.encode(), engine_token.encode()):
        raise HTTPException(status_code=401, detail="엔진 인증 토큰이 올바르지 않습니다.")

    return True


# --------------------------------------------------
# 요청 모델
# 중요: 모델은 라우터 함수보다 위에 있어야 함
# --------------------------------------------------
class SubscriptionSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: Literal["basic"] = "basic"
    engine_settings: Optional[SubscriptionSettings] = None
    category: Optional[str] = None
    keywords: List[str] = Field(default_factory=list, max_length=5)
    send_time: Optional[str] = None
    duration_weeks: Optional[int] = None
    agreed_privacy: Optional[bool] = None

    @field_validator("duration_weeks")
    @classmethod
    def validate_duration(cls, v):
        if v is not None and v not in [1, 2, 4]:
            raise ValueError("duration_weeks must be 1, 2, or 4")
        return v

    @field_validator("send_time")
    @classmethod
    def validate_send_time(cls, v):
        if v is None:
            return v
        try:
            datetime.strptime(v, "%H:%M")
        except ValueError:
            raise ValueError("send_time must be HH:MM format")
        return v

    @field_validator("keywords")
    @classmethod
    def validate_keywords(cls, v):
        return SubscriptionSettings.valid_keywords(v)


class FeedbackCreate(BaseModel):
    subscription_id: str
    rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = None


# --------------------------------------------------
# 날짜 계산 함수
# --------------------------------------------------
def calculate_subscription_dates(duration_weeks: int):
    now_kst = datetime.now(KST)

    # 가입 다음 날부터 시작
    start_date = now_kst.date() + timedelta(days=1)

    # 1주 = 7일, 2주 = 14일, 4주 = 28일
    # 시작일 포함이므로 -1
    end_date = start_date + timedelta(days=(duration_weeks * 7) - 1)

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }


def normalize_target_date(target_date: Optional[str]):
    """
    엔진 API에서 날짜가 없으면 오늘 KST 날짜 사용
    형식: YYYY-MM-DD
    """

    if not target_date:
        return datetime.now(KST).date().isoformat()

    try:
        date.fromisoformat(target_date)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="target_date는 YYYY-MM-DD 형식이어야 합니다.",
        )

    return target_date


def is_sendable_subscription(data: dict, target_date: str):
    """
    특정 날짜 기준 발송 가능한 활성 구독인지 확인
    """

    if data.get("status") != "active":
        return False

    start_date = data.get("start_date")
    end_date = data.get("end_date")

    if not start_date or not end_date:
        return False

    return start_date <= target_date <= end_date


def is_expired_subscription(data: dict, target_date: str):
    """
    특정 날짜 기준 만료 대상 구독인지 확인
    """

    if data.get("status") != "active":
        return False

    end_date = data.get("end_date")

    if not end_date:
        return False

    return end_date < target_date


# --------------------------------------------------
# 기본 라우트
# --------------------------------------------------
@app.get("/")
def root():
    return {"message": "FastAPI + Firebase 서버 실행 중"}


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(create_router(lambda: db))


@app.get("/catalog")
def catalog():
    return {
        "categories": public_categories(),
        "durations": [7, 14, 28],
        "consent_version": os.getenv("CONSENT_VERSION", "v1"),
        "capabilities": {"settings_change": True, "feedback": True, "account_deletion": True},
    }


@app.get("/me")
def me(current_user: dict = Depends(get_current_user)):
    return {
        "message": "인증 성공",
        "user": current_user,
    }


# --------------------------------------------------
# 사용자 정보 Firestore 저장
# --------------------------------------------------
@app.post("/users/sync")
def sync_user(current_user: dict = Depends(get_current_user)):
    uid = current_user["uid"]

    user_ref = db.collection("users").document(uid)
    now = datetime.now(timezone.utc)
    @firestore.transactional
    def sync(tx):
        existing_doc = user_ref.get(transaction=tx)
        account_fence(db, uid, tx)
        old = existing_doc.to_dict() if existing_doc.exists else {}
        if deleted(old):
            raise HTTPException(410, "ACCOUNT_DELETION_PENDING")
        user_data = {"uid": uid, "email": current_user.get("email"), "updated_at": now,
                     "created_at": old.get("created_at", now)}
        tx.set(user_ref, user_data)
        return user_data
    user_data = sync(db.transaction())

    return {
        "message": "사용자 정보가 Firestore에 저장되었습니다.",
        "user": serialize_dict(user_data),
    }


# --------------------------------------------------
# 구독 정보 저장
# --------------------------------------------------
@app.post("/subscriptions/save")
def save_subscription(
    request: SubscriptionSaveRequest,
    current_user: dict = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, max_length=200),
):
    uid = current_user["uid"]
    email = current_user.get("email")
    settings = request.engine_settings
    if settings is None and any((request.category, request.send_time, request.duration_weeks)):
        if not request.agreed_privacy:
            raise HTTPException(400, "개인정보 동의가 필요합니다.")
        if not all((request.category, request.send_time, request.duration_weeks)):
            raise HTTPException(422, "구독 설정을 모두 입력해주세요.")
        hour, minute = map(int, request.send_time.split(":"))
        if minute:
            raise HTTPException(422, "발송 시간은 정시만 선택할 수 있습니다.")
        try:
            settings = SubscriptionSettings(categories=[request.category], keywords=request.keywords,
                delivery_hour_kst=hour, duration_days=request.duration_weeks * 7,
                consent_version=os.getenv("CONSENT_VERSION", "v1"))
        except ValidationError:
            raise HTTPException(422, "구독 설정이 올바르지 않습니다.") from None
    if settings is None:
        raise HTTPException(422, "SUBSCRIPTION_SETTINGS_REQUIRED")
    if any(v not in public_categories() for v in settings.categories):
        raise HTTPException(422, "CATEGORY_NOT_PUBLIC")
    if settings.consent_version != os.getenv("CONSENT_VERSION", "v1"):
        raise HTTPException(409, "개인정보 안내가 변경되었습니다. 다시 동의해주세요.")
    if not email:
        raise HTTPException(400, "인증 계정의 이메일이 필요합니다.")
    fingerprint = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
    now = datetime.now(timezone.utc)
    ref = db.collection("subscriptions").document(uid)

    @firestore.transactional
    def save(transaction):
        doc = ref.get(transaction=transaction)
        previous = doc.to_dict() if doc.exists else {}
        owner = db.collection("users").document(uid).get(transaction=transaction)
        account_fence(db, uid, transaction)
        if previous.get("status") == "privacy_cleaning":
            raise HTTPException(409, "PRIVACY_CLEANUP_IN_PROGRESS")
        if owner.exists and deleted(owner.to_dict()):
            raise HTTPException(410, "ACCOUNT_DELETION_PENDING")
        if idempotency_key and previous.get("request_key") == idempotency_key:
            if previous.get("request_fingerprint") != fingerprint:
                raise HTTPException(409, "동일한 요청 키에 다른 구독 설정이 사용되었습니다.")
            if subscription_status(previous) != "active":
                raise HTTPException(409, "종료된 신청입니다. 새 요청으로 재구독해주세요.")
            return previous
        # Read legacy random-ID documents too, before any transaction writes.
        legacy = list(db.collection("subscriptions").where(
            filter=FieldFilter("uid", "==", uid)).stream(transaction=transaction))
        if any(subscription_status(row.to_dict()) == "active" and
               (row.to_dict().get("end_date_exclusive") or row.to_dict().get("end_date"))
               for row in legacy) or (subscription_status(previous) == "active" and
                                     previous.get("end_date_exclusive")):
            raise HTTPException(409, "이미 활성 구독이 있습니다. 먼저 해제해주세요.")
        data = {"uid": uid, "email": email, "plan": request.plan, "status": "active",
                "subscription_id": str(uuid4()), "created_at": now, "updated_at": now,
                "cancelled_at": None, "canceled_at": None,
                "consent_at": now, "settings_version": 1,
                "request_key": idempotency_key, "request_fingerprint": fingerprint}
        if settings:
            start = now.astimezone(KST).date() + timedelta(days=1)
            end = start + timedelta(days=settings.duration_days)
            data.update(settings.model_dump())
            data.update(start_date=start.isoformat(), first_delivery_date=start.isoformat(),
                        end_date_exclusive=end.isoformat(), expires_at=instant(end, 0), cleanup_after=instant(end, 0) + timedelta(days=29),
                        end_date=(end - timedelta(days=1)).isoformat(),
                        category=settings.categories[0],
                        send_time=f"{settings.delivery_hour_kst:02d}:00",
                        duration_weeks=settings.duration_days // 7, agreed_privacy=True)
        data["effective_date"] = data["start_date"]
        data["settings_versions"] = [{**settings.model_dump(), "settings_version": 1,
                                      "effective_date": data["start_date"]}]
        data["preview_requested_at"] = now
        data["preview_settings"] = settings.model_dump()
        data["preview_dispatch_status"] = "pending"
        # Retain previous identity independently, so a new subscription cannot erase its retention deadline.
        if previous.get("subscription_id"):
            previous["snapshot_parent_id"] = uid
            if "cleanup_after" not in previous:
                ended = previous.get("cancelled_at") or previous.get("expires_at") or now
                previous["cleanup_after"] = ended + timedelta(days=29)
            transaction.set(db.collection("subscription_history").document(previous["subscription_id"]), previous)
        transaction.set(ref, data)
        if not owner.exists:
            transaction.set(db.collection("users").document(uid), {"uid": uid, "email": email, "created_at": now})
        return data

    subscription_data = save(db.transaction())
    try:
        preview_dispatch = dispatch_preview(db, ref, subscription_data["subscription_id"])
    except Exception:
        preview_dispatch = "failed"
    return {
        "message": "구독이 저장되었습니다.",
        "subscription_id": subscription_data["subscription_id"],
        "subscription": subscription_response(subscription_data),
        "preview_dispatch_status": preview_dispatch,
    }


def subscription_status(data):
    status = data.get("status")
    if status in ("cancelled", "canceled"):
        return "cancelled"
    end = data.get("end_date_exclusive")
    if not end and data.get("end_date"):
        end = (date.fromisoformat(data["end_date"]) + timedelta(days=1)).isoformat()
    if status == "active" and end and datetime.now(KST).date().isoformat() >= end:
        return "expired"
    return status


def subscription_response(data):
    result = {key: value for key, value in data.items()
              if key not in ("request_key", "request_fingerprint")}
    result["status"] = subscription_status(data)
    if not result.get("end_date_exclusive") and result.get("end_date"):
        result["end_date_exclusive"] = (date.fromisoformat(result["end_date"]) + timedelta(days=1)).isoformat()
    if not result.get("categories") and result.get("category"):
        result["categories"] = [result["category"]]
    if "delivery_hour_kst" not in result and result.get("send_time"):
        result["delivery_hour_kst"] = int(result["send_time"].split(":")[0])
    today = datetime.now(KST).date()
    applied = effective_settings(data, today)
    for key in ("categories", "keywords", "delivery_hour_kst"):
        if key in applied:
            result[key] = applied[key]
    if applied.get("categories"):
        result["category"] = applied["categories"][0]
    if "delivery_hour_kst" in applied:
        result["send_time"] = f'{applied["delivery_hour_kst"]:02d}:00'
    result["current_settings"] = {k: applied.get(k) for k in ("categories", "keywords", "delivery_hour_kst", "settings_version", "effective_date")}
    pending = [v for v in data.get("settings_versions", []) if v["effective_date"] > today.isoformat()]
    result["next_settings"] = max(pending, key=lambda v: v["settings_version"]) if pending else None
    result.pop("settings_versions", None)
    return serialize_dict(result)


def find_subscription(uid, transaction=None):
    doc = db.collection("subscriptions").document(uid).get(transaction=transaction)
    if doc.exists:
        return doc
    docs = list(db.collection("subscriptions").where(
        filter=FieldFilter("uid", "==", uid)).stream(transaction=transaction))
    if not docs:
        raise HTTPException(404, "구독이 없습니다.")
    return max(docs, key=lambda row: (subscription_status(row.to_dict()) == "active",
                                     str(row.to_dict().get("created_at", ""))))


# --------------------------------------------------
# 내 구독 조회
# --------------------------------------------------
@app.get("/subscriptions/me")
def get_my_subscription(current_user: dict = Depends(get_current_user)):
    doc = find_subscription(current_user["uid"])
    data = doc.to_dict()
    data.setdefault("subscription_id", doc.id)
    return {
        "has_active_subscription": subscription_status(data) == "active",
        "subscription": subscription_response(data),
    }


# --------------------------------------------------
# 구독 해제
# --------------------------------------------------
class CancelRequest(BaseModel):
    subscription_id: str = Field(min_length=1, max_length=1500, pattern=r"^[^/]+$")
    confirm: bool = Field(strict=True)


@app.patch("/subscriptions/cancel")
def cancel_subscription(request: CancelRequest, current_user: dict = Depends(get_current_user)):
    if request.confirm is not True:
        raise HTTPException(422, "CONFIRM_REQUIRED")
    @firestore.transactional
    def cancel(transaction):
        doc = find_subscription(current_user["uid"], transaction)
        data = doc.to_dict()
        data.setdefault("subscription_id", doc.id)
        if data["subscription_id"] != request.subscription_id:
            raise HTTPException(404, "SUBSCRIPTION_NOT_FOUND")
        if subscription_status(data) == "active":
            now = datetime.now(timezone.utc)
            data.update(status="cancelled", cancelled_at=now, canceled_at=now, updated_at=now,
                        cleanup_after=min(data.get("cleanup_after", now + timedelta(days=29)), now + timedelta(days=29)))
            transaction.set(doc.reference, data)
        return data
    data = cancel(db.transaction())
    return {
        "message": "구독이 해제되었습니다.",
        "subscription_id": data["subscription_id"],
        "subscription": subscription_response(data),
    }


# --------------------------------------------------
# 피드백 저장
# --------------------------------------------------
@app.post("/feedback/save")
def save_feedback(current_user: dict = Depends(get_current_user)):
    raise HTTPException(410, "USE_TOKEN_FEEDBACK_API")


# --------------------------------------------------
# 내 피드백 조회
# --------------------------------------------------
@app.get("/feedback/me")
def get_my_feedback(current_user: dict = Depends(get_current_user)):
    uid = current_user["uid"]

    feedbacks_ref = db.collection("feedbacks").where(filter=FieldFilter("user_id", "==", uid)).limit(100).stream()

    result = []

    for doc in feedbacks_ref:
        data = doc.to_dict()
        data["id"] = doc.id
        result.append(serialize_dict(data))

    return {
        "feedbacks": result,
    }


# --------------------------------------------------
# 엔진 상태 확인
# AI 뉴스 발송 엔진 전용 API
# --------------------------------------------------
@app.get("/engine/health")
def engine_health(_: bool = Depends(get_engine_auth)):
    return {
        "message": "엔진 API 인증 성공",
        "status": "ok",
    }


# --------------------------------------------------
# 엔진용 발송 대상 구독 조회
# target_date 기준 active + 기간 내 구독 조회
# send_time을 넘기면 해당 발송 시간 구독만 조회
# --------------------------------------------------
@app.get("/engine/subscriptions/active")
@app.get("/engine/subscriptions/expired")
@app.patch("/engine/subscriptions/expire")
def legacy_engine_routes(_: bool = Depends(get_engine_auth)):
    raise HTTPException(410, "USE_V1_ENGINE_API")


try:
    from .user_api import create_user_router
except ImportError:
    from user_api import create_user_router
user_router = create_user_router(lambda: db, get_current_user, subscription_response, cancel_subscription)
app.include_router(user_router)
app.include_router(user_router, prefix="/api/v1")


@app.exception_handler(RequestValidationError)
async def invalid_input(request, exc):
    return JSONResponse(status_code=422, content={"detail": "INVALID_INPUT",
        "error": {"code": "INVALID_INPUT", "message": "입력 값을 확인해주세요."}}, headers={"Cache-Control": "no-store"})


@app.exception_handler(HTTPException)
async def api_error(request, exc):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail,
        "error": {"code": str(exc.detail), "message": str(exc.detail)}},
        headers={**(exc.headers or {}), "Cache-Control": "no-store"})


@app.post("/subscriptions")
def create_subscription(settings: SubscriptionSettings, current_user: dict = Depends(get_current_user),
                        idempotency_key: Optional[str] = Header(None, max_length=200)):
    return save_subscription(SubscriptionSaveRequest(engine_settings=settings), current_user, idempotency_key)


# Versioned common-PRD routes plus the existing browser paths during frontend migration.
from fastapi import APIRouter
from fastapi.routing import APIRoute
browser_routes = [r for r in app.routes if isinstance(r, APIRoute) and
                  r.path.startswith(("/catalog", "/me", "/users/", "/subscriptions", "/feedback", "/account-deletion-requests"))]
app.include_router(APIRouter(routes=browser_routes), prefix="/api/v1")


@app.middleware("http")
async def private_response_headers(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response
