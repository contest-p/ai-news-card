"""Run backend privacy retention independently of AI generation and mail sending."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from engine.http_gateway import HttpEngineGateway


def load_gateway():
    token = os.environ.get("ENGINE_API_TOKEN", "")
    if len(token) < 32:
        raise ValueError("ENGINE_API_TOKEN_REQUIRED")
    return HttpEngineGateway(os.environ.get("ENGINE_API_BASE_URL", ""), token)


def run_cleanup(gateway):
    return gateway.privacy_cleanup(datetime.now(timezone.utc))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Validate local backend configuration without network access")
    mode.add_argument("--run", action="store_true", help="Invoke backend privacy cleanup; sends no mail")
    args = parser.parse_args(argv)
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    try:
        gateway = load_gateway()
        if args.check:
            print(json.dumps({"status": "configured", "backend_verified": False}))
        else:
            result = run_cleanup(gateway)
            if result.get("status") != "completed":
                raise ValueError("CLEANUP_INCOMPLETE")
            print(json.dumps({"status": "completed"}))
        return 0
    except Exception:
        # Never print credentials, URLs, user data or remote exception messages.
        print(json.dumps({"status": "failed", "error": "PRIVACY_CLEANUP_FAILED"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
