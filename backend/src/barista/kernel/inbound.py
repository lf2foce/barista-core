"""WEBHOOK VÀO từ nhà cung cấp ngoài (0153, 0172 §2) — MỘT cách kiểm chữ ký cho mọi cửa ``Module.inbound``.

Cửa ``POST /v1/hooks/<module>/<công ty>/<mã kết nối>`` không đăng nhập: quyền = khoá webhook RIÊNG của kết nối. Thao tác nội bộ của module gọi
``verify_inbound`` TRƯỚC khi ghi gì. Nhận ba dạng (nhà cung cấp chọn cái nó biết gửi), mạnh trước:

1. **Standard Webhooks** (``webhook-id`` + ``webhook-timestamp`` + ``webhook-signature: v1,<base64 HMAC-SHA256("{id}.{ts}.{thân}")>`` — cùng chuẩn
   Barista ký thư RA, ``kernel/webhooks.py``): có giờ ký ⇒ lệch quá ``tolerance_s`` (mặc định 5 phút) là THƯ PHÁT LẠI ⇒ từ chối.
2. **HMAC có giờ** ``x-signature: t=<giây Unix>,v1=<hex HMAC-SHA256("{t}.{thân}")>``: cũng chặn phát lại theo giờ.
3. **HMAC thân** ``x-signature: sha256=<hex HMAC-SHA256(thân)>``, hoặc **khoá tĩnh** ở một tiêu đề khai (``token_headers``) / ``authorization: Bearer``
   — nhà cung cấp không gửi giờ ký. Không chặn được phát lại theo giờ ⇒ chống lặp là KHOÁ CHÍNH theo mã sự kiện của nhà cung cấp ở DB (bắt buộc với
   mọi cửa, kể cả dạng 1–2: trong cửa sổ 5 phút thư vẫn có thể bị gửi lại).

Mọi so sánh là thời gian hằng (``hmac.compare_digest``); khoá ngắn hơn 16 ký tự ⇒ từ chối (không có chế độ "không kiểm"). Trả "" khi đạt, không thì
mã lý do ngắn (``short_secret`` / ``missing`` / ``bad_signature`` / ``stale``) để module ghi nhật ký + trả lỗi rõ — không bao giờ ném lỗi.
"""

import base64
import hashlib
import hmac
import time

MIN_SECRET = 16
TOLERANCE_S = 300


def _fresh(ts: str, tolerance_s: int, now: float | None) -> bool | None:
    """True / False = giờ ký trong / ngoài cửa sổ; None = không đọc được giờ."""
    try:
        t = int(ts.strip())
    except ValueError:
        return None
    return abs((time.time() if now is None else now) - t) <= tolerance_s


def verify_inbound(headers: dict[str, str], body: bytes, secret: str, *, token_headers: tuple[str, ...] = (), tolerance_s: int = TOLERANCE_S,
                   now: float | None = None) -> str:
    """Kiểm một webhook VÀO bằng khoá ``secret`` của kết nối. ``headers``: tên viết thường. "" = đạt; còn lại là mã lý do (xem đầu file)."""
    if len(secret) < MIN_SECRET:
        return "short_secret"
    key = secret.encode()
    h = {k.lower(): v for k, v in headers.items()}
    if sigs := h.get("webhook-signature", "").strip():   # 1. Standard Webhooks
        fresh = _fresh(h.get("webhook-timestamp", ""), tolerance_s, now)
        if fresh is None:
            return "bad_signature"
        want = "v1," + base64.b64encode(hmac.new(key, f"{h.get('webhook-id', '')}.{h['webhook-timestamp'].strip()}.".encode() + body,
                                                 hashlib.sha256).digest()).decode()
        if not any(hmac.compare_digest(want.encode(), s.strip().encode()) for s in sigs.split(" ") if s.strip()):
            return "bad_signature"
        return "" if fresh else "stale"
    if sig := h.get("x-signature", "").strip():
        parts: dict[str, str] = {}
        for piece in sig.split(","):
            k, eq, v = piece.strip().partition("=")
            if eq:
                parts[k] = v
        if "t" in parts and "v1" in parts:   # 2. HMAC có giờ
            fresh = _fresh(parts["t"], tolerance_s, now)
            if fresh is None:
                return "bad_signature"
            want = hmac.new(key, f"{parts['t'].strip()}.".encode() + body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(want.encode(), parts["v1"].strip().lower().encode()):
                return "bad_signature"
            return "" if fresh else "stale"
        want = "sha256=" + hmac.new(key, body, hashlib.sha256).hexdigest()   # 3a. HMAC thân
        return "" if hmac.compare_digest(sig.lower().encode(), want.encode()) else "bad_signature"
    auth = h.get("authorization", "")   # 3b. khoá tĩnh
    given = [h.get(t.lower(), "") for t in token_headers] + ([auth[7:].strip()] if auth[:7].lower() == "bearer " else [])
    if not any(g.strip() for g in given):
        return "missing"
    # mọi ứng viên đều so (không dừng ở cái đầu khớp) — thời gian không lộ tiêu đề nào mang khoá
    hits = [hmac.compare_digest(g.strip().encode(), key) for g in given if g.strip()]
    return "" if any(hits) else "bad_signature"


REASONS = {"short_secret": "khoá webhook của kết nối quá ngắn", "missing": "thiếu chữ ký / khoá", "bad_signature": "sai chữ ký / khoá",
           "stale": "giờ ký lệch quá 5 phút — thư cũ bị gửi lại"}
