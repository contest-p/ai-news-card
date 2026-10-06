"""발송 작업 1건 처리: 선점 → 선별 → 카드 → 이미지 → 메일 조립·보관 → 최신 상태 재확인 → SMTP.

외부 의존성(Backend·DB·AI·렌더러·SMTP)은 PipelineDeps로 주입한다. 상태 변경은 모두
JobStore의 선점 토큰 조건으로만 수행하며, 저장소 트랜잭션 밖에서 AI·SMTP를 호출한다.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.parser import BytesParser
from email.policy import SMTP
from typing import Callable

from engine.delivery import SMTP_MAX_ATTEMPTS, ClaimLost, DeliveryJob, JobStore
from engine.gateway import Eligibility
from engine.mail_archive import MailArchive
from engine.mail_assembly import (
    EndNoticeMailData, NewsMailData, NoNewsMailData, WebMailLinks, assemble_mail,
)
from engine.selection import Article, CollectionUnavailable, select_article

RETRY_DELAY = timedelta(minutes=10)   # 다음 매시간 실행에서 다시 선점된다.
HISTORY_WINDOW = timedelta(days=7)    # P-10


@dataclass(frozen=True)
class CardOutcome:
    status: str                   # ready / failed
    card_data: dict | None
    retryable: bool
    error_code: str | None
    generation_key: str | None
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class DailyContext:
    """이번 배치에서 한 번 수집·저장한 후보 기사와 수집 성공 여부(작업 간 공유)."""
    articles: list[Article]
    collection_succeeded: bool


@dataclass
class PipelineDeps:
    gateway: object                                   # engine.gateway.EngineGateway
    jobs: JobStore
    archive: MailArchive
    build_cards: Callable[[DeliveryJob, Article], CardOutcome]
    render_images: Callable[[dict, DeliveryJob], tuple]
    send: Callable[[object, str], object]             # (message, recipient) -> SmtpOutcome
    sender_email: str = field(repr=False)
    web_links: WebMailLinks
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)


class JobStop(Exception):
    """작업을 지정 상태로 끝낸다(실패·해제·기한 경과)."""

    def __init__(self, status, error_code, *, retryable=False):
        super().__init__(error_code)
        self.status, self.error_code, self.retryable = status, error_code, retryable


def stop_for_eligibility(job: DeliveryJob, eligibility: Eligibility):
    if not isinstance(eligibility, Eligibility):
        raise TypeError("ELIGIBILITY_REQUIRED")
    if job.mail_kind == "subscription_end":
        if eligibility.reason != "expired":
            # 수동 해제·삭제 요청·재구독 사용자에게 종료 안내를 보내지 않는다.
            raise JobStop("cancelled", "END_NOTICE_NOT_NATURAL_EXPIRY_" + eligibility.reason.upper())
        return
    if eligibility.eligible:
        return
    if eligibility.reason == "expired":
        raise JobStop("skipped_late", "SUBSCRIPTION_EXPIRED")
    raise JobStop("cancelled", "SUBSCRIPTION_" + eligibility.reason.upper())


def check_deadline(job: DeliveryJob, now: datetime):
    if now >= job.deadline_at:
        raise JobStop("skipped_late", "DEADLINE_PASSED")


def build_news_or_no_news(job, token, context, deps, now, issues):
    snapshot = job.settings_snapshot
    history = deps.jobs.recent_history(job.user_id, since=job.scheduled_at - HISTORY_WINDOW)
    try:
        selected = select_article(snapshot, context.articles, history, now=now,
                                  collection_succeeded=context.collection_succeeded)
    except CollectionUnavailable:
        raise JobStop("failed", "COLLECTION_UNAVAILABLE", retryable=True) from None
    day = date.fromisoformat(job.scheduled_date_kst)
    common = dict(job_id=job.job_id, recipient_email=snapshot["recipient_email"],
                  sender_email=deps.sender_email, scheduled_date_kst=day, web_links=deps.web_links,
                  preview=False)
    if selected.status == "ineligible":
        raise JobStop("cancelled", "SNAPSHOT_NOT_ACTIVE")
    if selected.status == "no_candidates":
        return "no_news", NoNewsMailData(**common, collection_succeeded=True, selection_result=selected), token
    article = selected.article
    token = deps.jobs.transition(job.job_id, claim_token=token, to_status="processing", now=now,
                                 selected_article_id=article.article_id,
                                 selected_article_url=article.url).claim_token
    cards = deps.build_cards(job, article)
    issues.extend(cards.issues)
    if cards.status != "ready" or cards.card_data is None:
        raise JobStop("failed", cards.error_code or "CARD_GENERATION_FAILED", retryable=cards.retryable)
    images, image_issues = deps.render_images(cards.card_data, job)
    issues.extend(image_issues)
    feedback_token = deps.gateway.issue_feedback_token(job.job_id)
    data = NewsMailData(**common, card_data=cards.card_data, selection_reason=selected.selection_reason,
                        inline_images=tuple(images), feedback_token=feedback_token)
    if cards.generation_key:
        token = deps.jobs.transition(job.job_id, claim_token=token, to_status="processing", now=now,
                                     generation_key=cards.generation_key).claim_token
    return "news_card", data, token


def build_end_notice(job, deps):
    snapshot = job.settings_snapshot
    return "end_notice", EndNoticeMailData(
        job_id=job.job_id, recipient_email=snapshot["recipient_email"], sender_email=deps.sender_email,
        scheduled_date_kst=date.fromisoformat(job.scheduled_date_kst), web_links=deps.web_links,
        subscription_status="expired", start_date=date.fromisoformat(snapshot["start_date"]),
        end_date_exclusive=date.fromisoformat(snapshot["end_date_exclusive"]), preview=False)


def process_job(job_id: str, *, context: DailyContext, deps: PipelineDeps, run_id: str) -> dict:
    """작업 1건을 처리하고 로그용 요약(수신 주소·토큰 없음)을 돌려준다."""
    claimed = deps.jobs.claim(job_id, run_id=run_id, now=deps.clock())
    if claimed is None:
        current = deps.jobs.get(job_id)
        return {"job_id": job_id, "status": "skipped", "current_status": current.status if current else None,
                "content_kind": current.content_kind if current else None, "error_code": None, "issues": []}
    job, token = claimed, claimed.claim_token
    issues: list[str] = []
    content_kind = job.content_kind
    try:
        stop_for_eligibility(job, deps.gateway.check_delivery_eligibility(job.subscription_id, deps.clock()))
        archived = deps.archive.load(job.job_id)
        if archived is not None:
            # 이전 시도에서 만든 메일을 그대로 다시 보낸다. 토큰·내용을 새로 만들지 않는다.
            message = BytesParser(policy=SMTP).parsebytes(archived)
        else:
            if job.mail_kind == "subscription_end":
                content_kind, data = build_end_notice(job, deps)
            else:
                content_kind, data, token = build_news_or_no_news(job, token, context, deps, deps.clock(), issues)
            _, message = assemble_mail(data)
            deps.archive.save(job.job_id, message.as_bytes())
            token = deps.jobs.transition(job.job_id, claim_token=token, to_status="processing",
                                         now=deps.clock(), content_kind=content_kind).claim_token
        # SMTP 직전: 최신 해제·만료·삭제 요청과 발송 기한을 다시 확인한다.
        now = deps.clock()
        check_deadline(job, now)
        stop_for_eligibility(job, deps.gateway.check_delivery_eligibility(job.subscription_id, now))
        attempts = job.smtp_attempts + 1
        if attempts > SMTP_MAX_ATTEMPTS:
            raise JobStop("failed", "SMTP_ATTEMPTS_EXHAUSTED")
        # 전송 전에 sending과 시도 횟수를 기록한다. 중단되면 다음 실행이 unknown으로 처리한다.
        token = deps.jobs.transition(job.job_id, claim_token=token, to_status="sending", now=now,
                                     smtp_attempts=attempts).claim_token
        outcome = deps.send(message, job.settings_snapshot["recipient_email"])
        done = deps.clock()
        if outcome.status == "accepted":
            final = deps.jobs.transition(job.job_id, claim_token=token, to_status="sent", now=done,
                                         sent_at=done, error_code=None)
        elif outcome.status == "failed":
            retry = outcome.retryable and attempts < SMTP_MAX_ATTEMPTS
            final = deps.jobs.transition(job.job_id, claim_token=token, to_status="failed", now=done,
                                         retryable=retry, error_code=outcome.error_code,
                                         next_retry_at=done + RETRY_DELAY if retry else None)
        else:
            final = deps.jobs.transition(job.job_id, claim_token=token, to_status="unknown", now=done,
                                         error_code=outcome.error_code or "SMTP_RESULT_UNCERTAIN")
        return {"job_id": job_id, "status": final.status, "content_kind": content_kind,
                "error_code": final.error_code, "issues": issues}
    except JobStop as stop:
        now = deps.clock()
        fields = {"error_code": stop.error_code}
        if stop.status == "failed":
            fields.update(retryable=stop.retryable, next_retry_at=now + RETRY_DELAY if stop.retryable else None)
        try:
            final = deps.jobs.transition(job.job_id, claim_token=token, to_status=stop.status, now=now, **fields)
        except ClaimLost:
            return {"job_id": job_id, "status": "claim_lost", "content_kind": content_kind,
                    "error_code": stop.error_code, "issues": issues}
        return {"job_id": job_id, "status": final.status, "content_kind": content_kind,
                "error_code": stop.error_code, "issues": issues}
