import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from engine.live_collection import NewsSource, allowed_url, collect_live_sources, load_sources, live_published_time


SOURCE = NewsSource("test", "테스트", "https://example.com/feed", "politics", "ko", ("example.com",))
HTML = ("<html><body><article><h1>기사 표본</h1><p>" + "검증 가능한 기사 본문입니다. " * 40
        + "</p></article></body></html>").encode()


def item(url="https://example.com/news/1", date="Mon, 05 Oct 2026 12:00:00 +0900", category=""):
    return (f"<item><title>기사</title><link>{url}</link><pubDate>{date}</pubDate>"
            f"{category}</item>")


def feed(items):
    return ("<rss version='2.0'><channel><title>표본</title><link>https://example.com</link>"
            "<description>표본</description>" + items + "</channel></rss>").encode()


class LiveCollectionTests(unittest.TestCase):
    def collect(self, payload, *, source=SOURCE, fetch_body=lambda url, source: HTML):
        def fetch(url, current):
            return payload if url == current.url else fetch_body(url, current)
        return collect_live_sources(sources=[source], fetch=fetch, max_entries=10)

    def test_real_extractor_duplicate_and_storage_compatibility(self):
        from datetime import datetime, timezone, timedelta
        from engine.article_store import InMemoryArticleRepository
        result = self.collect(feed(item() + item()))
        self.assertTrue(result.collection_succeeded)
        self.assertEqual(len(result.articles), 1)
        article = result.articles[0]
        self.assertEqual(article.published_at.hour, 3)
        self.assertIn("검증 가능한 기사 본문", article.body)
        store = InMemoryArticleRepository()
        now = datetime.now(timezone.utc)
        self.assertEqual(store.save(article, observed_at=now).action, "inserted")
        self.assertEqual(store.save(article, observed_at=now + timedelta(seconds=1)).action, "unchanged")

    def test_missing_date_does_not_use_now_or_stop_next_article(self):
        result = self.collect(feed(item(date="") + item(url="https://example.com/news/2")))
        self.assertEqual(len(result.articles), 1)
        # 게시 시각 누락은 기사 단위 정책 제외다. 소스 장애로 보지 않는다.
        self.assertTrue(result.collection_succeeded)
        self.assertEqual(result.issues[0].code, "ENTRY_PUBLISHED_INVALID")
        self.assertEqual(result.source_reports[0]["status"], "complete_with_exclusions")

    def test_policy_exclusions_keep_normal_no_news_possible(self):
        general = NewsSource("mk", "전체", SOURCE.url, None, "ko", SOURCE.hosts)
        result = self.collect(feed(item(category="<category>헤드라인</category>")), source=general)
        self.assertEqual(result.articles, [])
        self.assertEqual(result.issues[0].code, "CATEGORY_UNMAPPED")
        self.assertTrue(result.collection_succeeded)

    def test_unknown_qa_source_fails_only_that_source(self):
        unknown = NewsSource("unknown", "새 소스", "https://example.com/new", None, "ko", ())
        result = collect_live_sources(sources=[unknown, SOURCE],
                                      fetch=lambda url, source: feed(item()) if url == source.url else HTML)
        self.assertEqual(len(result.articles), 1)
        self.assertEqual(result.source_reports[0]["issue_codes"], ["SOURCE_METADATA_MISSING"])
        self.assertFalse(result.collection_succeeded)

    def test_collection_budget_stops_and_is_not_success(self):
        ticks = iter([0, 0, 100, 100, 100, 100])
        result = collect_live_sources(sources=[SOURCE], max_entries=5, deadline=50,
                                      clock=lambda: next(ticks),
                                      fetch=lambda url, source: feed(item() + item(url="https://example.com/news/2"))
                                      if url == source.url else HTML)
        self.assertIn("COLLECTION_BUDGET_EXCEEDED", [issue.code for issue in result.issues])
        self.assertFalse(result.collection_succeeded)

    def test_load_sources_skips_unmapped_names_without_crashing(self):
        rows = [{"name": "BBC 뉴스 (글로벌)", "url": "http://feeds.bbci.co.uk/news/rss.xml"},
                {"name": "새 언론사", "url": "https://example.org/rss"}]
        with patch("engine.live_collection.qa_source_rows", return_value=rows):
            sources = load_sources()
        self.assertEqual([row.source_id for row in sources], ["bbc", "unmapped:새 언론사"])
        self.assertEqual(sources[1].hosts, ())

    def test_body_403_does_not_use_rss_summary_and_other_source_continues(self):
        other = NewsSource("other", "다른 소스", "https://example.com/other", "world", "en", SOURCE.hosts)
        def fetch(url, source):
            if url == source.url:
                return feed(item())
            if source.source_id == "test":
                raise HTTPError(url, 403, "blocked", {}, None)
            return HTML
        result = collect_live_sources(sources=[SOURCE, other], fetch=fetch)
        self.assertEqual(len(result.articles), 1)
        self.assertEqual(result.source_reports[0]["issue_codes"], ["BODY_HTTP_403"])
        self.assertFalse(result.collection_succeeded)

    def test_general_feed_requires_unambiguous_category(self):
        general = NewsSource("mk", "전체", SOURCE.url, None, "ko", SOURCE.hosts)
        result = self.collect(feed(item(category="<category>헤드라인</category>") +
                                   item(url="https://example.com/news/2", category="<category>경제</category>")),
                              source=general)
        self.assertEqual([row.category for row in result.articles], ["economy"])
        self.assertEqual(result.issues[0].code, "CATEGORY_UNMAPPED")

    def test_mk_colon_timezone_and_publisher_url_category(self):
        general = NewsSource("mk", "전체", SOURCE.url, None, "ko", SOURCE.hosts)
        result = self.collect(feed(item(url="https://example.com/news/politics/123",
                                       date="Mon, 05 Oct 2026 12:00:00 +09:00",
                                       category="<category>헤드라인</category>")), source=general)
        self.assertEqual(result.articles[0].category, "politics")
        self.assertEqual(result.articles[0].published_at.hour, 3)
        with self.assertRaises(ValueError):
            live_published_time("Mon, 05 Oct 2026 12:00:00")

    def test_updated_is_not_treated_as_publication(self):
        payload = feed(item().replace("<pubDate>Mon, 05 Oct 2026 12:00:00 +0900</pubDate>",
                                      "<lastBuildDate>Mon, 05 Oct 2026 12:00:00 +0900</lastBuildDate>"))
        result = self.collect(payload)
        self.assertEqual(result.articles, [])
        self.assertEqual(result.source_reports[0]["valid_bodies"], 1)
        self.assertEqual(result.issues[0].code, "ENTRY_PUBLISHED_INVALID")

    def test_bad_and_empty_feeds_are_distinguished(self):
        self.assertFalse(self.collect(b"<rss><broken>").collection_succeeded)
        self.assertTrue(self.collect(feed("")).collection_succeeded)

    def test_domains_and_bounds(self):
        for url in ("https://example.com.evil.test/", "https://localhost/", "file:///etc/passwd"):
            with self.assertRaises(ValueError):
                allowed_url(url, SOURCE)
        with self.assertRaises(ValueError):
            collect_live_sources(max_entries=0)
        result = self.collect(feed(item(url="https://evil.test/news")))
        self.assertEqual(result.articles, [])

    def test_qa_urls_are_shared(self):
        sources = load_sources()
        self.assertEqual(len(sources), 4)
        self.assertEqual({row.source_id for row in sources}, {"bbc", "sbs", "mk", "khan"})

    def test_empty_body_is_not_candidate(self):
        with patch("engine.live_collection.trafilatura.extract", return_value=None):
            result = self.collect(feed(item()))
        self.assertEqual(result.articles, [])
        self.assertEqual(result.issues[0].code, "BODY_INVALID")


if __name__ == "__main__":
    unittest.main()
