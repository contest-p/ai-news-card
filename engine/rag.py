"""로컬 기사·임베딩 기반 과거 근거 검색. 카드 생성·메일 전송은 하지 않는다."""

from dataclasses import dataclass
from datetime import date, datetime, time, timezone

from engine.article_store import StoredArticle, prepare_article
from engine.embeddings import Encoder, validate_vector
from engine.selection import KST, aware_utc, canonical_url


@dataclass(frozen=True)
class ArticleEmbedding:
    article_id: str
    content_version: int
    content_hash: str
    model_id: str
    model_revision: str
    status: str
    vector: tuple[float, ...] | None
    error_code: str | None = None


@dataclass(frozen=True)
class RagHit:
    record: StoredArticle
    score: float


@dataclass(frozen=True)
class RagResult:
    status: str  # ready / no_evidence / embedding_failed
    cutoff_utc: datetime
    retrieved: tuple[RagHit, ...]
    usable_evidence: tuple[RagHit, ...]
    excluded: tuple[tuple[str, str], ...]
    model_id: str
    model_revision: str


def past_cutoff(work_date_kst: date) -> datetime:
    return datetime.combine(work_date_kst, time.min, tzinfo=KST).astimezone(timezone.utc)


def index_articles(records: list[StoredArticle], encoder: Encoder) -> list[ArticleEmbedding]:
    output = []
    for record in records:
        try:
            article, digest = prepare_article(record.article)
            if digest != record.content_hash:
                raise ValueError("기사 내용과 저장 hash가 다릅니다.")
            vectors = encoder.encode_passages([article.title + "\n" + article.body])
            if len(vectors) != 1:
                raise ValueError("임베딩 결과 개수가 다릅니다.")
            vector = validate_vector(vectors[0])
            status, code = "ready", None
        except Exception:
            # 기사 하나의 실패가 정상 기사 임베딩까지 막지 않게 한다.
            vector, status, code = None, "failed", "EMBEDDING_INVALID_OR_FAILED"
        output.append(ArticleEmbedding(record.article.article_id, record.content_version,
                                       record.content_hash, encoder.model_id, encoder.model_revision,
                                       status, vector, code))
    return output


def search_past_articles(current: StoredArticle, records: list[StoredArticle],
                         embeddings: list[ArticleEmbedding], encoder: Encoder, *,
                         work_date_kst: date, min_score: float = 0.85) -> RagResult:
    """최대 5개 검색·최대 3개 사용. 유사도 기준과 동일 분야 제한은 제안이다."""
    if not 0 <= min_score <= 1:
        raise ValueError("유사도 기준은 0~1이어야 합니다.")
    cutoff = past_cutoff(work_date_kst)
    current_article, current_hash = prepare_article(current.article)
    if current_hash != current.content_hash:
        raise ValueError("질의 기사 내용과 저장 hash가 다릅니다.")
    vectors = {}
    for embedding in embeddings:
        key = (embedding.article_id, embedding.content_version, embedding.content_hash)
        if key in vectors:
            raise ValueError("동일 기사 버전의 임베딩이 중복 입력됐습니다.")
        vectors[key] = embedding
    excluded = []
    candidates = []
    eligible_count = 0
    seen = set()
    for record in records:
        article = record.article
        try:
            normalized, digest = prepare_article(article)
            if digest != record.content_hash:
                raise ValueError("내용 hash 불일치")
        except (ValueError, TypeError, AttributeError):
            excluded.append((article.article_id, "ARTICLE_INVALID"))
            continue
        if normalized.url == current_article.url or article.article_id == current_article.article_id:
            excluded.append((article.article_id, "CURRENT_ARTICLE"))
            continue
        if aware_utc(article.published_at) >= cutoff:
            excluded.append((article.article_id, "NOT_BEFORE_KST_MIDNIGHT"))
            continue
        if article.category != current_article.category:
            excluded.append((article.article_id, "CATEGORY_MISMATCH"))
            continue
        eligible_count += 1
        embedding = vectors.get((article.article_id, record.content_version, record.content_hash))
        if (embedding is None or embedding.status != "ready"
                or embedding.model_id != encoder.model_id
                or embedding.model_revision != encoder.model_revision):
            excluded.append((article.article_id, "EMBEDDING_MISSING_STALE_OR_FAILED"))
            continue
        try:
            vector = validate_vector(embedding.vector)
        except (ValueError, TypeError):
            excluded.append((article.article_id, "VECTOR_INVALID"))
            continue
        url = canonical_url(article.url)
        if url in seen:
            raise ValueError("같은 URL의 최신 기사가 중복 입력됐습니다.")
        seen.add(url)
        candidates.append((record, vector))
    if not candidates:
        status = "embedding_failed" if eligible_count else "no_evidence"
        return RagResult(status, cutoff, (), (), tuple(excluded), encoder.model_id, encoder.model_revision)
    try:
        query = validate_vector(encoder.encode_query(current_article.title + "\n" + current_article.body))
    except Exception:
        return RagResult("embedding_failed", cutoff, (), (), tuple(excluded),
                         encoder.model_id, encoder.model_revision)
    hits = [RagHit(record, max(-1.0, min(1.0, sum(a * b for a, b in zip(query, vector)))))
            for record, vector in candidates]
    hits.sort(key=lambda hit: hit.record.article.article_id)
    hits.sort(key=lambda hit: hit.record.article.published_at, reverse=True)
    hits.sort(key=lambda hit: hit.score, reverse=True)
    retrieved = tuple(hits[:5])
    usable = tuple(hit for hit in retrieved if hit.score >= min_score)[:3]
    return RagResult("ready" if usable else "no_evidence", cutoff, retrieved, usable, tuple(excluded),
                     encoder.model_id, encoder.model_revision)


def evidence_context(result: RagResult) -> list[dict]:
    """후속 카드 생성에 전달할 원문. 보도 시각은 사건 발생일이 아니다."""
    return [{"article_id": hit.record.article.article_id,
             "content_version": hit.record.content_version, "content_hash": hit.record.content_hash,
             "title": hit.record.article.title, "body": hit.record.article.body,
             "url": hit.record.article.url, "published_at": hit.record.article.published_at.isoformat(),
             "temporal_role": "past", "similarity_score": hit.score}
            for hit in result.usable_evidence]
