# Frontend — 뉴스 브리핑

바닐라 HTML/CSS/JavaScript 앱입니다. 프론트는 Firebase로 Google 로그인하고, Firebase ID Token을 백엔드에 전달해 사용자와 구독을 Firestore에 저장합니다. DB 접근과 엔진 인증은 서버에서 처리합니다.

## 로컬 실행

프로젝트 루트에서 각각 실행합니다.

```powershell
.\backend\.venv\Scripts\python.exe -B -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
.\backend\.venv\Scripts\python.exe -B frontend/tools/dev_server.py --port 5500
```

웹은 **http://localhost:5500** 에서 엽니다. 실제 프로젝트의 Firebase 승인 도메인에 localhost가 등록되어 있고 127.0.0.1은 등록되어 있지 않아, 개발 서버는 숫자 주소를 localhost로 이동시킵니다. 이 서버는 SPA 경로의 직접 진입/새로고침도 지원합니다.

`config.js`의 Firebase 공개 설정은 기존 `backend/login_test.html`과 동일한 ai-news-card 웹 앱을 사용합니다. 로컬에서는 백엔드 주소 http://127.0.0.1:8000을 사용합니다. 서비스 계정 JSON, 엔진 인증키, 비밀번호는 프론트에 넣지 않습니다.

## 실제 백엔드 계약

API base URL은 백엔드 origin이고 사용자 API에 `/api/v1`을 붙이지 않습니다. 엔진 전용 `/api/v1/engine`과 별개입니다.

| 기능 | API |
|---|---|
| 관심 분야/기간/동의 버전 | GET /catalog (공개) |
| 로그인/세션 복원 후 사용자 저장 | POST /users/sync |
| 구독 신청 | POST /subscriptions/save |
| 내 구독 조회 | GET /subscriptions/me |
| 구독 해제 | PATCH /subscriptions/cancel |

구독 신청 본문은 `plan: "basic"`과 `engine_settings: {categories, keywords, delivery_hour_kst, duration_days, consent_version}`입니다. uid/email은 토큰에서 백엔드가 정합니다. 시작일은 다음 한국 날짜, 종료일은 시작일+7/14/28일(종료일 제외)입니다. 프론트는 백엔드의 평탄한 설정과 날짜 필드를 화면에 맞게 표시합니다. 404 구독 없음은 정상 빈 상태로 처리합니다.

보호된 요청마다 Firebase `getIdToken()`을 사용하고 401이면 강제 갱신 후 한 번 재시도합니다. 로그인/구독 버튼의 중복 요청을 막습니다. 아직 백엔드 구독 저장의 서버 멱등 처리까지 구현한 것은 아닙니다.

활성 구독의 설정 변경과 피드백 resolve/저장은 백엔드 후속 작업입니다. 구독 관리 화면은 현재 설정 조회와 해제를 제공하고 설정 변경은 준비 중으로 표시합니다. 피드백 전용 진입에서는 Firebase 사용자 인증을 사용하지 않습니다.

## 팀 배포

- `config.js`의 apiBaseUrl을 배포한 백엔드 HTTPS origin으로 설정합니다. 배포 웹이 방문자의 localhost에 접속하지 않도록 비로컬 환경의 기본값은 비워 두었습니다.
- `backend/.env`의 FRONTEND_ORIGINS에 배포 웹 origin을 쉼표로 구분해 추가합니다. 로컬 5500/5501은 기본 허용됩니다.
- Firebase Authentication에서 Google 공급자 활성화와 실제 웹 도메인 승인을 확인합니다. 권한/도메인 변경은 프로젝트 담당자가 처리합니다.
- CONSENT_VERSION을 변경하면 구독 신청 전에 새 안내를 제공하고 최신 동의를 받아야 합니다.

## 검증

```powershell
node --test frontend/tests/firebase-auth.test.cjs frontend/tests/backend-connection.test.cjs
.\backend\.venv\Scripts\python.exe -B -m unittest discover -s backend/tests -v
```

API 계약/소유권/구독 신청·조회·해제·재구독/토큰 갱신은 대역을 사용하는 자동 테스트로 검증합니다. 실제 Firebase 설정과 localhost 승인, SDK 파일, 로컬 카탈로그/CORS 통신도 확인했습니다. 실제 Google 계정 선택·동의·구독 저장은 사용자가 로그인한 후 확인해야 하며 자동 테스트 통과와 구분합니다.

근거: [Google 로그인](https://firebase.google.com/docs/auth/web/google-signin), [ID 토큰 검증](https://firebase.google.com/docs/auth/admin/verify-id-tokens).
