"""뉴스·뉴스 없음·종료 안내의 HTML·텍스트 MIME 조립. DB·SMTP 호출 없음."""

from dataclasses import dataclass, field
from datetime import date, timedelta
from email.message import EmailMessage
from email.policy import SMTP
from html import escape
import hashlib
import re
from urllib.parse import quote, urlsplit

from engine.card_render import kst_time, validate_render_data
from engine.cards import string
from engine.selection import SelectionResult, canonical_url

MAIL_TEMPLATE_VERSION = "briefing-mail-v2"
PREVIEW_NOTICE = "로컬 검수용 메일입니다. 실제 구독·피드백 저장과 연결되지 않은 미리보기입니다."


@dataclass(frozen=True)
class WebMailLinks:
    """운영자가 승인한 웹 origin만 주입. 기사·AI·사용자 입력 URL을 받지 않는다."""

    origin: str

    def __post_init__(self):
        parsed = urlsplit(https_url(self.origin))
        if parsed.path not in {"", "/"} or parsed.query or "#" in self.origin or "?" in self.origin:
            raise ValueError("WEB_ORIGIN_REQUIRED")
        object.__setattr__(self, "origin", f"{parsed.scheme}://{parsed.netloc}")

    @property
    def management_url(self):
        return self.origin + "/manage"

    def feedback_urls(self, token):
        # Backend에서 받은 opaque token을 정규화하거나 재발급하지 않는다.
        if (type(token) is not str or not 1 <= len(token) <= 4096
                or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in token)):
            raise ValueError("FEEDBACK_TOKEN_INVALID")
        return {rating: self.origin + "/feedback#t=" + quote(token, safe="") + "&rating=" + rating
                for rating in ("up", "down")}


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
    recipient_email: str = field(repr=False)
    sender_email: str = field(repr=False)
    scheduled_date_kst: date
    card_data: dict
    selection_reason: dict
    inline_images: tuple[InlineImage, ...] = ()
    # 실제 구독 관리 주소가 준비되기 전에는 로컬 미리보기에서 생략한다.
    subscription_management_url: str | None = None
    web_links: WebMailLinks | None = None
    feedback_token: str | None = field(default=None, repr=False)
    preview: bool = True


@dataclass(frozen=True)
class NoNewsMailData:
    job_id: str
    recipient_email: str = field(repr=False)
    sender_email: str = field(repr=False)
    scheduled_date_kst: date
    web_links: WebMailLinks
    collection_succeeded: bool
    selection_result: SelectionResult
    preview: bool = True


@dataclass(frozen=True)
class EndNoticeMailData:
    job_id: str
    recipient_email: str = field(repr=False)
    sender_email: str = field(repr=False)
    scheduled_date_kst: date
    web_links: WebMailLinks
    subscription_status: str
    start_date: date
    end_date_exclusive: date
    preview: bool = True


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
    if type(value) is not str or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("HTTPS_LINK_REQUIRED")
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


def validate_envelope(data):
    recipient, sender = email_address(data.recipient_email), email_address(data.sender_email)
    string(data.job_id, 120)
    if any(char in data.job_id for char in "\r\n"):
        raise ValueError("JOB_ID_INVALID")
    if type(data.scheduled_date_kst) is not date or type(data.preview) is not bool:
        raise ValueError("MAIL_DATE_OR_PREVIEW_INVALID")
    return recipient, sender


def management_url(data):
    if data.web_links is not None:
        if not isinstance(data.web_links, WebMailLinks):
            raise ValueError("WEB_LINKS_INVALID")
        url = data.web_links.management_url
        if isinstance(data, NewsMailData) and data.subscription_management_url not in {None, url}:
            raise ValueError("MANAGEMENT_LINK_MISMATCH")
        return url
    # 기존 로컬 미리보기 인자는 유지하되 해제 실행 URL이나 토큰 URL은 허용하지 않는다.
    if isinstance(data, NewsMailData) and data.preview:
        raw = data.subscription_management_url
        if raw is None:
            return None
        url = https_url(raw)
        parsed = urlsplit(url)
        if parsed.path != "/manage" or "?" in raw or "#" in raw:
            raise ValueError("MANAGEMENT_PAGE_REQUIRED")
        return url
    raise ValueError("APPROVED_WEB_LINKS_REQUIRED")


