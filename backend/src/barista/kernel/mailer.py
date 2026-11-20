"""Gửi email hệ thống (0205 §5c — mã đăng nhập). SMTP chuẩn ⇒ đổi nhà cung cấp (Resend, Google Workspace, SES…) chỉ là đổi cấu hình:

    BARISTA_SMTP_URL   smtps://<user>:<mật khẩu / khoá API>@<host>:465   (TLS ngay)  hoặc  smtp://…:587 (STARTTLS)
                       Resend: smtps://resend:<khoá API>@smtp.resend.com:465
    BARISTA_MAIL_FROM  người gửi, vd "Công ty <no-reply@vi-du.test>" — tên miền gửi phải khai SPF / DKIM ở nhà cung cấp, không thì thư vào spam

Chưa cấu hình ⇒ ``configured()`` False: nơi gọi tự quyết (đăng nhập bằng mã báo "chưa bật"; máy dev ghi mã ra log).
"""

import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid
from urllib.parse import unquote, urlparse

from barista.config import settings


class MailError(Exception):
    """Không gửi được (máy chủ thư từ chối / không nối được) — chữ không chứa mật khẩu."""


def configured() -> bool:
    return bool(settings.smtp_url and settings.mail_from)


def _send_sync(to: str, subject: str, text: str) -> None:
    u = urlparse(settings.smtp_url)
    if u.scheme not in ("smtp", "smtps") or not u.hostname:
        raise MailError("BARISTA_SMTP_URL phải dạng smtps://user:pass@host:465 hoặc smtp://…:587")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = settings.mail_from, to, subject
    msg["Message-ID"] = make_msgid(domain=(settings.mail_from.rsplit("@", 1)[-1].strip(" >") or None))
    msg.set_content(text)
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
    await asyncio.to_thread(_send_sync, to, subject, text)
