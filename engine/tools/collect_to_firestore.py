"""Collect articles and optionally persist them using the existing repository."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from engine.collection import collect_local_samples
from engine.firestore_article_store import FirestoreArticleRepository
from engine.firestore_connection import create_client
from engine.live_collection import collect_live_sources


def save_collection(repository, collected, observed_at):
    return dict(Counter(repository.save(article, observed_at=observed_at).action
                        for article in collected.articles))


def verify_collection(repository, collected, observed_at):
    actions = Counter()
    for article in collected.articles:
        saved = repository.save(article, observed_at=observed_at)
        revision = repository.get_revision(saved.record.article.article_id, saved.record.content_version)
        if revision is None or revision.content_hash != saved.record.content_hash:
            raise RuntimeError("ARTICLE_READBACK_MISMATCH")
        repeated = repository.save(article, observed_at=observed_at)
        if repeated.action not in {"unchanged", "stale"}:
            raise RuntimeError("ARTICLE_REPLAY_MISMATCH")
        actions[saved.action] += 1
    return dict(actions)


def main():
    parser = argparse.ArgumentParser(description="기사 수집 → Firestore (기본: 샘플·쓰기 없음)")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--verify", action="store_true", help="저장 후 버전 읽기와 같은 기사 재저장 검증")
    parser.add_argument("--project")
    parser.add_argument("--credentials")
    parser.add_argument("--max-per-source", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.max_per_source <= 20:
        parser.error("--max-per-source must be 1..20")
    if args.write and (not args.live or not args.project):
        parser.error("--write requires --live and --project; fixtures cannot enter team DB")
    if args.verify and not args.write:
        parser.error("--verify requires --write")
    client = None
    try:
        # Check credentials before spending time on live collection.
        if args.write:
            client = create_client(args.project, args.credentials)
        collected = (collect_live_sources(max_entries=args.max_per_source) if args.live else
                     collect_local_samples(Path(__file__).resolve().parents[1] / "samples" / "collection"))
        report = {"mode": "live" if args.live else "fixture", "database_written": False,
                  "collection_succeeded": collected.collection_succeeded,
                  "article_count": len(collected.articles), "issue_count": len(collected.issues),
                  "ai_called": False, "mail_sent": False}
        if args.write and collected.articles:
            save = verify_collection if args.verify else save_collection
            report["save_actions"] = save(FirestoreArticleRepository(client), collected, datetime.now(timezone.utc))
            report["database_written"] = True
            report["readback_and_replay_verified"] = args.verify
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if collected.collection_succeeded else 1
    except Exception as error:
        print(json.dumps({"error_type": type(error).__name__,
                          "writes_may_be_partial": args.write,
                          "action": "Check credentials/network; retry reuses article URL IDs."}))
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    sys.exit(main())
