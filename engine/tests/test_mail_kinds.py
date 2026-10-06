from dataclasses import replace
from datetime import date, datetime, timedelta
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit
import unittest

from engine.mail_assembly import (
    EndNoticeMailData, NewsMailData, NoNewsMailData, WebMailLinks, assemble_mail,
)
from engine.selection import SelectionResult
from engine.tests import test_cards


class Links(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.urls = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.urls.append(dict(attrs)["href"])


class MailKindTests(unittest.TestCase):
    def setUp(self):
        fixture = test_cards.CardTests()
        fixture.setUp()
        self.card = fixture.result().card_data
        self.day = date(2026, 10, 18)
        self.web = WebMailLinks("https://briefing.example.invalid/")
        self.common = dict(job_id="job-fixture", sender_email="sender@example.invalid",
                           recipient_email="reader@example.invalid", scheduled_date_kst=self.day,
                           web_links=self.web, preview=False)

    def news(self, **changes):
        data = NewsMailData(**self.common, card_data=self.card,
                            selection_reason={"type": "category", "label": "관심 분야의 최신 기사"},
                            feedback_token="TOKEN_FIXTURE_ONLY")
        return replace(data, **changes)

    def no_news(self, **changes):
        data = NoNewsMailData(**self.common, collection_succeeded=True,
                              selection_result=SelectionResult("no_candidates", None, None))
        return replace(data, **changes)

    def end(self, **changes):
        data = EndNoticeMailData(**self.common, subscription_status="expired",
                                 start_date=self.day - timedelta(days=7), end_date_exclusive=self.day)
        return replace(data, **changes)

    def bodies(self, data):
        subject, message = assemble_mail(data)
        parsed = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
        self.assertEqual(str(parsed["Subject"]), subject)
        return parsed, parsed.get_body(preferencelist=("plain",)).get_content(), parsed.get_body(preferencelist=("html",)).get_content()

    def test_feedback_roundtrips_special_token_in_fragment_only(self):
        token = 'TOKEN_가+/=&?#"<fixture>'
        _, plain, html = self.bodies(self.news(feedback_token=token))
        links = [url for url in Links(html).urls if urlsplit(url).path == "/feedback"]
        self.assertEqual(len(links), 2)
        self.assertEqual({parse_qs(urlsplit(url).fragment)["rating"][0] for url in links}, {"up", "down"})
        for url in links:
            self.assertEqual(urlsplit(url).query, "")
            self.assertEqual(parse_qs(urlsplit(url).fragment)["t"], [token])
            self.assertIn(url, plain)
        self.assertIn("화면에서 제출해야", plain)
        self.assertNotIn("<fixture>", html)
        self.assertIn(self.web.management_url, Links(html).urls)

    def test_non_preview_news_requires_web_and_token(self):
        for changes in ({"web_links": None}, {"feedback_token": None}, {"feedback_token": ""}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                assemble_mail(self.news(**changes))

    def test_preview_compatibility_does_not_claim_connected_feedback(self):
        _, plain, html = self.bodies(self.news(preview=True, web_links=None, feedback_token=None))
        self.assertIn("로컬 검수용", plain)
        self.assertNotIn("/feedback", html)
        self.assertNotIn("로컬 검수용", self.bodies(self.news())[1])

    def test_only_approved_origin_and_management_page_are_accepted(self):
        for origin in ("http://example.com", "https://user:pass@example.com", "https://example.com/cancel",
                       "https://example.com?t=x", "https://example.com#t=x", "https://example.com/#",
                       "https://example.com\n", "https://example.com/feedback", "//example.com"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                WebMailLinks(origin)
        for link in ("https://other.example/manage", self.web.origin + "/cancel",
                     self.web.management_url + "?t=fixture", self.web.management_url + "#t=fixture"):
            with self.subTest(link=link), self.assertRaises(ValueError):
                assemble_mail(self.news(subscription_management_url=link))
        for link in ("https://example.com/cancel", "https://example.com/manage?t=x", "https://example.com/manage#t=x"):
            with self.subTest(link=link), self.assertRaises(ValueError):
                assemble_mail(self.news(preview=True, web_links=None, feedback_token=None,
                                        subscription_management_url=link))

    def test_invalid_token_errors_and_repr_do_not_expose_token_or_recipient(self):
        for token in ("secret\r\n", "secret token", "secret\x00", "x" * 4097):
            data = self.news(feedback_token=token)
            with self.assertRaises(ValueError) as error:
                assemble_mail(data)
            self.assertNotIn("secret", str(error.exception))
            self.assertNotIn(token, repr(data))
            self.assertNotIn(data.recipient_email, repr(data))

    def test_no_news_is_text_only_and_has_no_ai_or_feedback(self):
        message, plain, html = self.bodies(self.no_news())
        for body in (plain, html):
            self.assertIn("오늘은 새 브리핑이 없습니다", body)
            self.assertIn("2026.10.18", body)
            self.assertIn(self.web.management_url, body)
            self.assertNotIn("AI 편집", body)
            self.assertNotIn("/feedback", body)
            self.assertNotIn("TOKEN", body)
        self.assertNotIn("<img", html)
        self.assertEqual(len(list(message.iter_attachments())), 0)

    def test_failed_partial_or_ineligible_collection_cannot_be_no_news(self):
        cases = [self.no_news(collection_succeeded=False), self.no_news(collection_succeeded=1)]
        for status in ("selected", "ineligible", "generation_failed", "collection_failed"):
            cases.append(self.no_news(selection_result=SelectionResult(status, None, None)))
        cases.append(self.no_news(selection_result=SelectionResult("no_candidates", object(), None)))
        cases.append(self.no_news(selection_result=SelectionResult("no_candidates", None, {"type": "category"})))
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                assemble_mail(data)

    def test_all_subscription_periods_display_last_day_and_midnight(self):
        for duration in (7, 14, 28):
            start = self.day - timedelta(days=duration)
            message, plain, html = self.bodies(self.end(start_date=start))
            for body in (plain, html):
                self.assertIn(f"구독 시작일: {start:%Y.%m.%d}", body)
                self.assertIn("마지막 구독 날짜: 2026.10.17", body)
                self.assertIn("만료일: 2026.10.18 00:00 (한국 시간)", body)
                self.assertIn("재구독", body)
                self.assertNotIn("/feedback", body)
                self.assertNotIn("AI 편집", body)
            self.assertNotIn("<img", html)
            self.assertEqual(len(list(message.iter_attachments())), 0)

    def test_manual_cancellation_active_and_bad_periods_cannot_create_end_notice(self):
        for status in ("cancelled", "active", "deletion_requested", ""):
            with self.subTest(status=status), self.assertRaises(ValueError):
                assemble_mail(self.end(subscription_status=status))
        for duration in (0, 1, 6, 8, 30, -7):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                assemble_mail(self.end(start_date=self.day - timedelta(days=duration)))
        with self.assertRaises(ValueError):
            assemble_mail(self.end(scheduled_date_kst=self.day - timedelta(days=1)))

    def test_recipient_tokens_and_message_ids_do_not_cross_between_messages(self):
        one, one_plain, one_html = self.bodies(self.news(feedback_token="FIRST_FIXTURE"))
        two, two_plain, two_html = self.bodies(self.news(recipient_email="two@example.invalid", feedback_token="SECOND_FIXTURE"))
        self.assertNotEqual(one["Message-ID"], two["Message-ID"])
        self.assertEqual(two["To"], "two@example.invalid")
        for body in (one_plain, one_html):
            self.assertNotIn("SECOND_FIXTURE", body)
            self.assertNotIn("two@example.invalid", body)
        for body in (two_plain, two_html):
            self.assertNotIn("FIRST_FIXTURE", body)
            self.assertNotIn("reader@example.invalid", body)
        self.assertEqual(one["Message-ID"], self.bodies(self.news(feedback_token="FIRST_FIXTURE"))[0]["Message-ID"])

    def test_every_kind_requires_explicit_preview_flag(self):
        # 기본값으로 검수용 문구가 붙은 메일이 실제 발송되지 않도록 명시를 요구한다.
        for factory in (self.news, self.no_news, self.end):
            data = factory()
            values = {name: getattr(data, name) for name in data.__dataclass_fields__ if name != "preview"}
            with self.subTest(kind=factory.__name__), self.assertRaises(ValueError) as caught:
                assemble_mail(type(data)(**values))
            self.assertEqual(str(caught.exception), "PREVIEW_FLAG_REQUIRED")

    def test_every_kind_rejects_header_injection_and_ambiguous_date(self):
        for factory in (self.news, self.no_news, self.end):
            for changes in ({"recipient_email": "a@example.com\r\nBcc: b@example.com"},
                            {"job_id": "job\nprivate"}, {"scheduled_date_kst": datetime(2026, 10, 18)},
                            {"preview": "false"}):
                with self.subTest(kind=factory.__name__, changes=changes), self.assertRaises(ValueError):
                    assemble_mail(factory(**changes))


if __name__ == "__main__":
    unittest.main()
