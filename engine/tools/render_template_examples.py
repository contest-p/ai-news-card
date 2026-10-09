"""Fixed template examples, local only; no AI, database or SMTP calls."""
import json
import subprocess
from engine.card_render import ROOT, render_card_data, resolve_font_path


def main():
    examples = json.loads((ROOT / "engine/samples/template-layouts.json").read_text("utf-8"))
    root = ROOT / ".engine-local/template-examples"
    font = resolve_font_path()
    for example in examples:
        try:
            result = render_card_data(example["data"], root / example["name"], font_path=font)
        except subprocess.CalledProcessError as error:
            # This tool renders public fixtures only, never subscriber/article data.
            print(error.stderr or "Renderer subprocess failed")
            raise
        layout = result["layout_result"][0]
        print(example["name"], "overflow:", len(layout["overflow"]), "bytes:", layout["bytes"])
    images = "".join(f'<figure><img src="{e["name"]}/card1.png" alt="{e["name"]}"><figcaption>{e["name"]}</figcaption></figure>' for e in examples)
    (root / "index.html").write_text('<!doctype html><html lang="ko"><meta charset="utf-8"><title>고정 뉴스 카드 3종</title><style>body{background:#f1f4f8;font-family:system-ui;margin:24px}main{display:flex;align-items:flex-start;gap:24px;flex-wrap:wrap}figure{margin:0;width:360px}img{width:100%;height:auto}figcaption{padding:10px}</style><h1>고정 뉴스 카드 3종 · 가상 예시</h1><main>' + images + '</main></html>', encoding="utf-8")


if __name__ == "__main__":
    main()
