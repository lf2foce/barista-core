"""Webhook VÀO (0153, 0172 §2): một cách kiểm chữ ký / khoá / giờ ký (``inbound.verify_inbound``), chống lặp ở DB, thiếu trường ⇒ từ chối rõ + dòng nhật
ký, nhật ký KHÔNG chép khoá / thân. Ở đây kiểm HÀM của lõi; đường HTTP thật (``/v1/hooks/<module>/<công ty>/<kết nối>``) kiểm ở gói có cửa webhook
(gói tổng đài ở repo gói — ``telephony/tests/test_telephony_hooks.py``)."""

import base64
import hashlib
import hmac
import itertools
from pathlib import Path

from barista.api import inbound

SECRET = "hook-" + "k" * 27
NOW = 1_762_760_000
_seq = itertools.count(1)


def _std(body: bytes, ts: int, msg_id: str = "msg_1", secret: str = SECRET) -> dict[str, str]:
    mac = hmac.new(secret.encode(), f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()
    return {"webhook-id": msg_id, "webhook-timestamp": str(ts), "webhook-signature": "v1," + base64.b64encode(mac).decode()}


def _timed(body: bytes, ts: int, secret: str = SECRET) -> dict[str, str]:
    return {"x-signature": f"t={ts},v1=" + hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()}


def test_every_scheme_accepts_the_real_sender_and_names_why_it_refuses() -> None:
    body = b'{"eventId":"e1"}'
    v = inbound.verify_inbound
    assert v(_std(body, NOW), body, SECRET, now=NOW) == ""
    assert v(_std(body, NOW - 299), body, SECRET, now=NOW) == ""
    assert v(_std(body, NOW - 301), body, SECRET, now=NOW) == "stale"                         # thư thật nhưng cũ ⇒ phát lại
    assert v(_std(body, NOW, secret="x" * 32), body, SECRET, now=NOW) == "bad_signature"     # giả chữ ký
    assert v(_std(body, NOW), body + b" ", SECRET, now=NOW) == "bad_signature"               # sửa thân
    tampered = {**_std(body, NOW - 301), "webhook-timestamp": str(NOW)}                       # đổi giờ ký để lọt cửa sổ ⇒ chữ ký hỏng
    assert v(tampered, body, SECRET, now=NOW) == "bad_signature"
    assert v(_timed(body, NOW), body, SECRET, now=NOW) == ""
    assert v(_timed(body, NOW + 400), body, SECRET, now=NOW) == "stale"
    assert v({"x-signature": f"t={NOW},v1=" + "0" * 64}, body, SECRET, now=NOW) == "bad_signature"
    assert v({"x-signature": "t=abc,v1=00"}, body, SECRET, now=NOW) == "bad_signature"
    plain = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert v({"x-signature": plain.upper()}, body, SECRET) == ""
    assert v({"x-signature": "sha256=" + "0" * 64}, body, SECRET) == "bad_signature"
    assert v({"token": SECRET}, body, SECRET, token_headers=("token",)) == ""
    assert v({"Token": SECRET}, body, SECRET, token_headers=("token",)) == ""                # tên tiêu đề không phân biệt hoa thường
    assert v({"token": SECRET}, body, SECRET) == "missing"                                   # tiêu đề không khai ⇒ không nhận
    assert v({"authorization": "Bearer " + SECRET}, body, SECRET) == ""
    assert v({"authorization": "Bearer " + SECRET[:-1] + "x"}, body, SECRET) == "bad_signature"
    assert v({}, body, SECRET) == "missing"
    assert v({"token": "short-key"}, body, "short-key", token_headers=("token",)) == "short_secret"   # khoá ngắn ⇒ cửa đóng


def test_every_comparison_is_constant_time() -> None:
    from barista.kernel import inbound

    src = Path(inbound.__file__).read_text(encoding="utf-8").split("def verify_inbound")[1]
    assert src.count("compare_digest") >= 4 and " == want" not in src and "want ==" not in src
