"""Run one complete engine batch. Deployment/scheduling is deliberately separate."""

import argparse
import json
import sys

from engine.firestore_connection import create_client
from engine.runtime import run_connected
from engine.tools.connected_batch import preflight


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
    args = parser.parse_args()
    client = None
    try:
        report = preflight(args.project, args.credentials, test=False)
        if args.check or report["missing_configuration"]:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1 if report["missing_configuration"] else 0
        client = create_client(args.project, args.credentials)
        result = run_connected(client, text_only=args.text_only)
        print(json.dumps(result, ensure_ascii=False, indent=2))
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
