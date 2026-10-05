"""python -m engine.demos.collect_demo: 로컬 RSS 수집 → 기사 선별 시연."""

import argparse
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path
import sys

from engine.collection import collect_local_samples
from engine.gateway import SampleEngineGateway
from engine.selection import CollectionUnavailable, DeliveryHistory, parse_timestamp, select_article


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="로컬 RSS·HTML 수집 시연 (외부 호출 없음)")
    parser.add_argument("--samples", type=Path, default=Path(__file__).resolve().parents[1] / "samples" / "collection")
    args = parser.parse_args()
    try:
        fixture = json.loads((Path(__file__).resolve().parents[1] / "samples" / "selection.json").read_text("utf-8"))
        gateway = SampleEngineGateway({**fixture, "delivery_eligible_fixture": True})
        original = fixture["subscription_snapshot"]
        snapshot = gateway.get_subscription_snapshot(original["subscription_id"],
                                                       date.fromisoformat(original["scheduled_date_kst"]))
        now = parse_timestamp(fixture["now"])
        collected = collect_local_samples(args.samples)
        history = [DeliveryHistory(**{**row, "attempted_at": parse_timestamp(row["attempted_at"])})
                   for row in fixture["history"]]
        try:
            result = select_article(snapshot, collected.articles, history, now=now,
                                    collection_succeeded=collected.collection_succeeded)
            status = result.status
            selected = result.article
            reason = result.selection_reason
        except CollectionUnavailable:
            status, selected, reason = "collection_failed", None, None
        output = {
            "demo_only": True, "contract_status": "proposal",
            "collected_count": len(collected.articles),
            "collection_status": ("complete" if collected.collection_succeeded else
                                  "partial" if collected.articles else "failed"),
            "collection_succeeded": collected.collection_succeeded,
            "issues": [asdict(issue) for issue in collected.issues],
            "selection_status": status,
            "selected_title": selected.title if selected else None,
            "selection_reason": reason,
            "delivery_eligible_fixture": gateway.check_delivery_eligibility(snapshot["subscription_id"], now),
            "mail_sent": False,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if status == "collection_failed":
            parser.exit(1)
    except (OSError, ValueError, KeyError, TypeError, LookupError) as exc:
        parser.exit(1, f"로컬 수집 실패 ({type(exc).__name__}). 샘플 파일과 입력 형식을 확인하세요.\n")


if __name__ == "__main__":
    main()
