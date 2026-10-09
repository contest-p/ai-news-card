import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from engine.card_images import approved_images, render_with_fallback
from engine.card_render import card_data_hash, resolve_font_path
from engine.cards import assemble_cards
from engine.tests import test_cards

PNG = b"\x89PNG\r\n\x1a\n" + b"fixture-image"


def fake_renderer(failures):
    """failures회 실패한 뒤 실제 렌더러와 같은 형식의 결과 파일을 쓴다."""
    calls = []

    def render(data, output_dir, *, font_path):
        calls.append(output_dir)
        if len(calls) <= failures:
            raise RuntimeError("renderer crashed")
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        files, layouts = [], []
        for number in (1, 2):
            if number == 2 and data["card2"] is None:
                continue
            path = output_dir / f"card{number}.png"
            path.write_bytes(PNG)
            files.append(str(path))
            layouts.append({"overflow": [], "externalRequests": 0, "fontLoaded": True,
                            "sha256": hashlib.sha256(PNG).hexdigest()})
        manifest = {"card_data_sha256": card_data_hash(data), "image_files": files, "layout_result": layouts}
        (output_dir / "render_result.json").write_text(json.dumps(manifest), encoding="utf-8")
        return manifest
    return render, calls


class CardImageTests(unittest.TestCase):
    def setUp(self):
        fixture = test_cards.CardTests()
        fixture.setUp()
        self.data = assemble_cards(fixture.draft, fixture.current, fixture.rag,
                                   publishers=fixture.publishers, work_date_kst=fixture.day).card_data
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_first_failure_is_retried_once(self):
        render, calls = fake_renderer(failures=1)
        images, issues = render_with_fallback(self.data, self.root, font_path="font.ttf", renderer=render)
        self.assertEqual(len(calls), 2)
        self.assertEqual([image.card_number for image in images], [1, 2])
        self.assertEqual(issues, ["CARD_IMAGE_RETRIED"])

    def test_two_failures_switch_to_full_text_without_exception(self):
        render, calls = fake_renderer(failures=5)
        images, issues = render_with_fallback(self.data, self.root, font_path="font.ttf", renderer=render)
        self.assertEqual(len(calls), 2)  # P-16: 최초 + 1회
        self.assertEqual(images, ())
        self.assertIn("CARD_IMAGE_FAILED_TEXT_ONLY", issues)

    def test_image_for_other_card_data_is_not_attached(self):
        render, _ = fake_renderer(failures=0)
        render(self.data, self.root / "attempt-1", font_path="font.ttf")
        self.data["title"] = "다른 제목"
        images, issues = approved_images(self.data, self.root / "attempt-1")
        self.assertEqual(images, ())
        self.assertEqual(issues, ["IMAGE_CARD_INPUT_MISMATCH_TEXT_ONLY"])

    def test_bundled_font_is_the_default_without_os_font_installation(self):
        from engine.card_render import ROOT
        self.assertEqual(resolve_font_path({}), ROOT / "frontend/assets/fonts/NanumGothic-Regular.ttf")

    def test_font_path_uses_environment_then_candidates(self):
        font = self.root / "card.ttf"
        font.write_bytes(b"font")
        self.assertEqual(resolve_font_path({"CARD_FONT_PATH": str(font)}), font)
        self.assertEqual(resolve_font_path({}, candidates=[self.root / "missing.ttf", font]), font)
        with self.assertRaises(FileNotFoundError):
            resolve_font_path({"CARD_FONT_PATH": str(self.root / "missing.ttf")})
        with self.assertRaises(FileNotFoundError):
            resolve_font_path({}, candidates=[self.root / "missing.ttf"])


if __name__ == "__main__":
    unittest.main()
