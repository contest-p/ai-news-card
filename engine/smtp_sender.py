"""운영 SMTP 1회 전송과 결과 분류(FR-16). 재시도 횟수·상태 저장은 발송 작업이 담당한다.

- accepted: 서버가 수락함. 수신함 도착·열람 확인은 아니다.
- failed: 서버가 명시적으로 거부했거나 제출 전에 실패. 4xx·연결 실패만 retryable.
- unknown: 제출 중 연결이 끊김·시간 초과 등 수락 여부가 불확실. 자동 재전송 금지.
"""

from dataclasses import dataclass, field
from email.utils import formatdate
import os
from pathlib import Path
import smtplib
import ssl

from dotenv import load_dotenv

from engine.mail_assembly import email_address

SMTP_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class SmtpAccount:
    host: str
    port: int
    security: str
    user: str = field(repr=False)
    password: str = field(repr=False)
    sender: str = field(repr=False)


@dataclass(frozen=True)
class SmtpOutcome:
    status: str  # accepted / failed / unknown
    retryable: bool
    error_code: str | None = None
    smtp_code: int | None = None


def load_smtp_account(env_path: Path | None = None) -> SmtpAccount:
    """배포 Secrets가 로컬 .env보다 우선한다. 값은 출력하지 않는다."""
    load_dotenv(env_path or Path(__file__).with_name(".env"), override=False)
    names = ("SMTP_HOST", "SMTP_PORT", "SMTP_SECURITY", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM")
    values = {name: os.environ.get(name, "").strip() for name in names}
    if any(not value for value in values.values()):
        raise ValueError("SMTP_SETTINGS_MISSING")
    host = values["SMTP_HOST"]
    if any(char.isspace() for char in host) or any(char in host for char in "/:@"):
        raise ValueError("SMTP_HOST_INVALID")
    port = int(values["SMTP_PORT"])
    if not 1 <= port <= 65535 or values["SMTP_SECURITY"] not in {"ssl", "starttls"}:
        raise ValueError("SMTP_TLS_SETTINGS_INVALID")
    return SmtpAccount(host, port, values["SMTP_SECURITY"], values["SMTP_USER"], values["SMTP_PASSWORD"],
                       email_address(values["SMTP_FROM"]))


def connect(account):
    """TLS 연결 후 로그인. 실패하면 연결을 닫고 예외를 전달한다."""
    context = ssl.create_default_context()
    if account.security == "ssl":
        smtp = smtplib.SMTP_SSL(account.host, account.port, timeout=SMTP_TIMEOUT_SECONDS, context=context)
    else:
        smtp = smtplib.SMTP(account.host, account.port, timeout=SMTP_TIMEOUT_SECONDS)
        try:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
        except Exception:
            smtp.close()
            raise
    try:
        smtp.login(account.user, account.password)
    except Exception:
        smtp.close()
        raise
    return smtp


def reply_outcome(code, error_code):
    temporary = isinstance(code, int) and 400 <= code < 500
    return SmtpOutcome("failed", temporary, error_code + ("_TEMPORARY" if temporary else "_PERMANENT"),
                       code if isinstance(code, int) else None)


def send_message(account, message, *, recipient, connector=connect) -> SmtpOutcome:
    """작업의 수신자 1명에게만 1회 제출한다. 서버 응답 원문·비밀번호는 기록하지 않는다."""
    recipient = email_address(recipient)
    if (str(message["To"]) != recipient or str(message["From"]) != account.sender
            or message.get("Cc") or message.get("Bcc") or message.get("Resent-To")):
        raise ValueError("ENVELOPE_MISMATCH")
    if "Date" not in message:
        message["Date"] = formatdate(usegmt=True)
    try:
        smtp = connector(account)
    except smtplib.SMTPAuthenticationError:
        return SmtpOutcome("failed", False, "SMTP_AUTH_FAILED")
    except (OSError, smtplib.SMTPException):
        # 메시지를 제출하기 전이므로 수신자가 받았을 가능성이 없다.
        return SmtpOutcome("failed", True, "SMTP_CONNECT_FAILED")
    try:
        refused = smtp.send_message(message, from_addr=account.sender, to_addrs=[recipient])
        if refused:
            return reply_outcome(refused.get(recipient, (None, b""))[0], "SMTP_RECIPIENT_REFUSED")
        return SmtpOutcome("accepted", False)
    except smtplib.SMTPRecipientsRefused as exc:
        return reply_outcome(exc.recipients.get(recipient, (None, b""))[0], "SMTP_RECIPIENT_REFUSED")
    except smtplib.SMTPResponseException as exc:
        # SMTPSenderRefused·SMTPDataError 등: 서버가 코드로 명시적으로 거부했다.
        return reply_outcome(exc.smtp_code, "SMTP_REJECTED")
    except (OSError, smtplib.SMTPException):
        return SmtpOutcome("unknown", False, "SMTP_RESULT_UNCERTAIN")
    finally:
        try:
            smtp.close()  # QUIT 오류가 이미 정해진 결과를 바꾸지 않게 한다.
        except Exception:
            pass
