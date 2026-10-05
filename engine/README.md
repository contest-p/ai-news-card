# 박경연 — 핵심 엔진 개발

## 할 일과 첫 구현

담당 범위는 수집·기사 저장·중복 방지 → 관심 뉴스 선별 → 과거 기사 RAG → AI 카드 생성·근거 검사 → 김현서의 템플릿을 이용한 이미지 변환 → 메일 조립·발송 → 발송 상태·재실행·정리 연결입니다.

첫 구현은 **FR-10 관심 뉴스 선별**과 선별 단계의 **FR-09 URL 반복 제외**입니다. 검증된 RSS 목록과 DB가 아직 없어 가상 기사와 공통 10-1 형식의 샘플 구독으로 시작합니다. 첫 선별 시연은 Python 표준 라이브러리만 사용하여 추가 패키지·API 키 없이 실행합니다. 다음 단계로 feedparser·trafilatura를 사용하는 로컬 RSS·HTML 수집 시연을 추가했습니다. 외부 뉴스 수집·DB 중복 저장·RAG·AI·메일 전송은 아직 구현하지 않았습니다.

## 두 번째 기능: 로컬 RSS 수집 → 선별

현재 이 PC에는 프로젝트 전용 `.venv`를 만들고 필요한 패키지를 설치했습니다. 터미널에서 활성화하지 않고 다음 명령으로 실행할 수 있습니다.

```powershell
.\.venv\Scripts\python.exe -m engine.collect_demo
.\.venv\Scripts\python.exe -m unittest discover -s engine/tests -v
```

예상 결과는 `collected_count: 2`, `collection_status: partial`, `selection_status: selected`, `matched_keyword: 금리`, `mail_sent: false`입니다. `partial`과 `collection_succeeded: false`는 샘플에 일부러 넣은 날짜 누락 기사가 제외됐다는 뜻입니다. 정상 기사 2건은 계속 선별됩니다. 모든 기사 수집이 실패했을 때는 `collection_failed`와 종료 코드 1이 나옵니다. 정상 빈 RSS는 오류와 구분합니다.

`samples/collection/`의 RSS·HTML·sources.json을 직접 읽습니다. 기사 URL로 접속하거나 외부 서버·DB·챗 API에 요청하지 않습니다. 가상 URL이므로 실제 뉴스나 실제 소스 검증 결과로 사용하면 안 됩니다. API 키 없이 실행할 수 있습니다.

다른 PC에서는 Python 3.10 이상을 준비하고 저장소 루트에서 실행합니다.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r engine/requirements.txt
.\.venv\Scripts\python.exe -m engine.collect_demo
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

settings.py는 후속 연결용 설정 로더만 제공합니다. 실제 호출 코드는 아직 없고 샘플 수집에서는 `.env`를 읽지 않습니다. 배포 환경의 Secrets를 로컬 파일보다 우선하며 설정 오류에 키를 출력하지 않습니다.

환경 변수 이름은 사용자가 입력한 `OPENAI_*`에 맞췄습니다. 이름이 OPENAI여도 실제 제공자는 `OPENAI_BASE_URL`로 지정한 코디세이입니다. 사용자 `.env`의 키는 수정하거나 안내 문서에 복사하지 않습니다.

## 전달 방식 — 먼저 구현한 제안

현재는 추가 서버 없이 엔진 내부 Python 함수로 객체를 전달합니다. gateway.py의 `EngineGateway`는 연결 경계이고 `SampleEngineGateway`는 가상 응답만 제공합니다. 실제 Backend를 대신 구현하지 않습니다.

- `get_subscription_snapshot(subscription_id, scheduled_date_kst) → 공통 10-1 dict`
- `check_delivery_eligibility(subscription_id, now) → bool`

