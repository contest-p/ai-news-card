"""가상 데이터로 세 종류의 메일을 확인한다. 네트워크·DB·AI·SMTP 호출 없음."""

import argparse
import copy
from datetime import date
from email import policy
from email.parser import BytesParser
import json
from pathlib import Path
import sys

from engine.article_store import InMemoryArticleRepository
from engine.cards import assemble_cards
from engine.mail_assembly import (
    MAIL_TEMPLATE_VERSION, EndNoticeMailData, InlineImage, NewsMailData, NoNewsMailData,
    WebMailLinks, assemble_mail,
)
from engine.card_images import render_with_fallback
from engine.card_render import resolve_font_path
from engine.rag import RagHit, RagResult, past_cutoff
from engine.selection import Article, SelectionResult, parse_timestamp

ENGINE = Path(__file__).resolve().parents[1]


def sample_messages():
    samples = ENGINE / "samples"
    articles = json.loads((samples / "rag.json").read_text("utf-8"))
    cards = json.loads((samples / "cards.json").read_text("utf-8"))
    mail = json.loads((samples / "mail.json").read_text("utf-8"))
    if any(sample.get("demo_only") is not True for sample in (articles, cards, mail)):
        raise ValueError("DEMO_FIXTURES_REQUIRED")
    if (not mail["sender_email"].endswith("@example.invalid")
            or not mail["recipient_email"].endswith("@example.invalid")
            or mail["feedback_token"] != "TOKEN_FIXTURE_ONLY"):
        raise ValueError("PLACEHOLDERS_REQUIRED")
    repository = InMemoryArticleRepository()
    for row in articles["articles"]:
        repository.save(Article(**{**row, "published_at": parse_timestamp(row["published_at"])}),
                        observed_at=parse_timestamp(articles["observed_at"]))
    records = {record.article.article_id: record for record in repository.list_current()}
    day = date.fromisoformat(articles["work_date_kst"])
    # 메일 표시용 가상 검색 결과. 실제 검색 실행이나 품질 평가의 증거는 아니다.
    evidence = tuple(RagHit(records[key], 1.0) for key in articles["expected_relevant_ids"])
    rag = RagResult("ready", past_cutoff(day), evidence, evidence, (), "fixture_only", "fixture_only")
    checked = assemble_cards(cards["draft"], records[articles["current_article_id"]], rag,
                             publishers=cards["publishers"], work_date_kst=day)
    if checked.status != "ready_for_review":
        raise ValueError("FIXTURE_CARD_INVALID")
    common = dict(sender_email=mail["sender_email"], recipient_email=mail["recipient_email"],
                  scheduled_date_kst=day, web_links=WebMailLinks(mail["web_origin"]), preview=True)
    one_card = copy.deepcopy(checked.card_data)
    one_card["card2"] = None
    inputs = {
        "news_card_1": NewsMailData(**common, job_id="fixture-news-one", card_data=one_card,
                                    selection_reason=mail["selection_reason"], feedback_token=mail["feedback_token"]),
        "news_card_2": NewsMailData(**common, job_id="fixture-news-two", card_data=checked.card_data,
                                    selection_reason=mail["selection_reason"], feedback_token=mail["feedback_token"]),
        "no_news": NoNewsMailData(**common, job_id="fixture-no-news", collection_succeeded=True,
                                  selection_result=SelectionResult("no_candidates", None, None)),
        "end_notice": EndNoticeMailData(**common, job_id="fixture-end", subscription_status="expired",
                                         start_date=date.fromisoformat(mail["subscription_start_date"]),
                                         end_date_exclusive=date.fromisoformat(mail["subscription_end_date_exclusive"])),
    }
    return inputs


def write_previews(output):
    reports = []
    for kind, data in sample_messages().items():
        folder = output / kind
        folder.mkdir(parents=True, exist_ok=True)
        if isinstance(data, NewsMailData):
            images, image_issues = render_with_fallback(data.card_data, folder / "render",
                                                        font_path=resolve_font_path())
            data = NewsMailData(**{**data.__dict__, "inline_images": images})
        else:
            image_issues = []
        subject, message = assemble_mail(data)
        html = message.get_body(preferencelist=("html",)).get_content()
        plain = message.get_body(preferencelist=("plain",)).get_content()
        (folder / "briefing.eml").write_bytes(message.as_bytes())
        parsed = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
        rendered_html = parsed.get_body(preferencelist=("html",)).get_content()
        for part in parsed.walk():
            if part.get_content_type() == "image/png" and part["Content-ID"]:
                cid = part["Content-ID"].strip("<>")
                data_uri = "data:image/png;base64," + __import__("base64").b64encode(part.get_payload(decode=True)).decode()
                rendered_html = rendered_html.replace("cid:" + cid, data_uri)
        (folder / "preview.html").write_text(rendered_html, encoding="utf-8")
        # CID 이미지를 제거한 브라우저용 파일로 이미지 차단 시 대체 텍스트를 검수한다.
        import re
        images_blocked = re.sub(r'<img\b[^>]*>', '', rendered_html, flags=re.IGNORECASE)
        (folder / "images-blocked.html").write_text(images_blocked, encoding="utf-8")
        (folder / "text-fallback.txt").write_text(plain, encoding="utf-8")
        report = {"demo_only": True, "content_kind": kind, "subject": subject,
                  "mail_template_version": MAIL_TEMPLATE_VERSION, "inline_image_count": len(images) if isinstance(data, NewsMailData) else 0,
                  "message_bytes": len(message.as_bytes()), "preview": str(folder / "preview.html"),
                  "image_issues": image_issues,
                  "feedback_and_management_links": "fixture_only",
                  "database_connected": False, "chat_api_called": False, "mail_sent": False}
        (folder / "mail_result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        reports.append(report)
    links = []
    for report in reports:
        kind = report["content_kind"]
        links.append(f'<li><a href="{kind}/preview.html">{kind} · 브라우저 미리보기</a> '
                     f'(<a href="{kind}/images-blocked.html">이미지 차단</a>, '
                     f'<a href="{kind}/preview-800.png">PC</a>, <a href="{kind}/preview-390.png">모바일</a>)</li>')
    index = ('<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
             '<title>뉴스 브리핑 메일 미리보기</title><body style="margin:0;background:#f1f4f7;font:16px Arial,\'Malgun Gothic\',sans-serif;color:#192333;">'
             '<main style="max-width:680px;margin:32px auto;padding:24px;background:#fff;border-radius:16px;">'
             '<h1>뉴스 브리핑 · 로컬 미리보기</h1><p>가상 데이터만 사용했습니다. 메일 전송·AI·DB 호출은 없습니다.</p><ul>'
             + "".join(links) + '</ul></main></body></html>')
    (output / "index.html").write_text(index, encoding="utf-8")
    return reports


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="가상 데이터로 1·2장 뉴스, 뉴스 없음, 종료 안내 메일 미리보기")
    parser.add_argument("--output", type=Path, default=ENGINE / ".engine-local/previews/mail-types")
    args = parser.parse_args()
    print(json.dumps(write_previews(args.output.resolve()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
