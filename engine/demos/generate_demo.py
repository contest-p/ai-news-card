"""샘플 기사 생성 작업. --live를 지정한 경우만 실제 코디세이 요청."""

import argparse
from datetime import date
import json
from pathlib import Path
import sys

from engine.article_store import InMemoryArticleRepository
from engine.chat_client import CodysseyChatClient
from engine.embeddings import E5Encoder
from engine.generation import GenerationBusy, LocalGenerationStore, generate_cards
from engine.rag import index_articles, search_past_articles
from engine.selection import Article, parse_timestamp
from engine.settings import load_chat_settings


class FixtureClient:
    def __init__(self, draft):
        self.draft = draft

    def complete(self, messages):
        return self.draft


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="샘플 기사 기반 생성·검사·영속 기록")
    parser.add_argument("--live", action="store_true", help="실제 챗 API 1회 요청 허용 (크레딧 사용 가능)")
    parser.add_argument("--json-mode", action="store_true", help="실험용 공식 JSON 모드 요청. 현재 중계 API는 기본 호환 요청 사용")
    parser.add_argument("--retry-blocked", action="store_true", help="HTTP 400 수정 후 남은 1회 수동 사용. 횟수 초기화 없음")
    args = parser.parse_args()
    try:
        root = Path(__file__).resolve().parents[1] / "samples"
        fixture = json.loads((root / "rag.json").read_text("utf-8"))
        sample = json.loads((root / "cards.json").read_text("utf-8"))
        if fixture.get("demo_only") is not True or sample.get("demo_only") is not True:
            raise ValueError()
        repository = InMemoryArticleRepository()
        for row in fixture["articles"]:
            repository.save(Article(**{**row, "published_at": parse_timestamp(row["published_at"])}),
                            observed_at=parse_timestamp(fixture["observed_at"]))
        records = repository.list_current()
        current = next(record for record in records if record.article.article_id == fixture["current_article_id"])
        day = date.fromisoformat(fixture["work_date_kst"])
        encoder = E5Encoder()
        rag = search_past_articles(current, records, index_articles(records, encoder), encoder, work_date_kst=day)
        if rag.status == "embedding_failed":
            raise ValueError()
        if args.live:
            settings = load_chat_settings()
            client, model, base_url = CodysseyChatClient(settings, compatible_request=not args.json_mode), settings.model, settings.base_url
        else:
            client, model, base_url = FixtureClient(sample["draft"]), "local_fixture", "local_fixture"
        output = generate_cards(current, rag, publishers=sample["publishers"], work_date_kst=day,
                                job_id="sample-briefing-" + day.isoformat(), model=model, base_url=base_url,
                                client=client, store=LocalGenerationStore(), retry_blocked=args.retry_blocked)
        called = output.pop("api_called_this_run")
        print(json.dumps({"demo_only": True, "response_source": "codyssey" if args.live else "local_fixture",
                          "model": model, **output, "chat_api_called": bool(args.live and called),
                          "storage": "local_files", "database_connected": False, "mail_sent": False},
                         ensure_ascii=False, indent=2))
        if output["status"] != "completed":
            parser.exit(1)
    except GenerationBusy:
        parser.exit(1, "생성 작업이 실행 중이거나 중단 기록이 있습니다. 자동으로 잠금을 해제하지 않습니다.\n")
    except (ImportError, OSError, RuntimeError, ValueError, KeyError, TypeError, StopIteration):
        parser.exit(1, "생성 준비 실패. 설정·모델 캐시·샘플·로컬 작업 기록을 확인하세요.\n")


if __name__ == "__main__":
    main()
