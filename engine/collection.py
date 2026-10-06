"""로컬 RSS·HTML 샘플 수집. URL 다운로드나 실제 소스 검증은 하지 않는다."""

from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import feedparser
import trafilatura

from engine.selection import Article, aware_utc, canonical_url, parse_timestamp


@dataclass(frozen=True)
class CollectionIssue:
    source_id: str
    entry_index: int | None
    code: str


@dataclass
class CollectionResult:
    articles: list[Article] = field(default_factory=list)
    issues: list[CollectionIssue] = field(default_factory=list)
    successful_sources: int = 0
    failed_sources: int = 0

    @property
    def collection_succeeded(self) -> bool:
        # 로컬 fixture 수집은 모든 기록을 실패로 보는 엄격한 기준을 유지한다.
        return self.successful_sources > 0 and not self.issues


def local_path(root: Path, relative: str) -> Path:
    """샘플 manifest의 경로가 샘플 디렉터리 밖으로 나가지 않게 한다."""
    if not isinstance(relative, str) or not relative:
        raise ValueError("샘플 파일 경로가 필요합니다.")
    if urlsplit(relative).scheme or Path(relative).is_absolute():
        raise ValueError("URL·절대경로 대신 샘플의 상대경로가 필요합니다.")
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("샘플 폴더 밖의 파일은 읽을 수 없습니다.")
    return target


def published_time(value: str) -> datetime:
    # RSS pubDate 또는 offset이 있는 ISO 8601만 사용한다.
    # updated, 현재 시각, HTML 추정 날짜로 대체하지 않는다.
    try:
        return aware_utc(parsedate_to_datetime(value))
    except (ValueError, TypeError):
        return parse_timestamp(value)


def collect_local_samples(sample_root: Path, *, min_body_chars: int = 80) -> CollectionResult:
    """source manifest → RSS → 매핑된 HTML → 선별용 Article.

    최소 본문 길이는 로컬 개발용 제안이며 실제 공개 기준은 QA와 합의한다.
    샘플의 source_verified는 가상 fixture에 한해 사용한다.
    """
    if min_body_chars < 1:
        raise ValueError("최소 본문 길이는 양수여야 합니다.")
    root = sample_root.resolve()
    manifest = json.loads((root / "sources.json").read_text(encoding="utf-8"))
    if manifest.get("demo_only") is not True:
        raise ValueError("이 수집기는 demo_only 로컬 샘플만 처리합니다.")
    result = CollectionResult()
    seen = set()
    for source in manifest["sources"]:
        source_id = source["source_id"]
        if source.get("fixture_verified") is not True:
            result.issues.append(CollectionIssue(source_id, None, "UNVERIFIED_FIXTURE"))
            continue
        try:
            rss_bytes = local_path(root, source["rss_file"]).read_bytes()
            if len(rss_bytes) > 1_000_000:
                raise ValueError("샘플 RSS 제한 초과")
            # 문자열 URL이 아닌 bytes를 전달하므로 feedparser는 다운로드하지 않는다.
            feed = feedparser.parse(rss_bytes)
            if feed.get("bozo") or not feed.get("version") or len(feed.entries) > 100:
                raise ValueError("잘못된 RSS 샘플")
        except (OSError, ValueError, TypeError):
            result.issues.append(CollectionIssue(source_id, None, "FEED_INVALID_OR_UNREADABLE"))
            continue

        source_had_errors = False
        for index, entry in enumerate(feed.entries):
            try:
                url = canonical_url(entry["link"])
                title = entry["title"].strip()
                published = published_time(entry["published"])
                if not title:
                    raise ValueError("제목 누락")
            except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
                source_had_errors = True
                result.issues.append(CollectionIssue(source_id, index, "ENTRY_METADATA_INVALID"))
                continue
            if url in seen:
                continue
            try:
                html_path = local_path(root, source["html_files"][url])
                html = html_path.read_text(encoding="utf-8")
                if len(html.encode("utf-8")) > 1_000_000:
                    raise ValueError("샘플 HTML 제한 초과")
                body = trafilatura.extract(html, include_comments=False, include_tables=False,
                                          favor_precision=True)
                if not body or len(body.strip()) < min_body_chars:
                    raise ValueError("본문 부족")
            except Exception:
                # 추출 라이브러리의 기사 단위 실패도 기록 후 다음 기사로 진행한다.
                # 원문·개인정보·예외 메시지를 로그에 기록하지 않는다.
                source_had_errors = True
                result.issues.append(CollectionIssue(source_id, index, "BODY_INVALID_OR_UNREADABLE"))
                continue
            result.articles.append(Article(
                article_id="local_" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16],
                url=url, title=title, body=body.strip(), category=source["category"],
                published_at=published, source_verified=True, body_valid=True,
            ))
            seen.add(url)
        # 빈 정상 RSS는 성공. 기사 오류가 있었다면 정상 뉴스 없음으로 표시하지 않는다.
        if not source_had_errors:
            result.successful_sources += 1
    return result
