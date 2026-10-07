# 엔진 실제 연결과 테스트 실행

2026-10-07. 기사 DB·발송 작업 DB·생성 이력 DB·MIME 보관·Backend HTTP·AI·SMTP를 조립하는
`engine.tools.connected_batch`를 추가했다. 실제 프로젝트 로그인·API·수신함 검증과 코드 검증은 구분한다.

## 1. 개발 PC 인증

Google Cloud CLI를 설치하고 ai-news-card 접근 권한이 있는 계정으로 다음을 실행한다.
Firebase 웹 앱 로그인과 별도의 Google Cloud 개발자 인증이다.

```powershell
gcloud auth application-default login --disable-quota-project
# 프로젝트의 quota 사용 권한이 있으면 설정한다.
gcloud auth application-default set-quota-project ai-news-card
.\engine\.venv\Scripts\python.exe -B -m engine.tools.connected_batch --check-db
```

현재 터미널 PATH가 갱신되지 않았다면 gcloud 대신 아래 경로를 사용할 수 있다.

```powershell
& 'C:\Users\user\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd' auth application-default login --disable-quota-project
```

DB 읽기 권한과 엔진 데이터 쓰기 권한은 프로젝트 관리자가 IAM으로 부여한다.
서버 SDK는 웹 Security Rules가 아닌 IAM으로 접근한다. 엔진 코드의 컬렉션 분리는 IAM 권한 제한을 대신하지 않는다.
ADC 대안으로 저장소 밖 서비스 계정 JSON 경로를 --credentials에 지정할 수 있다.
배포에서는 연결된 서비스 계정/Workload Identity를 통한 ADC를 사용하고 개인 개발자 ADC를 복사하지 않는다.
GitHub Actions 인증·예약 설정은 배포 담당자와 조율할 후속 작업이다.

## 2. 실제 기사 저장 검증

```powershell
.\engine\.venv\Scripts\python.exe -B -m engine.tools.collect_to_firestore --live --write --verify --project ai-news-card --max-per-source 3
```

기사별 첫 저장 후 버전 문서를 다시 읽고 내용 해시를 비교하며 같은 관측을 재저장해 중복 방지를 검증한다.
기사별 트랜잭션이라 중간 오류가 나면 일부 저장될 수 있다. 오류를 DB 미사용 성공으로 바꾸지 않는다.
최신 기사 저장과 versions 보존은 기존 어댑터를 사용한다. 수집 소스 일부 실패 시 유효 기사는 저장될 수 있지만
종료 코드는 1이며 collection_succeeded=false다. 실제 내용을 변경해 운영 기사의 수정 버전을 강제로 만들지 않는다.
수정 버전·충돌·동시 실행은 자동 테스트로 검증하며 실제 동시 commit 검증은 별도로 필요하다.

## 3. Firestore 저장소

| 컬렉션 | 역할 |
|---|---|
| engine_articles / engine_article_ids | URL 중복 방지·수정 버전 |
| engine_delivery_jobs | 고정 작업 ID, 트랜잭션 선점·소유권 검사·SMTP 횟수·sent/unknown 이력 |
| engine_generation_jobs | 생성 횟수·결과 재사용·트랜잭션 잠금 |
| engine_mail_archives/{job_id}/chunks | 재시도용 MIME 조각과 부모 문서 해시·만료 시각 |

발송 상태 변경은 기존 apply_transition 규칙을 사용한다. 멈춘 sending은 자동 재전송 대신 unknown으로 남는다.
생성 잠금은 10분이다. 만료 후 선점을 회복해도 in_flight 생성 기록은 자동 API 재호출을 막는다.
생성 상태 JSON은 900KB까지, MIME은 7MB까지이며 400KB 조각을 한 트랜잭션으로 저장한다.
조회 상한을 초과하면 오류를 내고 목록을 조용히 잘라 처리하지 않는다. 규모 증가 시 날짜별 조회·인덱스를 추가한다.

메일에는 주소와 피드백 토큰이 들어 있다. ENGINE_ARCHIVE_RETENTION_DAYS를 백엔드와 합의해 1~30일로 설정해야 한다.
만료된 MIME은 복원하지 않는다. **만료 시각 기록은 실제 삭제를 뜻하지 않는다.** 부모·chunks, 작업 스냅샷,
생성 이력의 삭제/비식별 정책과 정리 작업은 백엔드의 개인정보 처리와 함께 통합해야 한다.
이 연결 도구는 production 예약 발송을 활성화하지 않는다.

## 4. Backend 설정

engine/.env에 실제 값을 로컬로 설정한다. 비밀값은 Git에 넣지 않는다.