함수명은 공통 10-3을 따르고 인자·반환 타입은 **고승희 확인 전 제안**입니다. 실제 연결은 Backend와 합의한 보호된 API 또는 DB 접근 어댑터로 교체합니다. 현재 샘플 함수는 권한·해제·삭제 여부를 실제 확인하지 않으므로 운영 발송에 사용할 수 없습니다. 피드백 토큰은 이번 범위에 포함하지 않았습니다.

운영 연결에서도 조회 실패를 빈 구독·발송 가능 true로 바꾸지 않고 오류로 처리해야 합니다. 무료 계정의 한도·접근 권한은 실제 배포 때 확인합니다. 이 로컬 함수 연결에는 별도 서버·서비스 비용이 발생하지 않습니다.

수집의 로컬 개발용 제안값은 본문 최소 80자, RSS/HTML 각각 최대 1MB, 소스당 최대 100개 항목입니다. 실제 운영값은 공통 P-22에 따라 실제 표본으로 검증·합의해야 합니다. 날짜는 RSS 게시 시각의 timezone을 확인해 UTC로 바꾸고, 누락된 날짜를 현재 시각이나 수정 시각으로 대체하지 않습니다.

## 실행·확인 (PowerShell)

저장소 루트에서 Python 3.10 이상으로 실행합니다.

```powershell
cd C:\Users\user\Desktop\ai-news-card
py -3 -m engine.demo
py -3 -m engine.demo --no-keywords
py -3 -m unittest discover -s engine/tests -v
```

첫 명령은 `fixture_keyword`와 `type: keyword`, 두 번째는 `fixture_latest`와 `type: category`가 나와야 합니다. 더 최근의 `fixture_already_sent`는 7일 이력에 있어 제외됩니다. 출력의 `demo_only: true`는 실제 뉴스·실제 발송이 아니라는 표시입니다.

`py`에 Python이 등록되어 있지 않다면 Codex의 현재 제공 런타임으로도 실행할 수 있습니다. 아래 경로는 이 PC에서만 확인한 경로이며 팀원은 자기 Python을 사용합니다.

```powershell
& 'C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m engine.demo
& 'C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m engine.demo --no-keywords
& 'C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest discover -s engine/tests -v
```

다른 샘플로 시험하려면 `python -m engine.demo --sample engine/samples/selection.json` 형태로 파일 경로를 지정합니다. 샘플은 가상 데이터만 넣고, 실제 이메일·토큰·비밀값은 저장하지 않습니다.

## 선별 동작

1. 샘플 구독이 active이고 `scheduled_at <= now < deadline_at`인지 확인합니다.
2. 검증 소스·유효 본문 표시가 있고, 관심 분야에 속하며 `예정 시각 - 24시간 <= 게시 시각 < 예정 시각`인 기사만 남깁니다. 재시도 시에도 예정 시각을 기준으로 합니다.
3. 최근 7일의 sent·unknown 이력에 있는 동일 URL을 제외합니다. 같은 날 재실행 중 이미 전송된 이력도 제외합니다.
4. 후보 안의 동일 URL을 하나로 정리합니다. 사건 묶기는 미구현이므로 기사별 언론사 수는 1로 취급하고 최신 게시 시각, ID 오름차순으로 정렬합니다.
5. 제목·본문에서 키워드 하나라도 매칭되는 기사를 우선합니다. 키워드가 없거나 매칭되지 않으면 관심 분야의 최신 기사로 대체합니다.
6. 후보가 없으면 `no_candidates`를 반환합니다. 수집 실패 상태에 후보도 없으면 오류로 멈춥니다. 일부 수집이 실패해도 기존 정상 후보는 사용할 수 있습니다.

`no_candidates`는 메일 발송 완료 상태가 아닙니다. 정상 수집 후 뉴스 없음 하루 1회 안내는 후속 발송 작업에서 구현합니다. `ineligible` 역시 선별 결과이며 발송 DB 상태를 바꾸지 않습니다.

## 연결 규격 — 팀 합의 전 제안

