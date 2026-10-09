"""Fixed template examples, local only; no AI, database or SMTP calls."""
import json
import subprocess
import base64
from datetime import date
from dataclasses import replace
from engine.card_images import approved_images
from engine.mail_assembly import NewsMailData, WebMailLinks, assemble_mail
from engine.card_render import ROOT, render_card_data, resolve_font_path


def main():
    examples = json.loads((ROOT / "engine/samples/template-layouts.json").read_text("utf-8"))
    root = ROOT / ".engine-local/template-examples"
    font = resolve_font_path()
    print("Font format:", font.read_bytes()[:4].hex(), "bytes:", font.stat().st_size)
    for example in examples:
        try:
            result = render_card_data(example["data"], root / example["name"], font_path=font)
        except subprocess.CalledProcessError as error:
            # This tool renders public fixtures only, never subscriber/article data.
            print(error.stderr or "Renderer subprocess failed")
            raise
        output = root / example["name"]
        images, issues = approved_images(example["data"], output)
        if not images or issues:
            raise ValueError("EXAMPLE_IMAGE_REQUIRED")
        mail_data = NewsMailData(job_id="layout-fixture", recipient_email="reader@example.invalid",
            sender_email="briefing@example.invalid", scheduled_date_kst=date(2026,10,9),
            card_data=example["data"], selection_reason={"type":"category","label":"가상 예시"},
            inline_images=images, preview=False, web_links=WebMailLinks("https://example.invalid"),
            feedback_token="FIXTURE_ONLY")
        message = assemble_mail(mail_data)[1]
        html = message.get_body(preferencelist=("html",)).get_content()
        for image in images:
            html = html.replace("cid:" + image.content_id, "data:image/png;base64," + base64.b64encode(image.png).decode())
        (output / "preview.html").write_text(html,encoding="utf-8")
        fallback = assemble_mail(replace(mail_data,inline_images=()))[1].get_body(preferencelist=("html",)).get_content()
        (output / "images-blocked.html").write_text(fallback,encoding="utf-8")
        (output / "briefing.eml").write_bytes(message.as_bytes())
        (output / "mail_result.json").write_text(json.dumps({"content_kind":"news_card","mail_sent":False}),encoding="utf-8")
        layout = result["layout_result"][0]
        print(example["name"], "overflow:", len(layout["overflow"]), "bytes:", layout["bytes"])
    images = "".join(f'<figure><img src="{e["name"]}/card1.png" alt="{e["name"]}"><figcaption>{e["name"]}</figcaption></figure>' for e in examples)
    (root / "index.html").write_text('<!doctype html><html lang="ko"><meta charset="utf-8"><title>고정 뉴스 카드 3종</title><style>body{background:#f1f4f8;font-family:system-ui;margin:24px}main{display:flex;align-items:flex-start;gap:24px;flex-wrap:wrap}figure{margin:0;width:360px}img{width:100%;height:auto}figcaption{padding:10px}</style><h1>고정 뉴스 카드 3종 · 가상 예시</h1><main>' + images + '</main></html>', encoding="utf-8")


if __name__ == "__main__":
    main()
