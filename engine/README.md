# 뉴스 브리핑 엔진 — 박경연

RSS 수집 → 기사 저장·선별 → 과거 기사 검색(RAG) → AI 카드 생성·검사 → 이미지 → 메일 조립을 담당합니다.

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
| `firebase/` | Firestore 연결·권한 안내 |

`.env`는 로컬 비밀 설정입니다. 결과물·생성 이력은 저장소 루트의 `.engine-local/`에 저장하며 Git에서 제외합니다. `node_modules/`, `__pycache__/`는 도구가 관리하는 로컬 파일입니다.

## 실행

모든 명령은 저장소 루트에서 실행합니다. 폴더 정리로 데모·도구의 명령 경로가 바뀌었습니다.

```powershell
# 자동 테스트: 외부 API·DB·실제 메일을 사용하지 않음
.\.venv\Scripts\python.exe -m unittest discover -s engine/tests -v

# 가상 기사 선별
.\.venv\Scripts\python.exe -m engine.demos.demo

# 카드 이미지 생성 → 로컬 메일 조립 → PC·모바일 미리보기 검사
.\.venv\Scripts\python.exe -m engine.card_render
.\.venv\Scripts\python.exe -m engine.demos.mail_demo
node engine/tools/render_mail_preview.cjs .engine-local/live-card/mail

# 최대 길이 카드의 이미지 검사
.\.venv\Scripts\python.exe -m engine.tools.render_layout_check
```

이미지·메일 명령에는 저장된 생성 결과와 Node·Playwright·브라우저·한글 폰트가 필요합니다. 실제 RSS 수집은 `engine.demos.live_collect_demo`, 실제 AI 생성은 `engine.demos.live_generate_demo --live`, SMTP 도구는 `engine.tools.smtp_test`입니다. 실행 전 상세 문서의 설정·호출 비용·전송 조건을 확인하세요. SMTP의 `--send`는 실제 메일을 전송합니다.

## 검토 결과와 남은 연결

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
- [Firestore 안내](firebase/README.md)

상세 문서의 날짜별 결과는 당시의 검증 기록입니다. 현재 연결 상태는 위의 검토 결과를 기준으로 확인하세요.
