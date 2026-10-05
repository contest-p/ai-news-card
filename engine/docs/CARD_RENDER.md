# 카드 JSON → 이미지 연결

프런트 템플릿은 `frontend/card-template.js`에 추가됐지만 엔진 렌더러에는 아직 연결되지 않았다. 이번 구현은 엔진 검수용 임시 템플릿으로, 같은 공통 카드 JSON(10-2)을 받아 추후 프런트 템플릿으로 교체할 수 있는 렌더링 경로다. Frontend 파일은 변경하지 않았다.

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
- 제목·설명·용어·출처·게시 시각·AI 편집/원문 발췌 표시만 사용한다.
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
