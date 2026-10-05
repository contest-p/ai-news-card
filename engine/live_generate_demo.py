"""실제 SBS 기사 → 카드 1 생성·검사·로컬 저장. --live만 챗 API를 호출한다."""

import argparse
import copy
import hashlib
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sys

from engine.article_store import InMemoryArticleRepository
from engine.chat_client import CodysseyChatClient
from engine.cards import CardInvalid, NUMBER, assemble_cards, normalized, validate_card
from engine.card_prompt import build_card_messages
from engine.generation import GenerationBusy, LocalGenerationStore, generate_cards
from engine.live_collection import collect_live_sources, load_sources
from engine.rag import RagResult, past_cutoff
from engine.selection import Article, CollectionUnavailable, parse_timestamp, select_article
from engine.settings import load_chat_settings

ROOT = Path(__file__).resolve().parents[1] / ".engine-local" / "live-card"
KST = timezone(timedelta(hours=9))


class RecordingClient:
    """검수용 응답만 Git 제외 폴더에 보관. 요청 키·헤더는 저장하지 않는다."""

    def __init__(self, client, path):
        self.client, self.path = client, path

    def complete(self, messages):
        draft = self.client.complete(messages)
        recorded = {"messages_hash": messages_hash(messages), "draft": draft}
        self.path.write_text(json.dumps(recorded, ensure_ascii=False, indent=2), encoding="utf-8")
        return draft


