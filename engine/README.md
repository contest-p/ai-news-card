> **2026-10-08 연결 보완:** [연결 보완 기록](../docs/10_연결_보완_2026-10-08.md)에 피드백 환경 분리와 발송에 독립적인 개인정보 정리 명령을 정리했습니다.

# 뉴스 브리핑 엔진 — 박경연

2026-10-08: 실제 RAG/임베딩 저장·검색, 전체 구독/만료 배치 진입점, 기사 복구·재처리·정리 호출을 구현했습니다. 최신 상태와 Backend 연결 계약은 [엔진 구현 보완](docs/ENGINE_COMPLETION.md)을 참조하세요. 아래 날짜별 기록은 당시 상태입니다. 배포·QA는 이번에 수행하지 않았습니다.

RSS 수집 → 기사 저장·선별 → 과거 기사 검색(RAG) → AI 카드 생성·검사 → 이미지 → 메일 조립을 담당합니다.

## 현재 상태 — 2026-10-06 KST (DB 연결 전 마무리)

**DB가 필요 없는 발송 흐름 전체를 코드로 연결했고, 가짜 저장소·가짜 Backend로 검증했습니다.** DB는 Cloud Firestore(Firebase)로 확정이며 실제 연결은 2026-10-07에 진행합니다. 기준은 공통 PRD 본문 v0.4와 박경연 엔진 PRD 본문 v0.3입니다.

### 오늘 추가·수정한 것

| 구분 | 파일 | 내용 |
|---|---|---|
| 신규 | `delivery.py` | 발송 작업 고정 ID, 뉴스 기한(+3시간·다음 자정·만료 중 이른 시각), 종료 안내 24시간, 원자적 선점·claim_token·상태 전이, 멈춘 processing 재선점, 멈춘 sending→unknown. `JobStore` 계약 + 메모리 구현 |
| 신규 | `smtp_sender.py` | 운영 SMTP 1회 전송. 수락/명시적 거부(4xx 재시도·5xx 영구)/결과 불확실(unknown) 구분, Date 헤더, 봉투 검사 |
| 신규 | `pipeline.py` | 작업 1건: 선점 → 최신 상태 확인 → 선별 → 카드 → 이미지 → 메일 조립·보관 → SMTP 직전 재확인 → 전송·상태 기록. SMTP 최초 포함 3회, 재시도 때 같은 메일·토큰 재사용 |
| 신규 | `batch.py` | 배치 1회: 수집 15분 예산 → 작업 생성·이전 미완료 작업 복구 → 45분 예산 → 개인정보 정리 호출 → 개인정보 없는 실행 요약 |
| 신규 | `card_builder.py`, `card_images.py`, `mail_archive.py` | 생성 작업 연결 어댑터 / 이미지 최초+1회 후 텍스트 전환(P-16) / 재시도용 MIME 보관 |
| 신규 | `demos/batch_demo.py` | 네트워크·DB·AI·SMTP 없이 배치 전체 실행. outbox에 .eml 저장 |
| 수정 | `live_collection.py` | 기사 단위 정책 제외(분류 불가·게시 시각 없음 등)는 정상 수집으로 판정 → 뉴스 없음 안내 가능. 새 QA 소스는 그 소스만 실패, CWD 무관 로드, 수집 예산 |
| 수정 | `selection.py`, `generation.py` | 60자 초과 제목은 후보 제외·API 호출 전 차단(불필요한 재호출 방지). 수치 형식 보정을 데모에서 생성 단계로 이동 |
| 수정 | `cards.py`, `card_render.py` | 날짜(2026-10-01, 10월 5일 등)·식별자(COVID-19, G7)를 수량으로 오인하지 않음. 용어 이름 20자(프런트와 동일), 초과 시 용어만 생략 |
| 수정 | `mail_assembly.py`, `gateway.py` | `preview` 명시 필수(검수용 문구가 실제 메일에 붙는 사고 방지), 메일 HTML 틀 공통화. 발송 가능 여부가 이유(cancelled/expired 등)를 함께 반환 |

자동 테스트 **201개 통과**(기존 139 + 신규 62). 실제 DB·AI·SMTP 호출은 하지 않았습니다.

### 2026-10-07 추가 검토·수정

