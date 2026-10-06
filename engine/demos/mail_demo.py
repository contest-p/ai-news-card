"""실제 생성 카드로 로컬 HTML·텍스트·EML을 만든다. 발송하지 않는다."""

import base64
import json
import sys

from engine.card_images import approved_images
from engine.card_render import ROOT, validate_render_data
from engine.mail_assembly import MAIL_TEMPLATE_VERSION, NewsMailData, assemble_mail


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    root = ROOT / ".engine-local/live-card"
    output_dir = root / "mail"
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        generated = json.loads((root / "result.json").read_text("utf-8"))
        if (generated["status"] != "completed" or generated["result"]["status"] != "ready_for_review"):
            raise ValueError("VALIDATED_CARD_REQUIRED")
        data = generated["result"]["card_data"]
        validate_render_data(data)
        images, issues = approved_images(data, root / "render")
        # 브리핑 날짜는 수집 시각의 KST 날짜로 계산한다.
        from engine.selection import parse_timestamp
        from engine.card_render import KST
        day = parse_timestamp(generated["input_collected_at"]).astimezone(KST).date()
        mail_data = NewsMailData(job_id="local-preview-" + data["article_id"],
                                recipient_email="reader@example.invalid", sender_email="briefing@example.invalid",
                                scheduled_date_kst=day, card_data=data,
                                selection_reason=generated["selection_reason"], inline_images=images,
                                preview=True)
        subject, message = assemble_mail(mail_data)
        (output_dir / "briefing.eml").write_bytes(message.as_bytes())
        html = message.get_body(preferencelist=("html",)).get_content()
        plain = message.get_body(preferencelist=("plain",)).get_content()
        preview = html
        for image in images:
            preview = preview.replace("cid:" + image.content_id, "data:image/png;base64," + base64.b64encode(image.png).decode())
        import re
        blocked = re.sub(r'<img\b[^>]*>', '<p style="padding:16px;background:#f1f3ee;color:#52635b;">이미지가 차단되어 아래 텍스트 설명을 표시합니다.</p>', preview)
        (output_dir / "preview.html").write_text(preview, encoding="utf-8")
        (output_dir / "images-blocked.html").write_text(blocked, encoding="utf-8")
        (output_dir / "text-fallback.txt").write_text(plain, encoding="utf-8")
        report = {"mode": "local_mail_preview", "subject": subject, "mail_template_version": MAIL_TEMPLATE_VERSION,
                  "content_kind": "news_card", "inline_image_count": len(images),
                  "message_bytes": len(message.as_bytes()), "issues": issues,
                  "eml": str(output_dir / "briefing.eml"), "preview": str(output_dir / "preview.html"),
                  "images_blocked": str(output_dir / "images-blocked.html"),
                  "sender_and_recipient": "example.invalid placeholders",
                  "feedback_and_management_links": "not_connected", "human_review_required": True,
                  "mail_sent": False, "database_connected": False, "chat_api_called": False}
        (output_dir / "mail_result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, TypeError):
        sys.exit("메일 조립 실패. 검사 완료 카드와 렌더링 결과를 확인하세요.")


if __name__ == "__main__":
    main()
