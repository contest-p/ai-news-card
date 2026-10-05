"""검사 완료 카드 JSON → 임시 HTML 템플릿 → PNG. API·DB·메일 호출 없음."""

import argparse
import base64
from datetime import timedelta, timezone
from html import escape
import hashlib
import json
import os
from pathlib import Path
import shutil
from string import Template
import subprocess
import sys
from urllib.parse import urlsplit

from engine.cards import fields, string
from engine.selection import canonical_url, parse_timestamp

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = Path(__file__).parent / "templates" / "card_preview.html"
TEMPLATE_VERSION = "engine-preview-v1"
KST = timezone(timedelta(hours=9))


def card_data_hash(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate_render_data(data):
    """표시 계약 검사. 원문 근거 검사는 생성 단계의 ready_for_review 결과를 사용."""
    fields(data, ("schema_version", "article_id", "title", "published_at", "card1", "card2", "sources", "ai_generated"))
    if data["schema_version"] != "1.0" or data["ai_generated"] is not True:
        raise ValueError("CARD_SCHEMA_INVALID")
    string(data["title"], 60)
    string(data["article_id"])
    parse_timestamp(data["published_at"])
    sources = {}
    if not isinstance(data["sources"], list) or not data["sources"]:
        raise ValueError("SOURCES_REQUIRED")
    for source in data["sources"]:
        fields(source, ("article_id", "publisher", "url", "published_at"))
        identity = string(source["article_id"])
        if identity in sources:
            raise ValueError("DUPLICATE_SOURCE")
        string(source["publisher"], 120)
        canonical_url(source["url"])
        parse_timestamp(source["published_at"])
        sources[identity] = source
    if data["article_id"] not in sources:
        raise ValueError("CURRENT_SOURCE_MISSING")
    if sources[data["article_id"]]["published_at"] != data["published_at"]:
        raise ValueError("CURRENT_DATE_MISMATCH")
    for index, card in enumerate((data["card1"], data["card2"])):
        if index == 1 and card is None:
            continue
        fields(card, ("sentences", "terms"))
        if (type(card["sentences"]) is not list or not card["sentences"]
                or type(card["terms"]) is not list or len(card["terms"]) > 2
                or (index == 1 and card["terms"])):
            raise ValueError("CARD_CONTENT_INVALID")
        count, past_count = 0, 0
        for sentence in card["sentences"]:
            fields(sentence, ("text", "source_article_id", "evidence_quote", "as_of", "temporal_role", "numbers"))
            count += len(string(sentence["text"]))
            identity = sentence["source_article_id"]
            if identity not in sources:
                raise ValueError("UNKNOWN_SOURCE")
            role = "current" if identity == data["article_id"] else "past"
            if sentence["temporal_role"] != role or (index == 0 and role != "current"):
                raise ValueError("TEMPORAL_ROLE_INVALID")
            past_count += role == "past"
            if sentence["as_of"] is not None:
                from datetime import date
                value = sentence["as_of"]
                if date.fromisoformat(value).isoformat() != value:
                    raise ValueError("FACT_DATE_INVALID")
        if count > 400 or (index == 1 and not past_count):
            raise ValueError("CARD_LENGTH_OR_BACKGROUND_INVALID")
        for term in card["terms"]:
            fields(term, ("term", "definition", "source_article_id", "evidence_quote"))
            string(term["term"], 100)
            string(term["definition"], 100)
            if term["source_article_id"] not in sources:
                raise ValueError("UNKNOWN_TERM_SOURCE")
    return sources


def kst_time(value):
    return parse_timestamp(value).astimezone(KST).strftime("%Y.%m.%d %H:%M KST")


def build_html(data, index, *, font_bytes):
    sources = validate_render_data(data)
    card = data["card1" if index == 1 else "card2"]
    if card is None:
        raise ValueError("CARD_OMITTED")
    paragraphs = []
    used = {data["article_id"]}
    for sentence in card["sentences"]:
        identity = sentence["source_article_id"]
        used.add(identity)
        label = ""
        if sentence["temporal_role"] == "past":
            label = (f"기준일 · {sentence['as_of']}" if sentence["as_of"] else
                     f"근거 보도일 · {kst_time(sources[identity]['published_at'])}")
        paragraphs.append('<p class="sentence">' +
                          (f'<span class="fact-date">{escape(label)}</span>' if label else "") +
                          escape(sentence["text"]) + "</p>")
    term_html = []
    for term in card["terms"]:
        used.add(term["source_article_id"])
        term_html.append(f'<p class="term"><strong>{escape(term["term"])}</strong> · {escape(term["definition"])}</p>')
    source_html = []
    for identity in sorted(used):
        source = sources[identity]
        url = canonical_url(source["url"])
        source_html.append(f'{escape(source["publisher"])} · {escape(kst_time(source["published_at"]))}<br>'
                           f'<a href="{escape(url, quote=True)}">원문 보기 · {escape(urlsplit(url).hostname)}</a>')
    return Template(TEMPLATE.read_text("utf-8")).substitute(
        page_title=escape(data["title"]), headline=escape(data["title"]),
        published=escape(kst_time(data["published_at"])), font_data=base64.b64encode(font_bytes).decode(),
        card_label="01 / 핵심 뉴스" if index == 1 else "02 / 배경 이해",
        sentences="\n".join(paragraphs), terms='<aside class="terms">' + "".join(term_html) + "</aside>" if term_html else "",
        sources="<br>".join(source_html), page_number=f"{index:02d}")


def render_card(result, output_dir, *, font_path, node="node"):
    if (result.get("status") != "completed" or result.get("result", {}).get("status") != "ready_for_review"):
        raise ValueError("VALIDATED_CARD_REQUIRED")
    data = result["result"]["card_data"]
    validate_render_data(data)
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    font_bytes = Path(font_path).read_bytes()
    images, layouts = [], []
    for index in (1, 2):
        if index == 2 and data["card2"] is None:
            continue
        html_path = output_dir / f"card{index}.html"
        png_path = output_dir / f"card{index}.png"
        html_path.write_text(build_html(data, index, font_bytes=font_bytes), encoding="utf-8")
        process = subprocess.run([node, str(Path(__file__).parent / "tools" / "render_card.cjs"),
                                  str(html_path), str(png_path)], capture_output=True, text=True,
                                 encoding="utf-8", timeout=60, check=True)
        layouts.append(json.loads(process.stdout))
        images.append(str(png_path))
    output = {"template_version": TEMPLATE_VERSION, "template_status": "temporary_engine_preview",
              "card_data_sha256": card_data_hash(data),
              "template_sha256": hashlib.sha256(TEMPLATE.read_bytes()).hexdigest(),
              "font": Path(font_path).name, "font_sha256": hashlib.sha256(font_bytes).hexdigest(),
              "image_files": images, "layout_result": layouts,
              "human_review_required": True, "mail_sent": False}
    (output_dir / "render_result.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="검사 완료 카드 → 임시 템플릿 → PNG")
    parser.add_argument("--input", type=Path, default=ROOT / ".engine-local/live-card/result.json")
    parser.add_argument("--output", type=Path, default=ROOT / ".engine-local/live-card/render")
    parser.add_argument("--font", type=Path, default=Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/NotoSansKR-VF.ttf")
    args = parser.parse_args()
    try:
        result = json.loads(args.input.read_text("utf-8"))
        print(json.dumps(render_card(result, args.output, font_path=args.font,
                                     node=shutil.which("node") or "node"), ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"이미지 변환 실패 ({type(exc).__name__}). 입력·로컬 폰트·Node·Playwright·브라우저 설정을 확인하세요.\n")


if __name__ == "__main__":
    main()
