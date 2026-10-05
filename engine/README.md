# 박경연 — 핵심 엔진 개발

## 할 일과 첫 구현

담당 범위는 수집·기사 저장·중복 방지 → 관심 뉴스 선별 → 과거 기사 RAG → AI 카드 생성·근거 검사 → 김현서의 템플릿을 이용한 이미지 변환 → 메일 조립·발송 → 발송 상태·재실행·정리 연결입니다.

첫 구현은 **FR-10 관심 뉴스 선별**과 선별 단계의 **FR-09 URL 반복 제외**입니다. 검증된 RSS 목록과 DB가 아직 없어 가상 기사와 공통 10-1 형식의 샘플 구독으로 시작합니다. Python 표준 라이브러리만 사용하여 추가 패키지·API 키 없이 실행합니다. 실제 수집·DB 중복 저장·RAG·AI·메일 전송은 아직 구현하지 않았습니다.

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

윤지민이 검증한 소스 목록을 전달하면 feedparser + trafilatura 수집기를 연결합니다. 검증 소스가 준비되기 전에는 로컬 RSS·본문 샘플로 실패/정상 수집을 구분하는 단계부터 개발할 수 있습니다. 기사 DB와 발송 DB 코드는 박경연이 작성하고 고승희의 리뷰·전체 마이그레이션 통합으로 진행합니다.

## 이번 확인 결과 — 2026-10-05 KST

- 환경: Windows PowerShell, Codex 제공 Python. 이 PC의 `py -3`에는 Python이 등록되어 있지 않아 위 제공 런타임 경로로 실행했습니다.
- 자동 테스트: 17개 통과. 키워드 OR·분야 대체·최신/ID 동점·24시간 경계·7일 sent/unknown·재실행 이력·수집 실패 구분·잘못된 기사 제외·발송 기한을 확인했습니다.
- 시연: 기본은 `fixture_keyword`, `--no-keywords`는 `fixture_latest` 선택. UTF-8 한글 출력도 확인했습니다.
- 추가 파일: 엔진 패키지 초기화, selection.py, demo.py, 가상 샘플 JSON, 테스트, 실행/규격 안내, 엔진 전용 .gitignore. 공통 PRD와 다른 담당자의 폴더는 수정하지 않았습니다.
- 외부 RSS·DB·AI·SMTP·배포 검증은 수행하지 않았으며 실제 발송 안전성 전체가 완료된 상태는 아닙니다. 커밋은 생성하지 않았습니다.
