"""Cloud Firestore용 기사 저장 어댑터 초안. 실제 DB 종류·권한 확인이 필요하다.

호출자가 서버 SDK Client를 제공한다. 샘플 시연에서는 생성하지 않는다.
"""

from datetime import datetime
import hashlib
import json

from engine.article_store import ArticleRevision, SaveResult, StoredArticle, plan_save, prepare_article
from engine.selection import Article, aware_utc


def document_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def record_document(record: StoredArticle) -> dict:
    article = record.article
    return {
        "schema_version": "1.0-proposal", "article_id": article.article_id,
        "canonical_url": article.url, "title": article.title, "body": article.body,
        "category": article.category, "published_at": article.published_at,
        "source_verified": article.source_verified, "body_valid": article.body_valid,
        "content_hash": record.content_hash, "content_version": record.content_version,
        "first_seen_at": record.first_seen_at, "last_seen_at": record.last_seen_at,
        "updated_at": record.updated_at, "embedding_status": record.embedding_status,
    }


def decode_article(document: dict) -> Article:
    return Article(document["article_id"], document["canonical_url"], document["title"], document["body"],
                   document["category"], aware_utc(document["published_at"]),
                   document["source_verified"], document["body_valid"])


def decode_record(document: dict) -> StoredArticle:
    return StoredArticle(decode_article(document), document["content_hash"], document["content_version"],
                         aware_utc(document["first_seen_at"]), aware_utc(document["last_seen_at"]),
                         aware_utc(document["updated_at"]), document["embedding_status"])


class FirestoreArticleRepository:
    """정규화 URL의 hash를 문서 ID로 사용하고 트랜잭션으로 중복·수정을 처리한다."""

    def __init__(self, client, *, max_scan: int = 100):
        if max_scan < 1:
            raise ValueError("조회 상한은 양수여야 합니다.")
        self.client = client
        self.max_scan = max_scan
        self.articles = client.collection("engine_articles")
        self.identities = client.collection("engine_article_ids")

    def save(self, article: Article, *, observed_at: datetime) -> SaveResult:
        article, digest = prepare_article(article)
        observed_at = aware_utc(observed_at)
        # 문서 1MiB 제한에 여유를 둔 개발용 제안값. 운영값은 표본으로 합의한다.
        size = len(json.dumps({"title": article.title, "body": article.body,
                               "url": article.url, "category": article.category,
                               "article_id": article.article_id}, ensure_ascii=False).encode("utf-8"))
        if size > 900_000:
            raise ValueError("기사 문서의 개발용 용량 상한을 초과했습니다.")
        from google.cloud import firestore

        @firestore.transactional
        def run(transaction):
            return self._save_transaction(transaction, article, digest, observed_at)

        # SDK가 트랜잭션 재시도·commit을 완료한 후에만 결과를 반환한다.
        # 실패를 메모리 저장 성공으로 바꾸지 않는다.
        return run(self.client.transaction())

    def _save_transaction(self, transaction, article: Article, digest: str,
                          observed_at: datetime) -> SaveResult:
        key = document_key(article.url)
        reference = self.articles.document(key)
        snapshot = reference.get(transaction=transaction)
        previous = decode_record(snapshot.to_dict()) if snapshot.exists else None
        if previous is not None and previous.article.url != article.url:
            raise ValueError("기사 URL 문서 식별자가 충돌합니다.")
        result = plan_save(article, digest, observed_at, previous)
        if result.action == "stale":
            return result
        identity_reference = None
        if result.action == "inserted":
            # 전역 기사 ID의 중복도 같은 트랜잭션에서 검사한다.
            identity_reference = self.identities.document(document_key(article.article_id))
            identity = identity_reference.get(transaction=transaction)
            if identity.exists:
                raise ValueError("이미 등록된 기사 ID입니다. DB의 ID 연결을 확인하세요.")
        # Firestore에서는 모든 읽기를 쓰기 전에 마친다.
        if result.action == "unchanged":
            if result.record.last_seen_at != previous.last_seen_at:
                transaction.update(reference, {"last_seen_at": observed_at})
            return result
        document = record_document(result.record)
        if identity_reference is not None:
            transaction.create(identity_reference, {"article_id": article.article_id, "document_key": key})
            transaction.create(reference, document)
        else:
            transaction.set(reference, document)
        revision_reference = reference.collection("versions").document(str(result.record.content_version))
        transaction.create(revision_reference, {**document, "captured_at": observed_at})
        return result

    def list_current(self) -> list[StoredArticle]:
        # 개발용 검수 조회. 운영 선별은 날짜·분야 제한 query로 별도 연결한다.
        documents = list(self.articles.limit(self.max_scan + 1).stream())
        if len(documents) > self.max_scan:
            raise RuntimeError("기사 조회 상한 초과: 날짜·분야 조회를 연결해야 합니다.")
        records = [decode_record(document.to_dict()) for document in documents]
        return sorted(records, key=lambda record: record.article.url)

    def get_revision(self, article_id: str, content_version: int) -> ArticleRevision | None:
        if content_version < 1:
            raise ValueError("내용 버전은 양수여야 합니다.")
        identity = self.identities.document(document_key(article_id)).get()
        if not identity.exists:
            return None
        mapping = identity.to_dict()
        if mapping["article_id"] != article_id:
            raise ValueError("기사 ID 문서 식별자가 충돌합니다.")
        reference = self.articles.document(mapping["document_key"])
        snapshot = reference.collection("versions").document(str(content_version)).get()
        if not snapshot.exists:
            return None
        document = snapshot.to_dict()
        return ArticleRevision(decode_article(document), document["content_hash"], document["content_version"],
                               aware_utc(document["captured_at"]))
