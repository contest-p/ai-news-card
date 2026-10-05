"""python -m engine.demos.demo: 외부 연결 없는 가상 데이터 시연."""

import argparse
import json
from pathlib import Path
import sys

from engine.selection import Article, DeliveryHistory, parse_timestamp, select_article


def main() -> None:
    # Windows에서 출력이 파이프로 전달돼도 한글을 UTF-8로 유지한다.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="샘플 기사 선별 (메일 전송 없음)")
    parser.add_argument("--sample", type=Path, default=Path(__file__).resolve().parents[1] / "samples" / "selection.json")
    parser.add_argument("--no-keywords", action="store_true", help="키워드 없는 관심 분야 대체 확인")
    args = parser.parse_args()
    try:
        fixture = json.loads(args.sample.read_text(encoding="utf-8"))
        snapshot = fixture["subscription_snapshot"]
        if args.no_keywords:
            snapshot["keywords"] = []
        articles = [Article(**{**row, "published_at": parse_timestamp(row["published_at"])})
                    for row in fixture["articles"]]
        history = [DeliveryHistory(**{**row, "attempted_at": parse_timestamp(row["attempted_at"])})
                   for row in fixture["history"]]
        result = select_article(snapshot, articles, history,
                                now=parse_timestamp(fixture["now"]),
                                collection_succeeded=fixture["collection_succeeded"])
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        # 입력 내용·이메일·토큰을 오류 로그로 출력하지 않는다.
        parser.exit(1, f"샘플 실행 실패 ({type(exc).__name__}). 입력 형식과 수집 상태를 확인하세요.\n")
    output = {
        "demo_only": True,
        "contract_status": "proposal",
        "status": result.status,
        "selected_article_id": result.article.article_id if result.article else None,
        "title": result.article.title if result.article else None,
        "selection_reason": result.selection_reason,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