- 배치의 monotonic 기한을 작업에 전달하고 생성·이미지·메일 조립 단계 및 SMTP 직전에서 확인합니다. 예산 초과 작업은 SMTP 없이 재시도 가능한 실패로 남깁니다. 이미 진행 중인 외부 호출은 각 의존성의 timeout까지 기다릴 수 있으며, SMTP를 시작한 뒤에는 결과 기록을 완료합니다. 마지막 작업이나 개인정보 정리 중 초과한 시간도 실행 요약에 반영합니다.
- 멈춘 `sending`을 `unknown`으로 복구하거나 기한 경과 작업을 `skipped_late`로 처리한 경우, 실행 요약에 해당 상태와 오류 코드를 남깁니다.
- 보관 MIME에 메일 종류를 함께 넣어 보관 직후 중단되더라도 종류를 복원합니다. 이전 형식은 고정 Message-ID로 식별하며, 재시도에는 같은 메일·피드백 토큰을 재사용합니다.
- 회귀 테스트 9개 추가 후 **전체 210개 통과**. DB·AI·SMTP 실제 호출은 하지 않았습니다.

### 다음 작업 순서

| 순서 | 작업 | 완료해야 할 내용 |
|---|---|---|
| 1 | 실제 DB·Backend 연결 (2026-10-07) | `JobStore`·생성 작업 저장소·기사/임베딩의 Firestore 구현, `EngineGateway` 함수(대상 목록·발송 가능 이유·피드백 토큰) 합의, 메일 보관 위치·기한 |
| 2 | 운영 실행 진입점·Actions | 실제 객체를 조립하는 실행 스크립트, 매시간 7분 예약(루트 workflow는 윤지민과 공유 후 추가), Secrets·폰트·Chromium 설치 |
| 3 | 카드 템플릿 연결 | 2026-10-07 공유 프론트 템플릿 연결·이미지/메일 검수 완료. 테스트 메일 실제 수신·정상 표시 확인 |
| 4 | 실제 통합 검증 | 가입 → 카드·메일 수신 → 피드백 → 해제·만료, 중단·동시 실행·재시도, 실제 메일 앱·사용자 테스트 |

공유 캐싱·의미 기반 사건 묶기·자동 타 보도 대조는 후순위입니다. 상세는 [구현 현황](docs/IMPLEMENTATION_STATUS.md)을 확인하세요.

## 폴더 안내

| 위치 | 내용 |
|---|---|
| 루트의 Python 모듈 | 수집·저장·선별·RAG·생성·카드·메일 핵심 로직 |
| `demos/` | 샘플 데이터 및 실제 연결을 확인하는 CLI |
| `tools/` | 카드·메일 브라우저 검수, SMTP 테스트 도구 |
| `docs/` | 구현 과정, 검증 결과, 상세 실행 안내 |
| `tests/` | 자동 테스트 |
| `samples/` | 가상 기사·구독·본문·생성 응답 표본 |
| `templates/` | 엔진 검수용 임시 카드 템플릿 |
| `docs/firebase/` | Firestore 연결·권한 안내와 규칙 조각 |
| `dependencies/` | Python 설치 목록과 버전 잠금 파일 |

파일별 역할은 [구조 안내](docs/STRUCTURE.md), 샘플 보관·삭제 기준은 [샘플 안내](samples/README.md)를 확인하세요.

`.env`는 로컬 비밀 설정입니다. 결과물·생성 이력은 저장소 루트의 `.engine-local/`에 저장하며 Git에서 제외합니다. `node_modules/`, `__pycache__/`는 도구가 관리하는 로컬 파일입니다.

### 주요 Python 파일

