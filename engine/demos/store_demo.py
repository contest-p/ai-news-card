"""python -m engine.demos.store_demo: 수집 → 샘플 저장 → 재저장 → 수정 → 선별."""

from collections import Counter
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import sys

from engine.article_store import InMemoryArticleRepository
from engine.collection import collect_local_samples
from engine.selection import DeliveryHistory, parse_timestamp, select_article


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    samples = Path(__file__).resolve().parents[1] / "samples"
    fixture = json.loads((samples / "selection.json").read_text("utf-8"))
    now = parse_timestamp(fixture["now"])
    collected = collect_local_samples(samples / "collection")
    if not collected.articles:
        sys.exit("수집된 샘플 기사가 없습니다.")
    store = InMemoryArticleRepository()
    first = Counter(store.save(article, observed_at=now).action for article in collected.articles)
    second = Counter(store.save(article, observed_at=now + timedelta(seconds=1)).action
                     for article in collected.articles)
    original = collected.articles[0]
    corrected = replace(original, body=original.body + "\n샘플 수정: 설명 자료를 보완했다고 가정합니다.")
    changed = store.save(corrected, observed_at=now + timedelta(seconds=2))
    stale = store.save(original, observed_at=now)
    earlier = store.get_revision(changed.record.article.article_id, 1)
    history = [DeliveryHistory(**{**row, "attempted_at": parse_timestamp(row["attempted_at"])})
               for row in fixture["history"]]
    selection = select_article(fixture["subscription_snapshot"],
                               [record.article for record in store.list_current()], history,
                               now=now, collection_succeeded=collected.collection_succeeded)
    print(json.dumps({
        "demo_only": True, "contract_status": "proposal", "storage": "in_memory",
        "first_save": dict(first), "repeat_save": dict(second),
        "unique_article_count": len(store.list_current()),
        "content_change": changed.action, "content_version": changed.record.content_version,
        "old_version_preserved": earlier is not None and earlier.article.body == original.body,
        "older_observation": stale.action, "embedding_status": changed.record.embedding_status,
        "selection_status": selection.status,
        "selected_title": selection.article.title if selection.article else None,
        "database_connected": False, "mail_sent": False,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
