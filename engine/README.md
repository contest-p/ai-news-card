# 뉴스 브리핑 엔진 — 박경연

RSS 수집 → 기사 저장·선별 → 과거 기사 검색(RAG) → AI 카드 생성·검사 → 이미지 → 메일 조립을 담당합니다.

## 현재 상태 — 2026-10-06 KST

**개별 기능 구현과 로컬 검증 단계입니다. DB 연결만으로 서비스가 완성되지는 않습니다.** 운영 발송 작업 관리·전체 배치·예약 실행·통합 검증이 남아 있습니다. 기준은 공통 PRD 본문 v0.4와 박경연 엔진 PRD 본문 v0.3입니다.

### 이번에 완료한 작업

- 코드 변경 없는 폴더 정리: Python 의존성 파일 4개를 `dependencies/`, Firestore 자료를 `docs/firebase/`로 이동했습니다. 기존 Python import·실행 경로는 유지했습니다.
- 이후 기능 구현: `mail_assembly.py`에 뉴스 피드백 링크, 뉴스 없음 안내, 자연 만료 종료 안내를 추가했습니다. 메일 본문 조립이며 실제 자동 발송 구현은 아닙니다.
- 정상 수집·후보 없음 조건, 수동 해제 사용자 종료 안내 차단, 7/14/28일 날짜, 토큰 fragment 처리·수신자 분리를 검사합니다.
- `demos/mail_types_demo.py`와 `samples/mail.json`으로 세 종류의 메일을 재현할 수 있습니다. 미리보기 결과는 상태 기록과 별도 위치에 저장합니다.
- 자동 테스트 **139개 통과**. PC 800px·모바일 390px의 3종 기본/이미지 없는 미리보기 총 12개 화면 검사 통과. 실제 DB·AI·SMTP 호출은 하지 않았습니다.

### 다음 작업 순서

| 순서 | 작업 | 완료해야 할 내용 |
|---|---|---|
| 1 | 카드 이미지 연결 | 프런트 카드 템플릿 연결, 최초+1회 이미지 재시도, 실패 시 검증된 전체 텍스트로 전환 |
| 2 | 발송 제어 로직 | 작업 ID·상태 전이·발송 기한, 명확한 일시 실패만 최대 3회 SMTP 시도, unknown 자동 재발송 금지; 가짜 저장소로 먼저 테스트 |
| 3 | 실제 DB·Backend 연결 | 사용자 요청상 2026-10-07 진행 예정. 기사·임베딩·구독·발송 이력·토큰, 트랜잭션 선점·생성 횟수, 발송 직전 해제·만료·삭제 요청 확인 |
| 4 | 전체 자동화 | 수집부터 발송까지 배치 연결, 매시간 7분 Actions 예약, 수집 15분/배치 45분 제한, 개인정보 정리 호출·실행 요약 |
| 5 | 실제 통합 검증 | 가입 → 카드·메일 수신 → 피드백 → 해제·만료, 중단·동시 실행·재시도, 실제 메일 앱·사용자 테스트 |

1~2번의 로직·가상 테스트는 DB 연결 전에 진행할 수 있습니다. 공유 캐싱·의미 기반 사건 묶기·자동 타 보도 대조는 후순위입니다. 요구사항별 상세 누락 항목은 [구현 현황](docs/IMPLEMENTATION_STATUS.md)에 기록했습니다.

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
| 메일 | `mail_assembly.py` | 뉴스·뉴스 없음·종료 안내와 링크·첨부 조립 |
| Backend 경계 | `gateway.py` | 구독·발송 가능 여부 연결 규격과 샘플 구현 |
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

모든 명령은 저장소 루트에서 실행합니다. 아래는 이번에 준비한 `engine/.venv/` 기준입니다. 기존 루트 `.venv/`를 사용하는 환경에서는 Python 실행 경로를 바꾸면 됩니다. 폴더 정리 단계에서는 코드를 유지했고, 이후 메일 기능 구현 단계에서 관련 코드와 테스트를 수정했습니다.

Python 의존성 설치: `python -m pip install -r engine/dependencies/requirements.txt`

RAG 추가 의존성 설치: `python -m pip install -r engine/dependencies/requirements-rag.txt`

```powershell
# 자동 테스트: 외부 API·DB·실제 메일을 사용하지 않음
.\engine\.venv\Scripts\python.exe -B -m unittest discover -s engine/tests -v

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
- **이미지:** `card_render.py`는 아직 `templates/card_preview.html`을 사용합니다. 프런트의 `frontend/card-template.js`가 추가됐지만 엔진에는 연결되지 않았습니다.
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
