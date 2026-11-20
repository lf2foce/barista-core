"""Gửi đẩy APNs (quyết định 0044) — hộp thông báo là nguồn sự thật, đẩy chỉ là "gõ cửa".

Thông báo ghi trong giao dịch nghiệp vụ với ``push_state = 'pending'``. Bộ gửi ở đây chạy NGOÀI giao dịch đó (được đánh thức bởi
NOTIFY, hoặc 30 giây một lần), nên APNs chậm / lỗi không bao giờ làm hỏng lệnh nghiệp vụ, và chết giữa chừng thì lần sau gửi tiếp.
Nội dung đẩy = dòng ``push`` (không tên khách / SĐT / số tiền) + mã để app mở đúng thẻ; chi tiết app đọc lại qua RLS.
Một máy đổi người dùng ⇒ chỉ người đăng ký mã đó MỚI NHẤT nhận.
"""

import logging
import time
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx
import jwt

from barista.config import settings
from barista.db import db

log = logging.getLogger("barista.push")
MAX_ATTEMPTS = 5
type Outcome = Literal["ok", "gone", "error"]


class Sender(Protocol):
    async def send(self, token: str, env: str, payload: dict[str, Any]) -> Outcome: ...


class APNs:
    """HTTP/2 tới Apple, xác thực bằng JWT ký khoá .p8 (một khoá dùng cho mọi app của nhóm, không hết hạn)."""

    def __init__(self, key_pem: str, key_id: str, team_id: str, topic: str) -> None:
        self.key, self.key_id, self.team_id, self.topic = key_pem, key_id, team_id, topic
        self._jwt, self._jwt_at = "", 0.0
        self._client = httpx.AsyncClient(http2=True, timeout=10)

    def _token(self) -> str:
        if time.time() - self._jwt_at > 50 * 60:   # Apple nhận JWT tới 60 phút; làm mới trước
            self._jwt = jwt.encode({"iss": self.team_id, "iat": int(time.time())}, self.key, algorithm="ES256", headers={"kid": self.key_id})
            self._jwt_at = time.time()
        return self._jwt

    async def send(self, token: str, env: str, payload: dict[str, Any]) -> Outcome:
        host = "api.sandbox.push.apple.com" if env == "sandbox" else "api.push.apple.com"
        r = await self._client.post(f"https://{host}/3/device/{token}", json=payload, headers={
            "authorization": f"bearer {self._token()}", "apns-topic": self.topic, "apns-push-type": "alert", "apns-priority": "10"})
        if r.status_code == 200:
            return "ok"
        reason = r.json().get("reason", "") if r.content else ""
        if r.status_code == 410 or reason in ("BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"):
            return "gone"
        log.warning("APNs %s: %s", r.status_code, reason)
        return "error"


def sender_from_settings() -> Sender | None:
    if not (settings.apns_key_file and settings.apns_key_id and settings.apns_team_id):
        return None
    return APNs(Path(settings.apns_key_file).read_text(), settings.apns_key_id, settings.apns_team_id, settings.apns_topic)


def payload(n: dict[str, Any], unread: int) -> dict[str, Any]:
    return {"aps": {"alert": {"title": "Barista", "body": n["push"]}, "badge": unread, "sound": "default", "thread-id": n["kind"]},
            "nid": n["id"], "open": n["open"]}


async def deliver_pending(sender: Sender | None, limit: int = 100) -> dict[str, int]:
    """Gửi các thông báo đang chờ đẩy. Không cấu hình APNs / không có dòng ``push`` / người nhận không có máy ⇒ ``skipped``."""
    out = {"sent": 0, "skipped": 0, "failed": 0}
    async with db.owner_tx() as conn:   # xuyên công ty như bộ phát sự kiện; chỉ đọc mã thiết bị + dòng push, không đọc dữ liệu nghiệp vụ
        cur = await conn.execute("""SELECT id, tenant_id, user_id, kind, push, open, push_attempts FROM core.notifications
                                    WHERE push_state = 'pending' ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED""", (limit,))
        for n in await cur.fetchall():
            state = "skipped"
            if sender is not None and n["push"]:
                c = await conn.execute(
                    """SELECT d.id, d.token, d.env FROM core.devices d
                       WHERE d.tenant_id = %s AND d.user_id = %s AND d.disabled_at IS NULL
                         AND NOT EXISTS (SELECT 1 FROM core.devices o WHERE o.token = d.token AND o.last_seen_at > d.last_seen_at)""",
                    (n["tenant_id"], n["user_id"]))
                devices = await c.fetchall()
                if devices:
                    c = await conn.execute("SELECT count(*) AS n FROM core.notifications WHERE tenant_id = %s AND user_id = %s AND read_at IS NULL",
                                           (n["tenant_id"], n["user_id"]))
                    row = await c.fetchone()
                    body = payload(n, int(row["n"]) if row else 0)
                    results = []
                    for d in devices:
                        try:
                            res = await sender.send(d["token"], d["env"], body)
                        except Exception as e:   # mạng / Apple lỗi: thử lại lần sau, không làm hỏng các thông báo khác
                            log.warning("gửi đẩy lỗi: %r", e)
                            res = "error"
                        if res == "gone":
                            await conn.execute("UPDATE core.devices SET disabled_at = now() WHERE id = %s", (d["id"],))
                        results.append(res)
                    if "ok" in results:
                        state = "sent"
                    elif "error" in results:
                        state = "failed" if n["push_attempts"] + 1 >= MAX_ATTEMPTS else "pending"
            await conn.execute("UPDATE core.notifications SET push_state = %s, push_attempts = push_attempts + 1 WHERE id = %s", (state, n["id"]))
            out["sent" if state == "sent" else "failed" if state in ("failed", "pending") else "skipped"] += 1
    return out
