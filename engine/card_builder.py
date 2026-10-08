"""발송 작업의 선택 기사 → 저장 기록 → 과거 근거 검색 → AI 카드 생성·검사 어댑터.

생성 횟수(P-13)와 결과 재사용은 generation.generate_cards가 담당한다.
운영 runtime은 Firestore 생성 저장소와 실제 RAG 검색·기록을 주입한다.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable

from engine.article_store import ArticleRepository, StoredArticle
from engine.generation import GenerationBusy, generate_cards
from engine.pipeline import CardOutcome
from engine.rag import RagResult, past_cutoff


def no_evidence_search(record: StoredArticle, work_date_kst: date) -> RagResult:
    """Firestore 벡터 검색 연결 전 기본값: 과거 근거 없음 → 카드 2 생략(정상 결과)."""
    return RagResult("no_evidence", past_cutoff(work_date_kst), (), (), (), "not_connected", "not_connected")


@dataclass
class GenerationCardBuilder:
    repository: ArticleRepository
    observed_at: datetime
    publisher_for: Callable[[str], str]
    client: object
    model: str
    base_url: str
    store: object
    search_evidence: Callable[[StoredArticle, date], RagResult] = no_evidence_search
    record_search: Callable | None = None
    record_usage: Callable | None = None

    def __call__(self, job, article) -> CardOutcome:
        work_date = date.fromisoformat(job.scheduled_date_kst)
        # 같은 내용이면 unchanged로 기존 기록(내용 버전·hash)을 돌려받는다.
        record = self.repository.save(article, observed_at=self.observed_at).record
        try:
            rag = self.search_evidence(record, work_date)
        except Exception:
            rag = RagResult("search_failed", past_cutoff(work_date), (), (), (), "unavailable", "unavailable")
        rag_issues = () if rag.status in {"ready", "no_evidence"} else ("RAG_" + rag.status.upper(),)
        if self.record_search is not None:
            try:
                self.record_search(job.job_id, record, rag)
            except Exception:
                return CardOutcome("failed", None, True, "RAG_RECORD_FAILED", None)
        identities = [record.article.article_id] + [hit.record.article.article_id for hit in rag.usable_evidence]
        try:
            publishers = {identity: self.publisher_for(identity) for identity in identities}
            output = generate_cards(record, rag, publishers=publishers, work_date_kst=work_date,
                                    job_id=job.job_id, model=self.model, base_url=self.base_url,
                                    client=self.client, store=self.store)
        except GenerationBusy:
            # 다른 실행이 생성 중이거나 중단 기록이 있다. 자동으로 잠금을 풀지 않는다.
            return CardOutcome("failed", None, False, "GENERATION_BUSY_OR_INTERRUPTED", None)
        except (KeyError, LookupError):
            return CardOutcome("failed", None, False, "SOURCE_PUBLISHER_MISSING", None)
        result = output.get("result") or {}
        issues = rag_issues + tuple(result.get("issues", ()))
        if output["status"] == "completed" and result.get("status") == "ready_for_review":
            if self.record_usage is not None:
                try:
                    self.record_usage(job.job_id, result["card_data"])
                except Exception:
                    return CardOutcome("failed", None, True, "RAG_USAGE_RECORD_FAILED", output["generation_key"], issues)
            return CardOutcome("ready", result["card_data"], False, None, output["generation_key"], issues)
        # retryable은 남은 호출 횟수 안에서 다음 실행에 다시 시도한다(최초 포함 2회).
        return CardOutcome("failed", None, output["status"] == "retryable",
                           output.get("error_code") or "CARD_GENERATION_FAILED", output["generation_key"], issues)
