"""같은 발송 작업의 재시도에 쓸 완성 MIME 보관(공통 PRD 10-6).

재시도 때 메일 내용·피드백 토큰을 바꾸지 않기 위해 첫 조립 결과를 그대로 다시 보낸다.
수신 주소와 피드백 토큰 원문이 들어 있으므로 접근 제한·보관 기한이 필요하다.
운영 보관 위치(Firestore/Storage 등)와 기한은 2026-10-07 DB 작업에서 Backend와 정한다.
"""

import os
from pathlib import Path
import tempfile
from threading import RLock
from typing import Protocol


class MailArchive(Protocol):
    def load(self, job_id: str) -> bytes | None: ...

    def save(self, job_id: str, message_bytes: bytes) -> None: ...


def valid_job_id(job_id: str) -> str:
    if len(job_id) != 64 or any(char not in "0123456789abcdef" for char in job_id):
        raise ValueError("JOB_ID_INVALID")
    return job_id


class InMemoryMailArchive:
    def __init__(self):
        self._items: dict[str, bytes] = {}
        self._lock = RLock()

    def load(self, job_id):
        with self._lock:
            return self._items.get(valid_job_id(job_id))

    def save(self, job_id, message_bytes):
        with self._lock:
            # 이미 있으면 덮어쓰지 않는다. 발송됐을 수 있는 내용과 다른 메일을 만들지 않는다.
            self._items.setdefault(valid_job_id(job_id), bytes(message_bytes))


class LocalMailArchive:
    """단일 PC 개발용. Git 제외 폴더에 저장하며 운영 저장소가 아니다."""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def load(self, job_id):
        path = self.root / (valid_job_id(job_id) + ".eml")
        return path.read_bytes() if path.exists() else None

    def save(self, job_id, message_bytes):
        path = self.root / (valid_job_id(job_id) + ".eml")
        if path.exists():
            return
        fd, temporary = tempfile.mkstemp(dir=self.root, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(message_bytes)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
