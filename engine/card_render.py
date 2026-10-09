"""검사 완료 카드 JSON → 공유 프론트 HTML 템플릿 → PNG. API·DB·메일 호출 없음."""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from engine.cards import TERM_NAME_MAX_CHARS, fields, string
from engine.selection import KST, canonical_url, parse_timestamp

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "frontend" / "card-template.js"
TEMPLATE_STYLE = ROOT / "frontend" / "styles.css"
TEMPLATE_VERSION = "article-only-layouts-v3"
# CARD_FONT_PATH가 없을 때 찾는 한글 TTF/OTF. 프로젝트 폰트를 우선 사용하며 OS 폰트는 로컬 대체 후보다.
FONT_CANDIDATES = (
    ROOT / "frontend/assets/fonts/NanumGothic-Regular.ttf",
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "NotoSansKR-VF.ttf",
    Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf"),
    Path("/usr/share/fonts/opentype/noto/NotoSansKR-Regular.otf"),
    Path("/Library/Fonts/NotoSansKR-Regular.ttf"),
)


def resolve_font_path(env=None, candidates=FONT_CANDIDATES) -> Path:
    """명시 설정이 있으면 그 파일만 사용한다. 없으면 OS별 후보 중 존재하는 첫 파일."""
    env = os.environ if env is None else env
    configured = env.get("CARD_FONT_PATH", "").strip()
    if configured:
        path = Path(configured)
        if not path.is_file():
            raise FileNotFoundError("CARD_FONT_NOT_FOUND")
        return path
    for path in candidates:
        if Path(path).is_file():
            return Path(path)
    raise FileNotFoundError("CARD_FONT_NOT_FOUND")


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
            string(term["term"], TERM_NAME_MAX_CHARS)
            string(term["definition"], 100)
            if term["source_article_id"] not in sources:
                raise ValueError("UNKNOWN_TERM_SOURCE")
    return sources


def kst_time(value):
    return parse_timestamp(value).astimezone(KST).strftime("%Y.%m.%d %H:%M KST")


def build_html(data, index, *, font_bytes):
    validate_render_data(data)
    if index not in (1, 2) or data["card1" if index == 1 else "card2"] is None:
        raise ValueError("CARD_OMITTED")
    # The shared frontend formats dates; normalize timestamp calendar dates to KST.
    displayed = json.loads(json.dumps(data))
    displayed["published_at"] = parse_timestamp(data["published_at"]).astimezone(KST).isoformat()
    for source in displayed["sources"]:
        source["published_at"] = parse_timestamp(source["published_at"]).astimezone(KST).isoformat()
    payload = {"data": displayed, "index": index,
               "font_base64": base64.b64encode(font_bytes).decode()}
    process = subprocess.run([shutil.which("node") or "node", str(Path(__file__).parent / "tools" / "build_card_html.cjs")],
                             input=json.dumps(payload, ensure_ascii=False), capture_output=True,
                             encoding="utf-8", timeout=15, check=True)
    return process.stdout


def render_card(result, output_dir, *, font_path, node="node"):
    """생성 작업 기록(completed + ready_for_review)을 받아 렌더링한다."""
    if (result.get("status") != "completed" or result.get("result", {}).get("status") != "ready_for_review"):
        raise ValueError("VALIDATED_CARD_REQUIRED")
    return render_card_data(result["result"]["card_data"], output_dir, font_path=font_path, node=node)


def render_card_data(data, output_dir, *, font_path, node=None):
    """검사 완료 카드 데이터 → HTML → PNG. 실패는 예외로 전달하며 재시도는 호출자가 정한다."""
    node = node or shutil.which("node") or "node"
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
    output = {"template_version": TEMPLATE_VERSION, "template_status": "shared_frontend_template",
              "card_data_sha256": card_data_hash(data),
              "template_sha256": hashlib.sha256(TEMPLATE.read_bytes()).hexdigest(),
              "style_sha256": hashlib.sha256(TEMPLATE_STYLE.read_bytes()).hexdigest(),
              "font": Path(font_path).name, "font_sha256": hashlib.sha256(font_bytes).hexdigest(),
              "image_files": images, "layout_result": layouts,
              "human_review_required": True, "mail_sent": False}
    (output_dir / "render_result.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="검사 완료 카드 → 프론트 공유 템플릿 → PNG")
    parser.add_argument("--input", type=Path, default=ROOT / ".engine-local/live-card/result.json")
    parser.add_argument("--output", type=Path, default=ROOT / ".engine-local/live-card/render")
    parser.add_argument("--font", type=Path, default=None, help="기본값: CARD_FONT_PATH 또는 OS별 한글 폰트")
    args = parser.parse_args()
    try:
        result = json.loads(args.input.read_text("utf-8"))
        font = args.font or resolve_font_path()
        print(json.dumps(render_card(result, args.output, font_path=font,
                                     node=shutil.which("node") or "node"), ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"이미지 변환 실패 ({type(exc).__name__}). 입력·로컬 폰트·Node·Playwright·브라우저 설정을 확인하세요.\n")


if __name__ == "__main__":
    main()
