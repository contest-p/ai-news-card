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

MAIL_TEMPLATE_VERSION = "briefing-mail-v4"
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
    # True=로컬 검수(안내 문구 포함), False=실제 발송용. 기본값 없이 반드시 명시한다.
    preview: bool | None = None
    welcome_notice: str | None = None


@dataclass(frozen=True)
class NoNewsMailData:
    job_id: str
    recipient_email: str = field(repr=False)
    sender_email: str = field(repr=False)
    scheduled_date_kst: date
    web_links: WebMailLinks
    collection_succeeded: bool
    selection_result: SelectionResult
    # True=로컬 검수(안내 문구 포함), False=실제 발송용. 기본값 없이 반드시 명시한다.
    preview: bool | None = None
    welcome_notice: str | None = None


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
    # True=로컬 검수(안내 문구 포함), False=실제 발송용. 기본값 없이 반드시 명시한다.
    preview: bool | None = None


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
    if data.preview is None:
        raise ValueError("PREVIEW_FLAG_REQUIRED")
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


def mail_shell(inner_html, *, preview=False):
    """세 메일 유형이 공유하는 반응형 표 레이아웃. 모든 스타일은 인라인으로 둔다."""
    preview_label = ('<td align="right" valign="middle" style="font-size:13px;color:#738091;">'
                     '메일 수신 예시</td>') if preview else ""
    return ('<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
            '<body style="margin:0;padding:0;background:#f1f4f8;color:#192333;'
            'font-family:Arial,\'Malgun Gothic\',sans-serif;">'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#f1f4f8">'
            '<tr><td align="center" style="padding:22px 10px;">'
            '<table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0" '
            'style="width:100%;max-width:600px;background:#ffffff;border:1px solid #e8ebef;'
            'border-radius:18px;box-shadow:0 8px 28px rgba(28,43,63,.07);">'
            '<tr><td style="padding:24px 24px 0;">'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">'
            '<tr><td width="36" valign="middle" style="width:36px;">'
            '<table role="presentation" cellspacing="0" cellpadding="0" border="0"><tr>'
            '<td width="34" height="26" align="center" valign="middle" bgcolor="#ef850e" '
            'style="width:34px;height:26px;border-radius:5px;color:#ffffff;font-size:20px;line-height:26px;">✉</td>'
            '</tr></table></td><td valign="middle" style="padding-left:10px;color:#ef850e;'
            'font-size:21px;font-weight:700;">뉴스 브리핑</td>' + preview_label +
            '</tr></table><table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">'
            '<tr><td style="padding:16px 0 0;border-bottom:1px solid #e7ebf0;font-size:1px;line-height:1px;">&nbsp;'
            '</td></tr></table></td></tr><tr><td style="padding:20px 24px 26px;overflow-wrap:anywhere;">'
            + inner_html + '</td></tr></table></td></tr></table></body></html>')


