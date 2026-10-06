# 박경연 — 핵심 엔진 개발

## 할 일과 첫 구현

담당 범위는 수집·기사 저장·중복 방지 → 관심 뉴스 선별 → 과거 기사 RAG → AI 카드 생성·근거 검사 → 김현서의 템플릿을 이용한 이미지 변환 → 메일 조립·발송 → 발송 상태·재실행·정리 연결입니다.

첫 구현은 **FR-10 관심 뉴스 선별**과 선별 단계의 **FR-09 URL 반복 제외**입니다. 가상 기사와 공통 10-1 형식의 샘플 구독으로 시작해 로컬 RSS·HTML 수집, 기사 저장·내용 버전, 과거 기사 임베딩·RAG, 실제 챗 API 발췌 카드 생성·로컬 기록을 추가했습니다. QA의 RSS 4개를 이용한 실제 수집·본문 추출·메모리 저장·선별 수동 확인도 추가했습니다. Firestore 실제 연결·실제 뉴스에서 카드 생성까지의 통합·이미지·메일 전송은 후속 범위입니다.

## 실제 RSS 수집·본문 확인

### 카드 이미지 → 로컬 메일 미리보기·EML

```powershell
.\.venv\Scripts\python.exe -m engine.card_render
.\.venv\Scripts\python.exe -m engine.demos.mail_demo
node engine/tools/render_mail_preview.cjs .engine-local/live-card/mail
```

검사 완료 카드와 승인된 PNG로 HTML·일반 텍스트·CID 이미지 첨부가 포함된 `.eml`을 만듭니다. `mail_assembly.py`는 MIME 조립만 담당하며 SMTP·챗 API·DB를 호출하지 않습니다. 발신자·수신자는 `example.invalid` 표본이고 실제 피드백·구독 관리 주소는 아직 연결하지 않습니다.

출력은 `.engine-local/live-card/mail/`의 `briefing.eml`, `preview.html`, `images-blocked.html`, `text-fallback.txt`, 보고서입니다. 현재 카드 데이터와 이미지 보고서의 hash가 다르거나 이미지 검사가 실패하면 해당 이미지를 붙이지 않고 전체 텍스트 설명을 유지합니다. 상세 구조와 확인 범위는 [메일 조립 안내](MAIL_PREVIEW.md)를 참고하세요.

2026-10-05 실제 생성 카드 1의 메일은 PNG 1개, MIME 총 152,661 bytes로 생성됐습니다. Chrome의 800px/390px 폭에서 정상/이미지 차단 미리보기 4개를 확인했고 가로 넘침·외부 요청은 없었습니다. 전체 자동 테스트 124개 통과. 실제 Outlook/Gmail의 CID 표시·이미지 차단 동작은 발송 설정 후 별도 확인해야 합니다.

### 생성 카드 → 임시 템플릿 → 이미지

최초 구현 당시 프런트 담당자의 템플릿이 없어 `engine/templates/card_preview.html`에 엔진 검수용 임시 템플릿을 추가했습니다. 최종 프런트 디자인으로 확정한 것이 아닙니다. `card_render.py`는 완료된 생성 작업의 `result.json`을 받아 카드 1·선택적인 카드 2를 PNG로 변환합니다. 상세 설정과 검수 기록은 [카드 이미지 연결 안내](CARD_RENDER.md)를 참고하세요.

```powershell
npm ci --prefix engine
.\.venv\Scripts\python.exe -m engine.card_render
```

Node·Playwright와 Chrome/Edge 또는 Playwright Chromium, 로컬 한글 폰트가 필요합니다. 기본 입력은 `.engine-local/live-card/result.json`, 출력은 `.engine-local/live-card/render/`의 HTML·PNG·렌더링 보고서입니다. 생성형 이미지 API는 사용하지 않습니다. 현재 PC에서는 이미 제공된 Playwright와 Chrome, Noto Sans KR을 사용해 별도 설치 없이 확인했습니다.

2026-10-05 KST 실제 카드 1은 1080×1109, PNG 107,061 bytes로 생성됐습니다. 제목 60자·설명 400자·용어 2개/풀이 각 100자의 최대 입력, 긴 영문, 카드 2의 기준일/근거 보도일 표본도 이미지로 확인했습니다. 글자 잘림·영역 넘침·외부 요청은 없었고 로컬 폰트가 정상 로드됐습니다. 글씨를 줄이거나 설명을 자르는 대신 카드 높이를 늘립니다. 전체 자동 테스트 118개가 통과했습니다. 실제 모바일·메일 표시 검증은 후속 단계입니다.

### 실제 SBS 기사 → 카드 1 생성

```powershell
.\.venv\Scripts\python.exe -m engine.demos.live_generate_demo --live
```

SBS 정치 피드 앞 2건 중 수집 시각 이전 24시간의 최신 기사를 선택해 기존 챗 API·카드 검사에 전달합니다. 과거 근거가 없으므로 임베딩 모델을 로드하지 않고 카드 2를 생략합니다. Firestore·이미지·메일은 연결하지 않습니다.