def messages_hash(messages):
    return hashlib.sha256(json.dumps(messages, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def prepare_input(*, now, collector=collect_live_sources):
    sources = [source for source in load_sources() if source.source_id == "sbs"]
    collected = collector(sources=sources, max_entries=2)
    snapshot = {"timezone": "Asia/Seoul", "status": "active", "scheduled_at": now.isoformat(),
                "deadline_at": (now + timedelta(hours=1)).isoformat(),
                "categories": ["politics"], "keywords": []}
    selected = select_article(snapshot, collected.articles, [], now=now,
                              collection_succeeded=collected.collection_succeeded)
    if selected.article is None:
        raise ValueError("NO_RECENT_SBS_ARTICLE")
    article = selected.article
    row = asdict(article)
    row["published_at"] = article.published_at.isoformat()
    return {"mode": "live_news", "work_date_kst": now.astimezone(KST).date().isoformat(),
            "observed_at": now.isoformat(), "article": row,
            "publisher": collected.article_sources[article.article_id]["publisher"],
            "selection_reason": selected.selection_reason,
            "collection_issues": [asdict(issue) for issue in collected.issues]}


def review_numeric_format(draft, current):
    """사실 문구는 수정하지 않는다. 수치 필드 형식 보정과 불일치 문장 제외만 허용."""
    repaired = copy.deepcopy(draft)
    changes = []
    retained = []
    for sentence in repaired["card1"]["sentences"]:
        # 원문 근거를 먼저 확인한 경우에만 수치 필드 형식을 정리한다.
        quote = sentence["evidence_quote"]
        if (sentence["source_article_id"] == current.article.article_id
                and normalized(quote) in normalized(current.article.body)
                and normalized(sentence["text"]) in normalized(quote)):
            for number in sentence["numbers"]:
                surface, unit = number["surface"], number["unit"]
                match = NUMBER.match(surface)
                if (match and unit and surface == match.group() + unit
                        and surface in sentence["text"]):
                    number["surface"] = match.group()
                    changes.append("NUMBER_SURFACE_UNIT_SEPARATED")
                if (number["evidence_quote"] != quote and number["evidence_quote"]
                        and number["evidence_quote"] in quote):
                    number["evidence_quote"] = quote
                    changes.append("NUMBER_QUOTE_EXPANDED_TO_VERIFIED_SENTENCE")
        try:
            validate_card({"sentences": [sentence], "terms": []},
                          {current.article.article_id: current}, current.article.article_id, background=False)
        except CardInvalid as exc:
            if not exc.code.startswith("NUMBER_"):
                raise
            changes.append("SENTENCE_OMITTED:" + exc.code)
        else:
            retained.append(sentence)
    repaired["card1"]["sentences"] = retained
    return repaired, list(dict.fromkeys(changes))


def generate_from_input(payload, *, client, model, base_url, store, draft_path=None):
    if payload.get("mode") != "live_news":
        raise ValueError("LIVE_INPUT_REQUIRED")
    day = date.fromisoformat(payload["work_date_kst"])
    row = payload["article"]
    article = Article(**{**row, "published_at": parse_timestamp(row["published_at"])})
    observed = parse_timestamp(payload["observed_at"])
    if not observed - timedelta(hours=24) <= article.published_at < observed:
        raise ValueError("INPUT_ARTICLE_NOT_RECENT_AT_COLLECTION")
    if observed.astimezone(KST).date() != day or article.category != "politics":
        raise ValueError("INPUT_DATE_OR_CATEGORY_INVALID")
    repository = InMemoryArticleRepository()
    current = repository.save(article, observed_at=observed).record
    # 과거 데이터 없음: 임베딩 모델을 로드하지 않고 배경 카드를 생략한다.
    rag = RagResult("no_evidence", past_cutoff(day), (), (), (), "not_used", "not_used")
    publishers = {article.article_id: payload["publisher"]}
    output = generate_cards(current, rag, publishers=publishers,
                          work_date_kst=day, job_id="live-sbs-card1-" + day.isoformat(),
                          model=model, base_url=base_url, client=client, store=store)
    if (output["error_code"] == "CARD_VALIDATION_FAILED" and draft_path is not None
            and draft_path.exists()):
        try:
            recorded = json.loads(draft_path.read_text("utf-8"))
            if recorded["messages_hash"] != messages_hash(build_card_messages(current, rag)):
                raise ValueError("RECORDED_INPUT_MISMATCH")
            repaired, changes = review_numeric_format(recorded["draft"], current)
            checked = assemble_cards(repaired, current, rag, publishers=publishers, work_date_kst=day)
            if checked.status == "ready_for_review":
                # 같은 키·시도 횟수를 유지하고, API 없는 재검사를 기록한다.
                with store.locked(output["generation_key"]) as path:
                    state = json.loads(path.read_text("utf-8"))
                    if state["status"] == "retryable" and state["error_code"] == "CARD_VALIDATION_FAILED":
                        state["format_review"] = {"version": "numeric-format-review-v1",
                                                  "original_issues": state["result"]["issues"],
                                                  "changes": changes}
                        state.update(status="completed", error_code=None, result=asdict(checked))
                        state["result"]["issues"] = list(checked.issues) + changes
                        store.save(path, state)
                        output = {**state, "api_called_this_run": output["api_called_this_run"],
                                  "reused": output["reused"]}
        except (CardInvalid, ValueError, KeyError, TypeError, AttributeError):
            pass  # 형식 보정으로 해결할 수 없는 경우 원래 실패 기록을 유지한다.
    return output


def write_preview(root, output):
    result = output.get("result") or {}
    data = result.get("card_data")
    if output["status"] != "completed" or data is None:
        # 새 입력의 생성 실패를 이전 카드의 성공으로 오해하지 않도록 최신 출력만 정리.
        for name in ("card.json", "preview.md"):
            (root / name).unlink(missing_ok=True)
        return
    (root / "card.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# " + data["title"], "", "AI 생성 · 원문 발췌 · 사람 검수 필요", ""]
    lines.extend(sentence["text"] + "\n" for sentence in data["card1"]["sentences"])
    for term in data["card1"]["terms"]:
        lines.append(f"- {term['term']}: {term['definition']}")
    lines.extend(["", "과거 기사 근거가 없어 배경 카드 2는 생략했습니다.", ""])
    for source in data["sources"]:
        lines.append(f"출처: [{source['publisher']}]({source['url']}) · 게시 시각 {source['published_at']}")
    (root / "preview.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="실제 SBS 기사 카드 1 생성 확인 (DB·메일 없음)")
    parser.add_argument("--live", action="store_true", help="챗 API 호출 허용 (크레딧 사용 가능)")
    parser.add_argument("--refresh-news", action="store_true", help="저장된 입력 대신 현재 RSS 재수집")
    args = parser.parse_args()
    ROOT.mkdir(parents=True, exist_ok=True)
    input_path = ROOT / "input.json"
    try:
        # 설정 누락은 뉴스 요청 전에 확인. 비밀값은 출력·결과 파일에 저장하지 않는다.
        settings = load_chat_settings() if args.live else None
        if input_path.exists() and not args.refresh_news:
            payload = json.loads(input_path.read_text("utf-8"))
        else:
            payload = prepare_input(now=datetime.now(timezone.utc))
            input_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if not args.live:
            print(json.dumps({"status": "input_prepared", "title": payload["article"]["title"],
                              "input_path": str(input_path), "chat_api_called": False}, ensure_ascii=False, indent=2))
            return
        client = RecordingClient(CodysseyChatClient(settings), ROOT / "draft.json")
        output = generate_from_input(payload, client=client, model=settings.model,
                                     base_url=settings.base_url, store=LocalGenerationStore(),
                                     draft_path=ROOT / "draft.json")
        called = output.pop("api_called_this_run")
        output.update(mode="live_news", response_source="codyssey", model=settings.model,
                      input_collected_at=payload["observed_at"], chat_api_called=called,
                      storage="local_files", database_connected=False, mail_sent=False,
                      selection_reason=payload["selection_reason"],
                      collection_issues=payload["collection_issues"])
        (ROOT / "result.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        write_preview(ROOT, output)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if output["status"] != "completed":
            parser.exit(1)
    except GenerationBusy:
        parser.exit(1, "생성 작업이 실행 중이거나 중단 기록이 있습니다. 잠금을 자동 해제하지 않습니다.\n")
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, CollectionUnavailable):
        parser.exit(1, "실제 기사 생성 준비 실패. 설정·수집 상태·로컬 입력 기록을 확인하세요.\n")


if __name__ == "__main__":
    main()