| 기능 | 파일 | 역할 |
|---|---|---|
| 수집 | `collection.py`, `live_collection.py` | 로컬 샘플 수집 / 실제 RSS·본문 수집 |
| 저장 | `article_store.py`, `firestore_article_store.py` | 저장 규칙·메모리 저장소 / Firestore 어댑터 |
| 선별 | `selection.py` | 관심 기사 선택, 기사 자료형·URL·시간 처리 |
| 검색 | `embeddings.py`, `rag.py` | 기사 벡터 생성 / 과거 근거 검색 |
| AI 요청 | `card_prompt.py`, `chat_client.py`, `chat_worker.py` | 프롬프트 / 호출·시간 제한 / 실제 HTTP 요청 |
| 생성 관리 | `generation.py` | 생성 흐름·로컬 잠금·호출 횟수·결과 저장 |
| 카드 검사 | `cards.py` | 근거·숫자·길이·시점 검사, 카드 데이터 조립 |
| 이미지 | `card_render.py` | HTML 구성·PNG 변환 도구 호출 |
| 메일 | `mail_assembly.py`, `mail_archive.py` | 뉴스·뉴스 없음·종료 안내와 링크·첨부 조립 / 재시도용 보관 |
| 이미지 연결 | `card_images.py` | 렌더 최초+1회, 승인된 PNG만 첨부, 실패 시 텍스트 |
| 발송 | `delivery.py`, `smtp_sender.py` | 작업 ID·기한·선점·상태 전이 / SMTP 전송·결과 분류 |
| 흐름 | `card_builder.py`, `pipeline.py`, `batch.py` | 생성 연결 / 작업 1건 처리 / 배치 1회 실행·요약 |
| Backend 경계 | `gateway.py` | 대상 목록·발송 가능 이유·피드백 토큰 연결 규격과 샘플 구현 |
| 설정·패키지 | `settings.py`, `__init__.py` | AI 설정 로드 / 패키지 표시 |

`tools/smtp_test.py`는 지정 테스트 주소 전송 도구이며 운영 배치를 대신하지 않습니다. `package.json`·`package-lock.json`은 Node/Playwright 의존성, `.env.example`은 비밀값 없는 설정 예시입니다.

### 샘플·출력·상태 기록 구분

| 위치 | 보관·정리 기준 |
|---|---|
| `samples/`, `tests/` 안의 가상 데이터 | 회귀 테스트·재현에 필요하므로 프로젝트 완성 후에도 보관 |
| `engine/.engine-local/previews/mail-types/` | 이번 3종 메일의 가상 미리보기만 저장. 검수 후 삭제·재생성 가능 |
| 저장소 루트 `.engine-local/` | 기존 생성 결과와 AI 호출·SMTP 상태 기록이 함께 있음. 통째 삭제하면 중복 방지 기록도 사라질 수 있음 |
| `engine/.venv/` | 이번 검증용 Python 환경. Git 제외, 소스·샘플과 별도 |

## 실행

2026-10-07 연결 작업: Firestore 발송·생성·MIME 저장소, Backend HTTP 어댑터, 테스트 구독 1개용 연결 배치 도구를 추가했습니다. [연결 실행 안내](docs/firebase/CONNECTED_ENGINE.md)와 [백엔드 API 규격 제안](docs/firebase/ENGINE_API_CONTRACT.md)을 참고하세요. 실제 계정 인증과 팀 API 연결 검증은 코드 테스트와 별도입니다.

기존 팀 DB 연결을 시작하는 도구를 추가했습니다. `engine.tools.db_check`는 사용자·구독·기사 컬렉션을 제한 조회하고 개인정보 없이 연결 준비 상태를 요약합니다. `engine.tools.collect_to_firestore`는 수집 결과를 기존 기사 저장 어댑터에 연결하며 `--live --write --project`를 지정할 때만 실제 기사를 저장합니다. 실행법과 현재 구독 필드의 한계는 [DB 연결 시작 안내](docs/firebase/ENGINE_DB_START.md)를 참고하세요. 실제 DB 연결에는 서버 자격 증명이 필요합니다.

모든 명령은 저장소 루트에서 실행합니다. 아래는 이번에 준비한 `engine/.venv/` 기준입니다. 기존 루트 `.venv/`를 사용하는 환경에서는 Python 실행 경로를 바꾸면 됩니다. 폴더 정리 단계에서는 코드를 유지했고, 이후 메일 기능 구현 단계에서 관련 코드와 테스트를 수정했습니다.

Python 의존성 설치: `python -m pip install -r engine/dependencies/requirements.txt`

RAG 추가 의존성 설치: `python -m pip install -r engine/dependencies/requirements-rag.txt`

