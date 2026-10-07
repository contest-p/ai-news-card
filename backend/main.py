
import os
import json
import hashlib
from uuid import uuid4
from datetime import datetime, timezone, timedelta, date
from typing import Optional, Any, List

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, field_validator, ValidationError

import firebase_admin
from firebase_admin import credentials, auth, firestore
from zoneinfo import ZoneInfo
from pathlib import Path
from google.cloud.firestore_v1.base_query import FieldFilter

try:
    from .engine_api import SubscriptionSettings, create_router, instant
except ImportError:
    from engine_api import SubscriptionSettings, create_router, instant

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
        decoded_token = auth.verify_id_token(token)

        return {
            "uid": decoded_token.get("uid"),
            "email": decoded_token.get("email"),
            "name": decoded_token.get("name")
            or decoded_token.get("email", "").split("@")[0],
        }

    except Exception as e:
        raise HTTPException(status_code=401, detail=f"토큰 검증 실패: {str(e)}")


def get_engine_auth(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
):
    """
    AI 뉴스 발송 엔진 전용 인증
    Authorization: Bearer ENGINE_API_TOKEN
    """

    engine_token = os.getenv("ENGINE_API_TOKEN")

    if not engine_token:
        raise HTTPException(
            status_code=500,
            detail="서버에 ENGINE_API_TOKEN 환경변수가 설정되어 있지 않습니다.",
        )

    if not credentials:
        raise HTTPException(status_code=401, detail="엔진 인증 토큰이 없습니다.")

    if credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Bearer 토큰 형식이 아닙니다.")

    if credentials.credentials != engine_token:
        raise HTTPException(status_code=401, detail="엔진 인증 토큰이 올바르지 않습니다.")

    return True


