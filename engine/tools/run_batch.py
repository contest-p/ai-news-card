"""Run one complete engine batch. Deployment/scheduling is deliberately separate."""

import argparse
import json
import sys
from datetime import datetime, timezone

from engine.firestore_connection import create_client
from engine.runtime import run_connected
from engine.tools.connected_batch import preflight


def log_summary(result):
    """Only aggregate counts reach public Actions logs; detailed results stay local."""
    return {key: value for key, value in result.items() if key not in {"jobs", "run_id"}} | {
        "jobs": {key: value for key, value in result.get("jobs", {}).items() if key != "results"}}


def parse_delivery_at(value, *, now=None):
    """외부 예약은 오프셋이 있는 절대 시각을 전달한다. 늦게 실행되면 즉시 처리한다."""
    if not value:
        return None
    target = datetime.fromisoformat(value)
    if target.tzinfo is None or target.utcoffset() is None:
        raise ValueError("DELIVERY_AT_TIMEZONE_REQUIRED")
    remaining = (target - (now or datetime.now(timezone.utc))).total_seconds()
    if remaining > 20 * 60:
        raise ValueError("DELIVERY_AT_TOO_FAR_IN_FUTURE")
    return target


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="엔진 전체 구독·만료 안내 배치 1회")
    parser.add_argument("--project", default="ai-news-card")
    parser.add_argument("--credentials")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="외부 호출 없이 설정만 확인")
    group.add_argument("--run", action="store_true", help="전체 대상 기사·AI·DB·SMTP 배치 실행")
    parser.add_argument("--text-only", action="store_true")
    parser.add_argument("--safe-log", action="store_true", help="작업별 식별자·상세 결과 없이 집계만 출력")
    parser.add_argument("--delivery-at", default="", help="대상 조회를 시작할 절대 시각(예: 2026-10-10T08:00:00+09:00)")
    args = parser.parse_args()
    client = None
    try:
        delivery_at = parse_delivery_at(args.delivery_at)
        report = preflight(args.project, args.credentials, test=False)
        if args.check or report["missing_configuration"]:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1 if report["missing_configuration"] else 0
        client = create_client(args.project, args.credentials)
        result = run_connected(client, text_only=args.text_only, delivery_at=delivery_at)
        print(json.dumps(log_summary(result) if args.safe_log else result, ensure_ascii=False, indent=2))
        bad = {"failed", "unknown", "crashed", "claim_lost"}
        return int(bool(result["errors"]) or any(result["jobs"]["by_status"].get(s, 0) for s in bad))
    except Exception as error:
        print(json.dumps({"status": "failed", "error_code": "ENGINE_BATCH_FAILED",
                          "error_type": type(error).__name__}))
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    sys.exit(main())
