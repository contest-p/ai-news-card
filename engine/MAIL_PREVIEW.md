# 로컬 뉴스 메일 조립·미리보기

실제 생성 카드 1과 PNG를 기존 카드 데이터 그대로 메일 HTML·일반 텍스트·EML에 연결했다. 설명을 새로 요약하거나 AI를 다시 호출하지 않는다. DB·SMTP 발송 기능은 포함하지 않는다.

## 실행과 출력

```powershell
.\.venv\Scripts\python.exe -m engine.card_render
.\.venv\Scripts\python.exe -m engine.mail_demo
node engine/render_mail_preview.cjs .engine-local/live-card/mail
```

첫 명령은 현재 카드와 연결된 이미지 hash 보고서를 만든다. 기존 이미지 보고서에 카드 hash가 없으면 텍스트 전용으로 처리하므로 이전 렌더링 결과는 한 번 다시 생성한다.

| 출력 파일 | 용도 |
|---|---|
| briefing.eml | text/plain + HTML + CID PNG가 들어 있는 MIME 메일 |
| preview.html | PNG를 data URL로 바꾼 브라우저용 미리보기 |
| images-blocked.html | 이미지를 제거한 텍스트 표시 확인 표본 |
| text-fallback.txt | 제목·선택 이유·전체 설명·용어·시점·출처 URL |
| mail_result.json | MIME 크기·첨부 수·실패 이유·미발송 상태 |
| preview-800.png / preview-390.png | 데스크톱·모바일 폭의 정상 화면 |
| images-blocked-800.png / images-blocked-390.png | 데스크톱·모바일 폭의 이미지 차단 표본 |
| layout_result.json | 브라우저 레이아웃 검증 결과 |

모두 `.engine-local/live-card/mail/`에 저장하며 Git에서 제외한다. 발신자·수신자는 실제 주소가 아닌 `example.invalid` 표본이다. 메일에 실제 발송·피드백 연결 전인 로컬 검수용이라는 안내를 표시한다.

## 조립 계약과 확인

`assemble_mail(NewsMailData) → subject, EmailMessage`. 수신자·선택 이유는 메일 데이터에만 넣고 공통 카드 이미지에 넣지 않는다. 수신자는 한 명이며 서로 다른 수신자의 Message-ID를 구분한다.

메일은 multipart/alternative의 text/plain과 multipart/related(HTML + inline image/png)로 구성한다. HTML의 CID와 첨부 Content-ID가 일치한다. 이미지와 별개로 전체 설명을 HTML에 항상 표시해 이미지가 없어도 읽을 수 있다. 출처는 이미지 밖의 HTTPS 링크로 제공한다. 카드 2 과거 문장은 기준일 또는 근거 보도일을 표시한다.

입력 문자열은 HTML 이스케이프하고 주소의 CR/LF 주입을 거부한다. 이미지 경로는 승인된 렌더링 디렉터리 안으로 제한한다. 현재 카드 hash, PNG hash, 폰트·넘침·외부 요청 검사 결과를 확인한다. 이미지가 누락·변조됐거나 다른 기사 이미지이면 텍스트 전용으로 처리하고 이유를 보고서에 남긴다. PNG당 2MB 제한은 개발용 제안이다.

2026-10-05 실제 카드 1의 EML 크기는 152,661 bytes, 이미지 1개다. Chrome에서 800px/390px 폭의 정상·이미지 차단 표본을 확인했다. 가로 넘침·깨진 이미지·외부 요청은 없었고 전체 설명과 출처 링크가 읽혔다. 모바일에서는 이미지 글씨가 작아질 수 있어 아래 16px 텍스트 설명을 함께 제공한다.

자동 테스트는 전체 124개 통과했다. MIME 재파싱·CID/첨부 일치·두 카드·이미지 없는 본문·과거 날짜·수신자 분리·HTML 이스케이프·헤더 주입·위험 링크·다른 기사 이미지·승인 경로 밖 파일·이미지 변조를 확인했다.

## 다음 연결

- 발송 계정과 테스트 수신자 설정.
- 실제 메일 앱에서 CID 이미지·이미지 차단·모바일 표시 검증.
- Backend가 제공하는 피드백 fragment 링크와 승인된 구독 관리 주소 연결.
- Firestore의 최신 구독·발송 자격 확인과 작업별 발송 상태 연결.

현재는 news_card 로컬 검수 단계다. 운영 필수 링크가 아직 없으므로 운영 발송 준비 완료로 처리하지 않는다. no_news·subscription_end 유형은 후속 구현이다.
