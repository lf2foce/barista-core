"""Chạy dev: ``uv run uvicorn barista.main:app --port 8300``."""

import logging

from barista.http_api import app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# httpx / httpcore ghi MỖI lời gọi ra ở INFO KÈM tham số URL ("HTTP Request: GET https://…?access_token=…") — khoá app, token của nhà cung cấp (OAuth,
# Graph) sẽ vào log container. Chỉ giữ cảnh báo / lỗi của hai thư viện này (rà OAuth Meta 19/11/2026).
for _quiet in ("httpx", "httpcore"):
    logging.getLogger(_quiet).setLevel(logging.WARNING)

__all__ = ["app"]