def assemble_mail(data: NewsMailData | NoNewsMailData | EndNoticeMailData):
    if isinstance(data, (NoNewsMailData, EndNoticeMailData)):
        return assemble_notice(data)
    if not isinstance(data, NewsMailData):
        raise ValueError("MAIL_DATA_INVALID")
    recipient, sender = validate_envelope(data)
    manage_url = management_url(data)
    feedback = {}
    if data.feedback_token is not None:
        if data.web_links is None:
            raise ValueError("APPROVED_WEB_LINKS_REQUIRED")
        feedback = data.web_links.feedback_urls(data.feedback_token)
    if not data.preview and not feedback:
        raise ValueError("NEWS_FEEDBACK_REQUIRED")
    validate_render_data(data.card_data)
    reason = selection_label(data.selection_reason)
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
    if manage_url:
        plain.extend(["", "구독 관리 (웹사이트 로그인 필요): " + manage_url])
        management = f'<p><a href="{escape(manage_url, quote=True)}" style="color:#1c665a;">구독 관리</a> · 웹사이트 로그인 필요</p>'
    feedback_html = ""
    if feedback:
        plain.extend(["", "이번 브리핑은 어떠셨나요? 링크를 연 뒤 화면에서 제출해야 평가가 저장됩니다."])
        feedback_html = '<p>이번 브리핑은 어떠셨나요? 화면에서 제출해야 평가가 저장됩니다.</p><p>'
        for rating, label in (("up", "도움이 됐어요"), ("down", "아쉬웠어요")):
            plain.append(label + ": " + feedback[rating])
            feedback_html += f'<a href="{escape(feedback[rating], quote=True)}" style="color:#1c665a;">{label}</a> &nbsp; '
        feedback_html += "</p>"
    notice = PREVIEW_NOTICE if data.preview else ""
    if notice:
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
            + "".join(links) + feedback_html + management + (f'<p style="color:#63716b;">{notice}</p>' if notice else "") + '</div>'
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


def assemble_notice(data: NoNewsMailData | EndNoticeMailData):
    """정상 뉴스 없음과 자연 만료만 조립. 발송 가능 여부·기한은 배치에서 재확인."""
    recipient, sender = validate_envelope(data)
    manage_url = management_url(data)
    day = data.scheduled_date_kst.strftime("%Y.%m.%d")
    if isinstance(data, NoNewsMailData):
        result = data.selection_result
        if (data.collection_succeeded is not True or not isinstance(result, SelectionResult)
                or result.status != "no_candidates" or result.article is not None
                or result.selection_reason is not None):
            raise ValueError("SUCCESSFUL_COLLECTION_WITH_NO_CANDIDATES_REQUIRED")
        title = "오늘은 새 브리핑이 없습니다"
        lines = [f"{day} 브리핑 안내입니다.", "관심 분야에서 오늘 보내드릴 새 기사를 찾지 못했습니다.",
                 "구독 기간은 기존 일정대로 유지됩니다."]
        content_kind = "no_news"
        management_label = "구독 관리"
    else:
        if data.subscription_status != "expired":
            raise ValueError("NATURAL_EXPIRY_REQUIRED")
        if (type(data.start_date) is not date or type(data.end_date_exclusive) is not date
                or (data.end_date_exclusive - data.start_date).days not in {7, 14, 28}
                or data.scheduled_date_kst != data.end_date_exclusive):
            raise ValueError("SUBSCRIPTION_PERIOD_INVALID")
        title = "뉴스 브리핑 구독이 종료되었습니다"
        last = data.end_date_exclusive - timedelta(days=1)
        lines = ["선택하신 구독 기간이 만료되었습니다.",
                 f"구독 시작일: {data.start_date:%Y.%m.%d}",
                 f"마지막 구독 날짜: {last:%Y.%m.%d}",
                 f"만료일: {data.end_date_exclusive:%Y.%m.%d} 00:00 (한국 시간)",
                 "다시 받고 싶으시면 웹사이트에 로그인한 뒤 재구독해 주세요."]
        content_kind = "end_notice"
        management_label = "구독 관리·재구독"
    subject = f"[뉴스 브리핑] {day} · {title}"
    plain = [title, "", *lines, "", management_label + " (웹사이트 로그인 필요): " + manage_url]
    if data.preview:
        plain.extend(["", PREVIEW_NOTICE])
    paragraphs = "".join(f'<p style="font-size:16px;line-height:1.8;">{escape(line)}</p>' for line in lines)
    html = ('<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
            '<body style="margin:0;background:#eef0eb;color:#18272b;font-family:Arial,\'Malgun Gothic\',sans-serif;">'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:24px 12px;">'
            '<table role="presentation" width="600" cellspacing="0" cellpadding="0" style="width:100%;max-width:600px;background:#fff;">'
            '<tr><td style="padding:28px 24px;border-top:6px solid #1c665a;overflow-wrap:anywhere;">'
            f'<p style="color:#1c665a;">NEWS BRIEF · {day}</p>'
            f'<h1 style="font-size:25px;line-height:1.5;">{escape(title)}</h1>' + paragraphs
            + f'<p><a href="{escape(manage_url, quote=True)}" style="color:#1c665a;">{management_label}</a> · 웹사이트 로그인 필요</p>'
            + (f'<p style="color:#63716b;">{PREVIEW_NOTICE}</p>' if data.preview else "")
            + '</td></tr></table></td></tr></table></body></html>')
    message = EmailMessage(policy=SMTP)
    message["From"], message["To"], message["Subject"] = sender, recipient, subject
    identity = hashlib.sha256((data.job_id + "\n" + recipient + "\n" + content_kind).encode()).hexdigest()
    message["Message-ID"] = f"<{identity}@ai-news-card.local>"
    message.set_content("\n".join(plain), charset="utf-8")
    message.add_alternative(html, subtype="html", charset="utf-8")
    return subject, message
