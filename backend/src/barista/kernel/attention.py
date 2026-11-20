"""Câu TÍN HIỆU của bàn việc (0094) + nguồn đề xuất buổi sáng (0074 A).

Module khai tín hiệu NGAY TRÊN MÀN (``Screen(desk=Desk(group, say=...))`` — ``kernel/screens.py``); ``say`` nhận kết quả truy vấn của màn (chạy
bằng quyền người xem — ``agent/desk.py``) và trả câu bằng ``line``. File này chỉ giữ vài hàm đọc số trên trang + danh sách nguồn đề xuất;
kernel không biết module nào.
"""

from dataclasses import dataclass
from typing import Any

type Line = dict[str, Any]


def line(tone: str, title: str, detail: str = "", ask: str | None = None) -> Line:
    """Một câu tín hiệu: ``tone`` bad · warn · accent; ``ask`` = câu hỏi gợi ý cho trợ lý. Bấm câu ⇒ mở chính màn đã khai nó."""
    return {"tone": tone, "title": title, "detail": detail, "ask": ask}


@dataclass(frozen=True)
class Source:
    """Nguồn GỢI Ý buổi sáng (0074 A): truy vấn của module, chạy bằng quyền người nhận, trả ``[{command, input, reason, key, rank?}]``
    — code chọn việc nên làm; hub ``agent/morning.py`` chạy thử từng gợi ý thành thẻ đề xuất."""
    query: str
    rank: int = 500


SOURCES: list[Source] = []

# Nguồn NGỮ CẢNH của lượt trợ lý (0178): truy vấn của module trả ``{"context": "<một vài dòng>"}`` cho người đang hỏi (vd "cuộc gọi vừa xong chưa ghi chú") — trợ lý
# nhận dòng đó kèm câu hỏi nên "vừa gọi xong, khách muốn 2 phòng ngủ" không phải nêu tên khách. Chạy bằng quyền người hỏi, rỗng ⇒ không thêm gì.
CONTEXTS: list[str] = []


def kpi(out: Any, label: str) -> float:
    """Số của một ô KPI trong trang bảng hoặc thẻ (0 nếu không có)."""
    if not isinstance(out, dict):
        return 0
    items = list(out.get("kpis") or [])
    for b in out.get("blocks") or []:
        if b.get("type") == "kpis":
            items += b.get("items") or []
    for k in items:
        if k.get("label") == label and isinstance(k.get("value"), int | float):
            return float(k["value"])
    return 0


def flagged(o: Any, flag: str) -> int:
    """Số dòng có cột ``flag`` bằng ``flag``."""
    return sum(1 for r in (o or {}).get("rows") or [] if r.get("flag") == flag)


def num(x: float) -> str:
    return str(int(x))
