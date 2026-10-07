# 로컬 뉴스 메일 조립·미리보기

실제 발송 파이프라인에서 쓰는 메일 조립 경로를 가상 fixture로 확인한다. 카드 PNG는 `frontend/card-template.js`와 `frontend/styles.css`에서 생성하며 기사 데이터·설명을 새로 만들거나 AI를 호출하지 않는다. 이 데모는 MIME만 조립하고 실제 DB·SMTP 전송은 하지 않는다.

## 실행과 출력

```powershell
.\.venv\Scripts\python.exe -m engine.card_render
.\.venv\Scripts\python.exe -m engine.demos.mail_demo
node engine/tools/render_mail_preview.cjs .engine-local/live-card/mail
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

테스트 SMTP 연결 코드는 `engine/smtp_test.py`에 준비했다. `engine/.env`에 `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY`(ssl/starttls), `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_TEST_TO`를 입력한다. 비밀번호는 채팅·Git에 올리지 않는다. 서비스별 앱 비밀번호/OAuth 지원은 발송 서비스가 정해진 뒤 확인한다. 현재 어댑터는 SMTP 사용자명·비밀번호 인증 방식이다.

```powershell
.\.venv\Scripts\python.exe -m engine.tools.smtp_test --check
.\.venv\Scripts\python.exe -m engine.tools.smtp_test --send
```

`--check`는 인증만 확인하고 메일을 보내지 않는다. `--send`는 설정한 테스트 수신자 한 곳으로 실제 메일을 제출한다. 아직 발송 계정·수신자·인증 정보가 없어 실제 접속·발송은 수행하지 않았다.

SSL 또는 STARTTLS와 인증서 검증을 사용하며 평문 전송은 지원하지 않는다. 동일 카드·발신자·수신자의 EML과 상태를 `.engine-local/smtp-test/`에 보관하고 다시 실행해도 성공 또는 불확실 상태는 재전송하지 않는다. 연결·인증 실패는 최대 2번까지만 시도할 수 있다. `smtp_accepted`는 서버 접수 확인이며 수신함 도착 확인이 아니다. `unknown`/`in_flight`이면 수신함·서버 결과를 확인하기 전 기록을 지우거나 재전송하지 않는다.

연결 코드 추가 후 전체 테스트 128개 통과. 지정 테스트 수신자 한 명, Bcc 금지, 성공 후 재전송 방지, 전송 중 연결 끊김의 불확실 처리, 인증 실패 시 미제출·횟수 제한을 대역 서버로 확인했다. 실제 서비스의 인증·도착·메일 앱 표시는 아직 미검증이다.

- 발송 계정과 테스트 수신자 설정.
- 실제 메일 앱에서 CID 이미지·이미지 차단·모바일 표시 검증.
- Backend가 제공하는 피드백 fragment 링크와 승인된 구독 관리 주소 연결.
- Firestore의 최신 구독·발송 자격 확인과 작업별 발송 상태 연결.

## 네 가지 메일 표본

`engine.demos.mail_types_demo`는 1장 뉴스, 2장 뉴스(배경 정보 포함), 뉴스 없음, 구독 종료 안내를 만듭니다. 날짜·출처·본문은 저장소의 `demo_only` fixture에서 오며 실제 발송 기사나 이미지 예시의 숫자를 사용하지 않습니다. 1장·2장 MIME에는 승인된 카드 이미지가 CID로 첨부되고, `images-blocked.html`은 이미지를 제거해도 설명·용어·출처가 읽히는지 확인합니다. `index.html`에서 화면과 캡처 링크를 엽니다.

기존 카드 데이터 계약에는 비교할 지표 쌍(대상·단위·비교 기간)이 없으므로 숫자 강조 상자는 출력하지 않습니다. `numbers` 근거는 기사 문장에만 남겨두며 집계·증가율을 임의로 만들지 않습니다.

확인 환경은 로컬 Chrome 기반 브라우저 렌더입니다. Gmail·Outlook 실제 계정 수신과 클라이언트별 CID 동작은 아직 확인하지 않았습니다. Firestore 및 운영 발송 설정을 연결하기 전이므로 이 미리보기 자체는 실제 발송을 의미하지 않습니다.
