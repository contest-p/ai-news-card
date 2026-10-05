from datetime import date, datetime, timezone
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from engine.collection import collect_local_samples, local_path, published_time
from engine.gateway import SampleEngineGateway
from engine.selection import CollectionUnavailable, DeliveryHistory, parse_timestamp, select_article


SAMPLES = Path(__file__).parents[1] / "samples"


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "collection"
        shutil.copytree(SAMPLES / "collection", self.root)

    def test_local_collection_selection_and_no_network(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")), \
                patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
            result = collect_local_samples(self.root)
        self.assertEqual(len(result.articles), 2)
        self.assertEqual(len({article.url for article in result.articles}), 2)
        self.assertIn("ENTRY_METADATA_INVALID", [issue.code for issue in result.issues])
        self.assertFalse(result.collection_succeeded)  # 부분 실패가 숨겨지지 않는다.
        self.assertIn("실제 보도가 아닌", result.articles[0].body)
        fixture = json.loads((SAMPLES / "selection.json").read_text("utf-8"))
        history = [DeliveryHistory(**{**row, "attempted_at": parse_timestamp(row["attempted_at"])})
                   for row in fixture["history"]]
        selected = select_article(fixture["subscription_snapshot"], result.articles, history,
                                  now=parse_timestamp(fixture["now"]),
                                  collection_succeeded=result.collection_succeeded)
        self.assertEqual(selected.selection_reason["matched_keyword"], "금리")

    def test_normal_empty_feed_is_success(self):
        (self.root / "economy.xml").write_text(
            '<rss version="2.0"><channel><title>empty</title><link>https://example.com/</link>'
            '<description>empty fixture</description></channel></rss>', encoding="utf-8")
        result = collect_local_samples(self.root)
        self.assertEqual(result.articles, [])
        self.assertTrue(result.collection_succeeded)

    def test_bad_feed_is_failure_and_not_no_news(self):
        (self.root / "economy.xml").write_text("<rss><broken>", encoding="utf-8")
        result = collect_local_samples(self.root)
        self.assertFalse(result.collection_succeeded)
        fixture = json.loads((SAMPLES / "selection.json").read_text("utf-8"))
        with self.assertRaises(CollectionUnavailable):
            select_article(fixture["subscription_snapshot"], result.articles, [],
                           now=parse_timestamp(fixture["now"]),
                           collection_succeeded=result.collection_succeeded)

    def test_missing_body_does_not_block_other_article(self):
        (self.root / "rate.html").unlink()
        result = collect_local_samples(self.root)
        self.assertEqual(len(result.articles), 1)
        self.assertEqual(result.articles[0].url, "https://example.com/news/economy")
        self.assertIn("BODY_INVALID_OR_UNREADABLE", [issue.code for issue in result.issues])

    def test_empty_extracted_body_is_excluded(self):
        with patch("engine.collection.trafilatura.extract", return_value=None):
            result = collect_local_samples(self.root)
        self.assertEqual(result.articles, [])
        self.assertFalse(result.collection_succeeded)

    def test_unverified_fixture_is_not_selected(self):
        path = self.root / "sources.json"
        manifest = json.loads(path.read_text("utf-8"))
        manifest["sources"][0]["fixture_verified"] = False
        path.write_text(json.dumps(manifest), encoding="utf-8")
        result = collect_local_samples(self.root)
        self.assertEqual(result.articles, [])
        self.assertEqual(result.issues[0].code, "UNVERIFIED_FIXTURE")

    def test_local_only_and_path_boundaries(self):
        with self.assertRaises(ValueError):
            local_path(self.root, "../outside.html")
        with self.assertRaises(ValueError):
            local_path(self.root, "https://example.com/page")
        path = self.root / "sources.json"
        manifest = json.loads(path.read_text("utf-8"))
        manifest["demo_only"] = False
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(ValueError):
            collect_local_samples(self.root)

    def test_timezone_required_and_utc_conversion(self):
        self.assertEqual(published_time("Sun, 18 Oct 2026 06:00:00 +0900"),
                         datetime(2026, 10, 17, 21, tzinfo=timezone.utc))
        with self.assertRaises(ValueError):
            published_time("Sun, 18 Oct 2026 06:00:00")

    def test_gateway_returns_isolated_sample_and_checks_identity(self):
        fixture = json.loads((SAMPLES / "selection.json").read_text("utf-8"))
        gateway = SampleEngineGateway({**fixture, "delivery_eligible_fixture": False})
        subscription_id = fixture["subscription_snapshot"]["subscription_id"]
        snapshot = gateway.get_subscription_snapshot(subscription_id, date(2026, 10, 18))
        snapshot["keywords"].clear()
        self.assertEqual(gateway.get_subscription_snapshot(subscription_id, date(2026, 10, 18))["keywords"],
                         ["금리"])
        self.assertFalse(gateway.check_delivery_eligibility(subscription_id, datetime.now(timezone.utc)))
        with self.assertRaises(LookupError):
            gateway.get_subscription_snapshot("other", date(2026, 10, 18))
        with self.assertRaises(LookupError):
            gateway.get_subscription_snapshot(subscription_id, date(2026, 10, 19))


if __name__ == "__main__":
    unittest.main()
