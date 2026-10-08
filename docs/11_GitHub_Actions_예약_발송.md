# GitHub Actions 예약 발송·개인정보 정리

2026-10-08. 공통 PRD의 매시간 7분 예약 결정을 구현했다. Vercel 프론트·백엔드는 그대로 사용하고, GitHub Actions의 Ubuntu 실행기가 엔진을 실행한다.

## 실행 구조

- `.github/workflows/engine-mail.yml`: 매시간 UTC 7분에 전체 구독 운영 배치를 실행한다. 한국 시간에도 매시간 7분이다. 기사 수집, Firestore 저장·과거 근거 검색, AI 생성, 카드 PNG 렌더링, SMTP 전송을 기존 엔진 명령으로 연결한다.
- `.github/workflows/privacy-cleanup.yml`: 매시간 UTC 17분에 백엔드의 개인정보 정리 API를 호출한다. 발송 실패·AI 설정 누락과 독립적으로 실행한다. SMTP·AI·Firestore 클라이언트 인증을 주입하지 않는다.
- 기본 브랜치에서만 실행한다. 같은 종류의 작업은 동시에 실행하지 않으며 실행 중 작업을 취소하지 않는다. 기존 Firestore 작업 ID·선점·상태로 재실행 중복을 방지한다. SMTP 수락 여부가 불분명한 unknown 작업은 자동 재발송하지 않는다.
- 발송은 Python 3.12, Node 22, 고정 버전 Playwright Chromium, 한글 NanumGothic 폰트와 고정 revision E5 모델을 준비한다. 모델 캐시는 재사용한다. 카드 렌더링 실패 시 기존 텍스트 대체 경로를 사용한다.
- 각 워크플로는 수동 실행에서 check 또는 run을 선택한다. 기본 check는 앱의 DB·AI·SMTP를 호출하지 않으며, 패키지 설치 자체는 네트워크를 사용한다. run은 실제 메일 발송 또는 개인정보 삭제를 수행한다.
- 예약은 설정 전까지 비활성화한다. ENGINE_SCHEDULE_ENABLED와 ENGINE_PRIVACY_ENABLED가 각각 true일 때 예약 작업이 실행된다. 수동 실행은 해당 변수와 무관하게 가능하다.

## GitHub 설정

저장소 Settings → Environments에서 `engine-production`을 만들고 실행 브랜치를 기본 브랜치로 제한한다. 아래 값은 이 환경의 Secrets와 Variables 또는 저장소 Actions Secrets와 Variables에 등록한다. 같은 이름을 양쪽에 두면 환경 설정이 우선하므로 중복 관리하지 않는다. 비밀값을 채팅·Git·프론트에 넣지 않는다.

### Secrets

| 이름 | 값 |
|---|---|
| FIREBASE_SERVICE_ACCOUNT_JSON | 해당 Firestore 프로젝트의 서버 서비스 계정 JSON 전체. 엔진은 메모리에서 읽으며 파일로 저장하지 않는다. Firestore 엔진 컬렉션 읽기·쓰기 권한 필요 |
| ENGINE_API_TOKEN | 배포된 백엔드와 동일한 32자 이상의 엔진 전용 인증 키 |
| OPENAI_API_KEY | 사용 중인 AI 공급자의 CHAT API 키 |
| SMTP_USER | SMTP 로그인 사용자 |
| SMTP_PASSWORD | SMTP 인증 비밀번호. Gmail이면 공급자가 발급한 앱 비밀번호 |
| SMTP_FROM | 발송자 이메일 |

### Variables

| 이름 | 값 |
|---|---|
| ENGINE_FIREBASE_PROJECT | ai-news-card 또는 실제 Firestore 프로젝트 ID |
| ENGINE_API_BASE_URL | 백엔드 엔진 prefix 전체. 기존 배포 안내 값: https://ai-news-card-nine.vercel.app/api/v1/engine |
| ENGINE_WEB_BASE_URL | 실제 프론트 origin. 기존 배포 안내 값: https://ai-news-card-frontend.vercel.app |
| OPENAI_BASE_URL | 현재 AI 공급자의 정확한 HTTPS API 주소 |
| OPENAI_MODEL | 현재 API 키로 사용 가능한 모델. 생략 시 기존 gpt-5.5 사용 |
| SMTP_HOST / SMTP_PORT / SMTP_SECURITY | 실제 SMTP 설정. Gmail 예: smtp.gmail.com / 465 / ssl |
| ENGINE_ARCHIVE_RETENTION_DAYS | 합의된 메일 보관 기간 1~30일. 필수이며 임의 기본값 없음 |
| ENGINE_MAX_CARD_JOBS_PER_BATCH | 선택. 초기 지인 테스트는 배치당 최대 10건 생성. 늘릴 경우 1~1000 범위. 나머지 작업은 기존 기한·복구 정책 적용 |
| ENGINE_SCHEDULE_ENABLED | 준비·본인 수신 확인 후 true. 미설정 또는 false면 발송 예약 중지 |
| ENGINE_PRIVACY_ENABLED | 정리 API 설정 확인 후 true. 미설정 또는 false면 개인정보 정리 예약 중지 |

