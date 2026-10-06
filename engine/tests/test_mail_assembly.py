import copy
from datetime import date
from email import policy
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from engine.card_render import card_data_hash
from engine.cards import assemble_cards
from engine.mail_assembly import InlineImage, NewsMailData, assemble_mail
from engine.demos.mail_demo import approved_images
from engine.tests import test_cards

PNG = b"\x89PNG\r\n\x1a\nlayout-test-only"


class MailAssemblyTests(unittest.TestCase):
    def setUp(self):
        fixture = test_cards.CardTests()
        fixture.setUp()
        self.data = assemble_cards(fixture.draft, fixture.current, fixture.rag,
                                   publishers=fixture.publishers, work_date_kst=fixture.day).card_data

    def mail(self, **options):
        values = dict(job_id="fixture-job", recipient_email="one@example.invalid",
                      sender_email="sender@example.invalid", scheduled_date_kst=date(2026, 10, 5),
                      card_data=self.data, selection_reason={"type": "category", "label": "관심 분야의 최신 기사"},
                      preview=True)
        values.update(options)
        return assemble_mail(NewsMailData(**values))[1]

    def test_eml_roundtrip_has_both_bodies_and_matching_cid(self):
        message = self.mail(inline_images=(InlineImage(1, PNG), InlineImage(2, PNG)))
        parsed = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
        plain = parsed.get_body(preferencelist=("plain",)).get_content()
        html = parsed.get_body(preferencelist=("html",)).get_content()
        images = [part for part in parsed.walk() if part.get_content_type() == "image/png"]
        self.assertEqual(len(images), 2)
        for image in images:
            self.assertIn("cid:" + image["Content-ID"].strip("<>"), html)
            self.assertEqual(image.get_content_disposition(), "inline")
            self.assertEqual(image.get_payload(decode=True), PNG)
        for card in (self.data["card1"], self.data["card2"]):
            for sentence in card["sentences"]:
                self.assertIn(sentence["text"], plain)
        self.assertIn("근거 보도일:", plain)
        self.assertIn("AI 편집", html)
        self.assertIn("텍스트로 읽기", html)
        self.assertEqual(parsed["To"], "one@example.invalid")

    def test_missing_image_still_has_complete_text(self):
        self.data["card2"] = None
        message = self.mail()
        html = message.get_body(preferencelist=("html",)).get_content()
        self.assertNotIn("<img", html)
        self.assertIn(self.data["card1"]["sentences"][0]["text"], html)
        self.assertEqual(len(list(message.iter_attachments())), 0)

    def test_personal_reason_is_escaped_and_recipients_are_separate(self):
        reason = {"type": "keyword", "label": "관심 키워드", "matched_keyword": "<script>"}
        one = self.mail(selection_reason=reason)
        two = self.mail(recipient_email="two@example.invalid", selection_reason=reason)
        html = one.get_body(preferencelist=("html",)).get_content()
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("one@example.invalid", html)
        self.assertNotEqual(one["Message-ID"], two["Message-ID"])
        self.assertEqual(two["To"], "two@example.invalid")

    def test_header_injection_and_unsafe_management_link_rejected(self):
        with self.assertRaises(ValueError):
            self.mail(recipient_email="one@example.invalid\r\nBcc: other@example.invalid")
        with self.assertRaises(ValueError):
            self.mail(subscription_management_url="javascript:alert(1)")

    def test_render_manifest_requires_matching_card_and_approved_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "card1.png").write_bytes(PNG)
            manifest = {"card_data_sha256": card_data_hash(self.data), "image_files": [str(root / "card1.png")],
                        "layout_result": [{"overflow": [], "externalRequests": 0, "fontLoaded": True,
                                           "sha256": hashlib.sha256(PNG).hexdigest()}]}
            (root / "render_result.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(len(approved_images(self.data, root)[0]), 1)
            changed = copy.deepcopy(self.data)
            changed["title"] = "다른 기사 제목"
            self.assertEqual(approved_images(changed, root)[0], ())
            manifest["image_files"] = [str(root / "../card1.png")]
            (root / "render_result.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(approved_images(self.data, root)[0], ())

    def test_modified_image_is_not_attached(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "card1.png").write_bytes(PNG)
            manifest = {"card_data_sha256": card_data_hash(self.data), "image_files": [str(root / "card1.png")],
                        "layout_result": [{"overflow": [], "externalRequests": 0, "fontLoaded": True, "sha256": "wrong"}]}
            (root / "render_result.json").write_text(json.dumps(manifest), encoding="utf-8")
            images, issues = approved_images(self.data, root)
            self.assertEqual(images, ())
            self.assertIn("CARD1_IMAGE_INVALID_TEXT_ONLY", issues)


if __name__ == "__main__":
    unittest.main()
