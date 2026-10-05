"""테스트 주소 한 곳에만 SMTP 전송. --check는 인증 확인, --send는 실제 전송."""

import argparse
from dataclasses import dataclass, field
from email.parser import BytesParser
from email.policy import SMTP
import hashlib
import json
import os
from pathlib import Path
import smtplib
import ssl
import sys

from dotenv import load_dotenv

from engine.card_render import ROOT, card_data_hash
from engine.generation import LocalGenerationStore
from engine.mail_assembly import NewsMailData, assemble_mail, email_address
from engine.demos.mail_demo import approved_images
from engine.selection import parse_timestamp
from engine.card_render import KST


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    security: str
    user: str = field(repr=False)
    password: str = field(repr=False)
    sender: str = field(repr=False)
    recipient: str = field(repr=False)


def load_smtp_settings():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    names = ("SMTP_HOST", "SMTP_PORT", "SMTP_SECURITY", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_TEST_TO")
    values = {name: os.environ.get(name, "").strip() for name in names}
    if any(not value for value in values.values()):
        raise ValueError("SMTP_SETTINGS_MISSING")
    host = values["SMTP_HOST"]
    if any(char.isspace() for char in host) or any(char in host for char in "/:@"):
        raise ValueError("SMTP_HOST_INVALID")
    port = int(values["SMTP_PORT"])
    if not 1 <= port <= 65535 or values["SMTP_SECURITY"] not in {"ssl", "starttls"}:
        raise ValueError("SMTP_TLS_SETTINGS_INVALID")
    sender, recipient = email_address(values["SMTP_FROM"]), email_address(values["SMTP_TEST_TO"])
    if sender.endswith(".invalid") or recipient.endswith(".invalid"):
        raise ValueError("REAL_TEST_ADDRESSES_REQUIRED")
    return SmtpSettings(host, port, values["SMTP_SECURITY"], values["SMTP_USER"], values["SMTP_PASSWORD"], sender, recipient)


def connect(settings):
    context = ssl.create_default_context()
    if settings.security == "ssl":
        smtp = smtplib.SMTP_SSL(settings.host, settings.port, timeout=30, context=context)
    else:
        smtp = smtplib.SMTP(settings.host, settings.port, timeout=30)
        try:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
        except Exception:
            smtp.close()
            raise
    try:
        smtp.login(settings.user, settings.password)
    except Exception:
        smtp.close()
        raise
    return smtp


def send_once(settings, message, *, store, key, connector=connect):
    # MIME 헤더와 실제 SMTP envelope 모두 설정한 테스트 주소 한 곳으로 고정.
    if (str(message["To"]) != settings.recipient or str(message["From"]) != settings.sender
            or message.get("Cc") or message.get("Bcc") or message.get("Resent-To")):
        raise ValueError("TEST_ENVELOPE_MISMATCH")
    with store.locked(key) as path:
        state = json.loads(path.read_text("utf-8")) if path.exists() else {"status": "pending", "attempts": 0}
        if state["status"] in {"smtp_accepted", "in_flight", "unknown"} or state["attempts"] >= 2:
            return {**state, "smtp_called_this_run": False}
        state["attempts"] += 1
        store.save(path, state)
        smtp = None
        submitted = False
        try:
            smtp = connector(settings)
            # 실제 제출 전에 디스크에 기록. 중단된 요청은 재전송하지 않는다.
            state.update(status="in_flight", error_code=None)
            store.save(path, state)
            submitted = True
            refused = smtp.send_message(message, from_addr=settings.sender, to_addrs=[settings.recipient])
            state.update(status="smtp_accepted" if not refused else "rejected", error_code=None)
        except (OSError, smtplib.SMTPException):
            # 응답·주소·비밀번호는 출력하지 않는다. 전송 중 오류는 보수적으로 불확실 처리.
            state.update(status="unknown" if submitted else "failed_before_send",
                         error_code="SMTP_RESULT_UNCERTAIN" if submitted else "SMTP_CONNECTION_OR_AUTH_FAILED")
        finally:
            if smtp is not None:
                try:
                    smtp.close()  # QUIT 오류가 이미 접수된 전송 결과를 바꾸지 않도록 한다.
                except OSError:
                    pass
        store.save(path, state)
        return {**state, "smtp_called_this_run": True}


def prepare_message(settings, output_root):
    root = ROOT / ".engine-local/live-card"
    result = json.loads((root / "result.json").read_text("utf-8"))
    if result["status"] != "completed" or result["result"]["status"] != "ready_for_review":
        raise ValueError("VALIDATED_CARD_REQUIRED")
    data = result["result"]["card_data"]
    images, issues = approved_images(data, root / "render")
    job = "smtp-test-" + card_data_hash(data)
    identity = job + "\n" + settings.sender + "\n" + settings.recipient
    key = hashlib.sha256(identity.encode()).hexdigest()
    path = output_root / (key + ".eml")
    if path.exists():
        message = BytesParser(policy=SMTP).parsebytes(path.read_bytes())
    else:
        _, message = assemble_mail(NewsMailData(job_id=job, recipient_email=settings.recipient,
                                  sender_email=settings.sender,
                                  scheduled_date_kst=parse_timestamp(result["input_collected_at"]).astimezone(KST).date(),
                                  card_data=data, selection_reason=result["selection_reason"], inline_images=images))
        path.write_bytes(message.as_bytes())
    return message, key, issues


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="SMTP 인증 확인 또는 테스트 주소에 실제 1회 전송")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="TLS 연결·인증만 확인, 전송하지 않음")
    group.add_argument("--send", action="store_true", help="SMTP_TEST_TO에 실제 테스트 메일 전송")
    args = parser.parse_args()
    try:
        settings = load_smtp_settings()
        if args.check:
            smtp = connect(settings)
            smtp.close()
            print(json.dumps({"status": "smtp_auth_verified", "message_submitted": False}))
            return
        root = ROOT / ".engine-local/smtp-test"
        root.mkdir(parents=True, exist_ok=True)
        message, key, issues = prepare_message(settings, root)
        output = send_once(settings, message, store=LocalGenerationStore(root / "state"), key=key)
        output.update(test_only=True, image_issues=issues, delivery_confirmed=False,
                      recipient_is_configured_test_address=True)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if output["status"] != "smtp_accepted":
            parser.exit(1)
    except (OSError, smtplib.SMTPException, ValueError, KeyError, TypeError, RuntimeError):
        parser.exit(1, "SMTP 준비·인증 확인 실패. engine/.env의 메일 설정과 로컬 작업 기록을 확인하세요.\n")


if __name__ == "__main__":
    main()