ENGINE_SCHEDULE_ENABLED와 ENGINE_PRIVACY_ENABLED는 예약 job 시작 전에 검사하므로 저장소 Actions Variables로 등록한다. 나머지 값은 engine-production 환경에 등록해도 된다.

백엔드 Vercel에도 ENGINE_API_TOKEN, 독립된 FEEDBACK_TOKEN_SECRET(32자 이상), 서버 Firebase 인증이 필요하다. FRONTEND_ORIGINS는 실제 프론트 origin이어야 한다. PUBLIC_CATEGORIES는 기사 소스 검증을 마친 분야만 쉼표로 등록한다. 사용자에게 보이는 비밀 키는 없다.

## 활성화 순서와 수신 확인

1. 워크플로를 기본 브랜치에 반영한다. 프론트·백엔드는 최신 코드 배포가 완료됐는지 확인한다.
2. Secrets와 Variables를 등록하고 두 워크플로를 check로 수동 실행한다. check 성공은 설정 형태만 확인하며 실제 인증·메일 수신 성공을 뜻하지 않는다.
3. /catalog에 검증된 분야가 표시되는지 확인한 후 본인 계정으로 Google 로그인 → 신청 → 조회 → 설정 변경·해제를 확인한다. 메일 테스트용 본인 구독은 다시 활성 상태로 신청한다.
4. 첫 발송은 신청 다음 날 선택한 한국 시간이다. 예정 시각부터 3시간 이내에 발송 워크플로를 run으로 실행하거나 준비된 예약을 켠다. run은 전체 대상 운영 배치이므로 본인 검증을 끝내기 전 지인 신청을 받지 않는다. 날짜를 조작하거나 만료된 작업을 강제 전송하지 않는다.
5. Actions의 집계 sent는 SMTP 서버 수락이다. 실제 수신함·스팸함에서 뉴스 카드, 출처, 구독 관리·피드백 링크를 직접 확인한다. 로그에는 개별 사용자·작업 결과를 출력하지 않으며 HTML·MIME·이미지를 Actions 아티팩트로 업로드하지 않는다.
6. 개인정보 정리는 독립 run으로 수행한다. 삭제 요청을 내면 실제 Firebase Auth와 사용자 데이터가 삭제되므로 본인 테스트 계정으로 검증한다. 진행 상태와 재로그인 차단까지 확인한다.
7. 확인 후 두 예약 변수를 true로 설정하고 예약 실행 이력을 확인한다. 발송 중지 시 ENGINE_SCHEDULE_ENABLED=false로 바꾼다. 이미 실행 중인 SMTP 작업을 강제 취소하지 않는다. 개인정보 정리는 발송 중지와 별개로 유지할 수 있다.

## 배포 공개 상태 확인

2026-10-08 기존 배포 안내 URL의 공개 GET 요청만 확인했다. /health는 200·ok, CORS는 프론트 origin 허용, /catalog는 200·consent_version=v1이지만 categories=[]였다. 따라서 공개 분야 설정이 현재 가입 테스트의 선행 작업이다. 프론트 공개 config.js는 200이며 기존 백엔드 origin과 Firebase 설정이 포함돼 있었다. 실제 Google 로그인·구독 쓰기·SMTP·계정 삭제는 실행하지 않았다.

## 운영 한계

GitHub 예약은 지연되거나 실행이 누락될 수 있으며 정각 도착을 보장하지 않는다. 예정 시각 이후 3시간 복구 범위 안에서 다음 배치가 재시도한다. 첫 예약 기준으로는 선택 시각보다 최소 수 분 늦게 도착할 수 있다. 사용자 증가·긴 배치·즉시 발송 요구가 생기면 전용 실행기나 큐 방식으로 옮기는 편이 적합하다. 공개 저장소는 60일 활동이 없으면 예약이 비활성화될 수 있으므로 Actions 실패·실행 누락·사용량을 확인한다.

공식 문서: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule

## 검증 범위

외부 서비스 대역을 사용한 엔진 회귀 테스트, 서비스 계정 JSON 프로젝트 불일치·잘못된 값 차단, 공개 집계 로그의 작업별 정보 제외, 두 워크플로의 actionlint 검증을 수행했다. Secrets 미등록 상태이므로 GitHub 실제 실행과 메일 수신은 미검증이다.