```dotenv
ENGINE_API_BASE_URL=
ENGINE_API_TOKEN=
ENGINE_WEB_BASE_URL=
ENGINE_TEST_SUBSCRIPTION_ID=
ENGINE_ARCHIVE_RETENTION_DAYS=
```

API base URL은 엔진 전용 prefix다. [API 연결 규격](ENGINE_API_CONTRACT.md)은 현재 제안이며
백엔드가 경로·응답과 서비스 인증을 구현하거나 합의된 기존 API로 어댑터를 조정해야 한다.
사용자용 /subscriptions/me는 전체 발송 대상 조회를 대체하지 않는다.
기존 OPENAI_*와 SMTP_* 설정도 필요하다. ENGINE_WEB_BASE_URL은 실제 HTTPS 웹 origin이다.

## 5. 실제 테스트 구독 1개 발송

테스트용 구독을 백엔드에 저장한다. recipient_email은 SMTP_TEST_TO와 같아야 하고 분야·키워드·기간·발송 시각이 있어야 한다.
아직 발송 시간이 아니거나 이미 기한이 지난 경우 현재 API의 due 목록에 나타나지 않는다. 엔진이 테스트를 위해 날짜를 바꾸지 않는다.

```powershell
# 외부 호출 없이 부족한 설정만 확인
.\engine\.venv\Scripts\python.exe -B -m engine.tools.connected_batch --check
# SMTP 인증만 확인하는 기존 도구
.\engine\.venv\Scripts\python.exe -B -m engine.tools.smtp_test --check
# 실제 DB·API·AI·메일 사용. 테스트 구독의 실제 수신 주소 한 곳에만 전송
.\engine\.venv\Scripts\python.exe -B -m engine.tools.connected_batch --send-test
# 이미지 렌더링을 빼고 본문으로만 연결 검증
.\engine\.venv\Scripts\python.exe -B -m engine.tools.connected_batch --send-test --text-only
```

시험은 engine_test_delivery_jobs/engine_test_generation_jobs/engine_test_mail_archives에 상태를 기록한다.
기사 저장은 engine_articles를 사용한다. 실제 피드백 토큰 API가 호출된다. API 없는 가상 토큰을 실제 메일에 넣지 않는다.
테스트 도구는 종료 안내 발송을 포함하지 않는다. 자연 만료 안내 정책은 기존 pipeline과 자동 테스트로 검증한다.
오래된 미완료 테스트 작업도 복구하되 다른 구독/수신 주소는 SMTP를 호출하지 않는다.
SMTP 수락 1건, 배치 오류 없음일 때 종료 코드 0이다. 대상 없음·실패·unknown은 1이다.
수락은 실제 수신함 도착을 보장하지 않으며 수신자는 제목·본문·링크·피드백 저장을 직접 확인한다.
같은 날짜 재실행 시 sent/unknown을 삭제하거나 초기화하지 않는다.

## 확인 결과

2026-10-07 실제 확인 기록:

- Google Cloud CLI 설치 및 본인 계정 ADC 로그인 완료.
- ai-news-card quota project 설정 실패: serviceusage.services.use 권한 없음.
- 실제 Firestore 읽기: 샌드박스 밖 재확인에서도 PermissionDenied. 실제 기사 쓰기는 실행하지 않음.
- 실제 SMTP TLS 연결·로그인 성공(smtp_auth_verified). 메일은 전송하지 않음.
- Backend API 설정, 테스트 구독 ID, 웹 origin, 합의된 MIME 보관 일수 미설정.
- 전체 엔진 자동 테스트 233개 통과. 전체 배치의 HTTP→기사 저장→생성→MIME→발송 기록과
  저장소 객체 재생성 후 재발송 방지는 서비스 대역으로 검증.

다음 실제 검증은 관리자가 ADC 로그인 계정에 Firestore 읽기/쓰기와 필요한 API 사용 권한을 부여하고,
백엔드가 ENGINE_API_CONTRACT.md의 API 및 인증 설정을 제공한 뒤 진행한다.
DB 서버 트랜잭션과 실제 메일 수신 완료로 보고하지 않는다.

신규 저장소·HTTP 어댑터는 DB 대역과 가상 HTTP/SMTP로 검증한다. 실제 서버 트랜잭션 재시도·IAM·메일 수신은 별도다.
현재 RAG는 no_evidence_search 기본값이며 과거 벡터 검색은 후속 작업이다. 카드는 현재 엔진 템플릿으로 렌더한다.
운영 예약·사용자 전체 발송·개인정보 자동 정리는 아직 활성화하지 않았다.

공식 인증 근거: [로컬 ADC](https://docs.cloud.google.com/docs/authentication/set-up-adc-local-dev-environment),
[서버 Firestore 권한](https://firebase.google.com/docs/firestore/security/overview).
