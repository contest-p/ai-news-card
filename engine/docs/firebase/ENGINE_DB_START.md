# 기존 팀 DB로 시작하는 엔진 작업

2026-10-07. 백엔드 `main.py` 기준 users/{uid}, subscriptions/{uid}를 사용한다.
구독에는 uid/email/name/plan/status/expires_at/created_at/updated_at/cancelled_at만 저장된다.
관심 분야·키워드·발송 시간·시작일·종료일과 날짜별 스냅샷이 없어 자동 발송은 아직 연결하지 않는다.
이메일·비밀번호 테스트 여부는 서버용 Firestore 연결과 별개다. 엔진은 로그인 ID 토큰 대신 서버 자격 증명을 사용한다.

## 지금 실행 가능한 작업

저장소 루트에서 실행한다. 서비스 계정 파일은 저장소 밖에 보관한다.
`--credentials` 또는 FIREBASE_SERVICE_ACCOUNT_KEY/GOOGLE_APPLICATION_CREDENTIALS 환경변수를 사용한다.
engine/.env, backend/.env의 기존 설정도 읽으며 이미 설정된 환경변수를 덮어쓰지 않는다.
상대 자격 증명 경로는 실행하는 현재 디렉터리 기준이다. 서비스 계정 프로젝트와 --project가 다르면 중단한다.
파일이 지정되지 않으면 서버 환경의 ADC를 사용한다.

```powershell
# 외부 접속 없이 샘플 기사 수집 흐름 확인
.\engine\.venv\Scripts\python.exe -B -m engine.tools.collect_to_firestore

# 기존 DB 읽기 확인: 사용자 개인정보와 문서 ID는 출력하지 않는다
.\engine\.venv\Scripts\python.exe -B -m engine.tools.db_check --project ai-news-card --credentials 'C:\outside-repo\service-account.json'

# 실제 RSS 수집, 저장 전 확인
.\engine\.venv\Scripts\python.exe -B -m engine.tools.collect_to_firestore --live --max-per-source 3

# 실제 기사 저장: 엔진 기사 컬렉션에만 쓰기
.\engine\.venv\Scripts\python.exe -B -m engine.tools.collect_to_firestore --live --write --project ai-news-card --credentials 'C:\outside-repo\service-account.json' --max-per-source 3
```

프로젝트 ID와 파일 경로는 실제 팀 값으로 바꾼다. `db_check`는 컬렉션마다 최대 limit+1개만 읽고
표본 건수·잘림 여부·구독 상태별 수·빠진 설정 필드 수를 출력한다. 필드 존재 검사는 발송 가능성 검증이 아니다.
컬렉션 없음은 0건으로 표시되며 접속 실패는 오류와 종료 코드 1로 구분한다.

기사 쓰기는 기존 FirestoreArticleRepository를 사용한다. URL 기준 중복 방지, 내용 변경 버전 보존,
이전 관측 거부를 그대로 적용한다. 각 기사별 트랜잭션이므로 중간 실패 시 일부가 저장될 수 있다.
재실행은 같은 URL의 중복 문서를 만들지 않는다. 수집 일부가 실패해도 유효 기사는 저장될 수 있고,
collection_succeeded=false와 종료 코드 1을 반환한다. 샘플은 --write로 저장할 수 없다.
AI 호출과 메일 전송은 포함하지 않는다.

## 백엔드와 다음에 맞출 것

- 분야·키워드·발송 시간·기간·동의·삭제 요청 필드와 검증 규칙
- subscription_id와 날짜별 설정 스냅샷, 발송 직전 상태 확인
- 대상 목록 API와 피드백 토큰 발급 API
- 기사·임베딩·생성·발송 이력 컬렉션과 IAM/보관 정책 통합

현재 도구는 기존 사용자·구독 문서를 수정하지 않는다. 실제 서버 접속·쓰기 검증 결과는 별도 기록한다.