입력은 `.engine-local/live-card/input.json`, 생성 결과는 `result.json`, 카드 데이터는 `card.json`, 읽기용 미리보기는 `preview.md`에 저장합니다. 모두 Git에서 제외됩니다. `--live` 없이 실행하면 입력 준비만 하고 챗 API를 호출하지 않습니다. 기본적으로 저장된 입력을 다시 사용하며 완료된 같은 생성 작업은 API 없이 결과를 재검사·재사용합니다. 새로운 뉴스를 수집하려면 `--refresh-news`를 추가합니다. 새 기사·내용은 별도 생성 작업이 되어 크레딧을 사용할 수 있습니다. 실패한 동일 작업의 API 시도는 기존 한도 2회를 유지합니다.

수치가 `surface: "53년", unit: "년"`처럼 중복된 형식이면 원문에 같은 표기가 있는 경우만 숫자와 단위를 분리합니다. 수치의 근거가 검증된 문장 근거의 일부이면 전체 문장 근거로 확장합니다. 사실 문구·수치·단위·대상을 추측해서 바꾸지 않습니다. 그 뒤 기존 검사에서 수치 근거가 맞지 않는 문장은 제외하고, 남은 카드 전체를 다시 검사합니다. 보정·제외 내역과 원래 오류는 생성 기록에 남습니다. 응답 기록은 현재 입력 메시지 hash가 맞을 때만 재검사에 사용합니다. 문장 제외로 설명 범위가 줄어들 수 있어 사람 검수가 필요합니다.

2026-10-05 KST 실제 SBS 병역특례 기사로 API 2회 시도 후 저장된 응답을 추가 호출 없이 형식 보정·재검사했습니다. 2문장·202자의 카드 1이 `ready_for_review`로 저장됐고 수치 근거 불일치 문장 1개가 제외됐습니다. 재실행은 `reused: true`, `chat_api_called: false`였습니다. 텍스트는 자유 요약이 아닌 원문 발췌이며 원문의 사실성이나 설명 완전성을 자동 검사로 보장하지 않습니다. 전체 자동 테스트 111개가 통과했습니다.

### 수집·저장·선별만 확인

저장소 루트에서 실행합니다. 인터넷에 접속하며 기본적으로 소스별 앞 3건을 검사합니다.

```powershell
.\.venv\Scripts\python.exe -m engine.demos.live_collect_demo --max-per-source 2
.\.venv\Scripts\python.exe -m engine.demos.live_collect_demo --category it_science --keyword AI
```

QA의 `deployment-qa/rss_validator/sources.py`에서 주소를 직접 읽습니다. `live_collection.py`는 RSS → 기사 URL → 본문 추출 → 명시된 게시 시각/분야 검사를 거쳐 기존 `Article` 형식으로 반환합니다. 소스·기사별 실패는 기록하고 다음 항목을 계속 처리합니다. RSS 요약을 본문으로 대신 쓰거나 HTTP 오류를 우회하지 않습니다. 소스 도메인 밖의 URL·리디렉션과 3MB 초과 응답을 거부합니다. 요청 timeout은 12초, 소스당 검사 수는 1~20건입니다.

`live_collect_demo.py`는 기존 메모리 저장소에 저장·재저장하고 현재 시각 이전 24시간 기사 중 관심 분야·키워드로 선별합니다. 구독·발송 이력은 운영 Backend에서 받은 것이 아닌 수동 확인용 값입니다. Firestore·챗 API·메일 호출은 없으며 메모리 데이터는 종료 후 사라집니다. 보고서는 Git에서 제외되는 `.engine-local/live_collection_report.json`에 저장하고 기사 본문은 포함하지 않습니다.

BBC는 `world`, SBS는 `politics`, 경향신문은 `it_science`로 매핑합니다. 매일경제 전체 피드는 명확한 RSS 분야 또는 `/news/politics/숫자`, `/news/it/숫자` 등 언론사가 지정한 URL 분야를 사용합니다. 분야 누락·충돌은 제외합니다. 이 매핑은 공개 카테고리 승인과 별개인 엔진 구현 제안입니다. BBC 영어 원문은 번역하지 않고 언어 메타데이터를 유지합니다.

2026-10-05 KST 소스별 앞 2건 검사에서 BBC·SBS·매일경제 각 2건, 총 6건이 본문·게시 시각 검사를 통과했습니다. 재저장 6건은 모두 `unchanged`, 정치 기사 선별은 `selected`였습니다. 경향신문 2건은 본문 추출은 성공했지만 RSS의 `published` 누락으로 제외했습니다. 첫 항목의 `updated`는 2025-03-18이며 게시 시각으로 대신 사용하지 않습니다. 소스 전체의 운영 사용 가능성을 확정한 결과는 아닙니다. 상세 결과와 QA 전달 내용은 [실제 수집 확인 기록](LIVE_RSS_CHECK.md)을 참고하세요.

자동 테스트: 기존 기능을 포함해 106개 통과. 날짜 누락·시간대 없는 날짜·매경의 `+09:00` 날짜 형식·전체 피드 분류·본문 403·중복 기사·정상 빈 피드·잘못된 피드·소스 외부 URL을 검사합니다.

