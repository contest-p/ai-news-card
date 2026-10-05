"""검사 완료 뉴스 카드의 HTML·텍스트·CID MIME 조립. SMTP를 사용하지 않는다."""

from dataclasses import dataclass
from datetime import date
from email.message import EmailMessage
from email.policy import SMTP
from html import escape
import hashlib
import re

from engine.card_render import kst_time, validate_render_data
from engine.cards import string
from engine.selection import canonical_url

MAIL_TEMPLATE_VERSION = "news-mail-preview-v1"


@dataclass(frozen=True)
class InlineImage:
    card_number: int
    png: bytes

    @property
    def content_id(self):
        return f"card{self.card_number}.{hashlib.sha256(self.png).hexdigest()[:20]}@ai-news-card.local"


@dataclass(frozen=True)
class NewsMailData:
    job_id: str
    recipient_email: str
    sender_email: str
    scheduled_date_kst: date
    card_data: dict
    selection_reason: dict
    inline_images: tuple[InlineImage, ...] = ()
    # 실제 구독 관리 주소가 준비되기 전에는 로컬 미리보기에서 생략한다.
    subscription_management_url: str | None = None


def email_address(value):
    if (type(value) is not str or len(value) > 254
            or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", value)
            or any(char in value for char in "\r\n")):
        raise ValueError("EMAIL_ADDRESS_INVALID")
    return value


def selection_label(reason):
    if reason.get("type") not in {"keyword", "category"}:
        raise ValueError("SELECTION_REASON_INVALID")
    label = string(reason["label"], 120)
    if reason["type"] == "keyword":
        label += " · " + string(reason["matched_keyword"], 20)
    return label


def https_url(value):
    value = canonical_url(value)
    if not value.startswith("https://"):
        raise ValueError("HTTPS_LINK_REQUIRED")
    return value


def card_text(data, card, *, background):
    sources = {row["article_id"]: row for row in data["sources"]}
    lines = []
    for sentence in card["sentences"]:
        if background and sentence["temporal_role"] == "past":
            lines.append("기준일: " + sentence["as_of"] if sentence["as_of"] else
                         "근거 보도일: " + kst_time(sources[sentence["source_article_id"]]["published_at"]))
        lines.append(sentence["text"])
    lines.extend(term["term"] + ": " + term["definition"] for term in card["terms"])
    return lines


def assemble_mail(data: NewsMailData):
    validate_render_data(data.card_data)
    recipient, sender = email_address(data.recipient_email), email_address(data.sender_email)
    reason = selection_label(data.selection_reason)
    string(data.job_id, 120)
    if any(char in data.job_id for char in "\r\n"):
        raise ValueError("JOB_ID_INVALID")
    images = {}
    for image in data.inline_images:
        if (image.card_number not in {1, 2} or image.card_number in images
                or not image.png.startswith(b"\x89PNG\r\n\x1a\n") or len(image.png) > 2_000_000
                or (image.card_number == 2 and data.card_data["card2"] is None)):
            raise ValueError("INLINE_IMAGE_INVALID")
        images[image.card_number] = image
    title = data.card_data["title"]
    day = data.scheduled_date_kst.strftime("%Y.%m.%d")
    subject = f"[뉴스 브리핑] {day} · 오늘의 관심 뉴스"
    plain = [subject, "", title, "기사 게시: " + kst_time(data.card_data["published_at"]),
             "선택 이유: " + reason, "AI 편집 · 원문 발췌", ""]
    sections = []
    for number, card in enumerate((data.card_data["card1"], data.card_data["card2"]), 1):
        if card is None:
            continue
        label = "핵심 뉴스" if number == 1 else "배경 이해"
        plain.append(label)
        lines = card_text(data.card_data, card, background=number == 2)
        plain.extend(lines + [""])
        image_html = ""
        if number in images:
            image_html = (f'<img src="cid:{images[number].content_id}" width="600" '
                          f'alt="{escape(label + ": " + title + ". 아래에 전체 텍스트 설명이 있습니다.", quote=True)}" '
                          'style="display:block;width:100%;max-width:600px;height:auto;border:0;margin:0 0 24px;">')
        paragraphs = "".join('<p style="margin:0 0 16px;font-size:16px;line-height:1.8;overflow-wrap:anywhere;white-space:pre-wrap;">'
                             + escape(line) + "</p>" for line in lines)
        sections.append(image_html + f'<h2 style="margin:0 0 16px;font-size:20px;">{label} · 텍스트로 읽기</h2>' + paragraphs)
    links = []
    for source in data.card_data["sources"]:
        url = https_url(source["url"])
        plain.extend(["출처: " + source["publisher"] + " · " + kst_time(source["published_at"]), url])
        links.append(f'<p style="margin:0 0 12px;"><a href="{escape(url, quote=True)}" style="color:#1c665a;">'
                     f'{escape(source["publisher"])} · 원문 보기</a><br>{escape(kst_time(source["published_at"]))}</p>')
    management = ""
    if data.subscription_management_url:
        url = https_url(data.subscription_management_url)
        plain.extend(["", "구독 관리: " + url])
        management = f'<p><a href="{escape(url, quote=True)}" style="color:#1c665a;">구독 관리</a></p>'
    notice = "로컬 검수용 메일입니다. 실제 발송 및 피드백 연결은 아직 하지 않았습니다."
    plain.extend(["", notice])
    html = ('<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
            '<body style="margin:0;background:#eef0eb;color:#18272b;font-family:Arial,\'Malgun Gothic\',sans-serif;">'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:24px 12px;">'
            '<table role="presentation" width="600" cellspacing="0" cellpadding="0" style="width:100%;max-width:600px;background:#fff;">'
            '<tr><td style="padding:28px 24px;border-top:6px solid #1c665a;overflow-wrap:anywhere;">'
            f'<p style="font-size:13px;color:#1c665a;letter-spacing:2px;">NEWS BRIEF · {day}</p>'
            f'<h1 style="font-size:25px;line-height:1.5;margin:16px 0;overflow-wrap:anywhere;">{escape(title)}</h1>'
            f'<p style="font-size:14px;color:#52635b;line-height:1.8;">기사 게시 · {escape(kst_time(data.card_data["published_at"]))}<br>'
            f'선택 이유 · {escape(reason)}<br>AI 편집 · 원문 발췌</p>'
            + "".join(sections) + '<div style="border-top:1px solid #d3d9d0;padding-top:20px;font-size:14px;line-height:1.7;">'
            + "".join(links) + management + f'<p style="color:#63716b;">{notice}</p></div>'
            '</td></tr></table></td></tr></table></body></html>')
    message = EmailMessage(policy=SMTP)
    message["From"], message["To"], message["Subject"] = sender, recipient, subject
    # 수신자별 고유 Message-ID. 발송 시각/발송 상태는 여기서 만들지 않는다.
    identity = hashlib.sha256((data.job_id + "\n" + recipient).encode()).hexdigest()
    message["Message-ID"] = f"<{identity}@ai-news-card.local>"
    message.set_content("\n".join(plain), charset="utf-8")
    message.add_alternative(html, subtype="html", charset="utf-8")
    for number, image in images.items():
        message.get_payload()[-1].add_related(image.png, maintype="image", subtype="png",
                                              cid=f"<{image.content_id}>", filename=f"card{number}.png", disposition="inline")
    return subject, message