def email_button(url, label, *, background="#ef850e", color="#ffffff"):
    """메일 클라이언트 호환을 위해 table 기반 버튼을 만든다. URL은 호출 전에 검증·이스케이프한다."""
    return ('<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" '
            'style="margin:18px 0;"><tr><td align="center"><table role="presentation" cellspacing="0" '
            'cellpadding="0" border="0"><tr><td align="center" bgcolor="' + background + '" '
            'style="border-radius:11px;"><a href="' + escape(url, quote=True) + '" '
            'style="display:inline-block;padding:14px 28px;color:' + color + ';font-size:16px;'
            'font-weight:700;text-decoration:none;border-radius:11px;">' + escape(label) +
            '</a></td></tr></table></td></tr></table>')


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
    current_source = next(row for row in data.card_data["sources"]
                          if row["article_id"] == data.card_data["article_id"])
    current_url = https_url(current_source["url"])
    current_published = kst_time(data.card_data["published_at"])
    subject = f"[뉴스 브리핑] {day} · 오늘의 관심 뉴스"
    if data.welcome_notice:
        subject = "[구독 완료] 첫 뉴스 브리핑 미리보기"
    plain = ["오늘의 관심 뉴스", f"{day} · 하루 한 번, 한눈에 읽는 브리핑", "",
             title, "기사 게시: " + current_published, "선택 이유: " + reason,
             "AI 생성 · 원문을 함께 확인해 주세요.", ""]
    if data.welcome_notice:
        plain.insert(0, data.welcome_notice + "\n")
    sections = []
    source_rows = {row["article_id"]: row for row in data.card_data["sources"]}
    for number, card in enumerate((data.card_data["card1"], data.card_data["card2"]), 1):
        if card is None:
            continue
        label = "오늘의 핵심" if number == 1 else "배경 정보"
        plain.append(label)
        lines = card_text(data.card_data, card, background=number == 2)
        plain.extend(lines + [""])
        image_html = ""
        if number in images:
            image_html = (f'<tr><td style="padding:0 0 16px;"><img src="cid:{images[number].content_id}" width="552" '
                          f'alt="{escape(label + ": " + title + ". 전체 설명과 출처는 아래 텍스트로도 읽을 수 있습니다.", quote=True)}" '
                          'style="display:block;width:100%;max-width:552px;height:auto;border:0;outline:none;'
                          'text-decoration:none;margin:0 auto;"></td></tr>')
        card_heading = ""
        if number not in images:
            card_heading = (
                '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">'
                '<tr><td><span style="display:inline-block;background:#ef850e;border-radius:20px;padding:6px 12px;'
                'color:#ffffff;font-size:12px;font-weight:700;">' + label +
                '</span></td><td align="right"><span style="display:inline-block;background:#f1f3f6;'
                'border-radius:16px;padding:5px 9px;color:#687587;font-size:11px;">AI 생성</span></td></tr></table>'
                '<h2 style="margin:15px 0 7px;font-size:21px;line-height:1.45;letter-spacing:-.3px;'
                'color:#192333;overflow-wrap:anywhere;word-break:break-word;">' + escape(title) + '</h2>'
                '<p style="margin:0 0 15px;font-size:12px;line-height:1.6;color:#738091;">기사 게시 · '
                + escape(current_published if number == 1 else "과거 근거와 사실 기준일은 아래에 표시") + '</p>')
        sentence_html = []
        for sentence in card["sentences"]:
            if number == 2 and sentence["temporal_role"] == "past":
                source = source_rows[sentence["source_article_id"]]
                date_label = ("사실 기준일 · " + sentence["as_of"] if sentence["as_of"] else
                              "근거 보도일 · " + kst_time(source["published_at"]).split(" ")[0])
                sentence_html.append('<tr><td style="padding:0 0 4px;font-size:12px;line-height:1.5;'
                                     'color:#5479ad;">' + escape(date_label) + '</td></tr>')
            sentence_html.append('<tr><td style="padding:0 0 13px;font-size:15px;line-height:1.8;'
                                 'color:#344154;overflow-wrap:anywhere;word-break:break-word;">'
                                 + escape(sentence["text"]) + '</td></tr>')
        terms = ""
        if card["terms"]:
            term_rows = []
            for term in card["terms"][:2]:
                term_rows.append('<tr><td width="145" valign="top" bgcolor="#f1f3f6" style="width:145px;'
                                 'padding:9px 10px;border-radius:7px;font-size:13px;font-weight:700;'
                                 'line-height:1.6;color:#283449;overflow-wrap:anywhere;">'
                                 + escape(term["term"]) + '</td><td valign="top" style="padding:9px 11px;'
                                 'font-size:13px;line-height:1.7;color:#59677a;overflow-wrap:anywhere;'
                                 'word-break:break-word;">' + escape(term["definition"]) + '</td></tr>')
            terms = ('<tr><td style="padding:16px 14px;background:#f4f6f8;border-radius:11px;">'
                     '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">'
                     '<tr><td colspan="2" style="padding:0 0 8px;font-size:15px;font-weight:700;'
                     'color:#192333;">기사 속 용어</td></tr>' + ''.join(term_rows) +
                     '</table></td></tr>')
        card_source_ids = list(dict.fromkeys(
            [sentence["source_article_id"] for sentence in card["sentences"]]
            + [term["source_article_id"] for term in card["terms"]]))
        citations = []
        for source_id in card_source_ids:
            source = source_rows[source_id]
            url = https_url(source["url"])
            posted = kst_time(source["published_at"]).split(" ")[0]
            citations.append('<a href="' + escape(url, quote=True) + '" style="color:#5479ad;'
                             'font-size:12px;line-height:1.7;text-decoration:underline;">'
                             + escape(source["publisher"]) + ' · ' + escape(posted) + ' 보도</a>')
            plain.extend(["출처: " + source["publisher"] + " · " + posted + " 보도", url])
        sections.append(
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" '
            'style="margin:0 0 16px;border:1px solid #e8ebef;border-radius:14px;background:#ffffff;">'
            '<tr><td style="padding:18px 17px 0;">' + card_heading + '</td></tr>' + image_html +
            '<tr><td style="padding:0 17px 3px;"><h3 style="margin:0 0 9px;font-size:16px;line-height:1.5;'
            'color:#192333;">' + ("핵심 뉴스 · 텍스트로 읽기" if number == 1 else "배경 정보 · 텍스트로 읽기") +
            '</h3><table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">'
            + ''.join(sentence_html) + '</table></td></tr>' + terms +
            '<tr><td style="padding:12px 17px 17px;border-top:1px solid #edf0f3;font-size:12px;line-height:1.7;">'
            + ' &nbsp;·&nbsp; '.join(citations) + '</td></tr></table>')
    feedback_html = ""
    if feedback:
        plain.extend(["", "오늘의 브리핑이 도움이 됐나요? 화면에서 제출해야 평가가 저장됩니다."])
        feedback_rows = []
        for rating, label in (("up", "도움이 됐어요"), ("down", "아쉬웠어요")):
            plain.append(label + ": " + feedback[rating])
            feedback_rows.append('<td width="50%" align="center" style="width:50%;padding:0 4px;">'
                                 '<a href="' + escape(feedback[rating], quote=True) + '" style="display:block;'
                                 'padding:10px 4px;border:1px solid #d8dee7;border-radius:9px;color:#536176;'
                                 'font-size:12px;line-height:1.4;text-decoration:none;">' + escape(label) + '</a></td>')
        feedback_html = ('<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" '
                         'style="margin:18px 0 0;border-top:1px solid #e7ebf0;"><tr><td align="center" '
                         'colspan="2" style="padding:15px 0 10px;font-size:14px;font-weight:700;color:#192333;">'
                         '오늘의 브리핑이 도움이 됐나요?</td></tr><tr>' + ''.join(feedback_rows) + '</tr></table>')
    management = ""
    if manage_url:
        plain.extend(["", "웹사이트에서 구독 관리하기 (로그인 필요): " + manage_url])
        management = ('<p style="margin:15px 0 4px;text-align:center;font-size:13px;line-height:1.6;">'
                      '<a href="' + escape(manage_url, quote=True) + '" style="color:#1671cf;'
                      'text-decoration:none;">웹사이트에서 구독 관리하기 →</a></p>')
    disclaimer = ('<p style="margin:4px 0 0;text-align:center;font-size:11px;line-height:1.7;color:#738091;">'
                  'AI가 기사 내용을 요약·설명했어요. 정확한 맥락은 원문을 확인해 주세요.</p>')
    notice = PREVIEW_NOTICE if data.preview else ""
    if notice:
        plain.extend(["", notice])
    reason_block = ('<table role="presentation" cellspacing="0" cellpadding="0" border="0" '
                    'style="margin:13px 0 18px;"><tr><td bgcolor="#fff4e7" style="padding:8px 13px;'
                    'border-radius:20px;font-size:13px;line-height:1.5;color:#70491f;">'
                    '<strong style="color:#283449;">선택 이유</strong> · ' + escape(reason) +
                    '</td></tr></table>')
    ai_note = '<p style="margin:0;font-size:12px;line-height:1.6;color:#738091;">AI 생성 · 원문 근거를 바탕으로 설명했어요. 영문 기사는 한국어로 번역했어요.</p>'
    notice_html = ('<p style="margin:12px 0 0;padding:10px 12px;background:#f4f6f8;border-radius:8px;'
                   'font-size:11px;line-height:1.6;color:#63716b;">' + escape(notice) + '</p>') if notice else ""
    html = mail_shell(
        ('<p style="padding:14px;background:#fff4e7;font-size:15px;line-height:1.8;color:#70491f;">'
         + escape(data.welcome_notice) + '</p>' if data.welcome_notice else '') +
        '<p style="margin:0 0 4px;font-size:12px;line-height:1.5;color:#738091;">' +
        escape(day) + ' · 하루 한 번, 한눈에 읽는 브리핑</p>'
        '<h1 style="margin:0;font-size:30px;line-height:1.3;letter-spacing:-.5px;color:#192333;">오늘의 관심 뉴스</h1>'
        + reason_block + ai_note + ''.join(sections)
        + email_button(current_url, "원문 읽기 ↗") + feedback_html + management + disclaimer + notice_html,
        preview=data.preview)
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
        if data.welcome_notice:
            title = "뉴스 브리핑 구독이 완료됐어요"
            lines = [data.welcome_notice, "현재 선택 분야의 새 기사를 찾지 못해 구독 완료 안내를 먼저 보내드립니다."]
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
    if isinstance(data, NoNewsMailData) and data.welcome_notice:
        subject = "[구독 완료] 첫 뉴스 브리핑 안내"
    plain = [title, "", *lines, "", management_label + " (웹사이트 로그인 필요): " + manage_url]
    if data.preview:
        plain.extend(["", PREVIEW_NOTICE])
    paragraphs = "".join('<tr><td style="padding:0 0 12px;font-size:15px;line-height:1.8;'
                         'color:#344154;overflow-wrap:anywhere;word-break:break-word;">'
                         + escape(line) + '</td></tr>' for line in lines)
    notice_card = (
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" '
        'style="margin-top:16px;border:1px solid #e8ebef;border-radius:14px;background:#fff;">'
        '<tr><td style="padding:19px 18px 0;"><span style="display:inline-block;padding:6px 12px;'
        'border-radius:20px;background:#fff4e7;color:#cf6800;font-size:12px;font-weight:700;">'
        + ("새 소식 안내" if content_kind == "no_news" else "구독 종료 안내") +
        '</span><h1 style="margin:14px 0 13px;font-size:25px;line-height:1.4;color:#192333;'
        'overflow-wrap:anywhere;word-break:break-word;">' + escape(title) + '</h1></td></tr>'
        '<tr><td style="padding:0 18px 8px;"><table role="presentation" width="100%" cellspacing="0" '
        'cellpadding="0" border="0">' + paragraphs + '</table></td></tr></table>')
    notice = ('<p style="margin:10px 0 0;padding:10px 12px;background:#f4f6f8;border-radius:8px;'
              'font-size:11px;line-height:1.6;color:#63716b;">' + escape(PREVIEW_NOTICE) + '</p>') if data.preview else ""
    html = mail_shell(
        '<p style="margin:0;font-size:12px;line-height:1.5;color:#738091;">' + escape(day) + '</p>'
        + notice_card + email_button(manage_url, management_label) +
        '<p style="margin:0;text-align:center;font-size:12px;line-height:1.6;color:#738091;">웹사이트 로그인이 필요합니다.</p>'
        + notice,
        preview=data.preview)
    message = EmailMessage(policy=SMTP)
    message["From"], message["To"], message["Subject"] = sender, recipient, subject
    identity = hashlib.sha256((data.job_id + "\n" + recipient + "\n" + content_kind).encode()).hexdigest()
    message["Message-ID"] = f"<{identity}@ai-news-card.local>"
    message.set_content("\n".join(plain), charset="utf-8")
    message.add_alternative(html, subtype="html", charset="utf-8")
    return subject, message
