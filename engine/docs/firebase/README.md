# Cloud Firestore 기사 저장 연결 — 팀 확인 전 제안

DB 종류는 사용자가 Cloud Firestore로 확인했습니다. 이 문서는 박경연의 기사 저장 범위만 다룹니다. 사용자·구독·피드백·인증·전체 DB 초기화는 고승희의 범위입니다. 실제 프로젝트에는 아직 접속하거나 규칙을 배포하지 않았습니다.

## 실행과 현재 확인 범위

저장소 루트에서:

```powershell
.\.venv\Scripts\python.exe -m engine.demos.store_demo
.\.venv\Scripts\python.exe -m unittest discover -s engine/tests -v
```

첫 저장 `inserted: 2`, 재저장 `unchanged: 2`, 최종 기사 수 2, 수정 버전 2, `old_version_preserved: true`, 이전 관측은 `stale`가 예상 결과입니다. 메모리 저장소의 데이터는 프로그램을 종료하면 사라집니다. 실제 지속 저장은 Firestore 어댑터를 연결한 뒤 별도로 확인해야 합니다.

48개 테스트가 통과했습니다. 기존 29개 + 공통 저장 정책 11개 + Firestore SDK 요청 구성 8개입니다. 메모리 저장소에서는 32개 동시 중복 저장도 1건으로 남습니다. Firestore 테스트는 익명 자격 증명의 테스트 Client와 문서 읽기 mock을 사용해 SDK 직렬화·트랜잭션 쓰기 구성·충돌·실패 전달을 검증합니다. 실제 트랜잭션 commit, 재시도, IAM·Security Rules·비용을 검증했다는 뜻은 아닙니다.

## 제안한 문서 구조

| 경로 | 내용 |
|---|---|
| `engine_articles/{sha256(canonical_url)}` | 기사별 최신 내용·버전·수집 시각·임베딩 대기 상태 |
| `engine_articles/{articleKey}/versions/{content_version}` | 수정 이전 내용을 재현할 불변 버전 |
| `engine_article_ids/{sha256(article_id)}` | 기사 ID → 기사 문서 ID 연결; 다른 URL에 같은 ID 사용 방지 |

URL이 같으면 문서 ID가 같아서 자동 생성 ID로 같은 기사를 계속 추가하지 않습니다. 기사 ID는 처음 저장한 값을 유지하며, 다른 URL은 같은 제목이어도 별도 기사입니다. 원문 URL의 fragment·호스트 대소문자·기본 포트 정리만 적용하고 query·경로는 보존합니다.

공통 `Article` 입력은 기존 선별 기능과 같습니다. 저장 레코드에는 다음 필드를 추가합니다.

| 필드 | 제안 의미 |
|---|---|
| schema_version | `1.0-proposal`, 아직 확정 계약 아님 |
| content_hash | SHA-256; title/body/category/UTC published_at의 결정적 JSON 기준 |
| content_version | 최초 1; 내용 수정 때 +1. 원래 내용으로 돌아와도 새 버전 |
| first_seen_at | 최초 수집 시각, 유지 |
| last_seen_at | 가장 최근 관측 시각 |
| updated_at | 마지막 내용 변경 시각 |
| embedding_status | 이번 단계는 `pending`만 사용. 벡터 생성·실패·재처리는 후속 기능 |

Datetime은 UTC로 정리해 Firestore Timestamp로 저장합니다. 제목·본문은 바깥 공백만 제거하며 사실 내용을 요약하거나 Unicode 정규화로 합치지 않습니다. 가상 데이터의 검증 표시를 실제 뉴스 소스 검증의 대체로 사용하면 안 됩니다.

## 저장 처리와 실패

- 최초 저장은 기사·ID 연결·버전을 하나의 트랜잭션에서 만듭니다.
- 동일 내용은 버전을 올리지 않고 last_seen_at만 갱신합니다. 같은 관측 시각이면 추가 쓰기도 하지 않습니다.
- 변경된 내용은 같은 기사 ID로 최신 문서를 갱신하고 새 버전 문서를 만듭니다. 이전 버전은 덮어쓰지 않습니다.
- last_seen_at보다 오래된 관측은 `stale`로 돌려줍니다. 시각이 같은데 내용이 다르면 충돌 오류로 멈춥니다.
- 저장 실패는 예외로 전달합니다. DB 실패를 메모리 저장 성공이나 정상 빈 목록으로 바꾸지 않습니다.

읽기를 쓰기 전에 끝내고 `@firestore.transactional` 내부에는 외부 API·메일·로그 쓰기를 넣지 않았습니다. Firestore 트랜잭션은 충돌 시 콜백을 다시 실행할 수 있고 성공한 쓰기를 한 번에 반영합니다. [Firebase 트랜잭션 문서](https://firebase.google.com/docs/firestore/manage-data/transactions)

임베딩·발송 결과는 아직 이 문서에 기록하지 않습니다. 기존 버전에 대한 카드 근거 연결·보관 기간·정리는 이후 담당자 합의로 추가합니다. 버전 보존은 무기한 원문 보관을 뜻하지 않습니다.

## 어댑터 사용 인터페이스

```python
from engine.firestore_article_store import FirestoreArticleRepository

# client: 서버 전용 google.cloud.firestore.Client
repository = FirestoreArticleRepository(client)
result = repository.save(article, observed_at=collected_at_utc)
```

이 코드는 연결 예시이며 자동 실행되는 초기화 스크립트가 아닙니다. 실제 Client 초기화와 자격 증명을 아직 코드에 넣지 않았습니다. 서버 SDK는 웹 Security Rules 대신 IAM으로 접근하므로 서버 역할을 확인해야 합니다. [Firebase 서버 접근 제어](https://firebase.google.com/docs/firestore/security/overview)

`firestore.rules.fragment`는 엔진 컬렉션에 대한 클라이언트 접근 거부 규칙 조각이며 전체 사용자 DB 규칙이 아닙니다. 기존의 넓은 허용 규칙과 겹치면 이 조각만으로 차단되지 않을 수 있습니다. 서비스 계정 파일은 저장소 밖에서 보관하며 서버 자격 증명은 Secrets/ADC 등으로 공급하는 설계입니다.

## 용량·조회·RAG 연결

- 문서 입력 900KB와 개발용 검수 조회 100건은 **합의 전 개발용 제안값**입니다. 본문·이전 버전 용량과 실제 과금·쿼터는 표본으로 확인합니다.
- `list_current()`는 작은 개발 데이터 검수용입니다. 조회 상한을 넘으면 오류를 내며 일부 기사만 반환해 정상 선별인 것처럼 처리하지 않습니다. 운영 선별에는 날짜·분야 제한 query와 인덱스를 연결해야 합니다.
- 벡터는 다음 단계에서 multilingual-e5-small·384차원·cosine 방식으로 연결합니다. pgvector SQL을 Firestore로 직접 옮기지는 않습니다. Firestore는 벡터 검색을 지원하지만 인덱스·권한·비용·과거 날짜 필터는 실제 환경에서 검증해야 합니다. [Firestore 벡터 검색](https://firebase.google.com/docs/firestore/vector-search)

## 변경 파일

공통 저장 계약/메모리 구현, Firestore 어댑터, 저장 시연, 두 테스트 파일, Firebase 변경 기록·기술 안내·규칙 조각, SDK 의존성과 버전 잠금, 엔진 README입니다. 공통 PRD 원문과 다른 담당자 코드는 수정하지 않았습니다.
