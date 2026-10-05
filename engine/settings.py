"""후속 챗 API 설정을 읽는다. 여기서는 API를 호출하지 않는다."""

from dataclasses import dataclass, field
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv


@dataclass(frozen=True)
class ChatSettings:
    api_key: str = field(repr=False)
    base_url: str
    model: str


def load_chat_settings(env_path: Path | None = None) -> ChatSettings:
    # 배포 환경의 Secrets가 로컬 파일보다 우선한다. 키는 출력하지 않는다.
    load_dotenv(env_path or Path(__file__).with_name(".env"), override=False)
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    base_url = os.environ.get("OPENAI_BASE_URL", "").strip()
    model = os.environ.get("OPENAI_MODEL", "gpt-5.5").strip()
    if not key or not base_url or not model:
        raise ValueError("챗 API 키·호출 주소·모델 설정이 필요합니다.")
    parts = urlsplit(base_url)
    if (parts.scheme != "https" or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.query or parts.fragment
            or any(char.isspace() for char in base_url)):
        raise ValueError("API 주소는 인증 정보·query·fragment 없는 HTTPS 주소여야 합니다.")
    return ChatSettings(key, base_url.rstrip("/"), model)
