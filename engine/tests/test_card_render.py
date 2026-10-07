import copy
import unittest

from engine.card_render import build_html, validate_render_data
from engine.cards import assemble_cards
from engine.tests import test_cards


class CardRenderTests(unittest.TestCase):
    def setUp(self):
        fixture = test_cards.CardTests()
        fixture.setUp()
        self.data = assemble_cards(fixture.draft, fixture.current, fixture.rag,
                                   publishers=fixture.publishers, work_date_kst=fixture.day).card_data

    def test_html_escapes_news_and_hides_review_fields(self):
        self.data["title"] = '<script>alert("test")</script>'
        self.data["card1"]["sentences"][0]["evidence_quote"] = "REVIEW_ONLY_MARKER"
        html = build_html(self.data, 1, font_bytes=b"font")
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertNotIn("REVIEW_ONLY_MARKER", html)
        self.assertIn("AI 생성", html)
        self.assertIn('data-card-number="1"', html)
        self.assertNotIn('data-card-number="2"', html)

    def test_maximum_lengths_preserved_and_extra_rejected(self):
        self.data["title"] = "제" * 60
        self.data["card1"]["sentences"] = [{**self.data["card1"]["sentences"][0], "text": "가" * 400}]
        html = build_html(self.data, 1, font_bytes=b"font")
        self.assertIn("가" * 400, html)
        self.data["card1"]["sentences"][0]["text"] += "가"
        with self.assertRaises(ValueError):
            validate_render_data(self.data)

    def test_unknown_source_and_unsafe_url_are_rejected(self):
        bad = copy.deepcopy(self.data)
        bad["card1"]["sentences"][0]["source_article_id"] = "unknown"
        with self.assertRaises(ValueError):
            validate_render_data(bad)
        self.data["sources"][0]["url"] = "javascript:alert(1)"
        with self.assertRaises(ValueError):
            validate_render_data(self.data)

    def test_past_uses_fact_date_or_explicit_report_date(self):
        sentence = next(row for row in self.data["card2"]["sentences"] if row["temporal_role"] == "past")
        sentence["as_of"] = "2026-10-01"
        self.assertIn("2026.10.01 기준", build_html(self.data, 2, font_bytes=b"font"))
        sentence["as_of"] = None
        self.assertIn("보도에 따르면", build_html(self.data, 2, font_bytes=b"font"))

    def test_null_card2_is_not_rendered(self):
        self.data["card2"] = None
        validate_render_data(self.data)
        with self.assertRaises(ValueError):
            build_html(self.data, 2, font_bytes=b"font")

    def test_utc_evening_is_displayed_as_next_kst_date(self):
        self.data["published_at"] = "2026-10-05T21:00:00Z"
        current = next(row for row in self.data["sources"] if row["article_id"] == self.data["article_id"])
        current["published_at"] = self.data["published_at"]
        html = build_html(self.data, 1, font_bytes=b"font")
        self.assertIn("기사 게시 · 2026.10.06", html)
        self.assertIn("2026.10.06 보도", html)


if __name__ == "__main__":
    unittest.main()
