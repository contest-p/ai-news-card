# 엔진 ↔ 백엔드 연결 규격 제안

2026-10-07. 엔진 HTTP 어댑터를 구현했다. 아래 경로·응답·인증은 **백엔드 구현 완료를 뜻하지 않는다**.
현재 backend/main.py의 사용자용 /me, /subscriptions/* 경로는 이 API를 대체하지 않는다.
백엔드와 아래 규격을 맞추거나, 합의된 규격에 맞춰 HttpEngineGateway를 수정한다.

ENGINE_API_BASE_URL은 엔진 전용 API prefix 전체다. 모든 요청은 서버용
`Authorization: Bearer <ENGINE_API_TOKEN>`을 사용한다. 사용자 Firebase ID 토큰을 사용하지 않는다.
백엔드는 엔진 서비스 인증을 별도로 검증하고 일반 사용자에게 접근을 허용하지 않아야 한다.
TLS 필수(로컬 localhost/127.0.0.1은 HTTP 가능), timeout 20초, redirect 금지, 자동 HTTP 재시도 없음.

| 메서드·경로(prefix 이후) | 요청 | HTTP 200 응답 |
|---|---|---|
| GET /subscriptions/due | now=offset 포함 ISO 8601 | {"subscriptions": [날짜별 스냅샷]} |
| GET /subscriptions/expired | now=offset 포함 ISO 8601 | {"subscriptions": [자연 만료 스냅샷]} |
| GET /subscriptions/{id}/snapshot | scheduled_date_kst=YYYY-MM-DD | {"subscription": 스냅샷} |
| GET /subscriptions/{id}/eligibility | now=offset 포함 ISO 8601 | {"eligible": true/false, "reason": 상태 이유} |
| POST /feedback-tokens | {"job_id": 고정 작업 ID}; Idempotency-Key=job_id | {"token": 원문 opaque 토큰} |

목록은 최대 1000건이며 현 어댑터에는 pagination이 없다. 이 규모를 넘기기 전 pagination 계약을 추가한다.
대상 없음은 subscriptions=[]로 반환한다. DB/권한/API 실패는 오류 상태로 반환하며 빈 목록으로 바꾸지 않는다.
reason은 active/cancelled/expired/deletion_requested/not_found 중 하나, eligible=true는 active일 때만 허용한다.

일일 스냅샷 예시(가상 데이터):

```json
{
  "subscription_id": "fixture-subscription",
  "user_id": "fixture-user",
  "recipient_email": "test@example.com",
  "timezone": "Asia/Seoul",
  "status": "active",
  "categories": ["economy"],
  "keywords": [],
  "start_date": "2026-10-08",
  "end_date_exclusive": "2026-10-15",
  "scheduled_date_kst": "2026-10-08",
  "scheduled_at": "2026-10-08T09:00:00+09:00",
  "deadline_at": "2026-10-08T12:00:00+09:00"
}
```

날짜별 스냅샷은 그날 적용되는 설정으로 백엔드가 만든다. 기간은 7/14/28일, 시작일 포함·종료일 제외다.
due 목록은 발송 예정 시각부터 기한 전까지만 포함한다. 만료 목록은 자연 만료 후 24시간만 포함하며
수동 해제·삭제 요청·재구독은 제외한다. 엔진은 기한을 추가로 검증하고 SMTP 직전 최신 eligibility를 확인한다.
구독 변경·재구독 정책을 엔진이 추측해 만들지 않는다.

피드백 토큰 발급은 job_id 기준 멱등 처리가 필요하다. HTTP 응답 유실 후 같은 작업이 다시 요청해도
이미 유효한 링크를 무효화하지 않아야 한다. 해시 저장·유효 기간·피드백 저장·삭제는 백엔드 담당이다.
연결 테스트는 실제 테스트 구독을 사용하므로 발급 토큰도 실제 백엔드에 저장될 수 있다.

Firestore 엔진 컬렉션은 engine_articles/engine_article_ids/engine_delivery_jobs/engine_generation_jobs/
engine_mail_archives다. DB 구조·인덱스·권한·보관 정책은 백엔드 리뷰 후 팀 DB에 통합한다.
시험 실행은 engine_test_delivery_jobs/engine_test_generation_jobs/engine_test_mail_archives를 사용한다.
기사 데이터는 같은 engine_articles에 저장한다. users/subscriptions는 엔진이 쓰지 않는다.
