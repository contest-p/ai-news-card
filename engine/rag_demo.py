"""python -m engine.rag_demo: 실제 로컬 임베딩으로 가상 과거 기사 검색."""

import argparse
from datetime import date
import json
from pathlib import Path
import sys

from engine.article_store import InMemoryArticleRepository
from engine.embeddings import DIMENSIONS, E5Encoder
from engine.rag import evidence_context, index_articles, past_cutoff, search_past_articles
from engine.selection import Article, parse_timestamp


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="가상 기사 RAG: 챗 API·Firestore 연결 없음")
    parser.add_argument("--download-model", action="store_true", help="최초 모델 다운로드 허용")
    parser.add_argument("--no-past", action="store_true", help="과거 근거가 없는 경우 확인")
    parser.add_argument("--min-score", type=float, default=0.85, help="합의 전 유사도 기준 제안값")
    args = parser.parse_args()
    try:
        fixture = json.loads((Path(__file__).parent / "samples" / "rag.json").read_text("utf-8"))
        if fixture.get("demo_only") is not True:
            raise ValueError("가상 샘플만 사용할 수 있습니다.")
        work_date = date.fromisoformat(fixture["work_date_kst"])
        observed_at = parse_timestamp(fixture["observed_at"])
        store = InMemoryArticleRepository()
        for row in fixture["articles"]:
            article = Article(**{**row, "published_at": parse_timestamp(row["published_at"])})
            if args.no_past and article.published_at < past_cutoff(work_date):
                continue
            store.save(article, observed_at=observed_at)
        records = store.list_current()
        current = next(record for record in records if record.article.article_id == fixture["current_article_id"])
        encoder = E5Encoder(allow_download=args.download_model)
        embeddings = index_articles(records, encoder)
        result = search_past_articles(current, records, embeddings, encoder,
                                      work_date_kst=work_date, min_score=args.min_score)
        output = {
            "demo_only": True, "model_id": encoder.model_id, "model_revision": encoder.model_revision,
            "dimensions": DIMENSIONS, "status": result.status,
            "cutoff_utc": result.cutoff_utc.isoformat(), "threshold_proposal": args.min_score,
            "embedding_ready_count": sum(embedding.status == "ready" for embedding in embeddings),
            "embedding_failed_count": sum(embedding.status == "failed" for embedding in embeddings),
            "retrieved": [{"article_id": hit.record.article.article_id, "score": round(hit.score, 6)}
                          for hit in result.retrieved],
            "usable_article_ids": [hit.record.article.article_id for hit in result.usable_evidence],
            "background_candidates_available": bool(result.usable_evidence),
            "excluded": [{"article_id": article_id, "reason": reason} for article_id, reason in result.excluded],
            "evidence_context_count": len(evidence_context(result)),
            "database_connected": False, "chat_api_called": False, "mail_sent": False,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if result.status == "embedding_failed":
            parser.exit(1)
    except (ImportError, OSError, RuntimeError) as exc:
        parser.exit(1, f"임베딩 준비 실패 ({type(exc).__name__}). RAG 패키지 설치와 최초 모델 다운로드를 확인하세요.\n")
    except (ValueError, KeyError, TypeError, StopIteration) as exc:
        parser.exit(1, f"RAG 입력 실패 ({type(exc).__name__}). 샘플·모델·유사도 설정을 확인하세요.\n")


if __name__ == "__main__":
    main()
