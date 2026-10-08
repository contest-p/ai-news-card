# 엔진 구현 현황과 다음 작업

2026-10-08: 실제 RAG/임베딩 저장·검색, 전체 구독/만료 배치 진입점, 기사 복구·재처리·정리 호출을 구현했습니다. 최신 상태와 Backend 연결 계약은 [엔진 구현 보완](ENGINE_COMPLETION.md)을 참조하세요. 아래 날짜별 기록은 당시 상태입니다. 배포·QA는 이번에 수행하지 않았습니다.

2026-10-07 추가: Firestore 발송·생성·MIME 저장소와 Backend HTTP 어댑터를 구현했다. 공유 프론트 카드 템플릿 연결, 이미지·PC/모바일 메일 검수, 실제 테스트 메일 수신·정상 표시 확인도 완료했다. 전체 테스트 234개 통과. 아래 10월 6일 표는 당시 기록이며, 최신 DB/API 실제 연결 상태는 [연결 안내](firebase/CONNECTED_ENGINE.md), 템플릿·수신 결과는 [렌더 검수](CARD_RENDER.md)를 참고한다.

확인일: 2026-10-06 (KST). 기준은 공통 PRD 본문 v0.4와 박경연 엔진 PRD 본문 v0.3입니다. DB는 Cloud Firestore(Firebase)로 확정이며 실제 연결은 2026-10-07에 진행합니다. 오늘은 DB가 없어도 되는 부분을 구현했고, 저장소는 Firestore와 같은 의미의 계약(Protocol)과 메모리 구현으로 검증했습니다. Backend·Frontend·공통 PRD는 변경하지 않았습니다.

## 요구사항 대조

| 요구사항 | 현재 구현 | 남은 일 |
|---|---|---|
| FR-07 RSS·본문 | 실제 수집, 소스·기사별 부분 실패, **정책 제외와 소스 장애 구분**, 새 QA 소스는 그 소스만 실패, 수집 15분 예산 | QA 공개 승인 목록 연결, 소스별 운영 검증 |
| FR-08 저장·임베딩 | 중복·내용 버전, Firestore 기사 어댑터, E5 384차원 | 임베딩 영속 저장·재처리, 날짜·분야 조회 **(DB)** |
| FR-09 URL 반복 방지 | 선별의 7일 sent/unknown 필터, `JobStore.recent_history` | Firestore 쿼리·인덱스 **(DB)** |
| FR-10 개인화 선별 | 키워드 OR 우선·분야 대체·24시간·동점 규칙, **60자 초과 제목 제외** | 실제 스냅샷 공급 **(Backend)** |
| FR-11 RAG | 로컬 검색, `card_builder.no_evidence_search` 기본값(카드 2 생략) | Firestore 벡터 검색 함수로 교체 **(DB)**, QA 관련성 평가 |
| FR-12 생성·검증 | 2회 한도, **제목 초과 시 API 호출 전 차단**, **같은 응답의 수치 형식 보정을 생성 단계에서 수행**, 날짜·식별자 오인 수정 | 생성 작업 저장소 Firestore화 **(DB)**, 사람 검수 정책 합의 |
| FR-14 이미지 | **최초+1회 렌더 후 텍스트 전환**(`card_images.py`), OS별 폰트 탐색 | `frontend/card-template.js` 연결, 실제 메일 클라이언트 검증 |
| FR-15 작업·선점 | **고정 ID, 기한, 선점·claim_token, 상태 전이, 멈춘 작업 복구·unknown 처리**(`delivery.py`) | 같은 계약의 Firestore 트랜잭션 구현 **(DB)** |
| FR-16 SMTP | **운영 전송·결과 분류**(`smtp_sender.py`), 최초 포함 3회, 직전 상태 재확인(`pipeline.py`) | 실제 Gmail 계정 연결 테스트 |
| FR-06 종료 안내 | 자연 만료만 발송, 24시간 기한, 작업 유일성 | 만료 대상 목록 함수 **(Backend)** |
| FR-25 메일 조립 | 3종, **preview 명시 필수**, 공통 HTML 틀, 재시도 때 보관 메일 재사용 | 보관 위치·접근 제한·기한 **(DB)**, 실제 수신 QA |
| FR-17 피드백 | 엔진은 `issue_feedback_token(job_id)` 결과로 링크만 조립, 재시도에 재발급 안 함 | Backend 토큰 발급·해시·30일 만료 |
| FR-18 정리 | 배치가 `privacy_cleanup(now)`를 호출하고 실패를 요약에 기록 | Backend 정리 함수 연결, 기사 보관 정리 |
| FR-20 배치·운영 | **배치 1회 실행·45분 예산·실행 요약 JSON**(`batch.py`), 오프라인 데모 | 운영 진입점, Actions 예약·Secrets |
| FR-21 실사용 검증 | 코드 테스트 | QA와 실제 사용자 5명 이상 |
| FR-13/22/23 Should | 없음 | 후순위 |

## 오늘 구현한 흐름

