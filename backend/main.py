import os
from datetime import datetime, timezone, date
from typing import Optional, Literal, Any
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

import firebase_admin
from firebase_admin import credentials, auth, firestore
try:
    from .engine_api import create_router, SubscriptionSettings, KST, instant
except ImportError:
    from engine_api import create_router, SubscriptionSettings, KST, instant


# --------------------------------------------------
# 환경변수 로드
# --------------------------------------------------
load_dotenv(Path(__file__).with_name(".env"), override=False)

app = FastAPI()


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
    # .env에 FIREBASE_SERVICE_ACCOUNT_KEY가 있으면 우선 사용
    env_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY")
    if env_path:
        if not os.path.isfile(env_path):
            raise FileNotFoundError("FIREBASE_SERVICE_ACCOUNT_KEY 파일을 찾을 수 없습니다.")
        return env_path

    # 현재 폴더 기준 기본 파일명 후보들
    candidates = [
        "firebase-service-account.json",
        "ai-news-card-firebase-adminsdk.json",
    ]

    for path in candidates:
        for candidate in (Path(__file__).parent / path, Path(path)):
            if candidate.is_file():
                return str(candidate)

    return None  # 로컬 gcloud 로그인/배포 서비스 계정의 ADC 사용


if not firebase_admin._apps:
    cred_path = get_firebase_cred_path()
    cred = credentials.Certificate(cred_path) if cred_path else credentials.ApplicationDefault()
    firebase_admin.initialize_app(cred, {"projectId": os.getenv("FIREBASE_PROJECT_ID", "ai-news-card")})

db = firestore.client()


# --------------------------------------------------
# 공통 함수
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
# 인증 의존성
# Swagger Authorize 버튼에도 잘 붙도록 HTTPBearer 사용
# --------------------------------------------------
security = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
):
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


# --------------------------------------------------
# 요청 모델
# --------------------------------------------------
class SubscriptionSaveRequest(BaseModel):
    plan: str
    status: Literal["active", "cancelled", "expired"] = "active"
    expires_at: Optional[datetime] = None
    engine_settings: Optional[SubscriptionSettings] = None
# --------------------------------------------------
# 기본 라우트
# --------------------------------------------------
@app.get("/")
def root():
    return {"message": "FastAPI + Firebase 서버 실행 중"}


@app.get("/catalog")
def catalog():
    return {
        "categories": ["economy", "it_science", "politics", "society", "world", "culture"],
        "durations": [7, 14, 28],
        "consent_version": os.getenv("CONSENT_VERSION", "v1"),
        "capabilities": {"settings_change": False, "feedback": False},
    }


def effective_subscription_status(data, now):
    if data.get("status") != "active":
        return data.get("status")
    if data.get("end_date_exclusive"):
        try:
            if now.astimezone(KST).date() >= date.fromisoformat(data["end_date_exclusive"]):
                return "expired"
        except (TypeError, ValueError):
            pass
    expiry = data.get("expires_at")
    if isinstance(expiry, datetime) and expiry.tzinfo and now >= expiry:
        return "expired"
    return "active"


@app.get("/me")
def me(current_user: dict = Depends(get_current_user)):
    return {
        "message": "인증 성공",
        "user": current_user
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
        "user": {
            "uid": uid,
            "email": current_user.get("email"),
            "name": current_user.get("name"),
            "created_at": to_iso(user_data["created_at"]),
            "updated_at": to_iso(user_data["updated_at"]),
        }
    }


