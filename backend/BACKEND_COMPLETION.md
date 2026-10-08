# 백엔드 구현 보완 — 2026-10-08

공통 PRD v0.4와 고승희 PRD v0.3의 미완성 API·개인정보 처리 부분을 구현했다. 배포, 실제 DB 이관, 권한 적용, SMTP/실제 메일 QA는 실행하지 않았다.

## 구현한 동작

- Firebase ID Token은 `check_revoked=True`로 검사한다. 인증 오류에 SDK 오류·토큰 원문을 반환하지 않는다. UID·이메일만 앱에 복사하며 사용자 동기화 때 기존 이름 필드를 제거한다.
- 설정 없는 `{plan: basic}` 신청은 422다. 서버는 키워드 NFKC·공백·1~20 code point·최대 5개를 검증하고 중복을 제거한다. 7/14/28일, 정시, 최신 동의 버전과 UTC 동의 시각을 저장한다.
- 활성 중복 신청·동일 생성 키의 다른 내용은 409다. 기간은 다음 KST 날짜부터 종료일 제외 방식이다. 새 신청은 UUID로 구독을 식별하고 이전 기록은 `subscription_history`에 보존한다.
- 설정 변경은 낙관적 버전 확인 후 다음 KST 날짜부터 적용한다. 같은 날 여러 변경은 마지막 버전 하나로 합쳐 DB 배열이 무한 증가하지 않는다. 기간 변경을 거부하고 발송 날짜별 스냅샷은 이미 만들어진 값을 유지한다.
- 해제는 인증 사용자·구독 ID·`confirm:true`가 필요하다. 오래된 ID는 새 구독을 해제할 수 없다. 동일 구독의 반복 해제는 저장된 시각을 유지한다.
- 피드백 토큰 수명은 발급 시각이 아닌 브리핑 예정 KST 날짜의 자정부터 30일이다. 원문은 저장하지 않는다. resolve는 읽기만 하고 POST 평가만 up/down, 이유 코드, 최대 500자 의견을 작업당 한 문서에 저장/갱신한다.
- 삭제 요청은 본인 인증과 확인으로 접수한다. 사용자 문서의 삭제 표시가 즉시 엔진 eligibility·새 토큰·구독 신청·사용자 동기화를 차단한다. 처리 전까지 본인만 요청 상태를 조회할 수 있다.
- 개인정보 정리는 엔진의 `POST /api/v1/engine/privacy-cleanup`과 연결했다. 입력 now는 서버 현재 시각과 1시간 이내여야 한다. 각 조회는 100건, 한 호출 삭제는 2000건까지 진행하고 남은 작업이나 실패는 503으로 재시도를 요구한다.
- 보관 기한은 종료/해제 후 29일로 잡아 30일 정책의 실행 여유를 확보했다. 이전 구독의 기록·스냅샷·피드백·토큰·발송 작업·MIME 조각·근거 기록을 지운다. 활성 재구독 정보는 유지한다.
- 계정 삭제는 위 개인정보와 Firebase Auth 계정을 삭제한다. 하위 문서부터 동기 삭제하고 부모/작업 참조는 마지막에 지워 부분 실패 때 재시도할 수 있다. Auth 실패 시 pending 표시를 유지한다. 완료 시 이메일·UID가 없는 해시 식별자 tombstone을 남겨 진행 중이던 인증 요청의 재생성을 막는다. 이 해시도 가명 식별자로 접근을 제한한다.
- API 응답에 no-store/no-referrer를 설정한다. 입력 검증 오류에는 원본 body를 넣지 않는다. `/feedback/save`와 구형 엔진 목록/변경 경로는 410으로 막아 보호된 새 경로를 사용하게 한다.

## 화면 연결 계약

아래 경로는 `/api/v1` prefix와 prefix 없는 경로를 모두 제공한다. 기존 `/subscriptions/save`, `/subscriptions/me`, `/users/sync`도 유지한다.

| 경로 | 요청·결과 |
|---|---|
| GET /catalog | 공개 승인 분야, 기간, consent_version, capabilities |
| POST /subscriptions | categories, keywords, delivery_hour_kst, duration_days, consent_version; Firebase 인증과 Idempotency-Key |
| GET /subscriptions/current | subscription 또는 null; current_settings·next_settings·settings_version(최신 수정 버전) |
| PATCH /subscriptions/{id}/settings | categories, keywords, delivery_hour_kst, expected_settings_version; 다음 날 적용 |
| POST /subscriptions/{id}/cancel | confirm:true; 기존 PATCH /subscriptions/cancel은 subscription_id·confirm:true body 필요 |
| POST /feedback/resolve | token을 body에만 전달; valid/current_rating/reasons/comment, 저장 없음 |
| POST /feedback | token, rating(up/down), reasons, comment; 사용자 버튼 확정 후 호출 |
| POST /account-deletion-requests | confirm:true; 202, request_id/status/sending_stopped |
| GET /account-deletion-requests/{id} | 본인 pending 상태; Auth 삭제 완료 후 인증·조회 불가 |

