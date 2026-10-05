"""실제 RSS → 본문 → 메모리 저장 → 관심 분야 선별 수동 확인 CLI."""

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

from engine.article_store import InMemoryArticleRepository
from engine.live_collection import collect_live_sources
from engine.selection import CollectionUnavailable, select_article


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="실제 뉴스 수집·저장·선별 확인 (DB·AI·메일 호출 없음)")
    parser.add_argument("--max-per-source", type=int, default=3)
    parser.add_argument("--category", choices=["economy", "it_science", "politics", "society", "world", "culture"],
                        default="politics")
    parser.add_argument("--keyword", action="append", default=[])
    parser.add_argument("--report", type=Path, default=Path(".engine-local/live_collection_report.json"))
    args = parser.parse_args()
    if not 1 <= args.max_per_source <= 20:
        parser.error("--max-per-source는 1~20이어야 합니다.")
    collected = collect_live_sources(max_entries=args.max_per_source)
    now = datetime.now(timezone.utc)
    store = InMemoryArticleRepository()
    saved = Counter(store.save(article, observed_at=now).action for article in collected.articles)
    repeated = Counter(store.save(article, observed_at=now + timedelta(seconds=1)).action
                       for article in collected.articles)
    snapshot = {"timezone": "Asia/Seoul", "status": "active", "scheduled_at": now.isoformat(),
                "deadline_at": (now + timedelta(hours=1)).isoformat(),
                "categories": [args.category], "keywords": args.keyword}
    try:
        selected = select_article(snapshot, [row.article for row in store.list_current()], [],
                                  now=now, collection_succeeded=collected.collection_succeeded)
        selection_status, article, reason = selected.status, selected.article, selected.selection_reason
    except CollectionUnavailable:
        selection_status, article, reason = "collection_failed", None, None
    output = {"checked_at": now.isoformat(), "mode": "live_news_manual_check",
              "coverage": "first_n_entries_per_source", "max_per_source": args.max_per_source,
              "collection_status": "complete" if collected.collection_succeeded else
                                   "partial" if collected.articles else "failed",
              "collection_succeeded": collected.collection_succeeded,
              "collected_count": len(collected.articles), "sources": collected.source_reports,
              "issues": [asdict(issue) for issue in collected.issues],
              "articles": [{"article_id": row.article_id, "title": row.title, "url": row.url,
                            "published_at": row.published_at.isoformat(), "category": row.category,
                            "body_chars": len(row.body), **collected.article_sources[row.article_id]}
                           for row in collected.articles],
              "storage": "in_memory", "first_save": dict(saved), "repeat_save": dict(repeated),
              "selection_status": selection_status,
              "selected_article_id": article.article_id if article else None,
              "selection_reason": reason, "database_connected": False,
              "ai_called": False, "mail_sent": False, "human_review_required": True}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    if not collected.articles:
        parser.exit(1)


if __name__ == "__main__":
    main()
