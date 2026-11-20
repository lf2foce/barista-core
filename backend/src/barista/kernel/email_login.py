"""Đăng nhập bằng MÃ GỬI QUA EMAIL (0205 §5c) — cho người không dùng Google; cùng luật vào cửa như Google: chỉ email ĐÃ ĐƯỢC MỜI, còn là thành viên
đang hoạt động. Mã 6 số, băm (``core.login_codes``), một lần, 10 phút, tối đa 5 lần nhập sai mỗi mã; xin mã mới ⇒ mã cũ hết hiệu lực.

Không lộ ai có tài khoản: xin mã cho email chưa được mời trả lời Y HỆT (chỉ là không gửi thư). Nhận đúng mã = chứng minh giữ hộp thư đó ⇒ email "đã
xác minh" như Google (``amr`` = ["email"]). Dùng cho đăng nhập Barista (phiên ``core.sessions`` provider "email") và cho ứng dụng first-party (server
ứng dụng xin / kiểm mã thay người dùng, kèm client secret; ``nonce`` gắn mã với trình duyệt đã xin).
"""

import asyncio
import hashlib
import hmac
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from barista.config import settings
from barista.db import db
from barista.kernel import mailer

log = logging.getLogger("barista.email_login")

CODE_TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 5   # mỗi mã
# Lượt SAI theo EMAIL, cộng qua MỌI mã và cả hai đường (Barista + ứng dụng first-party), đếm trong DB (không mất khi khởi động lại): xin mã mới không
# "nạp lại" lượt đoán. Quá ngưỡng ⇒ khoá đăng nhập bằng mã của email đó tới hết cửa sổ, kể cả mã đúng; đúng mã ⇒ xoá đếm (soát: phiên giữ lõi 12/11).
EMAIL_FAILS = ((timedelta(hours=1), 10), (timedelta(days=1), 20))

_sending: set[asyncio.Task[None]] = set()   # thư đang gửi nền — giữ tham chiếu mạnh để task không bị dọn giữa chừng


class EmailLoginUnavailable(Exception):
    """Bản cài chưa cấu hình gửi email (BARISTA_SMTP_URL / BARISTA_MAIL_FROM) hoặc máy chủ thư không nhận."""


class CodeRefused(Exception):
    """Mã sai / hết hạn / đã dùng / quá số lần thử — một câu chung, không nói lý do cụ thể (không giúp dò)."""


def _dev_machine() -> bool:
    """Máy dev THẬT (``BARISTA_ENV`` trống VÀ không địa chỉ công khai nào — API hay web — là https). KHÔNG dựa vào ``dev_auth``: bản cài demo cố ý giữ màn chọn vai (dev_auth bật) — dựa vào đó thì bản cài chưa
    cấu hình thư sẽ ghi mã đăng nhập của người thật ra log máy chủ thay vì gửi thư (lỗi bắt được sau phát hành 12/11)."""
    # bản cài đầu tiên (12/11) KHÔNG đặt BARISTA_ENV ⇒ env trống chưa đủ; bản cài nào cũng có địa chỉ công khai https, máy dev là http://localhost
    # bản đó cũng không đặt BARISTA_PUBLIC_API_URL (mặc định http://localhost) — chỉ BARISTA_WEB_URL https ⇒ xét CẢ HAI địa chỉ
    return settings.env == "" and not any(u.startswith("https://") for u in (settings.public_api_url, settings.web_url))


def available() -> bool:
    """Bản cài gửi được mã (đã cấu hình thư) — hoặc máy dev (mã ghi ra log)."""
    return mailer.configured() or _dev_machine()


def normalize(email: str) -> str:
    return (email or "").strip().lower()


def _hash(row_id: UUID, code: str) -> str:
    return hashlib.sha256(f"{row_id}:{code}".encode()).hexdigest()


def _nonce_hash(nonce: str | None) -> str | None:
    return hashlib.sha256(nonce.encode()).hexdigest() if nonce else None


