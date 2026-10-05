"""Backend 연동 경계의 초안. 실제 인증·DB·구독 정책 구현은 Backend 담당."""

from copy import deepcopy
from datetime import date, datetime
from typing import Protocol


class EngineGateway(Protocol):
    # 함수명은 공통 10-3, 인자·반환 타입은 팀 확인 전 제안이다.
    def get_subscription_snapshot(self, subscription_id: str, scheduled_date_kst: date) -> dict: ...

    def check_delivery_eligibility(self, subscription_id: str, now: datetime) -> bool: ...


class SampleEngineGateway:
    """저장된 가상 응답만 전달한다. 구독 날짜·권한 계산을 대신 구현하지 않는다."""

    def __init__(self, fixture: dict):
        if fixture.get("demo_only") is not True:
            raise ValueError("가상 구독 샘플만 사용할 수 있습니다.")
        self._snapshot = deepcopy(fixture["subscription_snapshot"])
        self._eligible = fixture["delivery_eligible_fixture"]
        if type(self._eligible) is not bool:
            raise ValueError("발송 가능 샘플은 bool이어야 합니다.")

    def get_subscription_snapshot(self, subscription_id: str, scheduled_date_kst: date) -> dict:
        if (subscription_id != self._snapshot["subscription_id"]
                or scheduled_date_kst.isoformat() != self._snapshot["scheduled_date_kst"]):
            raise LookupError("해당 구독·날짜의 샘플 응답이 없습니다.")
        return deepcopy(self._snapshot)

    def check_delivery_eligibility(self, subscription_id: str, now: datetime) -> bool:
        if subscription_id != self._snapshot["subscription_id"]:
            raise LookupError("해당 구독의 샘플 응답이 없습니다.")
        return self._eligible
