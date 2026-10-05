"""실제 로컬 RAG + 저장된 가상 챗 응답을 검사. 챗 API·메일 호출 없음."""

import argparse
import copy
from datetime import date
import json
from pathlib import Path
import sys

from engine.article_store import InMemoryArticleRepository
from engine.card_prompt import PROMPT_VERSION, build_card_messages
from engine.cards import assemble_cards
from engine.embeddings import E5Encoder
from engine.rag import index_articles, search_past_articles
from engine.selection import Article, parse_timestamp


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="로컬 카드 검사: API 비용 없음")
    parser.add_argument("--no-past", action="store_true")
    parser.add_argument("--invalid-background", action="store_true")
    args = parser.parse_args()
    try:
        root = Path(__file__).parent / "samples"
        fixture = json.loads((root / "rag.json").read_text("utf-8"))
        sample = json.loads((root / "cards.json").read_text("utf-8"))
        if fixture.get("demo_only") is not True or sample.get("demo_only") is not True:
            raise ValueError("가상 샘플만 허용합니다.")
        store = InMemoryArticleRepository()
        for row in fixture["articles"]:
            store.save(Article(**{**row, "published_at": parse_timestamp(row["published_at"])}),
                       observed_at=parse_timestamp(fixture["observed_at"]))
        records = store.list_current()
        current = next(record for record in records if record.article.article_id == fixture["current_article_id"])
        if args.no_past:
            records = [current]
        encoder = E5Encoder()
        rag = search_past_articles(current, records, index_articles(records, encoder), encoder,
                                   work_date_kst=date.fromisoformat(fixture["work_date_kst"]))
        messages = build_card_messages(current, rag)
        draft = copy.deepcopy(sample["draft"])
        if args.no_past:
            draft["card2"] = None
        if args.invalid_background and draft["card2"] is not None:
            draft["card2"]["sentences"][0]["text"] = "내일부터 모든 대출금리가 절반으로 내려갑니다."
        result = assemble_cards(draft, current, rag, publishers=sample["publishers"],
                                work_date_kst=date.fromisoformat(fixture["work_date_kst"]))
        print(json.dumps({"demo_only": True, "response_source": "local_fixture",
                          "prompt_version_proposal": PROMPT_VERSION, "prompt_message_count": len(messages),
                          "status": result.status, "human_review_required": result.human_review_required,
                          "issues": result.issues, "card_data": result.card_data,
                          "chat_api_called": False, "database_connected": False, "mail_sent": False},
                         ensure_ascii=False, indent=2))
        if result.status == "failed":
            parser.exit(1)
    except (ImportError, OSError, RuntimeError, ValueError, KeyError, TypeError, StopIteration) as exc:
        parser.exit(1, f"카드 시연 준비 실패 ({type(exc).__name__}). 패키지·모델 캐시·샘플을 확인하세요.\n")


if __name__ == "__main__":
    main()
