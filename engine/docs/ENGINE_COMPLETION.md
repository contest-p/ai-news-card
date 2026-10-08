# 엔진 구현 보완 — 2026-10-08

배포·QA·Backend·Frontend 코드는 변경하지 않았다. 아래는 엔진 구현과 대역을 사용하는 개발 테스트 결과다. 실제 DB 인덱스 적용, 외부 메일 발송, 모델 다운로드, 예약 배포는 하지 않았다.

## 구현

- `firestore_rag.py`: 기사 내용 버전/해시와 모델 revision에 맞춘 384차원 임베딩 영속 저장·재사용·실패 재처리. 인코딩은 트랜잭션 밖에서 실행하고, 내용이 바뀌면 늦게 도착한 이전 벡터를 저장하지 않는다.
- 같은 분야·작업 날짜 KST 자정 이전·유효 본문/소스·현재 모델의 ready 벡터로 사전 필터한 COSINE 검색. 최대 5개 검색, 기준 이상 최대 3개 선택. 전체 기사 읽기 폴백은 없다. 인덱스/쿼리 오류는 정상 근거 없음과 구분한다.
- 출처명은 기사 DB에 보존한다. `engine_rag_results/{job_id}`에 검색 후보·유사도·기사 버전·선택 근거·최종 카드에서 실제 사용한 과거 기사 ID를 기록한다. 검색 후보를 실제 사용한 출처라고 표시하지 않는다.
- `runtime.py`: 수집·기사 저장·임베딩·RAG·생성·렌더·SMTP·발송/생성/MIME 저장소를 조립한다. 전체 구독 및 자연 만료 안내를 처리하고, 이전 미완료 작업을 복구한다. 기존 테스트 도구도 같은 RAG 경로를 사용하되 지정 구독/수신자 제한을 유지한다.
- 최근 27시간의 저장된 기사 후보를 날짜 제한 조회로 복구한다. 일부 RSS 장애가 기존 유효 기사까지 막지 않는다. 조회 상한 초과/DB 실패를 정상 뉴스 없음으로 처리하지 않는다.
- 배치당 카드 생성 작업 기본 상한 50개. 초과 작업은 `GENERATION_BATCH_LIMIT` 재시도 가능한 실패로 남긴다. 각 생성 작업 최초 포함 최대 2회 및 SMTP 최대 3회·unknown 자동 재전송 금지는 유지한다.
- 모델은 최초 임베딩 요청에서 고정된 로컬 E5 캐시를 로드한다. 로드 실패 시 반복 로드를 막고 임베딩 장애를 기록한다. 검증된 카드 1은 계속 처리하고 카드 2는 생략한다. 이 상태는 RAG 검증 완료를 의미하지 않는다.
- 만료 MIME 정리는 부모/chunks를 한 batch로 실제 삭제한다. 만료 설정은 1~30일만 허용한다. Backend 개인정보 정리 API를 호출하는 엔진 어댑터를 추가했다. API 미구현/실패는 `PRIVACY_CLEANUP_FAILED`이며 완료로 표시하지 않는다.
- `tools/reindex_articles.py`: 기존 기사의 지정 날짜 범위를 제한 조회하고 출처/임베딩을 재처리한다. 모르는 출처는 만들지 않고 `publisher_missing`으로 남긴다. `--write` 없이 실행하면 DB 읽기만 한다.

## 개발 실행

```powershell
# 기본 엔진 의존성
engine/.venv/Scripts/python.exe -m pip install -r engine/dependencies/requirements.txt
# 실제 E5 사용에는 별도 RAG 의존성과 고정 모델 캐시가 필요하다.
engine/.venv/Scripts/python.exe -m pip install -r engine/dependencies/requirements-rag.txt
# 기존 engine.demos.rag_demo의 --download-model로 모델을 준비한다. 이번 작업에서는 실행하지 않았다.
engine/.venv/Scripts/python.exe -m engine.tools.run_batch --check
# 실제 DB/AI/SMTP 전체 실행 (이번 작업에서는 실행하지 않았다)
engine/.venv/Scripts/python.exe -m engine.tools.run_batch --run
# 기존 테스트 구독 제한 명령 (실제 외부 호출)
engine/.venv/Scripts/python.exe -m engine.tools.connected_batch --send-test
```