# --------------------------------------------------
# 구독 정보 저장
# 현재 구조는 uid를 문서 id로 사용
# --------------------------------------------------
@app.post("/subscriptions/save")
def save_subscription(
    payload: SubscriptionSaveRequest,
    current_user: dict = Depends(get_current_user)
):
    uid = current_user["uid"]
    sub_ref = db.collection("subscriptions").document(uid)
    existing_doc = sub_ref.get()

    now = datetime.now(timezone.utc)

    subscription_data = {
        "uid": uid,
        "email": current_user.get("email"),
        "name": current_user.get("name"),
        "plan": payload.plan,
        "status": payload.status,
        "expires_at": payload.expires_at,
        "updated_at": now,
    }

    if existing_doc.exists:
        old_data = existing_doc.to_dict()
        subscription_data["created_at"] = old_data.get("created_at", now)

        # 이미 취소된 문서였다가 active로 다시 저장하면 cancelled_at 초기화
        if payload.status == "active":
            subscription_data["cancelled_at"] = None
        elif payload.status == "cancelled":
            subscription_data["cancelled_at"] = now
        else:
            subscription_data["cancelled_at"] = old_data.get("cancelled_at")
    else:
        subscription_data["created_at"] = now
        subscription_data["cancelled_at"] = now if payload.status == "cancelled" else None

    if payload.engine_settings is not None:
        if payload.engine_settings.consent_version != os.getenv("CONSENT_VERSION", "v1"):
            raise HTTPException(409, "개인정보 안내가 변경되었습니다. 새로고침 후 다시 동의해 주세요.")
        if payload.status != "active":
            raise HTTPException(422, "엔진 설정은 활성 구독에만 저장할 수 있습니다.")
        if existing_doc.exists and effective_subscription_status(old_data, now) == "active" and old_data.get("start_date"):
            raise HTTPException(409, "활성 구독 설정 변경 정책은 별도 API에서 처리해야 합니다.")
        from datetime import timedelta
        import uuid
        start = now.astimezone(KST).date() + timedelta(days=1)
        end = start + timedelta(days=payload.engine_settings.duration_days)
        subscription_data.update(payload.engine_settings.model_dump())
        subscription_data.update(start_date=start.isoformat(), end_date_exclusive=end.isoformat(),
                                 expires_at=instant(end), timezone="Asia/Seoul", consent_at=now,
                                 subscription_id=uuid.uuid4().hex)

    sub_ref.set(subscription_data, merge=True)

    return {
        "message": "구독 정보가 Firestore에 저장되었습니다.",
        "subscription": serialize_dict(subscription_data)
    }


# --------------------------------------------------
# 내 구독 조회
# --------------------------------------------------
@app.get("/subscriptions/me")
def get_my_subscription(current_user: dict = Depends(get_current_user)):
    uid = current_user["uid"]
    sub_ref = db.collection("subscriptions").document(uid)
    sub_doc = sub_ref.get()

    if not sub_doc.exists:
        raise HTTPException(status_code=404, detail="구독 정보가 없습니다.")

    sub_data = sub_doc.to_dict()

    sub_data["status"] = effective_subscription_status(sub_data, datetime.now(timezone.utc))

    return {
        "message": "구독 조회 성공",
        "subscription": serialize_dict(sub_data)
    }


# --------------------------------------------------
# 다음 단계: 구독 해제
# --------------------------------------------------
@app.patch("/subscriptions/cancel")
def cancel_subscription(current_user: dict = Depends(get_current_user)):
    uid = current_user["uid"]
    sub_ref = db.collection("subscriptions").document(uid)
    sub_doc = sub_ref.get()

    if not sub_doc.exists:
        raise HTTPException(status_code=404, detail="해제할 구독 정보가 없습니다.")

    sub_data = sub_doc.to_dict()

    if sub_data.get("status") == "cancelled":
        raise HTTPException(status_code=400, detail="이미 해제된 구독입니다.")

    now = datetime.now(timezone.utc)

    update_data = {
        "status": "cancelled",
        "cancelled_at": now,
        "updated_at": now,
    }

    sub_ref.set(update_data, merge=True)

    updated_doc = sub_ref.get().to_dict()

    return {
        "message": "구독이 해제되었습니다.",
        "subscription": serialize_dict(updated_doc)
    }


app.include_router(create_router(lambda: db))
