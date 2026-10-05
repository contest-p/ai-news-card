"""샘플 및 저장소 어댑터가 공유하는 순수 기사 선별 함수.

입출력 세부 규격은 README의 팀 합의 전 제안을 따른다.
이 함수는 DB 변경, AI 호출, 메일 전송을 수행하지 않는다.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import unicodedata
from urllib.parse import urlsplit, urlunsplit


def parse_timestamp(value: str) -> datetime:
    """시간대 없는 값은 서버의 로컬 시간으로 추측하지 않는다."""
    return aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("시각에는 UTC 또는 시간대 정보가 필요합니다.")
    return value.astimezone(timezone.utc)


def canonical_url(value: str) -> str:
    """호스트 대소문자·기본 포트·fragment만 정리; query/path는 보존."""
    parts = urlsplit(value)
    if (parts.scheme.lower() not in {"http", "https"} or not parts.hostname
            or parts.username is not None or parts.password is not None
            or any(char.isspace() for char in value)):
        raise ValueError("기사 URL이 올바르지 않습니다.")
    scheme = parts.scheme.lower()
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    port = parts.port
    if port is not None and (scheme, port) not in {("http", 80), ("https", 443)}:
        host += f":{port}"
    return urlunsplit((scheme, host, parts.path or "/", parts.query, ""))


def match_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


@dataclass(frozen=True)
class Article:
    article_id: str
    url: str
    title: str
    body: str
    category: str
    published_at: datetime
    source_verified: bool
    body_valid: bool


@dataclass(frozen=True)
class DeliveryHistory:
    # 호출자가 해당 사용자의 최근 이력을 공급한다. unknown도 반복 금지.
    url: str
    status: str
    attempted_at: datetime


@dataclass(frozen=True)
class SelectionResult:
    status: str  # selected / no_candidates / ineligible
    article: Article | None
    selection_reason: dict[str, str] | None


class CollectionUnavailable(RuntimeError):
    """수집 실패를 정상적인 뉴스 없음으로 바꾸지 않는다."""


def select_article(
    snapshot: dict,
    articles: list[Article],
    history: list[DeliveryHistory],
    *,
    now: datetime,
    collection_succeeded: bool,
) -> SelectionResult:
    """공통 10-1 스냅샷을 입력받아 한 기사를 결정한다.

    subscription status는 샘플 확인용이다. 실제 발송 전 최신 상태 확인은
    Backend의 check_delivery_eligibility로 별도 수행해야 한다.
    """
    if snapshot["timezone"] != "Asia/Seoul":
        raise ValueError("구독 시간대는 Asia/Seoul이어야 합니다.")
    status = snapshot["status"]
    if status not in {"active", "cancelled", "expired"}:
        raise ValueError("알 수 없는 구독 상태입니다.")
    scheduled = parse_timestamp(snapshot["scheduled_at"])
    deadline = parse_timestamp(snapshot["deadline_at"])
    now = aware_utc(now)
    if deadline <= scheduled:
        raise ValueError("발송 기한은 예정 시각보다 늦어야 합니다.")
    if status != "active" or not scheduled <= now < deadline:
        return SelectionResult("ineligible", None, None)

    categories = snapshot["categories"]
    keywords = snapshot["keywords"]
    if (not isinstance(categories, list) or not categories
            or any(not isinstance(item, str) or not item.strip() for item in categories)):
        raise ValueError("정규화된 관심 분야 목록이 필요합니다.")
    if (not isinstance(keywords, list) or len(keywords) > 5
            or any(not isinstance(item, str) or not 1 <= len(match_text(item)) <= 20
                   or item != item.strip() for item in keywords)):
        raise ValueError("정규화된 키워드 0~5개(각 1~20자)가 필요합니다.")

    blocked = set()
    for item in history:
        attempted_at = aware_utc(item.attempted_at)
        if item.status not in {"pending", "processing", "sending", "sent", "failed",
                               "unknown", "skipped_late", "cancelled"}:
            raise ValueError("알 수 없는 발송 이력 상태입니다.")
        if item.status in {"sent", "unknown"} and attempted_at >= scheduled - timedelta(days=7):
            blocked.add(canonical_url(item.url))

    candidates = []
    for article in articles:
        # 잘못된 기사 하나가 정상 후보까지 막지 않게 한다.
        try:
            published = aware_utc(article.published_at)
            url = canonical_url(article.url)
        except (ValueError, AttributeError, TypeError):
            continue
        if (article.source_verified is not True or article.body_valid is not True
                or not article.article_id or not article.title.strip() or not article.body.strip()
                or article.category not in categories
                or not scheduled - timedelta(hours=24) <= published < scheduled
                or url in blocked):
            continue
        candidates.append(article)

    # FR-23 미구현 대체: 언론사 수는 기사별 1; 최신순 → ID 오름차순.
    candidates.sort(key=lambda item: item.article_id)
    candidates.sort(key=lambda item: aware_utc(item.published_at), reverse=True)
    unique = {}
    for article in candidates:
        unique.setdefault(canonical_url(article.url), article)
    candidates = list(unique.values())

    for article in candidates:
        haystack = match_text(article.title + "\n" + article.body)
        for keyword in keywords:
            if match_text(keyword) in haystack:
                return SelectionResult("selected", article, {
                    "type": "keyword", "label": "관심 키워드와 관련된 기사",
                    "matched_keyword": keyword,
                })
    if candidates:
        article = candidates[0]
        return SelectionResult("selected", article, {
            "type": "category", "label": "관심 분야의 최신 기사",
            "category": article.category,
        })
    if not collection_succeeded:
        raise CollectionUnavailable("수집 상태를 확인해야 합니다. 정상적인 뉴스 없음이 아닙니다.")
    return SelectionResult("no_candidates", None, None)
