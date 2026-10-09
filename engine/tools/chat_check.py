"""One provider request using public fixture data; no DB, SMTP or raw response logs."""
import json
import os
import sys

from engine.chat_client import ChatFailure, CodysseyChatClient
from engine.settings import load_chat_settings


def check_failed_job(settings):
    """Replay one failed article request without mutating jobs or sending mail."""
    from datetime import date
    from google.cloud.firestore_v1.base_query import FieldFilter
    from engine.firestore_connection import create_client
    from engine.firestore_article_store import decode_record, document_key
    from engine.card_prompt import build_card_messages
    from engine.cards import assemble_cards
    from engine.generation import repair_format
    from engine.rag import RagResult, RagHit, past_cutoff
    db = create_client(os.environ.get("ENGINE_FIREBASE_PROJECT", "ai-news-card"))
    try:
        rows = [doc.to_dict() for doc in db.collection("engine_delivery_jobs").where(
            filter=FieldFilter("status", "==", "failed")).limit(1000).stream(timeout=20)]
        rows = [row for row in rows if (row.get("error_code") or "").startswith("CHAT_")
                and row.get("selected_article_url")]
        if not rows:
            return {"stage": "failed_job", "status": "no_matching_failed_job"}
        job = max(rows, key=lambda row: row.get("updated_at") or row["scheduled_at"])
        day = date.fromisoformat(job["scheduled_date_kst"])
        current = decode_record(db.collection("engine_articles").document(
            document_key(job["selected_article_url"])).get(timeout=20).to_dict())
        saved_rag = db.collection("engine_rag_results").document(job["job_id"]).get(timeout=20).to_dict() or {}
        selected = saved_rag.get("selected_evidence_ids", [])
        hits, publishers = [], {}
        for identity in [current.article.article_id, *selected]:
            docs = list(db.collection("engine_articles").where(filter=FieldFilter(
                "article_id", "==", identity)).limit(1).stream(timeout=20))
            if not docs:
                raise ValueError("DIAGNOSTIC_ARTICLE_MISSING")
            data = docs[0].to_dict()
            publishers[identity] = data["publisher"]
            if identity != current.article.article_id:
                hits.append(RagHit(decode_record(data), 1.0))
        rag = RagResult("ready" if hits else "no_evidence", past_cutoff(day), tuple(hits),
                        tuple(hits), (), "diagnostic", "diagnostic")
        try:
            draft = CodysseyChatClient(settings).complete(build_card_messages(current, rag))
        except ChatFailure as error:
            return {"stage": "failed_job", "status": "failed", "error_code": error.code}
        result = assemble_cards(draft, current, rag, publishers=publishers, work_date_kst=day)
        repaired = repair_format(draft, current, rag, publishers, day) if result.status != "ready_for_review" else None
        if repaired:
            result = repaired[0]
        if result.status == "ready_for_review":
            import tempfile
            from engine.generation import LocalGenerationStore
            from engine.localization import korean_card
            with tempfile.TemporaryDirectory() as directory:
                korean_card(result.card_data, source_key="diagnostic", client=CodysseyChatClient(settings),
                            store=LocalGenerationStore(directory), model=settings.model, base_url=settings.base_url)
        report = {"stage": "failed_job", "status": result.status, "issues": list(result.issues),
                  "message_submitted": False}
        if result.status != "ready_for_review":
            from engine.cards import NUMBER
            counts = {}
            for sentence in draft.get("card1", {}).get("sentences", []):
                quote = sentence.get("evidence_quote", "")
                for number in sentence.get("numbers", []):
                    flags = {
                        "source_mismatch": number.get("source_article_id") != sentence.get("source_article_id"),
                        "quote_mismatch": number.get("evidence_quote") != quote,
                        "quote_not_subset": number.get("evidence_quote", "") not in quote,
                        "subject_not_in_quote": number.get("subject", "") not in quote,
                        "surface_not_numeric": NUMBER.fullmatch(str(number.get("surface", ""))) is None,
                        "surface_not_in_quote": str(number.get("surface", "")) not in quote,
                        "date_mismatch": number.get("as_of") != sentence.get("as_of"),
                    }
                    for key, value in flags.items():
                        if value:
                            counts[key] = counts.get(key, 0) + 1
            report["numeric_issue_counts"] = counts
        return report
    finally:
        db.close()


def main():
    try:
        settings = load_chat_settings()
        draft = CodysseyChatClient(settings).complete([
            {"role": "system", "content": "JSON 이외에는 출력하지 마세요. 코드 블록도 사용하지 마세요."},
            {"role": "user", "content": '다음 JSON을 그대로 출력하세요: {"card1":{"sentences":[],"terms":[]},"card2":null}'},
        ])
        valid = (type(draft) is dict and set(draft) == {"card1", "card2"}
                 and type(draft["card1"]) is dict and draft["card2"] is None)
        print(json.dumps({"status": "chat_response_verified" if valid else "draft_schema_invalid",
                          "message_submitted": False, "model": settings.model}))
        if valid and os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON"):
            replay = check_failed_job(settings)
            print(json.dumps(replay))
            return 0 if replay["status"] in {"ready_for_review", "no_matching_failed_job"} else 1
        return 0 if valid else 1
    except ChatFailure as error:
        print(json.dumps({"status": "failed", "error_code": error.code,
                          "error_hints": list(error.hints), "message_submitted": False}))
        return 1
    except Exception as error:
        print(json.dumps({"status": "failed", "error_code": "CHAT_CHECK_FAILED",
                          "error_type": type(error).__name__, "message_submitted": False}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
