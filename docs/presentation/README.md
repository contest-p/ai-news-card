# 프로젝트 발표 자료

- 발표 PDF: [PDF 열기](../../output/pdf/ai-news-briefing-presentation-v2.pdf)
- 수정 가능한 원본: [PowerPoint 열기](ai-news-briefing-presentation-v2.pptx)
- 15장, 16:9. 사용자 피드백(13장)과 개선 사항(14장)은 제목만 작성했다.

## 내용과 근거

| 슬라이드 | 내용 | 근거 |
|---|---|---|
| 1~3 | 서비스 정의, 구축·운영 부담, 이용 흐름 | 사용자 기획 의도, 프로젝트 기획서, REQUIREMENTS.md |
| 4~5 | 배포 화면, 로그인 동선, 가상 카드 미리보기 | 2026-10-10 배포 사이트 직접 방문·캡처 |
| 6 | 실제 이메일 수신 화면 | 사용자가 제공한 Gmail 메일 직접 확인·본문 캡처 |
| 7~9 | 처리 과정, AI 생성·검사, E5·Firestore RAG | engine/runtime.py, card_builder.py, cards.py, localization.py, embeddings.py, firestore_rag.py |
| 10~11 | 시스템 구성과 기술 선택 | 기획서, 요구사항 정의서, 현재 소스 코드·워크플로 |
| 12 | 발송과 장애 대응 | engine/delivery.py, firestore_runtime_store.py, pipeline.py, backend/preview_mail.py |
| 13~14 | 사용자 피드백·개선 사항 | 본문 미작성 |
| 15 | 배포 서비스·GitHub 링크 | 프로젝트 URL |

개별 근거와 상세 설명은 PowerPoint 발표자 노트에도 기록했다. PDF는 검수한 슬라이드 렌더를 사용하며, 본문 편집은 PowerPoint 원본에서 한다. 기존 14장 버전은 이전 파일명으로 보존했다.

## 실제 화면 확인 범위

공개 랜딩에서 로그인 화면으로 이동하는 동선과 서비스 소개의 가상 카드 선택을 확인했다. 로그인 완료와 신규 구독 저장은 이번 관찰에서 검증하지 않았다. 사이트의 가상 카드 샘플과 실제 수신 메일은 별도 슬라이드로 구분했다.

사용자가 제공한 뉴스 브리핑 메일 한 건을 Gmail에서 직접 열어 카드 이미지, 관심 키워드 AI 표시, 출처·원문 링크, 피드백·구독 관리 링크를 확인했다. 메일 제목은 '[뉴스 브리핑] 2026.10.10 · 오늘의 관심 뉴스'다. Gmail 계정·이메일 헤더를 제외하고 본문만 캡처했다. 피드백 제출과 구독 변경은 수행하지 않았다. 이 수신 사례는 정기 발송 전체나 수신율의 검증 결과가 아니다.

| 캡처 파일 | 화면 |
|---|---|
| assets/06-landing-action.jpg | 랜딩의 구독 시작 버튼 |
| assets/02-login.jpg | Google 로그인 화면 |
| assets/05-card-detail.jpg | 서비스 소개의 가상 카드 상세 |
| assets/07-email-top.jpg | 실제 메일의 브리핑·관심 키워드·카드 상단 |
| assets/08-email-links.jpg | 실제 메일의 원문·피드백·구독 관리 링크 |

사용자 테스트 결과, 만족도·정확도·수신율·시간 절감 수치는 포함하지 않았다.
