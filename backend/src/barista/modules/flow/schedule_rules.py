"""Luật lịch của việc (0222) — hàm thuần, không phụ thuộc module khác để ``tasks`` / ``plans`` / ``timeline`` cùng dùng không vòng import."""

from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

from barista.api import Invalid


def find_cycle(deps: Mapping[Any, Iterable[Any]]) -> list[Any] | None:
    """Một chu trình trong đồ thị phụ thuộc (khoá → các khoá phải xong trước), dạng ``[a, b, …, a]``; không có ⇒ None. Hàm thuần."""
    state: dict[Any, int] = {}   # 1 = đang đi, 2 = xong
    path: list[Any] = []

    def visit(n: Any) -> list[Any] | None:
        state[n] = 1
        path.append(n)
        for d in deps.get(n, ()):
            if state.get(d) == 1:
                return [*path[path.index(d):], d]
            if state.get(d) is None and d in deps and (c := visit(d)):
                return c
        path.pop()
        state[n] = 2
        return None

    for n in deps:
        if state.get(n) is None and (c := visit(n)):
            return c
    return None


def fit_start(start: date | None, asked_due: date | None, due: date | None) -> date | None:
    """Ngày bắt đầu sau khi HẠN bị lịch làm việc lùi về ngày làm việc liền trước (``_due``): người giao chọn bắt đầu ≤ hạn họ chọn, nhưng hạn rơi ngày nghỉ
    bị lùi qua ngày bắt đầu (bắt đầu thứ Bảy, hạn Chủ nhật ⇒ hạn lùi về thứ Sáu) ⇒ bắt đầu cũng lùi về hạn mới, không báo lỗi điều người giao không gây
    ra. Bắt đầu vốn đã sau hạn người chọn ⇒ giữ nguyên để ``check_schedule`` báo. (Lỗi lộ ra 20/11/2026: dữ liệu mẫu tạo vào thứ Sáu.)"""
    if start and due and asked_due and start > due and start <= asked_due:
        return due
    return start


def check_schedule(start: date | None, due: date | None, label: str = "") -> None:
    if start and due and start > due:
        raise Invalid(f"{label}bắt đầu {start:%d/%m/%Y} sau hạn {due:%d/%m/%Y}")