async def send_code(email: str, *, app: str, client_id: str | None = None, nonce: str | None = None) -> None:
    """Gửi mã cho email nếu email đó đã được mời (còn thành viên đang hoạt động ở ít nhất một công ty); không thì im lặng — người gọi trả lời như nhau."""
    email = normalize(email)
    if "@" not in email or len(email) > 254:
        return
    if not available():   # điều kiện CHUNG của bản cài, không phụ thuộc email ⇒ không lộ ai có tài khoản
        raise EmailLoginUnavailable("Đăng nhập bằng mã qua email chưa được bật trên hệ thống này")
    async with db.owner.connection() as c:
        cur = await c.execute("""SELECT 1 FROM core.users u WHERE u.email = %s
                                 AND EXISTS (SELECT 1 FROM core.memberships m WHERE m.user_id = u.id AND m.active)""", (email,))
        if await cur.fetchone() is None:
            log.info("xin mã đăng nhập cho email chưa được mời — không gửi")
            return
        if await _locked(c, email):
            log.info("xin mã đăng nhập cho email đang bị khoá vì nhập sai nhiều — không gửi")
            return
    code = f"{secrets.randbelow(1_000_000):06d}"
    row_id = uuid.uuid4()
    async with db.owner_tx() as c:
        await c.execute("DELETE FROM core.login_codes WHERE expires_at < now() - interval '1 day'")   # dọn mã cũ
        await c.execute("""UPDATE core.login_codes SET expires_at = least(expires_at, now())
                           WHERE email = %s AND client_id IS NOT DISTINCT FROM %s AND used_at IS NULL""", (email, client_id))   # mã cũ hết hiệu lực
        await c.execute("""INSERT INTO core.login_codes (id, email, code_hash, client_id, nonce_hash, expires_at) VALUES (%s, %s, %s, %s, %s, %s)""",
                        (row_id, email, _hash(row_id, code), client_id, _nonce_hash(nonce), datetime.now(UTC) + CODE_TTL))
    if not mailer.configured():   # tới đây chỉ có thể là máy dev (available()): ghi mã ra log để thử
        log.warning("[dev] mã đăng nhập cho %s: %s", email, code)
        return
    text = (f"Mã đăng nhập {app} của bạn: {code}\n\n"
            f"Mã dùng một lần, hết hạn sau {int(CODE_TTL.total_seconds() // 60)} phút. Không ai từ hệ thống hỏi mã này — đừng đưa cho người khác.\n"
            "Nếu bạn không vừa yêu cầu đăng nhập, bỏ qua thư này.")
    # gửi NỀN: trả lời ngay như với email lạ (chờ SMTP / lỗi SMTP mà lộ ra thì đo thời gian là biết email nào đã được mời — soát 12/11)
    task = asyncio.create_task(_send(email, f"{code} là mã đăng nhập {app}", text))
    _sending.add(task)
    task.add_done_callback(_sending.discard)


async def _send(to: str, subject: str, text: str) -> None:
    try:
        await mailer.send(to, subject, text)
    except mailer.MailError as e:
        log.error("không gửi được mã đăng nhập: %s", e)
    except Exception:
        log.exception("gửi mã đăng nhập lỗi bất ngờ")


async def wait_sent() -> None:
    """Chờ các thư đang gửi nền (test / tắt máy êm)."""
    if _sending:
        await asyncio.gather(*list(_sending), return_exceptions=True)


async def _locked(c: Any, email: str) -> bool:
    for window, limit in EMAIL_FAILS:
        cur = await c.execute("SELECT coalesce(sum(attempts), 0) AS n FROM core.login_codes WHERE email = %s AND created_at > now() - %s",
                              (email, window))
        row = await cur.fetchone()
        if row and row["n"] >= limit:
            return True
    return False


async def check_code(email: str, code: str, *, client_id: str | None = None, nonce: str | None = None) -> UUID:
    """Đúng mã (mới nhất, chưa dùng, còn hạn, đúng ứng dụng + trình duyệt) ⇒ mã dùng xong, trả mã người. Sai ⇒ tăng số lần thử (lưu lại), ``CodeRefused``."""
    email, code = normalize(email), (code or "").strip()
    refused = CodeRefused("Mã không đúng hoặc đã hết hạn — xin mã mới rồi thử lại")
    async with db.owner_tx() as c:
        cur = await c.execute("""SELECT id, code_hash, nonce_hash, attempts FROM core.login_codes
                                 WHERE email = %s AND client_id IS NOT DISTINCT FROM %s AND used_at IS NULL AND expires_at > now()
                                 ORDER BY created_at DESC LIMIT 1 FOR UPDATE""", (email, client_id))
        row = await cur.fetchone()
        if row is None or row["attempts"] >= MAX_ATTEMPTS or await _locked(c, email):
            raise refused
        good = (len(code) == 6 and code.isdigit() and hmac.compare_digest(row["code_hash"], _hash(row["id"], code))
                and (row["nonce_hash"] is None or hmac.compare_digest(row["nonce_hash"], _nonce_hash(nonce) or "")))
        if good:
            await c.execute("UPDATE core.login_codes SET used_at = now() WHERE id = %s", (row["id"],))
            await c.execute("UPDATE core.login_codes SET attempts = 0 WHERE email = %s AND attempts > 0", (email,))   # đúng mã ⇒ xoá đếm sai
            cur = await c.execute("SELECT id FROM core.users WHERE email = %s", (email,))
            user = await cur.fetchone()
        else:
            await c.execute("UPDATE core.login_codes SET attempts = attempts + 1 WHERE id = %s", (row["id"],))   # lưu lại kể cả khi báo sai
            user = None
    if user is None:
        raise refused
    return user["id"]
