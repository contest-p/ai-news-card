"""최대 입력과 과거 날짜를 가상 데이터로 이미지 확인. 실제 뉴스·AI 결과가 아니다."""

import copy
import json
import os
from pathlib import Path
import sys

from engine.card_render import ROOT, render_card


def layout_fixture():
    title = ("최대 길이 제목 표시 확인을 위한 가상 기사 표본 " * 3)[:60]
    text = ("최대 입력의 한글과 English 문장, 긴 문자열 표시를 확인하는 가상 데이터입니다. " * 10)[:400]
    sentence = {"text": text, "source_article_id": "layout-current", "evidence_quote": text,
                "as_of": None, "temporal_role": "current", "numbers": []}
    term = {"term": "가상 용어", "definition": ("화면 크기 확인을 위한 가상 용어 설명입니다. " * 5)[:100],
            "source_article_id": "layout-current", "evidence_quote": "가상 근거"}
    data = {"schema_version": "1.0", "article_id": "layout-current", "title": title,
            "published_at": "2026-10-05T06:00:00Z", "ai_generated": True,
            "card1": {"sentences": [sentence], "terms": [term, {**term, "term": "또 다른 가상 용어"}]},
            "card2": {"sentences": [
                {**sentence, "text": text[:200], "source_article_id": "layout-past", "temporal_role": "past", "as_of": "2026-10-01"},
                {**sentence, "text": text[200:], "source_article_id": "layout-past", "temporal_role": "past"}], "terms": []},
            "sources": [
                {"article_id": "layout-current", "publisher": "가상 테스트 출처", "url": "https://example.com/layout/current", "published_at": "2026-10-05T06:00:00Z"},
                {"article_id": "layout-past", "publisher": "가상 과거 출처", "url": "https://example.com/layout/past", "published_at": "2026-10-03T03:00:00Z"}]}
    return {"status": "completed", "mode": "layout_fixture_only", "result": {"status": "ready_for_review", "card_data": data}}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    root = ROOT / ".engine-local/render-layout-check"
    font = Path(os.environ.get("CARD_FONT_PATH", "C:/Windows/Fonts/NotoSansKR-VF.ttf"))
    fixture = layout_fixture()
    results = {"fixture_only": True, "maximum": render_card(fixture, root / "maximum", font_path=font)}
    long = copy.deepcopy(fixture)
    long["result"]["card_data"]["card1"]["sentences"][0]["text"] = "ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 15
    long["result"]["card_data"]["card2"] = None
    results["long_english"] = render_card(long, root / "long-english", font_path=font)
    (root / "checks.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
