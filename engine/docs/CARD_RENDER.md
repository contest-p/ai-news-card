# 카드 JSON → 이미지 연결

2026-10-07: 엔진 렌더러가 `frontend/card-template.js`의 renderCardTemplate과 `frontend/styles.css`를 직접 사용하도록 연결했다. Frontend 원본은 변경하지 않았다. Node에서 신뢰한 템플릿을 실행해 정적 HTML을 만들고, 브라우저에서는 JavaScript와 외부 요청을 차단한다. 아래 과거 검수 기록은 이전 임시 템플릿 기준이다.

## 2026-10-07 공유 템플릿 검수

- 템플릿 버전 frontend-shared-v1. 카드 1·2를 각각의 PNG로 출력하며 제목·브랜드·출처·AI 생성 표시를 유지한다.
- 날짜는 템플릿에 전달하기 전에 KST로 변환한다. UTC 저녁 기사 게시일의 다음 KST 날짜 표시를 회귀 테스트했다.
- 로컬 한글 폰트를 내장하고 540px 구성에 2배 배율을 적용해 폭 1080px PNG를 생성한다. 이미지 바깥의 빈 여백은 제외한다.
- 실제 검증 카드, 제목 60자·본문 400자·용어 2개 표본, 배경 날짜 표본, 긴 영문 표본의 폰트·영역 넘침·외부 요청 검사를 통과했다.
- 실제 검증 카드 1은 1080×1231, 113941 bytes. PC 800px·모바일 390px 메일과 이미지 차단 화면에서 가로 넘침·이미지 누락이 없고 텍스트·원문 링크가 표시된다. 실제 카드와 최대 입력 배경 카드, 모바일 정상/차단 화면을 눈으로 확인했다.
- 전체 엔진 테스트 234개 통과.
- 설정된 SMTP_TEST_TO에 이미지 1장이 포함된 템플릿 테스트 메일을 1회 제출했고 SMTP 서버가 수락했다. 제목은 `[템플릿 테스트] [뉴스 브리핑] 2026.10.05 · 오늘의 관심 뉴스`다. 기존 10월 5일 검증 데이터를 사용했으며 새 AI 호출과 DB 연결은 하지 않았다.
- 사용자가 수신함 도착과 카드 이미지·본문 정상 표시를 확인했다. 실제 구독 API·피드백 저장 연동 검증은 별도다.

미리보기는 `.engine-local/live-card/mail/preview.html`, 이미지 차단 화면은 같은 폴더의 `images-blocked.html`에 있다.
테스트 발송 도구는 카드 내용·템플릿·스타일·이미지 해시별로 기록을 구분한다. 같은 판본을 다시 실행해도 중복 제출하지 않는다.

## 실행

저장소 루트에서 Node와 Python 환경을 준비한다.

```powershell
npm ci --prefix engine
.\.venv\Scripts\python.exe -m engine.card_render
```

입력: `.engine-local/live-card/result.json`. 생성 상태가 completed이고 카드 검사 결과가 ready_for_review인 경우에만 렌더링한다. 원문 근거·수치는 생성 단계에서 검사하며 렌더러는 필드·길이·출처 ID·URL·시점 역할을 추가 확인한다.

기본 폰트는 `C:/Windows/Fonts/NotoSansKR-VF.ttf`다. 다른 환경에서는 로컬 한글 폰트를 지정한다.

```powershell
.\.venv\Scripts\python.exe -m engine.card_render --font C:/Windows/Fonts/malgun.ttf
```

설치된 Chrome 또는 Edge를 자동 탐색한다. 다른 Chromium 실행 파일은 `CARD_BROWSER_PATH`로 지정한다. Chrome/Edge가 없으면 `npx --prefix engine playwright install chromium`으로 Playwright 브라우저를 준비할 수 있다. 브라우저 실행 환경은 `render_result.json`에 기록된다. 이미 제공된 Playwright 패키지를 쓰는 환경은 `PLAYWRIGHT_MODULE_PATH`로 그 모듈의 절대경로를 지정할 수 있다.

출력: `.engine-local/live-card/render/card1.html`, `card1.png`, `render_result.json`. 배경 카드가 있을 때만 card2도 생성한다. HTML에는 원문 링크가 있지만 PNG의 링크는 클릭할 수 없으므로 메일 연결 시 원문 링크를 별도로 제공해야 한다. 생성 JSON·응답·원문·HTML·PNG는 Git에 올리지 않는다.

## 표시 방식

- 폭 1080px, 높이 자동 증가. 글꼴 축소·본문 생략·말줄임 없이 표시한다.
- 제목·설명·용어·출처·게시 날짜·AI 생성 표시만 사용한다. 메일 텍스트에는 게시 시각도 표시한다.
- 수신자·개인별 선택 이유·검수용 근거 구절은 이미지에 노출하지 않는다.
- 카드 2 과거 문장의 as_of가 있으면 기준일, 없으면 근거 기사의 보도일을 KST로 표시한다.
- 기사 텍스트는 HTML 이스케이프하고 페이지 JavaScript와 외부 네트워크 요청을 차단한다.
- 로컬 폰트를 HTML에 포함하고 폰트 로드와 글자 영역 넘침을 검사한다.
- 이미지 크기·용량·hash, 템플릿/폰트 hash, 브라우저/Playwright 버전을 보고서에 기록한다.

## 2026-10-05 검수 결과

| 표본 | 크기 | PNG 용량 | 영역 넘침 | 외부 요청 |
|---|---|---:|---:|---:|
| 실제 SBS 카드 1 | 1080×1109 | 107,061 bytes | 0 | 0 |
| 최대 길이 카드 1 | 1080×1688 | 225,122 bytes | 0 | 0 |
| 최대 길이 카드 2 | 1080×1551 | 192,987 bytes | 0 | 0 |
| 긴 영문 카드 1 | 1080×1635 | 224,893 bytes | 0 | 0 |

위 4장을 눈으로 확인했다. 한글·영문과 출처·날짜 표시가 정상이고 영역 겹침·잘림이 없었다. 로컬 Noto Sans KR, Chrome 154.0.8037.95, Playwright 1.62.1로 확인했다. 가상 최대 입력의 마지막 문장이 중간에서 끝나는 것은 정확한 글자 수를 채운 표본 데이터이며 렌더러가 자른 것이 아니다.

레이아웃 표본은 API 없이 다시 실행할 수 있다.

```powershell
.\.venv\Scripts\python.exe -m engine.tools.render_layout_check
```

전체 자동 테스트 118개 통과. HTML 주입 방지, 숨겨야 할 근거 필드, 최대 길이/초과 입력, 미등록 출처·위험 URL, 카드 2 생략, 과거 기준일·보도일 구분, 생성 캐시 재사용·수치 형식 보정을 검사했다. 다른 입력의 응답으로 실패 작업을 보정하는 것과 모든 문장을 제외한 빈 카드를 성공으로 처리하는 것도 차단했다.

현재는 검수용 로컬 이미지이며 실제 모바일 크기·메일 CID 첨부·이미지 차단 시 대체 설명은 아직 검증하지 않았다.
