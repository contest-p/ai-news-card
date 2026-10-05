"""코디세이의 OpenAI 호환 Chat Completions 어댑터. 숨은 재시도 없음."""

import json
from pathlib import Path
import subprocess
import sys

from engine.settings import ChatSettings

TIMEOUT_SECONDS = 60
MAX_COMPLETION_TOKENS = 3000  # 로컬 검증용 제안. 운영 비용 한도는 별도 확정.


class ChatFailure(RuntimeError):
    def __init__(self, code, hints=()):
        self.code = code
        self.hints = tuple(hints)
        super().__init__(code)


def strict_json(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    def bad_constant(value):
        raise ValueError("nonfinite JSON")
    return json.loads(text, object_pairs_hook=unique, parse_constant=bad_constant)


class CodysseyChatClient:
    def __init__(self, settings: ChatSettings, *, compatible_request=True):
        self.settings = settings
        self.compatible_request = compatible_request

    def complete(self, messages):
        request = {"base_url": self.settings.base_url, "api_key": self.settings.api_key,
                   "payload": {"model": self.settings.model, "messages": messages,
                               "response_format": {"type": "json_object"},
                               "max_completion_tokens": MAX_COMPLETION_TOKENS}}
        if self.compatible_request:
            request["payload"].pop("response_format")
            request["payload"].pop("max_completion_tokens")
            request["payload"]["max_tokens"] = MAX_COMPLETION_TOKENS
        try:
            # 키는 명령 인자·환경 변수 추가·디스크 로그 대신 자식 프로세스 stdin으로 전달.
            process = subprocess.run([sys.executable, "-m", "engine.chat_worker"],
                                     input=json.dumps(request), capture_output=True,
                                     encoding="utf-8", timeout=TIMEOUT_SECONDS,
                                     cwd=Path(__file__).resolve().parents[1])
        except subprocess.TimeoutExpired:
            raise ChatFailure("CHAT_TIMEOUT") from None
        except OSError:
            raise ChatFailure("CHAT_WORKER_UNAVAILABLE") from None
        try:
            envelope = strict_json(process.stdout)
            if type(envelope) is not dict or process.returncode != 0:
                raise ValueError()
            if "error" in envelope:
                code = envelope["error"]
                if code not in {"CHAT_NETWORK_ERROR", "CHAT_RESPONSE_INVALID", "CHAT_REDIRECT_BLOCKED"} and not (
                        type(code) is str and code.startswith("CHAT_HTTP_") and code[10:].isdigit()):
                    code = "CHAT_RESPONSE_INVALID"
                hints = envelope.get("hints", [])
                allowed = {"response_format", "max_completion_tokens", "max_tokens", "model", "messages", "credit", "quota", "balance"}
                raise ChatFailure(code, [hint for hint in hints if type(hint) is str and hint in allowed])
            return envelope["draft"]
        except (ValueError, TypeError, KeyError):
            raise ChatFailure("CHAT_RESPONSE_INVALID") from None
