"""가상 구독·기사로 배치 전체(작업 생성 → 선별 → 카드 → 메일 → 발송 기록)를 실행한다.

네트워크·DB·AI·SMTP를 사용하지 않는다. AI 응답은 기사 첫 문장을 발췌하는 가상 응답이고,
SMTP 대신 outbox 폴더에 .eml을 저장한다. 결과는 engine/.engine-local/previews/batch-demo/.
"""

import argparse
import json
from pathlib import Path
import sys

from engine.article_store import InMemoryArticleRepository
from engine.batch import run_batch, write_summary
from engine.card_builder import GenerationCardBuilder
from engine.delivery import InMemoryJobStore
from engine.gateway import SampleEngineGateway
from engine.generation import LocalGenerationStore
from engine.live_collection import LiveCollectionResult
from engine.mail_archive import InMemoryMailArchive
from engine.mail_assembly import WebMailLinks
from engine.pipeline import PipelineDeps
from engine.selection import Article, parse_timestamp
from engine.smtp_sender import SmtpOutcome

ENGINE = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ENGINE / ".engine-local" / "previews" / "batch-demo"


class FixtureChatClient:
    """현재 기사 본문의 첫 문장을 그대로 발췌하는 가상 응답. 실제 AI 호출이 아니다."""

    def complete(self, messages):
        current = json.loads(messages[-1]["content"])["current"]
        sentence = current["body"].split(". ")[0].rstrip(".") + "."
        if sentence not in current["body"]:
            sentence = current["body"]
        return {"card1": {"sentences": [{"text": sentence, "source_article_id": current["article_id"],
                                         "evidence_quote": sentence, "as_of": None,
                                         "temporal_role": "current", "numbers": []}], "terms": []},
                "card2": None}


class OutboxSender:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def __call__(self, message, recipient):
        name = str(message["Message-ID"]).strip("<>").split("@")[0]
        (self.root / f"{name}.eml").write_bytes(message.as_bytes())
        return SmtpOutcome("accepted", False)


def run_demo(output: Path, *, reuse_state=None) -> dict:
    output = Path(output)
    fixture = json.loads((ENGINE / "samples" / "selection.json").read_text("utf-8"))
    # fixture의 '이미 발송된 기사'는 이력과 짝을 이루는 표본이다. 빈 작업 저장소로 시작하므로 제외한다.
    articles = [Article(**{**row, "published_at": parse_timestamp(row["published_at"])})
                for row in fixture["articles"] if row["article_id"] != "fixture_already_sent"]
    now = parse_timestamp(fixture["now"])
    state = reuse_state or {"jobs": InMemoryJobStore(), "archive": InMemoryMailArchive()}
    gateway = SampleEngineGateway({**fixture, "delivery_eligible_fixture": True})
    builder = GenerationCardBuilder(repository=InMemoryArticleRepository(), observed_at=now,
                                    publisher_for=lambda article_id: "가상 테스트 출처",
                                    client=FixtureChatClient(), model="fixture-only",
                                    base_url="https://fixture.invalid",
                                    store=LocalGenerationStore(output / "generation"))
    deps = PipelineDeps(gateway=gateway, jobs=state["jobs"], archive=state["archive"], build_cards=builder,
                        render_images=lambda data, job: ((), ["CARD_IMAGE_SKIPPED_IN_DEMO_TEXT_ONLY"]),
                        send=OutboxSender(output / "outbox"), sender_email="briefing@example.invalid",
                        web_links=WebMailLinks("https://news.example.com"), clock=lambda: now)

    def collect(*, deadline):
        return LiveCollectionResult(articles=articles, successful_sources=1)

    summary = run_batch(deps=deps, collect=collect, privacy_cleanup=lambda current: None)
    summary["demo_only"] = True
    write_summary(summary, output / "runs")
    return {"summary": summary, "state": state}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="가상 데이터로 배치 전체 실행 (네트워크·DB·AI·SMTP 없음)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = run_demo(args.output.resolve())
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
