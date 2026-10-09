"""워크플로 생성 시각을 기준으로 외부 예약의 목표 정각을 고정한다(표준 라이브러리만 사용)."""

from datetime import datetime, timedelta
import json
import os
from urllib.request import Request, urlopen


def resolve_target(value, created_at):
    if value == "hourly":
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if created.tzinfo is None:
            raise ValueError("CREATED_AT_TIMEZONE_REQUIRED")
        # 매시간 50~55분에 요청한다. 실행기 대기로 넘어간 다음 시간은 사용하지 않는다.
        return (created.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).isoformat()
    if value:
        target = datetime.fromisoformat(value)
        if target.tzinfo is None or target.utcoffset() is None:
            raise ValueError("DELIVERY_AT_TIMEZONE_REQUIRED")
        return target.isoformat()
    return ""


def main():
    value = os.environ.get("DELIVERY_AT", "")
    created_at = ""
    if value == "hourly":
        url = (os.environ["GITHUB_API_URL"] + "/repos/" + os.environ["GITHUB_REPOSITORY"]
               + "/actions/runs/" + os.environ["GITHUB_RUN_ID"])
        request = Request(url, headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"],
                                       "Accept": "application/vnd.github+json"})
        with urlopen(request, timeout=30) as response:
            created_at = json.load(response)["created_at"]
    target = resolve_target(value, created_at)
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write("delivery_at=" + target + "\n")


if __name__ == "__main__":
    main()
