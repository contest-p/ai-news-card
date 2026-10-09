"""발송 작업(공통 PRD 10-4): 고정 작업 ID·발송 기한·원자적 선점·상태 전이.

저장소는 JobStore 계약으로 분리한다. 여기의 InMemoryJobStore는 테스트·로컬 시연용이며,
운영은 같은 계약을 Firestore 고정 문서 ID + 트랜잭션으로 구현한다(2026-10-07 DB 작업).
트랜잭션 콜백 안에서는 AI 호출·메일 발송을 하지 않는다.
"""

from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from threading import RLock
from typing import Protocol
import uuid

from engine.selection import KST, DeliveryHistory, aware_utc, parse_timestamp

JOB_SCHEMA = "delivery-job-v1-proposal"
MAIL_KINDS = frozenset({"daily_briefing", "subscription_end", "subscription_preview"})
STATUSES = frozenset({"pending", "processing", "sending", "sent", "failed", "unknown",
                      "skipped_late", "cancelled"})
# 공통 PRD 10-4 상태도. processing→processing은 선점 유지 중 필드 기록용이다.
TRANSITIONS = {
    "pending": frozenset({"processing", "skipped_late", "cancelled"}),
    "processing": frozenset({"processing", "sending", "failed", "skipped_late", "cancelled"}),
    "failed": frozenset({"processing", "skipped_late", "cancelled"}),
    "sending": frozenset({"sent", "failed", "unknown"}),
}
OWNED_STATUSES = frozenset({"processing", "sending"})
SMTP_MAX_ATTEMPTS = 3            # P-15: 명확한 일시 실패만, 최초 포함
NEWS_DELAY_LIMIT = timedelta(hours=3)      # P-07
END_NOTICE_WINDOW = timedelta(hours=24)    # P-08
# 배치 예산 45분보다 길게 둔다. 실행 중인 배치의 작업을 다음 실행이 빼앗지 않게 한다.
CLAIM_TTL = timedelta(minutes=60)


class ClaimLost(RuntimeError):
    """선점 토큰이 다르거나 선점이 만료되어 이 실행이 작업을 바꿀 수 없다."""


class InvalidTransition(ValueError):
    pass


def kst_midnight(day: date) -> datetime:
    """KST 날짜의 00:00을 UTC로 돌려준다."""
    return datetime.combine(day, time.min, tzinfo=KST).astimezone(timezone.utc)


