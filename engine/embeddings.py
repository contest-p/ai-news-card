"""고정된 multilingual-e5-small CPU 임베딩. 기본 실행은 로컬 캐시만 사용."""

from pathlib import Path
import math
import os
from typing import Protocol

MODEL_ID = "intfloat/multilingual-e5-small"
MODEL_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
DIMENSIONS = 384
MAX_TOKENS = 512
DEFAULT_CACHE = Path(__file__).resolve().parents[1] / ".venv" / "model-cache"


class Encoder(Protocol):
    model_id: str
    model_revision: str

    def encode_query(self, text: str) -> tuple[float, ...]: ...

    def encode_passages(self, texts: list[str]) -> list[tuple[float, ...]]: ...


def validate_vector(vector) -> tuple[float, ...]:
    if len(vector) != DIMENSIONS:
        raise ValueError("임베딩은 384차원이어야 합니다.")
    values = tuple(float(value) for value in vector)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("임베딩에 유효하지 않은 수치가 있습니다.")
    norm = math.sqrt(sum(value * value for value in values))
    if not math.isclose(norm, 1.0, abs_tol=1e-4):
        raise ValueError("임베딩은 정규화된 단위 벡터여야 합니다.")
    return values


class E5Encoder:
    model_id = MODEL_ID
    model_revision = MODEL_REVISION

    def __init__(self, *, allow_download: bool = False, cache_dir: Path = DEFAULT_CACHE):
        import torch
        from transformers import AutoModel, AutoTokenizer

        self._torch = torch
        torch.set_num_threads(min(4, os.cpu_count() or 1))
        options = dict(revision=MODEL_REVISION, cache_dir=str(cache_dir),
                       local_files_only=not allow_download, trust_remote_code=False, token=False)
        self._tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, **options)
        self._model = AutoModel.from_pretrained(MODEL_ID, use_safetensors=True, **options).to("cpu")
        self._model.eval()
        if self._model.config.hidden_size != DIMENSIONS:
            raise ValueError("모델 차원이 공통 규격과 다릅니다.")

    def _encode(self, texts: list[str], prefix: str) -> list[tuple[float, ...]]:
        if not texts:
            return []
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("임베딩 입력은 비어 있지 않은 문자열이어야 합니다.")
        inputs = self._tokenizer([prefix + text for text in texts], max_length=MAX_TOKENS,
                                 padding=True, truncation=True, return_tensors="pt")
        with self._torch.inference_mode():
            output = self._model(**inputs).last_hidden_state
            mask = inputs["attention_mask"]
            hidden = output.masked_fill(~mask[..., None].bool(), 0.0)
            pooled = hidden.sum(dim=1) / mask.sum(dim=1)[..., None]
            normalized = self._torch.nn.functional.normalize(pooled, p=2, dim=1)
        return [validate_vector(row) for row in normalized.tolist()]

    def encode_query(self, text: str) -> tuple[float, ...]:
        return self._encode([text], "query: ")[0]

    def encode_passages(self, texts: list[str]) -> list[tuple[float, ...]]:
        return self._encode(texts, "passage: ")