피드백 이유 코드: relevant(관심에 맞음), clear(이해 쉬움), useful(유용함), irrelevant(관심과 다름), inaccurate(내용 확인 필요), too_long(길음), other(기타). 선택과 의견은 선택사항이다. 프론트엔드는 메일 fragment의 token을 메모리에만 보관하고 즉시 주소에서 제거해야 한다. 해제 요청과 설정/피드백/삭제 화면 연결은 다음 프론트엔드 작업이다.

## 설정 및 DB 적용 준비

`PUBLIC_CATEGORIES`는 QA가 승인한 분야만 쉼표로 설정한다. 기본 빈 값은 공개 분야 없음이다. 실제 QA 없이 여섯 분야를 승인하지 않았다. 테스트에서만 가상 승인 값을 사용했다.

`CONSENT_VERSION`, 서버용 `ENGINE_API_TOKEN`, 별도 `FEEDBACK_TOKEN_SECRET`, Firebase 서버 인증, `FRONTEND_ORIGINS`는 `.env.example` 기준이다. 토큰 원문·요청 body/APM 수집을 활성화하지 않는다. uvicorn 실행은 기존 문서처럼 `--no-access-log`를 사용하고 호스팅/프록시 로그 설정도 적용 시 확인해야 한다.

새 구독은 `cleanup_after`와 설정 버전을 가진다. 기존 문서는 아래 이관 도구가 필요하다. 기본은 읽기 전용이며 출력에는 건수만 포함한다. 날짜나 동의가 없는 문서를 임의 보충하지 않고 오류 건수와 종료 코드 1로 알린다. 운영 이관은 이번에 실행하지 않았다.

```powershell
backend/.venv/Scripts/python.exe -m backend.tools.migrate_lifecycle
# 검토된 계획에 --write를 추가해야 실제 저장한다.
```

`db/firestore.rules`는 모든 브라우저 직접 접근을 거부하는 통합 규칙이다. Admin SDK는 Rules를 우회한다. Backend의 Firestore CRUD·Auth 사용자 삭제 권한과 엔진의 Firestore 접근 권한을 별도 최소 권한 서비스 계정으로 제한해야 한다. 실제 IAM·Rules 적용/검증은 이번에 수행하지 않았다.

`db/firestore.indexes.json`은 엔진 pending/failed 임베딩 재처리 복합 인덱스 명세다. 사용자/구독·이전 구독·작업·피드백/token 조회는 uid/user_id/subscription_id/status/token_hash 및 cleanup_after/expires_at 단일 필드를 사용한다. 필드 인덱스 면제 설정이 있으면 재검토해야 한다. 벡터 검색은 엔진 문서의 category/source_verified/body_valid/embedding_status/embedding_model/embedding_revision 동등 조건, published_at 범위와 embedding 384차원 flat 인덱스를 별도로 준비해야 한다. 실제 생성 안내·벡터 검색 검증은 배포·QA 단계에서 진행한다.

## 검증과 남은 운영 결정

백엔드 로컬 테스트 38개, 엔진 회귀 255개 통과. 가상 DB·가상 인증·가상 삭제를 사용했다. 오래된 해제, 버전 충돌, 오늘/내일 스냅샷, 토큰 예정일 만료, 읽기 방문, 실제 확인 POST, 재구독 정보 보존, Auth/하위 문서 삭제 실패와 재시도, 정리 호출 인증·위조 시각을 검증했다.

실제 배포/API 권한·DB 이관·벡터 인덱스·실제 삭제·SMTP 준비/메일 수신·QA는 미실행이다. 미가입 Auth 계정 보관 기간, 기사/근거의 보존·용량 경고 정책, 가명 삭제 차단 기록의 보관 기한은 PRD에서 아직 합의 전이다. 자동 개인정보 정리는 Firestore 앱 데이터를 지우며 자연 종료만으로 Google/Firebase 로그인 계정 자체를 삭제하지 않는다. 명시적 계정 삭제 요청에서 Firebase Auth 사용자 삭제를 수행한다. Google 계정이나 이미 수신한 메일을 지우는 기능은 없다.