## 여섯 번째 기능: 실제 코디세이 챗 연결·생성 작업 기록

현재 PC에서는 `engine/.env`의 기존 주소와 `gpt-5.5`로 실제 응답을 받았습니다. 기사 자체는 계속 가상 샘플이며, 이미지·메일·Firestore에는 연결하지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m engine.demos.generate_demo --live
```

현재 확인한 작업은 `status: completed`, `attempts: 2`, `result.status: ready_for_review`입니다. 이미 생성한 같은 작업을 실행하면 `reused: true`, `chat_api_called: false`로 저장된 결과를 다시 검사합니다. `completed`는 생성·자동 검사 완료이며 사람 검수·발송 완료가 아닙니다. 실제 응답의 용어 풀이가 ‘정하므로’에서 끊겨 이를 생략하도록 수정했습니다. 현재 결과는 `card1.terms: []`, `issues: ["TERM_OMITTED:INCOMPLETE_DEFINITION"]`이며 원문에 없는 정의로 대체하지 않습니다.

접속형 끝맺음(하므로/이므로/으므로/하지만/했지만/하며/으며/하는데/했는데/하고/해서) 검사는 **품질 제안**이며 용어 정의의 의미를 완전히 판별하지 않습니다. 타입·길이·근거 검사에 통과한 용어 중 이 패턴에 해당하는 선택적 풀이만 생략합니다. 그 밖의 잘못된 필드·근거는 기존대로 거부합니다. 기존 결과를 재검사할 때 생성 키와 호출 횟수는 유지하고, 출력 검사 버전은 `validation_version`으로 별도 기록합니다. 기존 생략 기록도 유지합니다. 재검사에서 카드 1이 실패하면 기록을 blocked로 바꾸고 API를 다시 호출하지 않습니다.

`--live`가 없으면 저장된 샘플 응답만 쓰는 별도 로컬 작업이며 비용이 들지 않습니다. 다른 PC나 기사·근거·모델·프롬프트 버전이 다른 작업에서 `--live`를 실행하면 실제 요청과 크레딧 사용이 발생할 수 있습니다. 잔여 크레딧·실제 차감 금액은 이번에 확인하지 않았습니다.

`generation.py`는 **단일 PC 개발용 제안**으로 `.engine-local/generation` 파일에 작업 키·호출 횟수·최종 상태·검사 결과를 저장합니다. 이 폴더와 `.env`는 Git에서 제외합니다. 서비스 DB는 계속 Cloud Firestore이며 이 파일 기록은 운영 DB를 대체하지 않습니다. Actions의 임시 체크아웃이나 다른 PC에서는 기록이 공유되지 않으므로 운영 발송에 사용할 수 없습니다. 운영 전에 Firestore의 원자적 선점·생성 횟수 기록으로 연결해야 합니다.

기사/근거 버전·hash·모델·주소·프롬프트·검사기·스키마·작업 ID로 생성 키를 만듭니다. 요청 전에 횟수를 영속 기록하며 같은 생성 작업은 총 2회까지만 허용합니다. SDK 숨은 재시도는 없고 한 실행에서 요청은 최대 1회입니다. 명확한 일부 HTTP 일시 오류·잘못된 응답·카드 1 검사 실패는 남은 횟수 안에서 다음 실행에 재시도할 수 있습니다. 타임아웃·네트워크 불확실·중단 상태는 자동 재호출을 막습니다.

프로세스 잠금으로 로컬 동시 실행을 막고 기록은 임시 파일 fsync 후 원자적으로 교체합니다. 강제 종료로 잠금 파일이 남거나 `in_flight`이면 자동 선점하지 않습니다. 횟수 기록이 손상됐을 때 0회로 복구하지 않습니다. 잠금·작업 파일을 지워 한도를 초기화해서는 안 됩니다. 수동 복구·운영 장애 처리는 후속 범위입니다.

`chat_client.py`는 키를 자식 프로세스 stdin으로 전달합니다. `chat_worker.py`는 HTTPS 요청·응답 최대 2MB·리다이렉트 거부를 사용하고, 부모가 전체 요청 프로세스를 60초에 중단합니다. 자식의 HTTP 소켓 제한은 55초입니다. 공급자의 오류 원문·stderr·헤더·키는 저장하거나 출력하지 않으며 허용한 오류 코드·필드 힌트만 기록합니다. 응답은 stop 종료·거절 없음·단일 JSON 객체를 확인하고 중복 키·NaN·코드펜스·잘린 JSON을 거부합니다.

실제 확인: 공식 형식의 `response_format: json_object`와 `max_completion_tokens`를 함께 넣은 첫 요청은 HTTP 400이었습니다. 같은 작업의 남은 1회를 명시적으로 사용해 `max_tokens: 3000`·일반 텍스트 JSON 요청으로 변경하자 정상 생성됐습니다. 두 필드를 함께 변경했으므로 어느 한 필드가 원인인지는 확정하지 않습니다. 현재 기본은 성공한 중계 호환 형식이며 출력 JSON·근거 검사는 그대로 적용합니다. 3000 출력 토큰·2MB 응답 크기는 **검증용 제안값**이고 운영 비용 상한은 별도입니다. [공식 Chat Completions 규격](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)과 중계 서버 지원 범위가 같다고 가정하지 않습니다.

`--json-mode`는 공식 형식의 실험용 옵션입니다. `--retry-blocked`는 HTTP 400 수정 후 같은 작업의 남은 횟수만 수동 사용하며 횟수를 초기화하지 않습니다. 타임아웃·인증 오류·중단에는 이 옵션으로 재호출하지 않습니다. 현재 완료된 시연 작업에는 두 옵션 모두 필요 없습니다.

확인 결과: 자동 테스트 96개 통과. 재시작 결과 재사용·동시 호출·2회 제한·호출 전 디스크 기록·중단/타임아웃 차단·HTTP 400 수동 복구·키 비노출·잘린/거절/중복 키 응답 거부를 확인했습니다. 불완전한 용어 풀이 생략·완전한 원문 정의 유지·기존 결과 재검사·잘못된 캐시 차단도 확인했습니다. 실제 생성 2회(400 1회 + 성공 1회) 후 동일 명령 재실행에서 생성 키·2회 기록을 유지하고 추가 API 요청 없이 용어 풀이를 생략한 결과를 확인했습니다.

## 다섯 번째 기능: 카드 설명 조립·자동 검사 (로컬 샘플)

현재 기사와 실제 로컬 RAG 검색 결과를 연결하고, 저장된 가상 챗 응답을 공통 10-2 카드 JSON으로 검사·조립합니다. 아직 코디세이 API를 호출하거나 새로운 AI 설명을 생성하지 않습니다. `response_source: local_fixture`로 구분합니다. 공통 샘플 카드의 `ai_generated: true`는 향후 템플릿 연결을 위한 표시이며 이 시연에서 AI 호출이 있었다는 뜻이 아닙니다.

```powershell
.\.venv\Scripts\python.exe -m engine.demos.card_demo
.\.venv\Scripts\python.exe -m engine.demos.card_demo --no-past
.\.venv\Scripts\python.exe -m engine.demos.card_demo --invalid-background
```

현재 PC에서는 추가 설치 없이 실행합니다. 다른 PC는 위 RAG 패키지·모델 준비 명령이 필요합니다. 첫 명령은 `status: ready_for_review`와 카드 2장, 두 번째는 카드 1만 있고 `card2: null`, 세 번째는 잘못된 배경 설명을 제외하고 카드 1만 남습니다. 세 번째의 `CARD2_OMITTED:...`는 의도적인 실패 표본입니다. `human_review_required: true`이며 실제 이미지·메일·DB 저장은 하지 않습니다.

`cards.py`는 제목 60자, 카드별 설명 합계 400자, 카드 1 용어 최대 2개·각 풀이 100자를 검사합니다. 길이는 공통 P-21 초안의 NFKC 정규화 후 code point 수를 따르며 초과 문장을 임의로 자르지 않습니다. 카드 1 검사가 실패하면 `failed`와 `card_data: null`입니다. 카드 2 실패는 이유를 기록하고 생략합니다.

출처 ID·원문 구절·현재/과거 역할·내용 hash·과거 게시 시각을 검사합니다. URL·출처명·제목·게시일은 엔진이 가진 기사와 소스 메타데이터에서 채웁니다. 샘플 출처명도 가상 데이터입니다. LLM이 출처 URL을 만들어 전달하는 필드는 허용하지 않습니다. 사실 기준일 `as_of`는 YYYY-MM-DD가 근거 구절에 명시된 경우만 허용하는 **보수적 제안**이며 확인되지 않으면 null입니다. null인 과거 문장은 템플릿에서 근거 보도일을 표시해야 합니다.

이번 검사 방식은 **원문 연속 발췌만 허용하는 제안**입니다. 자유 요약은 `PARAPHRASE_REQUIRES_SEMANTIC_REVIEW`로 거부합니다. 문자 일치만으로 수치·대상 관계, 사건 시점, 사실성·배경 유용성을 완전히 검증할 수 없으므로 통과 상태를 발송 가능으로 사용하면 안 됩니다. 사람의 검수 기록·자유 요약 의미 검사·실제 뉴스 표본 평가는 후속 범위입니다.

수치 메타데이터는 문자열 surface, unit, subject, as_of, source_article_id, evidence_quote를 사용합니다. 서양식 숫자·부호·소수·천 단위 쉼표와 인접 단위(%/%p/억원/만원/만명/억명/명/원/개/건/회/배/년/월/일/시간/분/초)를 검사하는 **지원 타입 제안**입니다. 대상·단위·수치가 같은 원문 구절에 있는지 확인하지만 대상과 숫자의 의미 관계까지 증명하지 않습니다. 한국어로 풀어 쓴 숫자·복잡한 표·범위·환산은 별도 지원·검수가 필요합니다. 용어 풀이의 수치는 이번 단계에서 거부합니다.

`card_prompt.py`는 후속 연결용 system/user 메시지를 만들며 기사 본문을 외부 데이터로 분리합니다. 프롬프트 분리만으로 모든 지시 주입을 막는다고 주장하지 않습니다. 현재는 실제 생성 호출이 0회이므로 자동 재시도도 없습니다. 실제 API 연결 때 P-13의 영속적인 작업별 총 2회 제한, P-14의 60초 제한을 먼저 구현해야 합니다. 이번 로컬 검사로 FR-12 전체가 완료된 것은 아닙니다.

확인 결과: 자동 테스트 75개 통과. 기존 59개에 카드 검사 16개를 추가했고 로컬 RAG+카드 시연 3가지(2장/과거 없음/잘못된 배경)를 확인했습니다. 수신자·선택 이유·피드백 토큰은 카드 데이터에 넣지 않습니다.

## 네 번째 기능: 로컬 과거 기사 임베딩·RAG

현재 PC에는 CPU 패키지와 고정 버전 모델 다운로드를 완료했습니다. API 키 없이 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m engine.demos.rag_demo
.\.venv\Scripts\python.exe -m engine.demos.rag_demo --no-past
```

