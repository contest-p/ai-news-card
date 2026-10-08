> **2026-10-08 최신 구현:** [백엔드 구현 보완](BACKEND_COMPLETION.md)을 먼저 확인하세요. 아래는 이전 작업 기록이며 설정 없는 신청, 설정 변경 미구현, 최초 발급부터 토큰 30일이라는 설명은 최신 구현으로 대체되었습니다.

# 엔진 API 연결 구현 — 2026-10-07

기존 사용자 API는 Firebase ID Token을 계속 사용한다. 새 엔진 API는 서버용 ENGINE_API_TOKEN을
별도로 검사한다. Firebase 사용자 토큰이나 인증 없는 호출로 전체 구독을 조회할 수 없다.
구현은 engine_api.py로 분리했고 main.py에서 router를 등록한다.

## 실행

저장소 루트에서 backend/.venv에 backend/requirements.txt를 설치한다.
프로젝트 설정은 backend/.env에 두고 .env.example을 참고한다. 로컬 ADC 또는 기존 서비스 계정 JSON을 사용한다.
현재 PC에는 전용 가상환경을 구성했고 서버를 127.0.0.1:8000에서 실행해 확인했다.

```powershell
.\backend\.venv\Scripts\python.exe -B -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

Swagger: http://127.0.0.1:8000/docs
엔진 prefix: http://127.0.0.1:8000/api/v1/engine
engine/.env에는 위 prefix를 ENGINE_API_BASE_URL로, backend/.env와 같은 서버 인증값을 ENGINE_API_TOKEN으로 설정한다.
서버 토큰과 FEEDBACK_TOKEN_SECRET은 별개이고 각각 최소 32자다. 값은 저장소나 브라우저 설정에 넣지 않는다.
FEEDBACK_TOKEN_SECRET은 발급한 토큰이 유효한 동안 유지한다. 로컬 두 .env는 설정했지만 비밀값은 Git에 포함하지 않는다.
배포 서버와 다른 팀원 PC는 별도 환경 설정이 필요하다. localhost 주소가 팀원 PC의 서버를 가리키지는 않는다.

## 구현 API

| 메서드·경로(prefix 이후) | 결과 |
|---|---|
| GET /subscriptions/due?now=ISO시간 | 발송 시각부터 3시간/자정/만료 중 이른 기한 전까지 대상 |
| GET /subscriptions/expired?now=ISO시간 | 자연 만료 24시간 이내 대상; 해제·삭제 요청 제외 |
| GET /subscriptions/{id}/snapshot?scheduled_date_kst=YYYY-MM-DD | 날짜별 고정 설정 |
| GET /subscriptions/{id}/eligibility?now=ISO시간 | 최신 발송 가능 상태와 이유 |
| POST /feedback-tokens | job_id와 같은 Idempotency-Key 필요; 원문 토큰 반환, DB에는 해시만 저장 |

now는 시간대가 필요하며 생략하면 현재 UTC다. 상태와 데이터 부족을 확인하고 삭제 요청/없는 사용자에는 발송하지 않는다.
기간 필드가 없는 기존 구독은 발송 대상에서 제외하고 skipped_counts로 요약한다. 조회 오류를 정상 빈 목록으로 바꾸지 않는다.
활성/만료 후보 조회는 1000건까지이며 초과하면 503이다. 규모 확대 전 pagination·조회 인덱스를 추가해야 한다.

## 구독 저장 필드 확장

기존 POST /subscriptions/save의 {"plan":"basic"} 요청은 계속 사용할 수 있다.
개인화 발송 설정을 저장하려면 다음처럼 engine_settings를 함께 전달한다(사용자 Firebase 인증 필요).

```json
{
  "plan": "basic",
  "engine_settings": {
    "categories": ["economy"],
    "keywords": [],
    "delivery_hour_kst": 9,
    "duration_days": 7,
    "consent_version": "실제-동의문-버전"
  }
}
```

시작일은 저장 다음 KST 날짜, 종료일은 시작일+7/14/28일이고 종료일은 제외한다.
Firestore subscriptions/{uid}에 필드를 펼쳐 저장한다. 새 기간마다 subscription_id를 생성하므로
재구독 후 과거 구독 ID로 SMTP를 시도하면 not_found로 차단된다.
이미 날짜가 설정된 활성 구독의 설정 변경은 409로 막는다. 다음날 변경 적용 정책/API는 후속 작업이다.
프론트의 /api/v1/subscriptions 계약 전체를 변경한 것은 아니므로 프론트 연결은 백엔드 담당자와 맞춘다.

## DB 구조와 리뷰할 부분

- subscriptions/{uid}/engine_snapshots/{day-kind}: 처음 요청한 날짜별 설정을 트랜잭션으로 고정.
- feedback_tokens/{job_id}: user_id, subscription_id, token_hash, created_at, expires_at. 토큰 수명은 최초 발급 30일.
- 원문 토큰은 별도 secret으로 job_id에서 결정적으로 만들어 멱등 재발급하며 원문을 DB에 저장하지 않는다.
- 토큰 발급은 processing/sending/sent 상태의 뉴스 작업과 유효한 사용자에 한정한다. 삭제 요청 사용자에는 발급하지 않는다.
- 피드백 resolve/저장 API와 개인정보 삭제·보관 정리는 이번 변경에 포함되지 않는다. 별도 백엔드 작업과 통합해야 한다.
- 동의 정책, 필드명, 다음날 설정 변경, 기존 데이터 이관, 토큰 키 교체는 백엔드 리뷰 후 팀 규격으로 확정한다.

## 검증

```powershell
.\backend\.venv\Scripts\python.exe -B -m unittest discover -s backend/tests -v
# 실제 Firestore의 별도 engine_test 컬렉션에 가상 문서 생성/검증/이번 문서만 정리. AI/SMTP 없음.
.\backend\.venv\Scripts\python.exe -B -m backend.tools.live_engine_check
```

실제 로컬 HTTP를 통해 엔진의 HttpEngineGateway로 발송/만료 목록 호출 성공, 인증 없는 호출은 401.
기존 팀 구독은 해제 상태라 두 목록 모두 0건이다. 기존 사용자·구독은 변경하지 않았다.
별도 실제 테스트 문서로 Firestore 쓰기·스냅샷 고정·피드백 토큰 멱등 발급·해시 저장을 검증하고 생성 문서를 삭제했다.
새 API는 준비됐지만 실제 활성 구독으로 AI·메일까지 이어지는 통합 검증은 아직 별도다.
