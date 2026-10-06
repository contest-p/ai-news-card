"""카드 이미지 렌더링 재시도(P-16)와 승인된 PNG만 메일에 붙이는 검사.

이미지가 없어도 메일 본문에는 검증된 전체 텍스트 설명이 들어가므로 발송을 막지 않는다.
"""

import hashlib
import json
from pathlib import Path

from engine.card_render import card_data_hash, render_card_data
from engine.mail_assembly import InlineImage

MAX_IMAGE_BYTES = 2_000_000
PNG_SIGNATURE = bytes.fromhex("89504e470d0a1a0a")
RENDER_ATTEMPTS = 2  # P-16: 최초 + 1회


def approved_images(data, render_root):
    """렌더링 결과가 현재 카드 데이터와 같고 레이아웃 검사를 통과한 PNG만 반환한다."""
    render_root = Path(render_root).resolve()
    path = render_root / "render_result.json"
    if not path.exists():
        return (), ["IMAGES_NOT_AVAILABLE_TEXT_ONLY"]
    try:
        manifest = json.loads(path.read_text("utf-8"))
        if manifest["card_data_sha256"] != card_data_hash(data):
            return (), ["IMAGE_CARD_INPUT_MISMATCH_TEXT_ONLY"]
        images, issues = [], []
        for index, raw_path in enumerate(manifest["image_files"]):
            image_path = Path(raw_path).resolve()
            number = index + 1
            if (not image_path.is_relative_to(render_root)
                    or image_path.name != f"card{number}.png" or number > 2):
                raise ValueError("UNAPPROVED_IMAGE_PATH")
            image_bytes = image_path.read_bytes()
            check = manifest["layout_result"][index]
            if (check["overflow"] or check["externalRequests"] or not check["fontLoaded"]
                    or hashlib.sha256(image_bytes).hexdigest() != check["sha256"]
                    or not image_bytes.startswith(PNG_SIGNATURE) or len(image_bytes) > MAX_IMAGE_BYTES):
                issues.append(f"CARD{number}_IMAGE_INVALID_TEXT_ONLY")
                continue
            images.append(InlineImage(number, image_bytes))
        return tuple(images), issues
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return (), ["IMAGE_MANIFEST_INVALID_TEXT_ONLY"]


def render_with_fallback(data, output_dir, *, font_path, renderer=render_card_data):
    """최초 + 1회 렌더링한다. 모두 실패하면 이미지 없이 (빈 튜플, 이유)를 돌려준다."""
    output_dir = Path(output_dir)
    issues = []
    for attempt in range(1, RENDER_ATTEMPTS + 1):
        attempt_dir = output_dir / f"attempt-{attempt}"
        try:
            renderer(data, attempt_dir, font_path=font_path)
        except Exception:
            # 렌더러·브라우저·폰트 오류 원문은 기록하지 않는다. 다음 시도 또는 텍스트 전환.
            continue
        images, image_issues = approved_images(data, attempt_dir)
        if images and not image_issues:
            return images, issues + (["CARD_IMAGE_RETRIED"] if attempt > 1 else [])
        issues.extend(image_issues)
    return (), issues + ["CARD_IMAGE_FAILED_TEXT_ONLY"]
