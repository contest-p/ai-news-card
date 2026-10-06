# 3종 메일 조립 연결

관련 요구사항: FR-25, FR-06, 공통 PRD 10-6. `mail_assembly.py`는 입력 → 검증 → `(subject, EmailMessage)`를 반환합니다. DB·API·SMTP에 접속하지 않습니다.

## 입력

공통: `job_id`, `sender_email`, `recipient_email`, `scheduled_date_kst`(KST의 date), `preview`.

`WebMailLinks(origin)`은 운영 설정에서 받은 승인 HTTPS origin입니다. 현재 웹 경로 `/manage`, `/feedback`을 사용합니다. 경로·query·fragment·인증정보가 붙은 origin은 거부합니다. 토큰 길이 1~4096자는 엔진의 개발용 방어 상한이며 Backend 계약 확정 시 재확인합니다.

| 입력 타입 | 추가 필드 | 필수 검사 |
|---|---|---|
| NewsMailData | card_data, selection_reason, inline_images, web_links, feedback_token | 기존 카드 표시 규격·첨부 검사. preview=False면 웹·토큰 필수 |
| NoNewsMailData | web_links, collection_succeeded, selection_result | 수집 성공 및 후보 없음만 허용 |
| EndNoticeMailData | web_links, subscription_status, start_date, end_date_exclusive | expired·7/14/28일·기준일=만료일 |

뉴스 입력의 기존 `subscription_management_url`은 로컬 미리보기 호환용입니다. `/manage` 페이지만 허용하며 웹 설정과 함께 넣으면 주소가 같아야 합니다. 운영에서는 `web_links`를 사용합니다.

```python
from engine.mail_assembly import NewsMailData, WebMailLinks, assemble_mail

# 값은 Backend·검증된 카드 생성 결과·운영 설정에서 공급합니다.
subject, message = assemble_mail(NewsMailData(
    job_id=job_id,
    sender_email=sender_email,
    recipient_email=recipient_email,
    scheduled_date_kst=scheduled_date_kst,
    card_data=validated_card_data,
    selection_reason=selection_reason,
    web_links=WebMailLinks(approved_web_origin),
    feedback_token=backend_feedback_token,
    preview=False,
))
```

이미지가 없어도 전체 카드 설명·용어·과거 기준일/보도일·출처를 text/plain과 HTML에 제공합니다. 피드백은 URL 인코딩한 token을 fragment에 넣습니다. up/down은 초기 선택이며 평가 저장은 사용자 제출 후 Backend에서 처리합니다.

수신자별로 새 메시지를 만듭니다. Message-ID는 같은 작업·수신자에서 안정적이지만 이것만으로 중복 전송이 방지되지는 않습니다. 동일 작업 메일 내용·토큰의 보관과 sent/unknown 처리는 향후 발송 저장소가 담당합니다.

## 실패·담당 경계

잘못된 입력은 ValueError로 거부하고, 실패를 뉴스 없음이나 발송 성공으로 바꾸지 않습니다. 새 검증 오류는 토큰·이메일 원문을 오류 문구에 넣지 않습니다.

메시지는 민감한 원문 토큰과 수신 주소를 포함합니다. 운영 MIME·입력 객체를 로그나 공개 출력에 저장하지 마세요. 토큰 해시/만료·삭제와 구독 상태의 진실성은 Backend 책임이며, 발송 직전 재확인·기한·하루 1회·재시도는 엔진 배치 책임입니다.

## 로컬 확인

```powershell
.\engine\.venv\Scripts\python.exe -B -m engine.demos.mail_types_demo
node engine/tools/render_mail_preview.cjs engine/.engine-local/previews/mail-types/news_card
node engine/tools/render_mail_preview.cjs engine/.engine-local/previews/mail-types/no_news
node engine/tools/render_mail_preview.cjs engine/.engine-local/previews/mail-types/end_notice
```

Python 데모는 가상 자료만 사용하며 네트워크 호출이 없습니다. 브라우저 검사는 Node·Playwright·Chrome/Edge가 필요합니다. `npm ci --prefix engine`으로 기존 Node 의존성을 설치하거나, 준비된 Playwright 모듈을 `PLAYWRIGHT_MODULE_PATH`로 지정할 수 있습니다.

생성물은 `engine/.engine-local/previews/mail-types/`에 있으며 Git에서 제외됩니다. 이 폴더만 지우면 미리보기 결과를 정리할 수 있습니다. `engine/samples/mail.json`은 재현용 가상 입력으로 보관합니다. 실행 결과는 [현재 구현 현황](IMPLEMENTATION_STATUS.md)에 기록했습니다.
