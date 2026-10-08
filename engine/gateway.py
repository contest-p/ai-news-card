"""Backend 연동 경계의 초안. 실제 인증·DB·구독 정책 구현은 Backend(고승희) 담당.

함수명은 공통 PRD 10-3을 따르고, 인자·반환 타입은 팀 확인 전 제안이다.
내부 함수 또는 보호된 API 중 무엇으로 구현할지는 2026-10-07 DB 작업에서 합의한다.
"""

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

# active: 뉴스 발송 가능 / cancelled: 사용자 해제 / expired: 자연 만료(종료 안내 대상)
# deletion_requested: 계정 삭제 요청 / not_found: 구독 없음·조회 불가
ELIGIBILITY_REASONS = frozenset({"active", "cancelled", "expired", "deletion_requested", "not_found"})


@dataclass(frozen=True)
class Eligibility:
    eligible: bool  # 뉴스(daily_briefing) 발송 가능 여부
    reason: str     # 위 ELIGIBILITY_REASONS. 작업 상태를 cancelled/skipped_late로 구분하는 데 사용

    def __post_init__(self):
        if type(self.eligible) is not bool or self.reason not in ELIGIBILITY_REASONS:
            raise ValueError("ELIGIBILITY_INVALID")
        if self.eligible != (self.reason == "active"):
            raise ValueError("ELIGIBILITY_REASON_MISMATCH")


class EngineGateway(Protocol):
    def list_due_subscriptions(self, now: datetime) -> list[dict]:
        """scheduled_at <= now < deadline_at 인 활성 구독의 10-1 스냅샷(그날 적용 설정)."""

    def list_expired_subscriptions(self, now: datetime) -> list[dict]:
        """최근 24시간 안에 자연 만료된 구독. 수동 해제·삭제 요청은 제외."""

    def get_subscription_snapshot(self, subscription_id: str, scheduled_date_kst: date) -> dict: ...

    def check_delivery_eligibility(self, subscription_id: str, now: datetime) -> Eligibility:
        """SMTP 직전 최신 상태. 조회 실패는 예외로 전달하며 뉴스 없음으로 바꾸지 않는다."""

    def issue_feedback_token(self, job_id: str) -> str:
        """메일에 한 번 넣을 원문 토큰. DB에는 해시만 저장(Backend)."""

    def privacy_cleanup(self, now: datetime) -> dict:
        """Backend-owned daily deletion/anonymous processing; failures must propagate."""


class SampleEngineGateway:
    """저장된 가상 응답만 전달한다. 구독 날짜·권한 계산을 대신 구현하지 않는다."""

    def __init__(self, fixture: dict):
        if fixture.get("demo_only") is not True:
            raise ValueError("가상 구독 샘플만 사용할 수 있습니다.")
        self._snapshot = deepcopy(fixture["subscription_snapshot"])
        eligible = fixture["delivery_eligible_fixture"]
        if type(eligible) is not bool:
            raise ValueError("발송 가능 샘플은 bool이어야 합니다.")
        reason = "active" if eligible else fixture.get("delivery_ineligible_reason_fixture", "cancelled")
        self._eligibility = Eligibility(eligible, reason)

    def list_due_subscriptions(self, now: datetime) -> list[dict]:
        return [deepcopy(self._snapshot)]

    def list_expired_subscriptions(self, now: datetime) -> list[dict]:
        return []

    def get_subscription_snapshot(self, subscription_id: str, scheduled_date_kst: date) -> dict:
        if (subscription_id != self._snapshot["subscription_id"]
                or scheduled_date_kst.isoformat() != self._snapshot["scheduled_date_kst"]):
            raise LookupError("해당 구독·날짜의 샘플 응답이 없습니다.")
        return deepcopy(self._snapshot)

    def check_delivery_eligibility(self, subscription_id: str, now: datetime) -> Eligibility:
        if subscription_id != self._snapshot["subscription_id"]:
            raise LookupError("해당 구독의 샘플 응답이 없습니다.")
        return self._eligibility

    def issue_feedback_token(self, job_id: str) -> str:
        return "TOKEN_FIXTURE_ONLY"
