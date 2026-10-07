import copy
import unittest

from engine.card_render import validate_render_data
from engine.cards import assemble_cards
from engine.tests import test_cards


class CardRenderTests(unittest.TestCase):
    def setUp(self):
        fixture = test_cards.CardTests()
        fixture.setUp()
        self.data = assemble_cards(fixture.draft, fixture.current, fixture.rag,
                                   publishers=fixture.publishers, work_date_kst=fixture.day).card_data

    def test_validated_card_contract_excludes_review_fields(self):
        self.data["title"] = '<script>alert("test")</script>'
        self.data["card1"]["sentences"][0]["evidence_quote"] = "REVIEW_ONLY_MARKER"
        self.assertIn(self.data["article_id"], validate_render_data(self.data))

    def test_maximum_lengths_preserved_and_extra_rejected(self):
        self.data["title"] = "제" * 60
        self.data["card1"]["sentences"] = [{**self.data["card1"]["sentences"][0], "text": "가" * 400}]
        validate_render_data(self.data)
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
        validate_render_data(self.data)
        sentence["as_of"] = None
        validate_render_data(self.data)

    def test_null_card2_is_valid_and_can_be_omitted(self):
        self.data["card2"] = None
        validate_render_data(self.data)


if __name__ == "__main__":
    unittest.main()