첫 명령은 `status: ready`, `usable_article_ids`에 `past_bank_cost`, `past_policy_rate` 2개가 나옵니다. 무관한 공연 기사는 유사도 제안 기준을 넘지 못해 사용하지 않습니다. 두 번째 명령은 `status: no_evidence`, `background_candidates_available: false`입니다. 근거 없음은 정상 결과이며 후속 생성기는 이때 과거 배경 카드 2를 만들면 안 됩니다. `embedding_failed`는 근거 없음과 구분되는 오류이며 시연은 종료 코드 1을 반환합니다.

다른 PC의 최초 준비는 다음과 같습니다. 패키지·모델 설치에 인터넷과 디스크 공간이 필요합니다. 이후 기본 시연은 캐시만 읽습니다. 모델 캐시는 Git에서 제외된 `.venv/model-cache`에 저장합니다.

```powershell
.\.venv\Scripts\python.exe -m pip install -r engine/dependencies/requirements-rag.txt
.\.venv\Scripts\python.exe -m engine.demos.rag_demo --download-model
```

`embeddings.py`는 공통 PRD의 `intfloat/multilingual-e5-small`, 384차원, 정규화·cosine 규격을 사용합니다. 공개 모델 revision `614241f622f53c4eeff9890bdc4f31cfecc418b3`을 고정하고 CPU에서 실행합니다. 질의에는 `query: `, 기사에는 `passage: ` 접두어를 붙이고 제목+본문 앞부분을 최대 512 토큰으로 처리합니다. [모델 공식 설명](https://huggingface.co/intfloat/multilingual-e5-small)

`rag.py`는 작업일 KST 자정 **이전** 기사만 검색하고 현재 기사 URL·ID를 제외합니다. 최대 5개 검색하고 최대 3개를 근거로 제공합니다. 내용 hash·버전·모델 revision이 일치하는 정상 임베딩만 사용합니다. 원문·URL·게시 시각·내용 버전이 포함된 `evidence_context`는 후속 카드 생성용 입력이며 아직 외부 API 계약이 아닙니다. 게시 시각을 사건 발생일로 해석하지 않습니다.

유사도 최소 `0.85`와 동일 분야 제한은 **합의 전 제안**입니다. `--min-score`로 시험할 수 있습니다. 이번 가상 샘플에서만 관련성 구분을 확인했으며 운영 뉴스의 품질을 보장하는 기준이 아닙니다. 유사도는 사실 정확도의 확률이 아닙니다.

기사와 임베딩은 현재 실행 중 메모리에서 연결합니다. Firestore의 임베딩 저장·검색 연결, 실제 기사 품질 검증, 챗 API·카드 생성·발송은 후속 범위입니다. `StoredArticle.embedding_status`는 영구 저장 상태이므로 이 시연의 별도 `ArticleEmbedding.status`와 구분합니다. 데모는 Firestore 필드를 변경하지 않습니다.

확인 결과: 자동 테스트 59개 통과. 실제 로컬 모델 검색에서 관련 기사 2개 사용, 무관 기사 제외, KST 자정·미래 기사 제외, 과거 기사 없는 실행의 빈 근거를 확인했습니다. 자동 테스트는 버전·hash·모델 불일치, 잘못된 벡터, 일부 임베딩 실패, 검색 5개·사용 3개 제한을 추가 검증합니다. 정책 테스트의 벡터는 테스트 대역이고 시연은 실제 모델을 사용합니다.

## 세 번째 기능: 기사 저장·URL 중복 방지·내용 버전

DB는 사용자의 최신 결정에 따라 **Cloud Firestore**로 개발합니다. [DB 변경 기록](firebase/DB_CHANGE.md)에 공통 PRD와의 차이를 기록했습니다. 공통 원문·구독·발송 정책은 변경하지 않았습니다.

```powershell
.\.venv\Scripts\python.exe -m engine.demos.store_demo
```

현재 시연은 메모리 샘플 저장소를 사용합니다. 최초 2개 저장 → 동일 기사 재저장 2개 unchanged → 본문 수정 시 버전 2 → 이전 본문 보존 → 오래된 관측은 stale → 저장 기사에서 선별까지 확인합니다. 프로그램 종료 후에는 데이터가 사라집니다. Firestore 프로젝트에 아직 연결하지 않았습니다.

실제 연결용 Firestore 어댑터도 작성했고 당시 총 48개 테스트가 통과했습니다. 새 환경에서는 `python -m pip install -r engine/dependencies/requirements.txt`로 SDK를 포함한 의존성을 설치합니다. [Firestore 문서 구조·어댑터 안내](firebase/README.md)에 기술 구조를 설명했습니다. FR-08 중 저장·내용 버전 부분이며 Firestore 임베딩 저장 연결은 후속 범위입니다.

## 두 번째 기능: 로컬 RSS 수집 → 선별

현재 이 PC에는 프로젝트 전용 `.venv`를 만들고 필요한 패키지를 설치했습니다. 터미널에서 활성화하지 않고 다음 명령으로 실행할 수 있습니다.

```powershell
.\.venv\Scripts\python.exe -m engine.demos.collect_demo
.\.venv\Scripts\python.exe -m unittest discover -s engine/tests -v
```

예상 결과는 `collected_count: 2`, `collection_status: partial`, `selection_status: selected`, `matched_keyword: 금리`, `mail_sent: false`입니다. `partial`과 `collection_succeeded: false`는 샘플에 일부러 넣은 날짜 누락 기사가 제외됐다는 뜻입니다. 정상 기사 2건은 계속 선별됩니다. 모든 기사 수집이 실패했을 때는 `collection_failed`와 종료 코드 1이 나옵니다. 정상 빈 RSS는 오류와 구분합니다.

`samples/collection/`의 RSS·HTML·sources.json을 직접 읽습니다. 기사 URL로 접속하거나 외부 서버·DB·챗 API에 요청하지 않습니다. 가상 URL이므로 실제 뉴스나 실제 소스 검증 결과로 사용하면 안 됩니다. API 키 없이 실행할 수 있습니다.

다른 PC에서는 Python 3.10 이상을 준비하고 저장소 루트에서 실행합니다.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r engine/dependencies/requirements.txt
.\.venv\Scripts\python.exe -m engine.demos.collect_demo
```

requirements-lock.txt에는 이번에 검증한 하위 의존성까지 버전을 고정했습니다. 설치에는 인터넷이 필요하지만 샘플 시연에는 인터넷이 필요 없습니다.

## 챗 API 설정 — 키를 입력할 곳

로컬 파일은 `engine/.env`이며 Git에서 제외됩니다. 팀 공유용 `engine/.env.example`에는 빈 설정만 있습니다. 팀원은 예시를 자기 `.env`로 복사합니다. 이 설정은 엔진 전용이며 프런트엔드에 넣지 않습니다.

```dotenv
OPENAI_API_KEY=
OPENAI_BASE_URL=
OPENAI_MODEL=gpt-5.5
```

- CHAT API 키를 첫 번째 항목에 넣습니다. IMAGE API 키는 필요 없습니다.
- 코디세이 안내에 있는 실제 API 기본 주소를 두 번째 항목에 넣습니다. 공개 OpenAI 주소로 임의 대체하지 않습니다.
- 모델은 사용자가 선택한 `gpt-5.5`로 설정했습니다. 제공 측의 실제 모델 ID·요청 방식·구조화 출력 지원·크레딧 차감·60초 제한 내 응답은 아직 검증하지 않았습니다.

카드는 **챗 API로 근거 있는 설명 생성 → 김현서의 HTML/CSS 템플릿 → 박경연의 Playwright/Chromium 이미지 변환** 순서입니다. 생성형 이미지 API로 카드 전체를 그리는 방식은 공통 PRD에서 보류되어 있습니다. 공식 OpenAI 문서에서 GPT-5.5의 텍스트 출력·구조화 출력 지원을 확인했지만, 코디세이 중계 API의 지원 여부·요금은 별도로 확인해야 합니다. [공식 GPT-5.5 문서](https://developers.openai.com/api/docs/models/gpt-5.5)

settings.py는 설정 로더입니다. 실제 호출은 `generate_demo --live`에서만 연결하며 샘플 수집에서는 `.env`를 읽지 않습니다. 배포 환경의 Secrets를 로컬 파일보다 우선하며 설정 오류에 키를 출력하지 않습니다.

환경 변수 이름은 사용자가 입력한 `OPENAI_*`에 맞췄습니다. 이름이 OPENAI여도 실제 제공자는 `OPENAI_BASE_URL`로 지정한 코디세이입니다. 사용자 `.env`의 키는 수정하거나 안내 문서에 복사하지 않습니다.

## 전달 방식 — 먼저 구현한 제안

현재는 추가 서버 없이 엔진 내부 Python 함수로 객체를 전달합니다. gateway.py의 `EngineGateway`는 연결 경계이고 `SampleEngineGateway`는 가상 응답만 제공합니다. 실제 Backend를 대신 구현하지 않습니다.

- `get_subscription_snapshot(subscription_id, scheduled_date_kst) → 공통 10-1 dict`
- `check_delivery_eligibility(subscription_id, now) → bool`

함수명은 공통 10-3을 따르고 인자·반환 타입은 **합의 전 제안**입니다. 실제 연결은 보호된 API 또는 DB 접근 어댑터를 사용하는 설계입니다. 현재 샘플 함수는 권한·해제·삭제 여부를 실제 확인하지 않으므로 운영 발송에 사용할 수 없습니다. 피드백 토큰은 이번 범위에 포함하지 않았습니다.

운영 연결에서도 조회 실패를 빈 구독·발송 가능 true로 바꾸지 않고 오류로 처리해야 합니다. 무료 계정의 한도·접근 권한은 실제 배포 때 확인합니다. 이 로컬 함수 연결에는 별도 서버·서비스 비용이 발생하지 않습니다.

수집의 로컬 개발용 제안값은 본문 최소 80자, RSS/HTML 각각 최대 1MB, 소스당 최대 100개 항목입니다. 실제 운영값은 공통 P-22에 따라 실제 표본으로 검증·합의해야 합니다. 날짜는 RSS 게시 시각의 timezone을 확인해 UTC로 바꾸고, 누락된 날짜를 현재 시각이나 수정 시각으로 대체하지 않습니다.

## 실행·확인 (PowerShell)

저장소 루트에서 Python 3.10 이상으로 실행합니다.

```powershell
cd C:\Users\user\Desktop\ai-news-card
py -3 -m engine.demos.demo
py -3 -m engine.demos.demo --no-keywords
py -3 -m unittest discover -s engine/tests -v
```

첫 명령은 `fixture_keyword`와 `type: keyword`, 두 번째는 `fixture_latest`와 `type: category`가 나와야 합니다. 더 최근의 `fixture_already_sent`는 7일 이력에 있어 제외됩니다. 출력의 `demo_only: true`는 실제 뉴스·실제 발송이 아니라는 표시입니다.

`py`에 Python이 등록되어 있지 않다면 Codex의 현재 제공 런타임으로도 실행할 수 있습니다. 아래 경로는 이 PC에서만 확인한 경로이며 팀원은 자기 Python을 사용합니다.

```powershell
& 'C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m engine.demos.demo
& 'C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m engine.demos.demo --no-keywords
& 'C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest discover -s engine/tests -v
```

다른 샘플로 시험하려면 `python -m engine.demos.demo --sample engine/samples/selection.json` 형태로 파일 경로를 지정합니다. 샘플은 가상 데이터만 넣고, 실제 이메일·토큰·비밀값은 저장하지 않습니다.

## 선별 동작

1. 샘플 구독이 active이고 `scheduled_at <= now < deadline_at`인지 확인합니다.
2. 검증 소스·유효 본문 표시가 있고, 관심 분야에 속하며 `예정 시각 - 24시간 <= 게시 시각 < 예정 시각`인 기사만 남깁니다. 재시도 시에도 예정 시각을 기준으로 합니다.
3. 최근 7일의 sent·unknown 이력에 있는 동일 URL을 제외합니다. 같은 날 재실행 중 이미 전송된 이력도 제외합니다.
4. 후보 안의 동일 URL을 하나로 정리합니다. 사건 묶기는 미구현이므로 기사별 언론사 수는 1로 취급하고 최신 게시 시각, ID 오름차순으로 정렬합니다.
5. 제목·본문에서 키워드 하나라도 매칭되는 기사를 우선합니다. 키워드가 없거나 매칭되지 않으면 관심 분야의 최신 기사로 대체합니다.
6. 후보가 없으면 `no_candidates`를 반환합니다. 수집 실패 상태에 후보도 없으면 오류로 멈춥니다. 일부 수집이 실패해도 기존 정상 후보는 사용할 수 있습니다.

`no_candidates`는 메일 발송 완료 상태가 아닙니다. 정상 수집 후 뉴스 없음 하루 1회 안내는 후속 발송 작업에서 구현합니다. `ineligible` 역시 선별 결과이며 발송 DB 상태를 바꾸지 않습니다.

## 연결 규격 — 팀 합의 전 제안

공통 PRD 10절은 합의 전 초안입니다. 샘플의 `contract_version: 1.0`은 공통 문서의 예시와 맞춘 값이며 팀이 계약을 확정했다는 뜻이 아닙니다. 이 단계의 추가 규격은 `engine/` 안에 있는 개발용 제안입니다.

| 입력·출력 | 현재 규격 |
|---|---|
| 구독 입력 | 공통 10-1의 전체 `subscription_snapshot`; 선별은 status/timezone/scheduled_at/deadline_at/categories/keywords 사용 |
| 기사 입력 (제안) | `Article`: article_id, url, title, body, category, published_at, source_verified, body_valid |
| 이력 입력 (제안) | `DeliveryHistory`: url, status, attempted_at. 해당 사용자의 모든 구독에 걸친 최근 7일 sent/unknown 포함 |
| 출력 (제안) | selected/no_candidates/ineligible, 선택된 Article 또는 null, selection_reason 또는 null |
| 선택 이유 | 공통 10-6의 type/label/matched_keyword 또는 category |

Python 호출은 `select_article(snapshot, articles, history, now=..., collection_succeeded=...)`입니다. 시간은 timezone-aware datetime, JSON에서는 UTC 또는 offset이 있는 ISO 8601입니다. `history`는 필수이며 DB 조회 실패를 빈 목록으로 바꾸면 안 됩니다. 입력 오류는 예외로 전달하고 성공으로 표시하지 않습니다.

현재 구현의 세부 규칙 — 합의 전 제안:

- 키워드는 관심 분야 안에서만 우선하고, NFKC·대소문자 정리 후 제목+본문 부분 문자열로 OR 매칭합니다. 키워드 개수로 점수를 더하지 않습니다. 매칭 이유는 입력 순서의 첫 일치 키워드입니다. 최종 키워드 정리는 Backend 담당이며 엔진은 저장된 값을 받습니다.
- 24시간의 하한은 포함하고 예정 시각과 동일한 게시 시각은 제외합니다. 7일 이력은 하한을 포함하며 attempted_at은 sent/unknown 발생 시각으로 연결합니다.
- URL은 scheme/host 대소문자·기본 포트·fragment만 정리합니다. 경로·query·끝 슬래시와 HTTP/HTTPS 구분은 보존합니다. 추적 query 제거 규칙은 실제 소스 확인 후 합의합니다. 이 구현은 중복 저장 방지를 위한 DB 유일성 제약의 대체가 아닙니다.
- `source_verified`·`body_valid`는 가상 샘플의 선별 관문입니다. 실제 수집기에서 QA 소스 검증 결과와 본문 품질 검사 결과로 채워야 하며 기본 true로 생성하지 않습니다. 본문 최소 길이 등 품질 기준은 아직 미정입니다.
- `collection_succeeded`는 정상 수집 여부입니다. false여도 기존 정상 후보가 있으면 선택할 수 있지만, 정상 후보가 없으면 `CollectionUnavailable`을 발생시킵니다. 상세 소스별 상태는 후속 수집 규격에서 제안합니다.

실제 환경에서는 Backend가 확정한 날짜별 스냅샷을 공급하고, 엔진이 SMTP 직전에 `check_delivery_eligibility`로 최신 해제·만료·삭제 요청을 별도 확인해야 합니다. 이 샘플의 상태 검사만으로 실제 발송을 허용하지 않습니다.

## 다음 작은 기능

로컬 RSS 수집·샘플 저장·선별·과거 기사 RAG·실제 챗 API·로컬 생성 기록은 연결했습니다. 다음은 용어 설명·카드 중복 등 실제 생성 품질 개선과 검수 상태 연결입니다. Firestore 실제 연결·권한·보관 정책은 미검증입니다. 검증된 소스 목록 기반의 실제 HTTP 수집 어댑터도 후속 개발 범위입니다.

## 두 번째 기능 확인 결과 — 2026-10-05 KST

- Windows PowerShell·프로젝트 Python 가상환경에서 총 29개 테스트 통과 (기존 17개 + 수집/샘플 연결 9개 + 설정 3개).
- 외부 연결을 차단한 수집 테스트 통과. 정상 빈 RSS, 잘못된 RSS, 날짜 누락, 본문 누락, 미검증 fixture, 경로 이탈을 확인했습니다.
- 수집 시연: 중복 URL 제외 후 2개 기사 추출, 날짜 누락 1개 기록, 금리 키워드 기사 선택.
- engine/.env와 .venv의 Git 제외 및 engine/.env.example의 공유 가능 상태 확인.
- 이번 변경은 engine/ 안의 로컬 수집·샘플 연결·설정·테스트·안내입니다. 공통 PRD와 다른 담당자의 코드는 수정하지 않았습니다. 실제 API·DB·메일 연결은 아직 검증하지 않았습니다.

## 첫 번째 기능 당시 확인 결과 — 2026-10-05 KST

- 환경: Windows PowerShell, Codex 제공 Python. 이 PC의 `py -3`에는 Python이 등록되어 있지 않아 위 제공 런타임 경로로 실행했습니다.
- 자동 테스트: 17개 통과. 키워드 OR·분야 대체·최신/ID 동점·24시간 경계·7일 sent/unknown·재실행 이력·수집 실패 구분·잘못된 기사 제외·발송 기한을 확인했습니다.
- 시연: 기본은 `fixture_keyword`, `--no-keywords`는 `fixture_latest` 선택. UTF-8 한글 출력도 확인했습니다.
- 추가 파일: 엔진 패키지 초기화, selection.py, demo.py, 가상 샘플 JSON, 테스트, 실행/규격 안내, 엔진 전용 .gitignore. 공통 PRD와 다른 담당자의 폴더는 수정하지 않았습니다.
- 외부 RSS·DB·AI·SMTP·배포 검증은 수행하지 않았으며 실제 발송 안전성 전체가 완료된 상태는 아닙니다. 커밋은 생성하지 않았습니다.
