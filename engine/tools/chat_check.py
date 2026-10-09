"""One provider request using public fixture data; no DB, SMTP or raw response logs."""
import json
import sys
from datetime import datetime, timezone

from engine.chat_client import ChatFailure, CodysseyChatClient
from engine.settings import load_chat_settings


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
