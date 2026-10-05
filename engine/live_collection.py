"""QA의 RSS 목록을 사용하는 실제 수집. 발송·DB·AI 호출은 하지 않는다."""

from dataclasses import dataclass, field
import hashlib
import importlib
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

import feedparser
import trafilatura

from engine.collection import CollectionIssue, CollectionResult, published_time
from engine.selection import Article, canonical_url


# QA 목록의 URL을 복제하지 않고 읽는다. 분야는 공통 PRD의 API 코드 초안.
SOURCE_METADATA = {
    "BBC 뉴스 (글로벌)": ("bbc", "world", "en", ("bbc.co.uk", "bbc.com", "bbci.co.uk")),
    "SBS 뉴스 (정치)": ("sbs", "politics", "ko", ("sbs.co.kr",)),
    "매일경제 (전체)": ("mk", None, "ko", ("mk.co.kr",)),
    "경향신문 (IT/과학)": ("khan", "it_science", "ko", ("khan.co.kr",)),
}
CATEGORY_TERMS = {
    "경제": "economy", "기업": "economy", "증권": "economy", "금융": "economy",
    "부동산": "economy", "economy": "economy", "정치": "politics", "politics": "politics",
    "사회": "society", "society": "society", "국제": "world", "세계": "world", "world": "world",
    "문화": "culture", "culture": "culture", "IT": "it_science", "과학": "it_science",
    "IT/과학": "it_science", "IT비즈·정책": "it_science", "it_science": "it_science",
}


@dataclass(frozen=True)
class NewsSource:
    source_id: str
    name: str
    url: str
    category: str | None
    language: str
    hosts: tuple[str, ...]


@dataclass
class LiveCollectionResult(CollectionResult):
    source_reports: list[dict] = field(default_factory=list)
    article_sources: dict[str, dict] = field(default_factory=dict)


def load_sources() -> list[NewsSource]:
    rows = importlib.import_module("deployment-qa.rss_validator.sources").SOURCES
    result = []
    for row in rows:
        source_id, category, language, hosts = SOURCE_METADATA[row["name"]]
        result.append(NewsSource(source_id, row["name"], row["url"], category, language, hosts))
    return result


def allowed_url(url: str, source: NewsSource) -> str:
    url = canonical_url(url)
    host = urlsplit(url).hostname
    if not any(host == root or host.endswith("." + root) for root in source.hosts):
        raise ValueError("소스 외부 URL")
    return url


class SourceRedirects(HTTPRedirectHandler):
    def __init__(self, source):
        self.source = source

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        allowed_url(newurl, self.source)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url: str, source: NewsSource, *, timeout: float = 12, limit: int = 3_000_000) -> bytes:
    """HTTP 오류는 우회하지 않는다. 시간·크기·소스 도메인에 제한을 둔다."""
    request = Request(allowed_url(url, source), headers={"User-Agent": "AI-News-Card/0.1 (RSS research)"})
    with build_opener(SourceRedirects(source)).open(request, timeout=timeout) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("응답 크기 제한 초과")
    return data


def entry_category(entry, source: NewsSource) -> str | None:
    if source.category:
        return source.category
    categories = {CATEGORY_TERMS[tag.get("term", "").strip()]
                  for tag in entry.get("tags", [])
                  if tag.get("term", "").strip() in CATEGORY_TERMS}
    # 전체 피드의 헤드라인·분류 누락·복수 분야를 임의로 경제로 바꾸지 않는다.
    if len(categories) == 1:
        return next(iter(categories))
    if not categories and source.source_id == "mk":
        # 매경 전체 RSS의 category는 '헤드라인'. 언론사가 URL에 지정한 분야만 사용.
        match = re.fullmatch(r"/news/(politics|economy|society|world|culture|it|stock|business|realestate)/\d+/?",
                             urlsplit(entry.get("link", "")).path)
        if match:
            return {"politics": "politics", "economy": "economy", "society": "society",
                    "world": "world", "culture": "culture", "it": "it_science",
                    "stock": "economy", "business": "economy", "realestate": "economy"}[match[1]]
    return None


def live_published_time(value):
    # 매경의 RFC 날짜에 있는 +09:00을 +0900으로 정규화. 명시된 시간대만 사용.
    return published_time(re.sub(r"([+-]\d{2}):(\d{2})$", r"\1\2", value.strip()))


def error_code(exc, fallback):
    if isinstance(exc, HTTPError):
        return f"{fallback}_HTTP_{exc.code}"
    if isinstance(exc, (TimeoutError, URLError, OSError)):
        return f"{fallback}_UNREACHABLE"
    return f"{fallback}_INVALID"


def collect_live_sources(*, sources=None, max_entries=3, min_body_chars=200,
                         fetch=download) -> LiveCollectionResult:
    """소스별 앞 N건만 검사. 게시 시각·본문·분야가 확인된 기사만 Article로 전달."""
    if not 1 <= max_entries <= 20 or min_body_chars < 1:
        raise ValueError("기사 수는 1~20, 최소 본문 길이는 양수여야 합니다.")
    result = LiveCollectionResult()
    seen = set()
    for source in load_sources() if sources is None else sources:
        report = {"source_id": source.source_id, "publisher": source.name,
                  "feed_url": source.url, "language": source.language,
                  "feed_entries": 0, "checked_entries": 0, "valid_bodies": 0, "collected": 0}
        result.source_reports.append(report)
        issue_start = len(result.issues)
        try:
            feed = feedparser.parse(fetch(source.url, source))
            if feed.get("bozo") or not feed.get("version"):
                raise ValueError("RSS 파싱 실패")
        except Exception as exc:
            code = error_code(exc, "FEED")
            result.issues.append(CollectionIssue(source.source_id, None, code))
            report.update(status="failed", issue_codes=[code])
            continue
        report["feed_entries"] = len(feed.entries)
        for index, entry in enumerate(feed.entries[:max_entries]):
            report["checked_entries"] += 1
            try:
                url = allowed_url(entry["link"], source)
                title = entry["title"].strip()
                if not title:
                    raise ValueError("제목 누락")
            except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
                result.issues.append(CollectionIssue(source.source_id, index, "ENTRY_METADATA_INVALID"))
                continue
            if url in seen:
                continue
            # 분류를 모르는 기사도 본문 접속 여부는 확인해 QA에 전달한다.
            try:
                html = fetch(url, source)
                body = trafilatura.extract(html, include_comments=False, include_tables=False,
                                          favor_precision=True)
                if not body or len(body.strip()) < min_body_chars:
                    raise ValueError("본문 부족")
            except Exception as exc:
                result.issues.append(CollectionIssue(source.source_id, index, error_code(exc, "BODY")))
                continue
            report["valid_bodies"] += 1
            try:
                published = live_published_time(entry["published"])
            except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
                result.issues.append(CollectionIssue(source.source_id, index, "ENTRY_PUBLISHED_INVALID"))
                continue
            category = entry_category(entry, source)
            if category is None:
                result.issues.append(CollectionIssue(source.source_id, index, "CATEGORY_UNMAPPED"))
                continue
            article_id = "rss_" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
            result.articles.append(Article(article_id, url, title, body.strip(), category,
                                           published, True, True))
            result.article_sources[article_id] = {"publisher": source.name, "language": source.language,
                                                   "source_id": source.source_id}
            seen.add(url)
            report["collected"] += 1
        codes = [issue.code for issue in result.issues[issue_start:]]
        report.update(status="partial" if codes else "complete", issue_codes=codes)
        if not codes:
            result.successful_sources += 1
    return result
