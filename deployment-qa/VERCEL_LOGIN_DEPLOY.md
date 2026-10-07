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
현재 backend/main.py의 구독 저장 형식은 프런트의 engine_settings 요청 형식과 다르다.
구독 신청·엔진 발송 연결은 별도 API 계약 수정과 검증이 필요하다.

공식 안내: https://vercel.com/docs/frameworks/backend/fastapi
