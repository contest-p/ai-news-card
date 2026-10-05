# Frontend — 뉴스 브리핑

바닐라 HTML/CSS/JavaScript로 구성된 정적 웹 앱입니다. 빌드 도구 없이 `index.html`을 로컬 정적 서버로 제공하면 됩니다. `/feedback` 경로도 앱으로 연결되도록 Vercel rewrite 설정이 포함되어 있습니다.

## 연결 설정

1. `config.js`의 `apiBaseUrl`에 Backend의 API 루트(`/api/v1` 포함)를 설정합니다.
2. Firebase 콘솔 → 프로젝트 설정 → 웹 앱의 `apiKey`, `authDomain`, `projectId`, `appId`를 `config.js`의 `firebase`에 설정합니다. 값을 임의로 만들지 말고 Backend와 같은 프로젝트를 사용합니다.
3. Authentication → 로그인 방법에서 Google 공급자를 활성화하고 지원 이메일을 설정합니다. 승인된 도메인에 실제 Vercel 도메인과 로컬 테스트용 `localhost`를 등록합니다. Google OAuth 동의 화면·테스터 설정도 확인합니다.
4. 웹 설정은 공개 식별 정보입니다. 서비스 계정 JSON·private_key·관리자 자격 증명은 브라우저에 넣지 않습니다.

카탈로그와 구독 API 경로·본문은 공통 PRD v0.4의 10-3 계약을 사용합니다. 기존 PRD 파일명은 유지합니다. 구독·피드백 데이터는 FastAPI를 통해 Cloud Firestore에 저장하며 프런트가 Firestore에 직접 쓰지 않습니다.

`firebase-auth.js`는 Firebase SDK 12.19.0의 App/Auth만 필요할 때 불러옵니다. Google 팝업 로그인·취소·팝업 차단 안내, 세션 복원·로그아웃을 처리합니다. 보호된 API 요청마다 `getIdToken()`으로 Firebase ID 토큰을 받아 Bearer 헤더에 전달합니다. 피드백 전용 진입에서는 Firebase SDK를 로드하지 않고 피드백 토큰만 POST 본문으로 전달합니다.

Backend는 Firebase Admin SDK의 `verify_id_token(token, check_revoked=True)`로 프로젝트·만료·폐기 상태를 확인하고 `uid`로 사용자 소유권을 검사해야 합니다. CORS에는 웹 주소와 Authorization/Content-Type/Idempotency-Key 헤더를 허용해야 합니다. 서버 Firestore 접근은 IAM을 사용하므로 API 권한 검사가 별도로 필요합니다.

실제 설정값은 현재 비어 있습니다. 설정 후 Google 로그인·새로고침·로그아웃·다른 계정 전환·토큰 만료·구독 저장을 실제 환경에서 확인해야 합니다. 이번 코드 변경 자체가 배포 또는 실제 로그인 검증 완료를 의미하지 않습니다.

근거: [Google 로그인](https://firebase.google.com/docs/auth/web/google-signin), [ID 토큰 검증](https://firebase.google.com/docs/auth/admin/verify-id-tokens), [서버 접근 제어](https://firebase.google.com/docs/firestore/security/overview).