```
batch.run_batch
  ├─ collect(deadline=15분)            # LiveCollectionResult
  ├─ gateway.list_due_subscriptions / list_expired_subscriptions → delivery.new_job → jobs.create_if_absent
  ├─ jobs.open_jobs() (기한 이른 순)      # 이전 실행의 pending·failed·멈춘 작업 포함
  │    └─ pipeline.process_job
  │         claim → 최신 상태 확인 → (보관 메일 있으면 재사용)
  │         선별 → card_builder(generate_cards) → card_images.render_with_fallback
  │         → issue_feedback_token → assemble_mail(preview=False) → archive.save
  │         → 기한·최신 상태 재확인 → sending 기록(시도 횟수) → smtp_sender.send_message
  │         → sent / failed(재시도 가능 여부) / unknown
  ├─ privacy_cleanup(now)
  └─ 실행 요약(수신 주소·토큰 없음)
```

상태 결정 규칙:
- 사용자 해제·삭제 요청 → `cancelled`, 만료·기한 경과 → `skipped_late`. 종료 안내는 최신 상태가 `expired`일 때만 발송합니다.
- 수집 장애로 후보가 없으면 `failed(COLLECTION_UNAVAILABLE, 재시도 가능)`이며 뉴스 없음 메일을 보내지 않습니다.
- SMTP 4xx·연결 실패는 남은 횟수 안에서 다음 실행에 재시도합니다. 5xx·인증 실패는 재시도하지 않습니다. 결과 불확실은 `unknown`으로 두고 자동 재전송하지 않습니다.
- `processing`이 60분 넘게 멈추면 새 토큰으로 다시 선점합니다(SMTP 전 단계). `sending`이 멈추면 `unknown`입니다.

## 검증 결과

- 자동 테스트 **201개 통과**(신규: 발송 작업 16, SMTP 7, 작업 처리 10, 배치 9, 이미지 4, 카드 어댑터 3, 데모 1, 기존 모듈 보강 12).
- `python -m engine.demos.batch_demo`: 가상 구독 1건이 `sent`, 같은 상태로 재실행 시 처리 대상 0건.
- 기존 오프라인 데모(mail_types, demo, collect, store) 정상 실행.
- 실제 DB·AI·SMTP·임베딩 모델 호출은 하지 않았습니다.

## 2026-10-07 DB 작업에서 정할 것

1. `delivery.JobStore` Firestore 구현: `engine_delivery_jobs/{job_id}` 고정 문서, `claim`·`transition`을 트랜잭션으로. 콜백 안에서 AI·SMTP 호출 금지. `recent_history`(user_id + 시각) 인덱스.
2. 생성 작업 저장소(`generation.py`의 LocalGenerationStore 대체): 생성 키별 호출 횟수·in_flight·결과.
3. `EngineGateway` 구현 방식(내부 함수 vs 보호 API)과 반환 형식: 대상 목록, `Eligibility(eligible, reason)`, `issue_feedback_token`.
4. 메일 보관(`MailArchive`) 위치·접근 제한·보관 기한. 피드백 토큰 원문이 포함됩니다.
5. 기사 날짜·분야 조회, 임베딩 저장(Vector 384, COSINE)·벡터 인덱스, 과거 기사 출처명(publisher) 필드.
6. 날짜는 `YYYY-MM-DD` 문자열로 저장(Firestore에 date 타입 없음).

## 2026-10-07 코드 검토 후 보완

- `batch.py`가 전체 기한을 `pipeline.py`에 전달합니다. 작업 단계 경계와 SMTP 직전에서 예산·발송 기한을 확인하며, 배치 예산 초과는 다음 실행에서 재개 가능한 실패로 기록합니다. 외부 호출 자체의 강제 중단은 하지 않으므로 호출별 timeout까지 실행 시간이 늘어날 수 있습니다. 전송을 시작한 작업은 결과를 기록한 뒤 끝냅니다.
- 마지막 작업·정리 단계의 시간 초과도 `budget_exceeded`에 반영하고, 수집 기한은 전체 배치 기한을 넘지 않게 제한합니다.
- 선점 과정에서 복구된 `unknown`·`skipped_late` 상태와 사유를 실행 요약에 그대로 반영합니다.
- 보관 메일의 `X-Briefing-Content-Kind` 헤더에서 종류를 복원하고 저장합니다. 이전 MIME은 기존 Message-ID 계산 규칙으로 종류를 복원합니다. 구독의 메일 종류와 맞지 않는 보관 데이터는 발송하지 않습니다.
- 새 회귀 테스트 9개를 포함해 **210개 통과**. 추가 검토의 세 재현 상황도 수정 후 정상 결과를 확인했습니다. 실제 외부 서비스 호출은 하지 않았습니다.

## 팀 확인이 필요한 정책

- 사람 검수: PRD는 자동 검사와 사람 검수를 모두 요구합니다. 현재 배치는 자동 검사를 통과한 카드를 발송합니다. 발송 전 검수가 어렵다면 표본 사후 검수로 할지 합의가 필요합니다.
- 용어 이름 20자: 프런트 템플릿 값에 맞췄습니다. 김현서와 최종 확인이 필요합니다.
- 정책 제외 코드(분류 불가·게시 시각 없음·본문 부족·소스 외 URL)는 정상 수집으로 봅니다. 본문 HTTP 오류·네트워크 실패·예산 초과는 장애입니다. QA(윤지민)와 확인이 필요합니다.
- AI 호출 timeout·네트워크 오류는 결과 불확실로 보고 재호출하지 않습니다(P-13 해석).
