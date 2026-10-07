"""Bounded read-only inspection of the current Backend collections. No PII output."""

import argparse
from collections import Counter
import json
import sys

from engine.firestore_connection import create_client


# These are gaps to discuss with Backend, not an EngineGateway implementation.
SETTING_FIELDS = ("categories", "keywords", "send_time", "start_date", "end_date_exclusive")


def inspect_database(client, limit: int = 100) -> dict:
    if not 1 <= limit <= 1000:
        raise ValueError("LIMIT_OUT_OF_RANGE")
    result = {"mode": "read_only", "database_connected": True,
              "limit_per_collection": limit, "collections": {},
              "automatic_delivery_enabled": False}
    for name in ("users", "subscriptions", "engine_articles"):
        documents = list(client.collection(name).limit(limit + 1).stream(timeout=20, retry=None))
        sampled = documents[:limit]
        summary = {"sampled_count": len(sampled), "truncated": len(documents) > limit}
        if name == "subscriptions":
            statuses = Counter()
            missing = Counter()
            for document in sampled:
                data = document.to_dict()
                status = data.get("status")
                statuses[status if status in ("active", "cancelled", "expired") else "other"] += 1
                for field in SETTING_FIELDS:
                    if field not in data or data[field] is None:
                        missing[field] += 1
            summary.update(status_counts=dict(statuses), missing_setting_counts=dict(missing))
        result["collections"][name] = summary
    return result


def main():
    parser = argparse.ArgumentParser(description="Firestore 기존 DB 읽기 확인 (개인정보·쓰기·메일 없음)")
    parser.add_argument("--project", required=True)
    parser.add_argument("--credentials")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error("--limit must be 1..1000")
    client = None
    try:
        client = create_client(args.project, args.credentials)
        print(json.dumps(inspect_database(client, args.limit), ensure_ascii=False, indent=2))
    except Exception as error:
        # SDK errors may contain resource paths or credential details.
        print(json.dumps({"database_connected": False, "error_type": type(error).__name__,
                          "action": "Check server credentials, project ID, IAM and network."}))
        return 1
    finally:
        if client is not None:
            client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