# --------------------------------------------------
# 요청 모델
# 중요: 모델은 라우터 함수보다 위에 있어야 함
# --------------------------------------------------
class SubscriptionSaveRequest(BaseModel):
    plan: str = "basic"
    engine_settings: Optional[SubscriptionSettings] = None
    category: Optional[str] = None
    keywords: List[str] = Field(default_factory=list)
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
        cleaned = [k.strip() for k in v if k.strip()]
        return cleaned


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
        "categories": ["economy", "it_science", "politics", "society", "world", "culture"],
        "durations": [7, 14, 28],
        "consent_version": os.getenv("CONSENT_VERSION", "v1"),
        "capabilities": {"settings_change": False, "feedback": False},
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
    existing_doc = user_ref.get()

    now = datetime.now(timezone.utc)

    user_data = {
        "uid": uid,
        "email": current_user.get("email"),
        "name": current_user.get("name"),
        "updated_at": now,
    }

    if existing_doc.exists:
        old_data = existing_doc.to_dict()
        user_data["created_at"] = old_data.get("created_at", now)
    else:
        user_data["created_at"] = now

    user_ref.set(user_data, merge=True)

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
    if settings and settings.consent_version != os.getenv("CONSENT_VERSION", "v1"):
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
                "request_key": idempotency_key, "request_fingerprint": fingerprint}
        if settings:
            start = datetime.now(KST).date() + timedelta(days=1)
            end = start + timedelta(days=settings.duration_days)
            data.update(settings.model_dump())
            data.update(start_date=start.isoformat(), first_delivery_date=start.isoformat(),
                        end_date_exclusive=end.isoformat(), expires_at=instant(end, 0),
                        end_date=(end - timedelta(days=1)).isoformat(),
                        category=settings.categories[0],
                        send_time=f"{settings.delivery_hour_kst:02d}:00",
                        duration_weeks=settings.duration_days // 7, agreed_privacy=True)
        transaction.set(ref, data)
        return data

    subscription_data = save(db.transaction())
    return {
        "message": "구독이 저장되었습니다.",
        "subscription_id": subscription_data["subscription_id"],
        "subscription": subscription_response(subscription_data),
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
@app.patch("/subscriptions/cancel")
def cancel_subscription(current_user: dict = Depends(get_current_user)):
    @firestore.transactional
    def cancel(transaction):
        doc = find_subscription(current_user["uid"], transaction)
        data = doc.to_dict()
        data.setdefault("subscription_id", doc.id)
        if subscription_status(data) == "active":
            now = datetime.now(timezone.utc)
            data.update(status="cancelled", cancelled_at=now, canceled_at=now, updated_at=now)
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
def save_feedback(
    feedback: FeedbackCreate,
    current_user: dict = Depends(get_current_user),
):
    uid = current_user["uid"]
    email = current_user.get("email")

    # 구독 문서가 실제로 존재하고, 본인 구독인지 확인
    subscription_ref = db.collection("subscriptions").document(feedback.subscription_id)
    subscription_doc = subscription_ref.get()

    if not subscription_doc.exists:
        raise HTTPException(status_code=404, detail="구독 정보를 찾을 수 없습니다.")

    subscription_data = subscription_doc.to_dict()

    if subscription_data.get("uid") != uid:
        raise HTTPException(
            status_code=403,
            detail="본인의 구독에만 피드백을 남길 수 있습니다.",
        )

    now = datetime.now(timezone.utc)

    feedback_data = {
        "uid": uid,
        "email": email,
        "subscription_id": feedback.subscription_id,
        "rating": feedback.rating,
        "comment": feedback.comment,
        "created_at": now,
    }

    doc_ref = db.collection("feedbacks").document()
    doc_ref.set(feedback_data)

    return {
        "message": "피드백이 저장되었습니다.",
        "feedback_id": doc_ref.id,
        "feedback": serialize_dict(feedback_data),
    }


# --------------------------------------------------
# 내 피드백 조회
# --------------------------------------------------
@app.get("/feedback/me")
def get_my_feedback(current_user: dict = Depends(get_current_user)):
    uid = current_user["uid"]

    feedbacks_ref = db.collection("feedbacks").where("uid", "==", uid).stream()

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
def get_active_subscriptions_for_engine(
    target_date: Optional[str] = None,
    send_time: Optional[str] = None,
    _: bool = Depends(get_engine_auth),
):
    target_date = normalize_target_date(target_date)

    docs = db.collection("subscriptions").where("status", "==", "active").stream()

    result = []

    for doc in docs:
        data = doc.to_dict()

        if not is_sendable_subscription(data, target_date):
            continue

        if send_time and data.get("send_time") != send_time:
            continue

        data["id"] = doc.id
        result.append(serialize_dict(data))

    return {
        "target_date": target_date,
        "send_time": send_time,
        "count": len(result),
        "subscriptions": result,
    }


# --------------------------------------------------
# 엔진용 만료 대상 구독 조회
# target_date 기준 end_date가 지난 active 구독 조회
# --------------------------------------------------
@app.get("/engine/subscriptions/expired")
def get_expired_subscriptions_for_engine(
    target_date: Optional[str] = None,
    _: bool = Depends(get_engine_auth),
):
    target_date = normalize_target_date(target_date)

    docs = db.collection("subscriptions").where("status", "==", "active").stream()

    result = []

    for doc in docs:
        data = doc.to_dict()

        if not is_expired_subscription(data, target_date):
            continue

        data["id"] = doc.id
        result.append(serialize_dict(data))

    return {
        "target_date": target_date,
        "count": len(result),
        "subscriptions": result,
    }


# --------------------------------------------------
# 엔진용 만료 처리
# target_date 기준 만료된 active 구독을 expired로 변경
# --------------------------------------------------
@app.patch("/engine/subscriptions/expire")
def expire_subscriptions_for_engine(
    target_date: Optional[str] = None,
    _: bool = Depends(get_engine_auth),
):
    target_date = normalize_target_date(target_date)

    docs = db.collection("subscriptions").where("status", "==", "active").stream()

    expired_items = []
    now_kst = datetime.now(KST).isoformat()

    for doc in docs:
        data = doc.to_dict()

        if not is_expired_subscription(data, target_date):
            continue

        update_data = {
            "status": "expired",
            "expired_at": now_kst,
            "updated_at": now_kst,
        }

        doc.reference.update(update_data)

        data.update(update_data)
        data["id"] = doc.id
        expired_items.append(serialize_dict(data))

    return {
        "message": "만료 구독 처리가 완료되었습니다.",
        "target_date": target_date,
        "count": len(expired_items),
        "subscriptions": expired_items,
    }
