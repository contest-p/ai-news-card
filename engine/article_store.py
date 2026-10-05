"""기사 저장 계약 초안과 DB 없이 실행하는 메모리 샘플 구현."""

from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
import json
from threading import RLock
from typing import Protocol

from engine.selection import Article, aware_utc, canonical_url


@dataclass(frozen=True)
class StoredArticle:
    article: Article
    content_hash: str
    content_version: int
    first_seen_at: datetime
    last_seen_at: datetime
    updated_at: datetime
    embedding_status: str = "pending"


@dataclass(frozen=True)
class ArticleRevision:
    article: Article
    content_hash: str
    content_version: int
    captured_at: datetime


@dataclass(frozen=True)
class SaveResult:
    action: str  # inserted / unchanged / updated / stale
    record: StoredArticle


class ObservationConflict(ValueError):
    """동일한 관측 시각에 서로 다른 내용을 받으면 최신값을 추측하지 않는다."""


class ArticleRepository(Protocol):
    def save(self, article: Article, *, observed_at: datetime) -> SaveResult: ...

    def list_current(self) -> list[StoredArticle]: ...

    def get_revision(self, article_id: str, content_version: int) -> ArticleRevision | None: ...


def prepare_article(article: Article) -> tuple[Article, str]:
    """URL 정리·입력 검사·내용 fingerprint를 두 저장 구현이 공유한다."""
    for value in (article.article_id, article.title, article.body, article.category):
        if not isinstance(value, str) or not value.strip() or "\x00" in value:
            raise ValueError("기사 ID·제목·본문·분야는 비어 있지 않은 문자열이어야 합니다.")
    if article.source_verified is not True or article.body_valid is not True:
        raise ValueError("검증 소스와 유효 본문이 있는 기사만 저장할 수 있습니다.")
    normalized = replace(article, url=canonical_url(article.url), title=article.title.strip(),
                         body=article.body.strip(), published_at=aware_utc(article.published_at))
    # 사실 내용에 NFKC나 요약을 적용하지 않는다. 서로 다른 문서를 합치지 않는다.
    payload = {
        "hash_schema": "article-content-v1-proposal",
        "title": normalized.title, "body": normalized.body,
        "category": normalized.category,
        "published_at": normalized.published_at.isoformat(),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                       separators=(",", ":")).encode("utf-8")).hexdigest()
    return normalized, digest


def plan_save(article: Article, digest: str, observed_at: datetime,
              previous: StoredArticle | None) -> SaveResult:
    """검사·정규화가 끝난 입력에 공통 저장 정책을 적용한다. 외부 I/O는 없다."""
    if previous is None:
        return SaveResult("inserted", StoredArticle(article, digest, 1, observed_at, observed_at, observed_at))
    if observed_at < previous.last_seen_at:
        return SaveResult("stale", previous)
    if digest == previous.content_hash:
        return SaveResult("unchanged", replace(previous, last_seen_at=observed_at))
    if observed_at == previous.last_seen_at:
        raise ObservationConflict("동일 시각의 기사 내용이 충돌합니다.")
    return SaveResult("updated", StoredArticle(
        article=replace(article, article_id=previous.article.article_id),
        content_hash=digest, content_version=previous.content_version + 1,
        first_seen_at=previous.first_seen_at, last_seen_at=observed_at,
        updated_at=observed_at, embedding_status="pending",
    ))


class InMemoryArticleRepository:
    """로컬 시연 전용. 프로세스 종료 시 데이터가 사라지며 운영 DB가 아니다."""

    def __init__(self):
        self._by_url: dict[str, StoredArticle] = {}
        self._id_to_url: dict[str, str] = {}
        self._revisions: dict[tuple[str, int], ArticleRevision] = {}
        self._lock = RLock()

    def save(self, article: Article, *, observed_at: datetime) -> SaveResult:
        article, digest = prepare_article(article)
        observed_at = aware_utc(observed_at)
        with self._lock:
            previous = self._by_url.get(article.url)
            if previous is None and article.article_id in self._id_to_url:
                raise ValueError("같은 기사 ID를 다른 URL에 사용할 수 없습니다.")
            result = plan_save(article, digest, observed_at, previous)
            if result.action == "stale":
                return result
            record = result.record
            self._by_url[article.url] = record
            self._id_to_url[record.article.article_id] = article.url
            if result.action in {"inserted", "updated"}:
                self._revisions[(record.article.article_id, record.content_version)] = ArticleRevision(
                    record.article, record.content_hash, record.content_version, observed_at)
            return result

    def list_current(self) -> list[StoredArticle]:
        with self._lock:
            return [self._by_url[url] for url in sorted(self._by_url)]

    def get_revision(self, article_id: str, content_version: int) -> ArticleRevision | None:
        with self._lock:
            return self._revisions.get((article_id, content_version))