공통 PRD 10절은 합의 전 초안입니다. 샘플의 `contract_version: 1.0`은 공통 문서의 예시와 맞춘 값이며 팀이 계약을 확정했다는 뜻이 아닙니다. 이 단계의 추가 규격은 `engine/` 안에 두고 확인 후 공유 계약 위치로 옮깁니다.

| 입력·출력 | 현재 규격 | 확인 대상 |
|---|---|---|
| 구독 입력 | 공통 10-1의 전체 `subscription_snapshot`; 선별은 status/timezone/scheduled_at/deadline_at/categories/keywords 사용 | 고승희 |
| 기사 입력 (제안) | `Article`: article_id, url, title, body, category, published_at, source_verified, body_valid | 박경연·윤지민 |
| 이력 입력 (제안) | `DeliveryHistory`: url, status, attempted_at. 해당 사용자의 모든 구독에 걸친 최근 7일 sent/unknown 포함 | 박경연·고승희 |
| 출력 (제안) | selected/no_candidates/ineligible, 선택된 Article 또는 null, selection_reason 또는 null | 박경연·고승희 |
| 선택 이유 | 공통 10-6의 type/label/matched_keyword 또는 category | 박경연·메일 조립 |

Python 호출은 `select_article(snapshot, articles, history, now=..., collection_succeeded=...)`입니다. 시간은 timezone-aware datetime, JSON에서는 UTC 또는 offset이 있는 ISO 8601입니다. `history`는 필수이며 DB 조회 실패를 빈 목록으로 바꾸면 안 됩니다. 입력 오류는 예외로 전달하고 성공으로 표시하지 않습니다.

팀 확인이 필요한 세부 제안:

- 키워드는 관심 분야 안에서만 우선하고, NFKC·대소문자 정리 후 제목+본문 부분 문자열로 OR 매칭합니다. 키워드 개수로 점수를 더하지 않습니다. 매칭 이유는 입력 순서의 첫 일치 키워드입니다. 최종 키워드 정리는 Backend 담당이며 엔진은 저장된 값을 받습니다.
- 24시간의 하한은 포함하고 예정 시각과 동일한 게시 시각은 제외합니다. 7일 이력은 하한을 포함하며 attempted_at은 sent/unknown 발생 시각으로 연결합니다.
- URL은 scheme/host 대소문자·기본 포트·fragment만 정리합니다. 경로·query·끝 슬래시와 HTTP/HTTPS 구분은 보존합니다. 추적 query 제거 규칙은 실제 소스 확인 후 합의합니다. 이 구현은 중복 저장 방지를 위한 DB 유일성 제약의 대체가 아닙니다.
- `source_verified`·`body_valid`는 가상 샘플의 선별 관문입니다. 실제 수집기에서 QA 소스 검증 결과와 본문 품질 검사 결과로 채워야 하며 기본 true로 생성하지 않습니다. 본문 최소 길이 등 품질 기준은 아직 미정입니다.
- `collection_succeeded`는 정상 수집 여부입니다. false여도 기존 정상 후보가 있으면 선택할 수 있지만, 정상 후보가 없으면 `CollectionUnavailable`을 발생시킵니다. 상세 소스별 상태는 후속 수집 규격에서 제안합니다.

실제 환경에서는 Backend가 확정한 날짜별 스냅샷을 공급하고, 엔진이 SMTP 직전에 `check_delivery_eligibility`로 최신 해제·만료·삭제 요청을 별도 확인해야 합니다. 이 샘플의 상태 검사만으로 실제 발송을 허용하지 않습니다.

## 다음 작은 기능

로컬 RSS·본문 수집과 선별은 연결했습니다. 다음은 기사 저장·URL 유일성·내용 버전의 DB 코드 제안입니다. 기사 DB와 발송 DB 코드는 박경연이 작성하고 고승희의 리뷰·전체 마이그레이션 통합으로 진행합니다. 윤지민이 검증한 소스 목록이 준비되면 시간·용량 제한을 적용한 실제 HTTP 수집 어댑터를 추가합니다.

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