```powershell
# 자동 테스트: 외부 API·DB·실제 메일을 사용하지 않음
.\engine\.venv\Scripts\python.exe -B -m unittest discover -s engine/tests -v

# DB·AI·SMTP 없이 배치 전체 실행 (결과: engine/.engine-local/previews/batch-demo/)
.\engine\.venv\Scripts\python.exe -B -m engine.demos.batch_demo

# DB·AI·SMTP 없이 세 종류의 메일 미리보기 생성
.\engine\.venv\Scripts\python.exe -B -m engine.demos.mail_types_demo

# 가상 기사 선별
.\engine\.venv\Scripts\python.exe -m engine.demos.demo

# 카드 이미지 생성 → 로컬 메일 조립 → PC·모바일 미리보기 검사
.\engine\.venv\Scripts\python.exe -m engine.card_render
.\engine\.venv\Scripts\python.exe -m engine.demos.mail_demo
node engine/tools/render_mail_preview.cjs .engine-local/live-card/mail

# 최대 길이 카드의 이미지 검사
.\engine\.venv\Scripts\python.exe -m engine.tools.render_layout_check
```

이미지·메일 명령에는 저장된 생성 결과와 Node·Playwright·브라우저·한글 폰트가 필요합니다. 실제 RSS 수집은 `engine.demos.live_collect_demo`, 실제 AI 생성은 `engine.demos.live_generate_demo --live`, SMTP 도구는 `engine.tools.smtp_test`입니다. 실행 전 상세 문서의 설정·호출 비용·전송 조건을 확인하세요. SMTP의 `--send`는 실제 메일을 전송합니다.

## 검토 결과와 남은 연결

2026-10-06 PRD 대조 및 다음 작업은 [구현 현황](docs/IMPLEMENTATION_STATUS.md)을 확인하세요. DB 연결 전 단계로 뉴스·뉴스 없음·자연 만료 안내의 메일 조립과 피드백 링크를 추가했습니다. 실제 예약 발송·DB·토큰 발급 연결은 남아 있습니다.

새 3종 메일은 외부 호출 없이 `.\engine\.venv\Scripts\python.exe -B -m engine.demos.mail_types_demo`로 확인할 수 있습니다. 출력은 `engine/.engine-local/previews/mail-types/`에 모이며 생성·전송 상태 기록과 분리됩니다. 이번에 준비한 테스트 환경은 `engine/.venv/`입니다. 기존 문서의 루트 `.venv/`를 사용하는 경우 해당 Python으로도 실행할 수 있습니다.

- 수집·선별·근거 검증·메일 조립 및 전송 중복 방지에 대한 자동 테스트가 있습니다. 테스트 통과는 운영 환경 검증을 대신하지 않습니다.
- **이미지:** `card_render.py`는 `frontend/card-template.js`와 `frontend/styles.css`를 사용합니다. 카드 1·2, 최대 입력·긴 영문과 PC·모바일 메일을 검수했고 테스트 메일의 실제 수신·정상 표시도 사용자가 확인했습니다.
- **생성 이력:** `generation.py`의 파일 저장·잠금은 단일 PC 개발용입니다. 여러 서버·GitHub Actions 실행 간 공유되는 Firestore 선점·호출 횟수 기록은 별도 연결해야 합니다.
- **Firestore:** 기사 저장 어댑터가 있지만 실제 DB 연결 검증은 남아 있습니다. `list_current()`는 개발용 제한 조회이며 운영용 날짜·분야 조회가 필요합니다.
- **수집:** 경향신문 표본은 게시 시각 누락으로 후보에서 제외됐습니다. RSS가 열린다는 이유만으로 공개 카테고리 사용을 승인하지 않습니다.
- **운영 발송:** SMTP 도구는 지정된 테스트 수신자용입니다. 구독 API·예약 실행·운영 발송 이력과 연결한 서비스 전체 흐름은 아직 검증되지 않았습니다.

## 상세 문서

- [개발·실행 기록](docs/DEVELOPMENT.md)
- [실제 RSS 수집 검증](docs/LIVE_RSS_CHECK.md)
- [카드 이미지 검수](docs/CARD_RENDER.md)
- [메일 조립·미리보기](docs/MAIL_PREVIEW.md)
- [뉴스·뉴스 없음·종료 메일 입력과 실행](docs/MAIL_ASSEMBLY.md)
- [PRD 구현 현황·남은 작업](docs/IMPLEMENTATION_STATUS.md)
- [Firestore 안내](docs/firebase/README.md)

상세 문서의 날짜별 결과는 당시의 검증 기록입니다. 현재 연결 상태는 위의 검토 결과를 기준으로 확인하세요.
