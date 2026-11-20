"""ĐỊA CHỈ NGƯỜI GỌI SAU PROXY BIÊN CỦA BẢN CÀI (review 0150 lát 4, 10/11/2026) — lớp ASGI bọc ``barista.main:app`` khi chạy trên VPS
(``deploy/serve.py``: ``uvicorn barista.edge:app --no-proxy-headers``).

Mạng overlay của VPS dùng chung với app khác và địa chỉ của Traefik đổi khi chạy lại ⇒ tin theo ĐỊA CHỈ bên gửi (``--forwarded-allow-ips``) hoặc tin
mọi bên (``*``) đều giả được. Thay vào đó tin theo KHOÁ: Traefik của stack này (nhãn ``headers.customRequestHeaders``) và BFF web gắn
``X-Barista-Edge: <khoá>`` (``BARISTA_EDGE_SECRET`` — ``deploy/ops/bootstrap.py`` sinh một lần); Traefik GHI ĐÈ giá trị trình duyệt tự gửi. Đúng
khoá (so thời gian hằng) ⇒ địa chỉ người gọi = hop CUỐI của ``X-Forwarded-For`` (hop do chính proxy biên nối thêm); sai / thiếu ⇒ địa chỉ kết nối
thật. Tiêu đề khoá luôn bị gỡ trước khi tới app (không lọt vào nhật ký / route của gói). Máy dev chạy ``barista.main:app`` thẳng — như cũ.
"""

import hmac
import ipaddress
import os
from typing import Any

from barista.main import app as core_app

HEADER = b"x-barista-edge"


def last_hop(value: bytes) -> str | None:
    hops = [h.strip() for h in value.decode("latin-1").split(",") if h.strip()]
    if not hops:
        return None
    try:
        return str(ipaddress.ip_address(hops[-1]))
    except ValueError:
        return None


class EdgeAddress:
    def __init__(self, inner: Any, secret: str) -> None:
        self.inner, self.secret = inner, secret.encode()

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] in ("http", "websocket"):
            headers = scope.get("headers") or []
            given = next((v for k, v in headers if k == HEADER), None)
            scope = {**scope, "headers": [(k, v) for k, v in headers if k != HEADER]}
            if given is not None and len(self.secret) >= 16 and hmac.compare_digest(given, self.secret):
                fwd = next((v for k, v in headers if k == b"x-forwarded-for"), b"")
                if ip := last_hop(fwd):
                    scope["client"] = (ip, 0)
        await self.inner(scope, receive, send)


app = EdgeAddress(core_app, os.environ.get("BARISTA_EDGE_SECRET", ""))
