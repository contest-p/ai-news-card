"""가상 데이터로 세 종류의 메일을 확인한다. 네트워크·DB·AI·SMTP 호출 없음."""

import argparse
from datetime import date
import json
from pathlib import Path
import sys

from engine.article_store import InMemoryArticleRepository
from engine.cards import assemble_cards
from engine.mail_assembly import (
    MAIL_TEMPLATE_VERSION, EndNoticeMailData, NewsMailData, NoNewsMailData,
    WebMailLinks, assemble_mail,
)
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
    inputs = {
        "news_card": NewsMailData(**common, job_id="fixture-news", card_data=checked.card_data,
                                  selection_reason=mail["selection_reason"], feedback_token=mail["feedback_token"]),
        "no_news": NoNewsMailData(**common, job_id="fixture-no-news", collection_succeeded=True,
                                  selection_result=SelectionResult("no_candidates", None, None)),
        "end_notice": EndNoticeMailData(**common, job_id="fixture-end", subscription_status="expired",
                                         start_date=date.fromisoformat(mail["subscription_start_date"]),
                                         end_date_exclusive=date.fromisoformat(mail["subscription_end_date_exclusive"])),
    }
    return {kind: assemble_mail(data) for kind, data in inputs.items()}


def write_previews(output):
    reports = []
    for kind, (subject, message) in sample_messages().items():
        folder = output / kind
        folder.mkdir(parents=True, exist_ok=True)
        html = message.get_body(preferencelist=("html",)).get_content()
        plain = message.get_body(preferencelist=("plain",)).get_content()
        (folder / "briefing.eml").write_bytes(message.as_bytes())
        (folder / "preview.html").write_text(html, encoding="utf-8")
        # 이미지가 없는 경우의 전체 텍스트 표시도 같은 렌더 검사로 확인한다.
        (folder / "images-blocked.html").write_text(html, encoding="utf-8")
        (folder / "text-fallback.txt").write_text(plain, encoding="utf-8")
        report = {"demo_only": True, "content_kind": kind, "subject": subject,
                  "mail_template_version": MAIL_TEMPLATE_VERSION, "inline_image_count": 0,
                  "message_bytes": len(message.as_bytes()), "preview": str(folder / "preview.html"),
                  "feedback_and_management_links": "fixture_only",
                  "database_connected": False, "chat_api_called": False, "mail_sent": False}
        (folder / "mail_result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        reports.append(report)
    return reports


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="가상 데이터로 뉴스·뉴스 없음·종료 메일 미리보기")
    parser.add_argument("--output", type=Path, default=ENGINE / ".engine-local/previews/mail-types")
    args = parser.parse_args()
    print(json.dumps(write_previews(args.output.resolve()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
