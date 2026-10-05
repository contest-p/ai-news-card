"""API 프로세스: 부모가 전체 60초 뒤 중단. 오류 응답·헤더·비밀값은 출력하지 않는다."""

import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from engine.chat_client import strict_json

MAX_BYTES = 2_000_000  # 응답 크기 제한 제안


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_draft(config):
    request = Request(config["base_url"] + "/chat/completions",
                      data=json.dumps(config["payload"]).encode("utf-8"),
                      headers={"Authorization": "Bearer " + config["api_key"],
                               "Content-Type": "application/json"}, method="POST")
    with build_opener(NoRedirect()).open(request, timeout=55) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError()
    body = strict_json(raw.decode("utf-8"))
    choices = body["choices"]
    if type(choices) is not list or len(choices) != 1 or choices[0]["finish_reason"] != "stop":
        raise ValueError()
    message = choices[0]["message"]
    if message.get("refusal") or type(message["content"]) is not str:
        raise ValueError()
    draft = strict_json(message["content"])
    if type(draft) is not dict:
        raise ValueError()
    return draft


def main():
    try:
        config = json.loads(sys.stdin.read())
        output = {"draft": request_draft(config)}
    except HTTPError as exc:
        code = "CHAT_REDIRECT_BLOCKED" if 300 <= exc.code < 400 else "CHAT_HTTP_" + str(exc.code)
        # 공급자 오류 원문 대신 허용된 키워드만 반환. 키·헤더·본문은 로그에 남기지 않는다.
        try:
            details = exc.read(MAX_BYTES).decode("utf-8", errors="replace").lower()
            hints = [word for word in ("response_format", "max_completion_tokens", "max_tokens", "model", "messages", "credit", "quota", "balance") if word in details]
        except Exception:
            hints = []
        output = {"error": code, "hints": hints}
    except (URLError, TimeoutError, OSError):
        output = {"error": "CHAT_NETWORK_ERROR"}
    except Exception:
        output = {"error": "CHAT_RESPONSE_INVALID"}
    print(json.dumps(output, ensure_ascii=True))


if __name__ == "__main__":
    main()
