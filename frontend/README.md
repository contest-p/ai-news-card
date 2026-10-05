# Frontend — 뉴스 브리핑

바닐라 HTML/CSS/JavaScript로 구성된 정적 웹 앱입니다. 빌드 도구 없이 `index.html`을 로컬 정적 서버로 제공하면 됩니다. `/feedback` 경로도 앱으로 연결되도록 Vercel rewrite 설정이 포함되어 있습니다.

## 연결 설정

1. `config.js`의 `apiBaseUrl`에 Backend의 API 루트(`/api/v1` 포함)를 설정합니다.
2. Backend 담당자가 전달한 Supabase 프로젝트 URL과 anon key를 `supabaseUrl`, `supabaseAnonKey`에 설정합니다. Google OAuth 공급자와 허용 callback URL은 Supabase 콘솔에서 등록해야 합니다.
3. 브라우저 앱에는 Supabase anon key만 둘 수 있습니다. 서비스 키, API 비밀값, 사용자 개인정보는 넣지 마세요.

카탈로그와 구독 API 경로·본문은 공통 PRD v0.3의 10-3 계약을 사용합니다. 현재 Backend 응답을 받기 전인 화면은 계약에 맞춰 연결 시도 후 오류 상태를 표시합니다. 실제 인증·API 연결 확인은 설정값과 Backend가 준비된 뒤 가능합니다.
