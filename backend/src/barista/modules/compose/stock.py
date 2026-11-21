"""ẢNH MINH HOẠ MẪU của lõi (ADR 0252): bộ ảnh do AI tạo (``stock/manifest.json`` kê khai từng ảnh — khổ, chủ đề, lời dặn đã dùng) để ấn phẩm có hình ngay từ đầu.

Ảnh AI là lớp nguồn RIÊNG của khối ảnh (``origin = "ai"``, ``image.stock = <mã>``), không lẫn ảnh thật trong kho tài liệu: luôn in chú thích ``LABEL`` trên PDF / trang web /
bản xem trước (mọi bộ vẽ lấy chữ từ đây), xuất / gửi link bị chặn nếu thiếu chú thích, và chỉ có khi công ty BẬT cài đặt ``compose.ai_images`` (mặc định tắt). Tạo ảnh theo yêu
cầu người dùng: chưa có (còn mở trong ADR)."""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

DIR = Path(__file__).parent / "stock"
LABEL = "Ảnh minh hoạ"
ORIGIN = "ai"
CONTENT_TYPE = "image/jpeg"


@dataclass(frozen=True)
class Item:
    id: str
    file: str
    ratio: str
    width: int
    height: int
    theme: str
    label: str


@lru_cache(maxsize=1)
def _manifest() -> dict[str, Any]:
    doc: dict[str, Any] = json.loads((DIR / "manifest.json").read_text(encoding="utf-8"))
    return doc


@lru_cache(maxsize=1)
def items() -> dict[str, Item]:
    return {i["id"]: Item(i["id"], i["file"], i["ratio"], i["width"], i["height"], i["theme"], i["label"]) for i in _manifest()["images"]}


def themes() -> dict[str, str]:
    return dict(_manifest()["themes"])


def get(stock_id: str) -> Item | None:
    return items().get(stock_id)


def read(stock_id: str) -> tuple[bytes, str] | None:
    it = get(stock_id)
    return ((DIR / it.file).read_bytes(), CONTENT_TYPE) if it else None


def pick(theme: str = "", ratio: str = "", *, skip: int = 0, limit: int = 3) -> list[Item]:
    """Ảnh theo chủ đề / khổ ('' = bất kỳ) theo thứ tự kê khai — công thức của gói chọn bằng dữ liệu (``{"type": "illustrations", "theme": …, "ratio": …}``)."""
    got = [i for i in items().values() if (not theme or i.theme == theme) and (not ratio or i.ratio == ratio)]
    return got[skip:skip + limit]


def block(it: Item, caption: str = "") -> dict[str, Any]:
    """Khối ảnh của một ảnh minh hoạ: ``image.stock`` thay cho ``document_id`` (không thuộc kho tài liệu nào)."""
    return {"type": "image", "origin": ORIGIN, "image": {"stock": it.id, "document_id": "", "filename": it.file, "caption": caption}}


def is_ai(b: dict[str, Any]) -> bool:
    return b.get("type") == "image" and b.get("origin") == ORIGIN


def check() -> None:
    """Tự kiểm lúc nạp: mọi ảnh kê khai có thật, đúng khổ, nhẹ — lệch ⇒ lỗi khi khởi động, không đợi người bấm."""
    for i in items().values():
        f = DIR / i.file
        if not f.is_file() or f.stat().st_size > 270 * 1024:
            raise ValueError(f"ảnh minh hoạ {i.id}: thiếu tệp hoặc quá nặng")


check()
