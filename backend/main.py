
import os
from datetime import datetime, timezone, timedelta, date
from typing import Optional, Any, List

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, field_validator

import firebase_admin
from firebase_admin import credentials, auth, firestore
from zoneinfo import ZoneInfo
from pathlib import Path

# --------------------------------------------------
# 환경변수 로드
# --------------------------------------------------
load_dotenv()


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
        or os.getenv("FIREBASE_CREDENTIALS")
        or os.getenv("FIREBASE_CREDENTIALS_PATH")
    )

    if env_path and os.path.exists(env_path):
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

    raise FileNotFoundError("Firebase 서비스 계정 JSON 파일을 찾을 수 없습니다.")


if not firebase_admin._apps:
    cred_path = get_firebase_cred_path()
    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred)

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
    category: str
    keywords: List[str]
    send_time: str
    duration_weeks: int
    agreed_privacy: bool

    @field_validator("duration_weeks")
    @classmethod
    def validate_duration(cls, v):
        if v not in [1, 2, 4]:
            raise ValueError("duration_weeks must be 1, 2, or 4")
        return v

    @field_validator("send_time")
    @classmethod
    def validate_send_time(cls, v):
        try:
            datetime.strptime(v, "%H:%M")
        except ValueError:
            raise ValueError("send_time must be HH:MM format")
        return v

    @field_validator("keywords")
    @classmethod
    def validate_keywords(cls, v):
        cleaned = [k.strip() for k in v if k.strip()]
        if not cleaned:
            raise ValueError("keywords must not be empty")
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
):
    uid = current_user["uid"]
    email = current_user.get("email")

    # 개인정보 동의 필수
    if not request.agreed_privacy:
        raise HTTPException(status_code=400, detail="개인정보 동의가 필요합니다.")

    # 이미 활성 구독이 있는지 확인
    existing_docs = db.collection("subscriptions").where("uid", "==", uid).stream()

    for doc in existing_docs:
        data = doc.to_dict()

        if data.get("status") == "active":
            raise HTTPException(
                status_code=400,
                detail="이미 활성 구독이 있습니다. 먼저 해제해주세요.",
            )

    dates = calculate_subscription_dates(request.duration_weeks)
    now_kst = datetime.now(KST).isoformat()

    subscription_data = {
        "uid": uid,
        "email": email,
        "category": request.category,
        "keywords": request.keywords,
        "send_time": request.send_time,
        "duration_weeks": request.duration_weeks,
        "agreed_privacy": request.agreed_privacy,
        "status": "active",
        "start_date": dates["start_date"],
        "end_date": dates["end_date"],
        "created_at": now_kst,
        "updated_at": now_kst,
        "canceled_at": None,
    }

    doc_ref = db.collection("subscriptions").document()
    doc_ref.set(subscription_data)

    return {
        "message": "구독이 저장되었습니다.",
        "subscription_id": doc_ref.id,
        "subscription": subscription_data,
    }


# --------------------------------------------------
# 내 구독 조회
# --------------------------------------------------
@app.get("/subscriptions/me")
def get_my_subscription(current_user: dict = Depends(get_current_user)):
    uid = current_user["uid"]

    docs = db.collection("subscriptions").where("uid", "==", uid).stream()

    active_subscription = None

    for doc in docs:
        data = doc.to_dict()

        if data.get("status") == "active":
            data["id"] = doc.id
            active_subscription = data
            break

    if not active_subscription:
        return {
            "has_active_subscription": False,
            "subscription": None,
        }

    return {
        "has_active_subscription": True,
        "subscription": serialize_dict(active_subscription),
    }


# --------------------------------------------------
# 구독 해제
# --------------------------------------------------
@app.patch("/subscriptions/cancel")
def cancel_subscription(current_user: dict = Depends(get_current_user)):
    uid = current_user["uid"]

    docs = db.collection("subscriptions").where("uid", "==", uid).stream()

    active_doc = None
    active_data = None

    for doc in docs:
        data = doc.to_dict()

        if data.get("status") == "active":
            active_doc = doc
            active_data = data
            break

    if not active_doc:
        raise HTTPException(status_code=404, detail="활성 구독이 없습니다.")

    now_kst = datetime.now(KST).isoformat()

    update_data = {
        "status": "canceled",
        "canceled_at": now_kst,
        "updated_at": now_kst,
    }

    active_doc.reference.update(update_data)

    return {
        "message": "구독이 해제되었습니다.",
        "subscription_id": active_doc.id,
        "previous_status": active_data.get("status"),
        "new_status": "canceled",
        "canceled_at": now_kst,
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