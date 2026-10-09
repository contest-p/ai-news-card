"""로컬 단일 PC 전용 생성 작업 기록. Firestore 운영 저장소의 대체가 아니다."""

from contextlib import contextmanager
from dataclasses import asdict
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import tempfile

from engine.card_prompt import PROMPT_VERSION, build_card_messages
from engine.cards import CardInvalid, REVIEW_VALIDATOR_VERSION, assemble_cards, repair_numeric_format
from engine.chat_client import ChatFailure
from engine.selection import CARD_TITLE_MAX_CHARS, nfkc_length

DEFAULT_STATE = Path(__file__).resolve().parents[1] / ".engine-local" / "generation"
VALIDATOR_VERSION = "extractive-validator-v1"
# 기존 요청의 생성 키를 유지한다. 출력 재검사 버전은 별도로 기록하며 호출 한도를 초기화하지 않는다.


class GenerationBusy(RuntimeError):
    pass


class LocalGenerationStore:
    def __init__(self, root=DEFAULT_STATE):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def locked(self, key):
        if len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
            raise ValueError("invalid generation key")
        lock = self.root / (key + ".lock")
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise GenerationBusy("GENERATION_BUSY_OR_INTERRUPTED") from None
        os.close(descriptor)
        try:
            yield self.root / (key + ".json")
        finally:
            lock.unlink()

    def save(self, path, state):
        fd, temporary = tempfile.mkstemp(dir=self.root, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(state, stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def load(self, path):
        return json.loads(path.read_text("utf-8")) if path.exists() else None


def generate_cards(current, rag, *, publishers, work_date_kst: date, job_id, model, base_url,
                   client, store: LocalGenerationStore, retry_blocked=False):
    messages = build_card_messages(current, rag)
    identity = {"job_id": job_id, "messages": messages, "model": model, "base_url": base_url,
                "prompt_version": PROMPT_VERSION, "validator_version": VALIDATOR_VERSION,
                "schema_version": "1.0", "publishers": publishers,
                "work_date_kst": work_date_kst.isoformat(),
                "current_version": current.content_version, "current_hash": current.content_hash,
                "rag_status": rag.status, "rag_cutoff": rag.cutoff_utc.isoformat()}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if nfkc_length(current.article.title.strip()) > CARD_TITLE_MAX_CHARS:
        # 원문 제목은 AI가 바꿀 수 없어 재호출해도 같은 실패다. 호출 횟수를 쓰지 않는다.
        return {"generation_key": key, "attempts": 0, "status": "blocked", "result": None,
                "error_code": "TITLE_TOO_LONG", "api_called_this_run": False, "reused": False}
    with store.locked(key) as path:
        state = store.load(path) or {
            "generation_key": key, "attempts": 0, "status": "pending", "result": None,
            "error_code": None}
        if (state.get("generation_key") != key or type(state.get("attempts")) is not int
                or not 0 <= state["attempts"] <= 2
                or state.get("status") not in {"pending", "in_flight", "completed", "retryable", "blocked"}
                or (state["status"] != "pending" and state["attempts"] == 0)):
            raise ValueError("GENERATION_STATE_INVALID")
        manual_retry = retry_blocked and state["status"] == "blocked" and state["error_code"] == "CHAT_HTTP_400"
        if state["status"] == "completed":
            cached = state["result"]["card_data"]
            checked = assemble_cards({"card1": cached["card1"], "card2": cached["card2"]},
                                     current, rag, publishers=publishers, work_date_kst=work_date_kst)
            previous_issues = state["result"].get("issues", [])
            state["result"] = asdict(checked)
            state["result"]["issues"] = list(dict.fromkeys([*previous_issues, *checked.issues]))
            state["validation_version"] = REVIEW_VALIDATOR_VERSION
            if checked.status != "ready_for_review":
                state.update(status="blocked", error_code="CACHED_CARD_VALIDATION_FAILED")
            store.save(path, state)
            return {**state, "api_called_this_run": False, "reused": True}
        if (state["status"] in {"completed", "in_flight"}
                or (state["status"] == "blocked" and not manual_retry) or state["attempts"] >= 2):
            return {**state, "api_called_this_run": False, "reused": state["status"] == "completed"}
        # 네트워크 요청 전에 영속 기록. 중단·불확실 상태는 자동 재호출하지 않는다.
        state.update(attempts=state["attempts"] + 1, status="in_flight", error_code=None, error_hints=[])
        store.save(path, state)
        try:
            draft = client.complete(messages)
            result = assemble_cards(draft, current, rag, publishers=publishers, work_date_kst=work_date_kst)
            if result.status != "ready_for_review":
                repaired = repair_format(draft, current, rag, publishers, work_date_kst)
                if repaired is not None:
                    result, changes, original = repaired[0], repaired[1], result.issues
                    state["format_review"] = {"version": "numeric-format-review-v1",
                                              "original_issues": list(original), "changes": changes}
            state["result"] = asdict(result)
            if "format_review" in state and result.status == "ready_for_review":
                state["result"]["issues"] = list(result.issues) + state["format_review"]["changes"]
            state["validation_version"] = REVIEW_VALIDATOR_VERSION
            state["status"] = "completed" if result.status == "ready_for_review" else "retryable"
            state["error_code"] = None if state["status"] == "completed" else "CARD_VALIDATION_FAILED"
        except ChatFailure as exc:
            state["error_code"] = exc.code
            state["error_hints"] = list(exc.hints)
            state["status"] = "retryable" if exc.code in {"CHAT_HTTP_429", "CHAT_HTTP_500", "CHAT_HTTP_502", "CHAT_HTTP_503", "CHAT_HTTP_504", "CHAT_RESPONSE_INVALID", "CHAT_RESPONSE_EMPTY", "CHAT_DRAFT_JSON_INVALID"} else "blocked"
        # 기타 중단은 in_flight 기록을 유지하고 재실행을 차단한다.
        store.save(path, state)
        return {**state, "api_called_this_run": True, "reused": False}


def repair_format(draft, current, rag, publishers, work_date_kst):
    """같은 응답의 수치 필드 형식만 보정해 재검사한다. API를 다시 호출하지 않는다."""
    try:
        repaired, changes = repair_numeric_format(draft, current)
        checked = assemble_cards(repaired, current, rag, publishers=publishers, work_date_kst=work_date_kst)
    except (CardInvalid, ValueError, KeyError, TypeError, AttributeError):
        return None
    return (checked, changes) if checked.status == "ready_for_review" and changes else None
