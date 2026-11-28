"""Gửi email (0205 §5c — mã đăng nhập; ADR 0265 — hộp thư đi của công ty). SMTP chuẩn ⇒ đổi nhà cung cấp (Resend, Google Workspace, SES…) chỉ là đổi cấu hình:

    BARISTA_SMTP_URL   smtps://<user>:<mật khẩu / khoá API>@<host>:465   (TLS ngay)  hoặc  smtp://…:587 (STARTTLS)
                       Resend: smtps://resend:<khoá API>@smtp.resend.com:465
    BARISTA_MAIL_FROM  người gửi, vd "Công ty <no-reply@vi-du.test>" — tên miền gửi phải khai SPF / DKIM ở nhà cung cấp, không thì thư vào spam

Chưa cấu hình ⇒ ``configured()`` False: nơi gọi tự quyết (đăng nhập bằng mã báo "chưa bật"; máy dev ghi mã ra log; hộp thư đi không cho bấm gửi).

``send`` — thư chữ thuần của hệ thống (mã đăng nhập). ``send_message`` — thư của CÔNG TY (hộp thư đi): thân chữ + HTML, tệp đính kèm, ``Reply-To`` và TÊN
người gửi theo công ty; ĐỊA CHỈ gửi luôn là ``BARISTA_MAIL_FROM`` của bản cài (tên miền đã khai SPF / DKIM) — công ty chỉ đổi tên hiển thị + địa chỉ nhận
trả lời, không giả địa chỉ gửi. ``build`` dựng thư (hàm thuần, test đọc header / phần đính kèm mà không cần máy chủ thư).
"""

import asyncio
import re
import smtplib
import ssl
from collections.abc import Sequence
from dataclasses import dataclass
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr
from urllib.parse import unquote, urlparse

from barista.config import settings


class MailError(Exception):
    """Không gửi được (máy chủ thư từ chối / không nối được) — chữ không chứa mật khẩu."""


def configured() -> bool:
    return bool(settings.smtp_url and settings.mail_from)


@dataclass(frozen=True)
class Attachment:
    filename: str
    data: bytes
    content_type: str = "application/octet-stream"


EMAIL = re.compile(r"^[^@\s<>,;\"]+@[^@\s<>,;\"]+\.[^@\s<>,;\"]+$")


def valid_address(addr: str) -> bool:
    """Một địa chỉ email trơn (không tên, không danh sách) — chặn chèn header / gửi nhiều người qua một ô."""
    return bool(EMAIL.fullmatch(addr.strip())) and len(addr) <= 320


def _sender(from_name: str) -> str:
    """Người gửi: địa chỉ của bản cài (``BARISTA_MAIL_FROM``), tên hiển thị của công ty nếu có (mặc định tên trong ``BARISTA_MAIL_FROM``)."""
    name, addr = parseaddr(settings.mail_from)
    clean = " ".join(from_name.split())[:80]
    return formataddr((clean or name, addr)) if (clean or name) else addr


def build(to: str, subject: str, text: str, *, html: str = "", attachments: Sequence[Attachment] = (), reply_to: str = "",
          from_name: str = "", to_name: str = "") -> EmailMessage:
    if not valid_address(to):
        raise MailError("Địa chỉ người nhận không hợp lệ")
    if reply_to and not valid_address(reply_to):
        raise MailError("Địa chỉ nhận trả lời không hợp lệ")
    msg = EmailMessage()
    msg["From"] = _sender(from_name)
    local, _, domain = to.strip().partition("@")
    msg["To"] = Address(" ".join(to_name.split())[:200], local, domain) if to_name.strip() else to.strip()
    msg["Subject"] = " ".join(subject.split())
    if reply_to:
        msg["Reply-To"] = reply_to.strip()
    msg["Message-ID"] = make_msgid(domain=(parseaddr(settings.mail_from)[1].rpartition("@")[2] or None))
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    for a in attachments:
        main, _, sub = a.content_type.split(";", 1)[0].strip().partition("/")
        msg.add_attachment(a.data, maintype=main or "application", subtype=sub or "octet-stream", filename=a.filename)
    return msg


def _send_sync(msg: EmailMessage) -> None:
    u = urlparse(settings.smtp_url)
    if u.scheme not in ("smtp", "smtps") or not u.hostname:
        raise MailError("BARISTA_SMTP_URL phải dạng smtps://user:pass@host:465 hoặc smtp://…:587")
    ctx = ssl.create_default_context()
    try:
        if u.scheme == "smtps":
            server: smtplib.SMTP = smtplib.SMTP_SSL(u.hostname, u.port or 465, timeout=15, context=ctx)
        else:
            server = smtplib.SMTP(u.hostname, u.port or 587, timeout=15)
            server.starttls(context=ctx)
        with server:
            if u.username:
                server.login(unquote(u.username), unquote(u.password or ""))
            server.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        raise MailError(f"Máy chủ thư {u.hostname} không nhận thư: {type(e).__name__}") from e


async def send(to: str, subject: str, text: str) -> None:
    """Thư chữ thuần của hệ thống (mã đăng nhập)."""
    await asyncio.to_thread(_send_sync, build(to, subject, text))


async def send_message(to: str, subject: str, text: str, *, html: str = "", attachments: Sequence[Attachment] = (), reply_to: str = "",
                       from_name: str = "", to_name: str = "") -> None:
    """Thư của công ty (hộp thư đi — ADR 0265): HTML + đính kèm + Reply-To + tên người gửi."""
    msg = build(to, subject, text, html=html, attachments=attachments, reply_to=reply_to, from_name=from_name, to_name=to_name)
    await asyncio.to_thread(_send_sync, msg)