def make_job_id(subscription_id: str, scheduled_date_kst: date, mail_kind: str) -> str:
    """(구독, KST 날짜, 메일 종류)를 JSON 배열로 인코딩해 hash. 구분자 충돌이 없다."""
    if type(subscription_id) is not str or not subscription_id:
        raise ValueError("SUBSCRIPTION_ID_REQUIRED")
    if type(scheduled_date_kst) is not date or mail_kind not in MAIL_KINDS:
        raise ValueError("JOB_IDENTITY_INVALID")
    encoded = json.dumps([JOB_SCHEMA, subscription_id, scheduled_date_kst.isoformat(), mail_kind],
                         ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def news_deadline(scheduled_at: datetime, scheduled_date_kst: date, end_date_exclusive: date) -> datetime:
    """예정 시각+3시간, 다음 KST 자정, 구독 만료 중 가장 이른 시각(공통 PRD 4-2)."""
    return min(aware_utc(scheduled_at) + NEWS_DELAY_LIMIT,
               kst_midnight(scheduled_date_kst + timedelta(days=1)),
               kst_midnight(end_date_exclusive))


@dataclass(frozen=True)
class DeliveryJob:
    job_id: str
    subscription_id: str
    user_id: str
    scheduled_date_kst: str          # Firestore에 date 타입이 없으므로 YYYY-MM-DD 문자열
    mail_kind: str
    scheduled_at: datetime
    deadline_at: datetime
    # 수신 주소가 포함된 날짜별 스냅샷. repr·로그에 노출하지 않는다.
    settings_snapshot: dict = field(repr=False)
    status: str = "pending"
    smtp_attempts: int = 0
    retryable: bool = False
    next_retry_at: datetime | None = None
    claim_token: str | None = field(default=None, repr=False)
    claimed_at: datetime | None = None
    run_id: str | None = None
    error_code: str | None = None
    content_kind: str | None = None
    selected_article_id: str | None = None
    selected_article_url: str | None = None
    generation_key: str | None = None
    sent_at: datetime | None = None
    updated_at: datetime | None = None


def new_job(snapshot: dict, mail_kind: str) -> DeliveryJob:
    """Backend 10-1 스냅샷으로 작업을 만든다. 기한은 Backend 값과 엔진 계산 중 이른 쪽."""
    if snapshot.get("timezone") != "Asia/Seoul":
        raise ValueError("구독 시간대는 Asia/Seoul이어야 합니다.")
    start = date.fromisoformat(snapshot["start_date"])
    end = date.fromisoformat(snapshot["end_date_exclusive"])
    if (end - start).days not in {7, 14, 28}:
        raise ValueError("SUBSCRIPTION_PERIOD_INVALID")
    if mail_kind == "daily_briefing":
        day = date.fromisoformat(snapshot["scheduled_date_kst"])
        scheduled = parse_timestamp(snapshot["scheduled_at"])
        if scheduled.astimezone(KST).date() != day or not start <= day < end:
            raise ValueError("SCHEDULED_DATE_INVALID")
        deadline = min(parse_timestamp(snapshot["deadline_at"]), news_deadline(scheduled, day, end))
    elif mail_kind == "subscription_end":
        day = end
        scheduled = kst_midnight(end)
        deadline = scheduled + END_NOTICE_WINDOW
    elif mail_kind == "subscription_preview":
        day = date.fromisoformat(snapshot["scheduled_date_kst"])
        scheduled = parse_timestamp(snapshot["scheduled_at"])
        if scheduled.astimezone(KST).date() != day:
            raise ValueError("SCHEDULED_DATE_INVALID")
        deadline = min(parse_timestamp(snapshot["deadline_at"]), scheduled + timedelta(hours=24),
                       kst_midnight(end))
    else:
        raise ValueError("MAIL_KIND_INVALID")
    if deadline <= scheduled:
        raise ValueError("DEADLINE_NOT_AFTER_SCHEDULE")
    return DeliveryJob(job_id=make_job_id(snapshot["subscription_id"], day, mail_kind),
                       subscription_id=snapshot["subscription_id"], user_id=snapshot["user_id"],
                       scheduled_date_kst=day.isoformat(), mail_kind=mail_kind,
                       scheduled_at=scheduled, deadline_at=deadline, settings_snapshot=deepcopy(snapshot))


def claim_decision(job: DeliveryJob, now: datetime, claim_ttl: timedelta = CLAIM_TTL) -> str:
    """claim / skip_late / mark_unknown / skip. 저장소 트랜잭션 안에서 쓰는 순수 판단."""
    now = aware_utc(now)
    if job.status in {"pending", "failed"}:
        if now >= job.deadline_at:
            return "skip_late"
        if now < job.scheduled_at:
            return "skip"
        if job.status == "failed" and (not job.retryable or job.smtp_attempts >= SMTP_MAX_ATTEMPTS
                                       or (job.next_retry_at is not None and now < job.next_retry_at)):
            return "skip"
        return "claim"
    stale = job.claimed_at is None or now >= job.claimed_at + claim_ttl
    if job.status == "processing" and stale:
        # processing은 SMTP 시도 전 단계다. 이전 소유권을 새 토큰으로 끊고 다시 처리한다.
        return "skip_late" if now >= job.deadline_at else "claim"
    if job.status == "sending" and stale:
        # 이미 전송됐을 수 있다. 자동 재전송하지 않는다.
        return "mark_unknown"
    return "skip"


class JobStore(Protocol):
    def create_if_absent(self, job: DeliveryJob) -> DeliveryJob: ...

    def get(self, job_id: str) -> DeliveryJob | None: ...

    def claim(self, job_id: str, *, run_id: str, now: datetime,
              claim_ttl: timedelta = CLAIM_TTL) -> DeliveryJob | None: ...

    def transition(self, job_id: str, *, claim_token: str, to_status: str, now: datetime,
                   **fields) -> DeliveryJob: ...

    def recent_history(self, user_id: str, *, since: datetime) -> list[DeliveryHistory]: ...

    def open_jobs(self) -> list[DeliveryJob]: ...


UPDATABLE_FIELDS = frozenset({"smtp_attempts", "retryable", "next_retry_at", "error_code", "content_kind",
                              "selected_article_id", "selected_article_url", "generation_key", "sent_at"})


def apply_transition(job: DeliveryJob, *, claim_token: str, to_status: str, now: datetime,
                     fields: dict) -> DeliveryJob:
    """소유권·전이 규칙을 검사한 새 작업 값. 저장소 구현이 공유한다."""
    if job.status not in OWNED_STATUSES or job.claim_token is None or claim_token != job.claim_token:
        raise ClaimLost("CLAIM_LOST")
    if to_status not in TRANSITIONS.get(job.status, frozenset()):
        raise InvalidTransition(f"{job.status}->{to_status}")
    unknown = set(fields) - UPDATABLE_FIELDS
    if unknown:
        raise ValueError("JOB_FIELDS_INVALID")
    if "smtp_attempts" in fields and not job.smtp_attempts <= fields["smtp_attempts"] <= SMTP_MAX_ATTEMPTS:
        raise ValueError("SMTP_ATTEMPTS_INVALID")  # 재실행으로 횟수를 줄이거나 초기화하지 않는다.
    updated = replace(job, status=to_status, updated_at=aware_utc(now), **fields)
    if to_status not in OWNED_STATUSES:
        updated = replace(updated, claim_token=None)  # 종료·실패 후에는 이 실행이 더 바꿀 수 없다.
    return updated


class InMemoryJobStore:
    """테스트·로컬 시연 전용. 프로세스 종료 시 사라지며 운영 DB를 대체하지 않는다."""

    def __init__(self):
        self._jobs: dict[str, DeliveryJob] = {}
        self._lock = RLock()

    def create_if_absent(self, job: DeliveryJob) -> DeliveryJob:
        with self._lock:
            return deepcopy(self._jobs.setdefault(job.job_id, deepcopy(job)))

    def get(self, job_id: str) -> DeliveryJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return deepcopy(job) if job else None

    def claim(self, job_id, *, run_id, now, claim_ttl=CLAIM_TTL):
        now = aware_utc(now)
        with self._lock:
            job = self._jobs[job_id]
            decision = claim_decision(job, now, claim_ttl)
            if decision == "skip_late":
                self._jobs[job_id] = replace(job, status="skipped_late", claim_token=None,
                                             error_code="DEADLINE_PASSED", updated_at=now)
            elif decision == "mark_unknown":
                self._jobs[job_id] = replace(job, status="unknown", claim_token=None,
                                             error_code="SENDING_INTERRUPTED", updated_at=now)
            elif decision == "claim":
                self._jobs[job_id] = replace(job, status="processing", claim_token=uuid.uuid4().hex,
                                             claimed_at=now, run_id=run_id, updated_at=now)
                return deepcopy(self._jobs[job_id])
            return None

    def transition(self, job_id, *, claim_token, to_status, now, **fields):
        with self._lock:
            updated = apply_transition(self._jobs[job_id], claim_token=claim_token,
                                       to_status=to_status, now=now, fields=fields)
            self._jobs[job_id] = updated
            return deepcopy(updated)

    def recent_history(self, user_id, *, since):
        since = aware_utc(since)
        with self._lock:
            rows = [DeliveryHistory(job.selected_article_url, job.status, job.updated_at or job.scheduled_at)
                    for job in self._jobs.values()
                    if job.user_id == user_id and job.selected_article_url
                    and (job.updated_at or job.scheduled_at) >= since]
        return sorted(rows, key=lambda row: row.attempted_at)

    def open_jobs(self):
        with self._lock:
            return [deepcopy(job) for job in self._jobs.values()
                    if job.status in {"pending", "processing", "sending"}
                    or (job.status == "failed" and job.retryable)]
