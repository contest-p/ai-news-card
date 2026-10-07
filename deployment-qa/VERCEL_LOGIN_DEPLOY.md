# Vercel 백엔드 배포와 로그인 연결

현재 프런트 프로젝트와 별도로 같은 GitHub 저장소의 백엔드 프로젝트를 만든다.
Vercel Hobby는 비상업적 개인 용도에서 무료이며 사용량 한도가 적용된다.

## 1. 백엔드 프로젝트

- Vercel에서 저장소를 Import하고 Root Directory를 `backend`로 지정한다.
- Framework Preset은 FastAPI를 사용한다. Python 3.12와 `main:app`은 pyproject.toml에 지정되어 있다.
- 환경변수 `FIREBASE_PROJECT_ID`를 `ai-news-card`로 지정한다.
- 환경변수 `FRONTEND_ORIGINS`를 `https://ai-news-card-frontend.vercel.app`로 지정한다.
- 환경변수 `FIREBASE_SERVICE_ACCOUNT_JSON`에 기존 프로젝트 서비스 계정 JSON 전체를 **Vercel의 서버 환경변수 입력란에서만** 저장한다. 채팅·Git·프런트 설정에 넣지 않는다.
- `CONSENT_VERSION`은 `v1`을 사용한다.
- 로그인 배포에는 엔진 토큰이나 메일 발송 설정이 필요하지 않다.
- 배포 후 `/health`가 `{"status":"ok"}`인지, `/catalog`가 JSON인지 확인한다.
- 인증 없는 `POST /users/sync`는 401이어야 한다.
- 백엔드 Production URL은 브라우저에서 접근 가능해야 한다. Vercel 계정 로그인을 요구하는 Deployment Protection이 사용자 API를 막지 않는지 확인한다.

## 2. 프런트 연결

- 기존 프런트 Vercel 프로젝트의 Production 환경변수 `BACKEND_API_URL`에 위 백엔드의 HTTPS origin을 저장한다. `/docs`나 다른 경로를 붙이지 않는다.
- 프런트 Root Directory는 `frontend`를 유지한다.
- Git의 frontend/vercel.json은 `node tools/build.cjs`를 실행하고 `dist`를 배포한다.
- 환경변수 저장 후 프런트를 재배포한다. 빌드에서 공개 API 주소만 config.js에 반영된다.
- Firebase Authentication의 허용 도메인에 `ai-news-card-frontend.vercel.app`을 유지한다.
- 실제 Google 로그인 후 개발자 도구 Network에서 `POST /users/sync`가 200인지 확인한다.

## 범위

이번 설정은 Google 로그인 → Firebase ID 토큰 검증 → 사용자 정보 동기화를 배포 대상으로 한다.
구독 API는 프런트의 `engine_settings` 형식에 맞춰 신청·조회·해제·재구독을 처리한다.
신청 다음 날부터 7/14/28일간 발송하며, `end_date_exclusive` 당일부터 만료된다.
재구독은 새 `subscription_id`를 발급하고, 같은 요청 키의 재시도는 중복 신청하지 않는다.

## 3. 테스트 구독 하나로 기사·메일 확인

1. 수정한 백엔드를 먼저 배포한다. 해당 계정으로 로그인 후 신청 → 조회 → 해제 → 재구독을 확인한다.
2. 마지막 신청 응답의 `subscription.subscription_id`를 `engine/.env`의 `ENGINE_TEST_SUBSCRIPTION_ID`에 저장한다.
3. `SMTP_TEST_TO`는 해당 로그인 계정의 이메일과 정확히 같아야 한다. 테스트 명령은 다른 계정으로 수신자를 바꾸지 않는다.
4. 백엔드의 `ENGINE_API_TOKEN`과 `engine/.env`의 토큰을 같은 값(32자 이상)으로 설정한다. 백엔드 `FEEDBACK_TOKEN_SECRET`도 독립된 32자 이상 값으로 설정한다.
5. `ENGINE_API_BASE_URL=https://ai-news-card-nine.vercel.app/api/v1/engine`, `ENGINE_WEB_BASE_URL=https://ai-news-card-frontend.vercel.app`로 연결한다.
6. `ENGINE_ARCHIVE_RETENTION_DAYS`는 1~30 범위로 정한다. 테스트 보관본은 `engine_test_mail_archives`에 저장한다.
7. 저장소 루트에서 아래 명령을 실행한다. `--check`는 설정 검사만 하며, `--send-test`는 실제 기사 수집·AI 생성·DB 저장·SMTP 전송을 한다.

```powershell
& engine/.venv/Scripts/python.exe -m engine.tools.connected_batch --check
& engine/.venv/Scripts/python.exe -m engine.tools.connected_batch --send-test --text-only
```

첫 발송은 신청 다음 날의 선택한 한국 시간부터 3시간 이내에 실행한다(자정·만료 시각에서 더 일찍 종료 가능).
발송 시간이 아니면 `TEST_SUBSCRIPTION_NOT_DUE`로 중단하며 구독 날짜를 조작하지 않는다.
테스트 조회와 미처리 작업은 지정한 구독 ID·수신자로 제한된다. 동일 작업 재실행은 중복 발송하지 않는다.
`--text-only`를 빼면 카드 이미지 렌더링도 시도한다.
요약의 `sent`는 SMTP 서버 수락을 뜻한다. 실제 수신함/스팸함 확인 후 수신 완료로 기록한다.
정기 자동 발송은 이 명령에서 설정하지 않는다. 메일의 피드백 링크 저장 API 연결은 별도 후속 작업이다.

공식 안내: https://vercel.com/docs/frameworks/backend/fastapi