`--check`는 환경변수 형식만 검사한다. 실제 인증·벡터 인덱스·모델 캐시·SMTP 연결이 검증됐다고 보고하지 않는다. 전체 실행은 SMTP_TEST_TO나 ENGINE_TEST_SUBSCRIPTION_ID를 요구하지 않는다. 테스트 명령은 둘 다 필요하다.

기존 DB 기사의 재처리 예시:

```powershell
engine/.venv/Scripts/python.exe -m engine.tools.reindex_articles --project ai-news-card --since 2026-10-01T00:00:00+09:00 --before 2026-10-08T00:00:00+09:00 --limit 500
# 동일 명령에 --write를 추가하면 지정 범위에 벡터/출처를 저장한다.
```

출처는 이미 저장된 값 또는 기존 엔진 소스 목록에서 URL 도메인이 유일하게 일치하는 값만 사용한다. 이것은 RSS 이용 조건이나 공개 분야에 대한 QA 승인을 대신하지 않는다. 모델 revision 변경 시 기존 ready 벡터도 이 도구로 재처리한다.

## Backend 후속 연결

기존 엔진 API prefix와 구독/eligibility/피드백 토큰 경로는 유지한다. 새 개인정보 정리 계약은 Backend 작업에서 구현/합의해야 한다.

- `POST <engine-prefix>/privacy-cleanup`
- 엔진 Bearer 인증 사용. body: `{"now":"시간대 포함 ISO UTC"}`.
- `Idempotency-Key: privacy-YYYY-MM-DD` (KST 날짜). Backend는 같은 날짜 재호출/중단 재개를 안전하게 처리하며 입력 시각을 서버 기준으로 검증한다.
- 완료 응답: `{"status":"completed"}`. 미완료/오류는 성공으로 반환하지 않는다.
- 사용자·구독·스냅샷·피드백·발송 기록·Auth 계정의 삭제/익명화 정책과 즉시 삭제 요청은 Backend 소유다. 엔진의 만료 MIME 정리만으로 FR-18/24가 완료되지는 않는다.
- `engine_rag_results`는 기사 근거 기록이며 개인정보를 담지 않는다. 발송 작업과 보관 기간/사용 권한은 Backend 스키마 관리에 함께 반영한다.

## 필요한 DB 인덱스 (이번에 적용하지 않음)

`engine_articles` 검색: category, source_verified, body_valid, embedding_status, embedding_model, embedding_revision의 동등 필터, published_at의 날짜 범위, embedding의 384차원 flat 벡터 인덱스. pending 재처리: embedding_status + first_seen_at. failed 재처리: embedding_status + embedding_updated_at. MIME 정리: expires_at.

Firestore의 사전 필터 벡터 검색은 복합 벡터 인덱스가 필요하다. 실제 환경의 생성 안내에 따라 Backend가 관리한다. [Firebase 공식 벡터 검색 문서](https://firebase.google.com/docs/firestore/vector-search).

## 개발 테스트 결과

엔진 기존 236개 + 신규 19개, 총 255개 통과. SDK 검색 요청의 COSINE/5개/날짜·분야 필터, 내용 변경 경합, 실패 재처리, 출처 보존, 검색 장애/카드 1 대체, 실제 사용 근거 기록, RAG 카드 2와 만료 메일 조립, 재실행 중복 방지, 만료 MIME 삭제, Backend 정리 실패를 검증한다.

실제 E5 모델의 품질, DB 인덱스 실행, 실제 수신/가독성, 사용자 테스트는 아직 확인하지 않았다. 배포 예약·권한·Secrets·QA는 사용자 요청에 따라 이번 범위에서 제외했다.
