"""정책 경계와 선별 결과를 외부 서비스 없이 검증한다."""

from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path
import unittest

from engine.selection import (
    Article, CollectionUnavailable, DeliveryHistory,
    canonical_url, parse_timestamp, select_article,
)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        fixture = json.loads((Path(__file__).parents[1] / "samples" / "selection.json").read_text("utf-8"))
        self.snapshot = fixture["subscription_snapshot"]
        self.scheduled = parse_timestamp(self.snapshot["scheduled_at"])
        self.now = self.scheduled + timedelta(minutes=7)
        self.articles = [Article(**{**row, "published_at": parse_timestamp(row["published_at"])})
                         for row in fixture["articles"]]
        self.history = [DeliveryHistory(**{**row, "attempted_at": parse_timestamp(row["attempted_at"])})
                        for row in fixture["history"]]

    def select(self, articles=None, history=None, **options):
        return select_article(self.snapshot,
                              self.articles if articles is None else articles,
                              self.history if history is None else history,
                              now=options.pop("now", self.now),
                              collection_succeeded=options.pop("collection_succeeded", True),
                              **options)

    def test_keyword_beats_newer_category_article(self):
        result = self.select()
        self.assertEqual(result.article.article_id, "fixture_keyword")
        self.assertEqual(result.selection_reason["matched_keyword"], "금리")

    def test_no_keywords_and_no_matches_fall_back_to_category(self):
        for keywords in ([], ["없는검색어"]):
            with self.subTest(keywords=keywords):
                self.snapshot["keywords"] = keywords
                result = self.select()
                self.assertEqual(result.article.article_id, "fixture_latest")
                self.assertEqual(result.selection_reason["type"], "category")

    def test_or_matching_and_nfkc_casefold(self):
        self.snapshot["keywords"] = ["없는검색어", "ＡＩ"]
        article = replace(self.articles[0], title="가상 ai 기사")
        self.assertEqual(self.select([article], []).selection_reason["matched_keyword"], "ＡＩ")

    def test_keywords_do_not_escape_interest_categories(self):
        other = replace(self.articles[0], category="politics")
        self.assertEqual(self.select([other], []).status, "no_candidates")

    def test_publication_window_uses_scheduled_time_not_retry_time(self):
        for delta, expected in ((-24, "selected"), (-24.001, "no_candidates"),
                                (0, "no_candidates"), (1, "no_candidates")):
            with self.subTest(delta=delta):
                article = replace(self.articles[0], published_at=self.scheduled + timedelta(hours=delta))
                self.assertEqual(self.select([article], []).status, expected)

    def test_sent_and_unknown_block_including_seven_day_boundary(self):
        article = self.articles[0]
        for status in ("sent", "unknown"):
            with self.subTest(status=status):
                history = [DeliveryHistory(article.url + "#top", status, self.scheduled - timedelta(days=7))]
                self.assertEqual(self.select([article], history).status, "no_candidates")

    def test_old_history_and_failed_delivery_do_not_block(self):
        article = self.articles[0]
        for status, when in (("sent", self.scheduled - timedelta(days=7, seconds=1)),
                             ("failed", self.now)):
            with self.subTest(status=status):
                self.assertEqual(self.select([article], [DeliveryHistory(article.url, status, when)]).status,
                                 "selected")

    def test_same_job_already_sent_during_retry_blocks(self):
        article = self.articles[0]
        history = [DeliveryHistory(article.url, "sent", self.now)]
        self.assertEqual(self.select([article], history).status, "no_candidates")

    def test_latest_then_id_is_independent_of_input_order(self):
        a = replace(self.articles[0], article_id="a", url="https://example.com/a")
        b = replace(a, article_id="b", url="https://example.com/b")
        for candidates in ([a, b], [b, a]):
            self.assertEqual(self.select(candidates, []).article.article_id, "a")

    def test_duplicate_url_keeps_latest_before_keyword_matching(self):
        older = self.articles[0]
        newer = replace(older, article_id="new", title="경제 행사", body="경제 행사 안내",
                        published_at=older.published_at + timedelta(minutes=1), url=older.url + "#top")
        result = self.select([older, newer], [])
        self.assertEqual(result.article.article_id, "new")
        self.assertEqual(result.selection_reason["type"], "category")

    def test_bad_or_unverified_articles_do_not_block_valid_candidate(self):
        valid = self.articles[0]
        invalid = [replace(valid, source_verified=False), replace(valid, body_valid=False),
                   replace(valid, body=" "), replace(valid, url="javascript:alert(1)"),
                   replace(valid, published_at=datetime(2026, 10, 17))]
        self.assertEqual(self.select(invalid + [valid], []).article, valid)

    def test_collection_failure_is_not_normal_no_news(self):
        with self.assertRaises(CollectionUnavailable):
            self.select([], [], collection_succeeded=False)
        self.assertEqual(self.select([], []).status, "no_candidates")

    def test_partial_collection_failure_can_use_existing_valid_candidate(self):
        self.assertEqual(self.select(collection_succeeded=False).status, "selected")

    def test_cancelled_expired_and_deadline_are_ineligible(self):
        for status in ("cancelled", "expired"):
            self.snapshot["status"] = status
            self.assertEqual(self.select().status, "ineligible")
        self.snapshot["status"] = "active"
        self.assertEqual(self.select(now=self.scheduled - timedelta(seconds=1)).status, "ineligible")
        self.assertEqual(self.select(now=parse_timestamp(self.snapshot["deadline_at"])).status, "ineligible")
        self.assertEqual(self.select(now=self.scheduled).status, "selected")

    def test_naive_timestamps_and_unknown_status_fail_closed(self):
        with self.assertRaises(ValueError):
            self.select(now=datetime(2026, 10, 18))
        self.snapshot["status"] = "typo"
        with self.assertRaises(ValueError):
            self.select()

    def test_keyword_limits(self):
        for keywords in (["x"] * 6, ["x" * 21], [""], [" space "], "금리"):
            self.snapshot["keywords"] = keywords
            with self.subTest(keywords=keywords), self.assertRaises(ValueError):
                self.select()

    def test_url_normalization_is_conservative(self):
        self.assertEqual(canonical_url("HTTPS://EXAMPLE.COM:443/a?id=1#top"),
                         "https://example.com/a?id=1")
        self.assertNotEqual(canonical_url("https://example.com/a?id=1"),
                            canonical_url("https://example.com/a?id=2"))
        self.assertNotEqual(canonical_url("https://example.com/a"),
                            canonical_url("https://example.com/a/"))
        with self.assertRaises(ValueError):
            canonical_url("https://name:password@example.com/a")


if __name__ == "__main__":
    unittest.main()
