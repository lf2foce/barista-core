"""CỬA NHÀ VẬN HÀNH (ADR 0206, quyết định chủ sản phẩm 12/11/2026): nhà vận hành (bên bán phần mềm — không phải nhân viên của khách) đổi cấu hình
của một công ty — đặt quyền cho vai, tạo vai, cài / xoay khoá / thu hồi app từ xa, quyết phiếu đổi quyền đang chờ — mà KHÔNG cần tài khoản siêu quản
trị trong app (lộ một phiên Google = mất mọi công ty) và không ai tự duyệt việc của chính mình.

Theo đúng mẫu chính sách trợ lý AI (0193, ``barista.ai_policy``): tài nguyên ``core.operator`` không vai nào của khách cấp được
(``registry.OPERATOR_RESOURCES`` + trigger ``core.grants_operator_only``, migration base/085); mọi thao tác trên nó là ``internal=True`` (không
gọi được qua HTTP / MCP / app — chỉ quyền hệ thống trong tiến trình), ``agent="deny"``; bắt buộc ``by`` (người vận hành) + ``why`` (lý do), ghi vào
``core.audit_log``; không hiện ở Nhật ký thao tác của khách (``registry.operator_command``). Đường chạy DUY NHẤT: ``python -m barista.operator`` trên
máy chủ của bản cài (cần quyền vào máy chủ — đó là lớp xác thực thứ hai), qua CÙNG executor bằng ``system_principal`` (bất biến 1). Ở đây chỉ phần
dùng chung (tài nguyên + ô người vận hành / lý do); thao tác nằm ở module sở hữu dữ liệu (``admin``, ``connect``, lõi duyệt phiếu).
"""

from typing import Any

from barista.kernel import registry
from barista.kernel.errors import Invalid
from barista.kernel.fields import field

RESOURCE = "core.operator"
registry.OPERATOR_RESOURCES.add(RESOURCE)
DECIDED_BY = "Nhà vận hành"   # tên người quyết trên phiếu / nhật ký khi nhà vận hành duyệt thay chủ tài khoản
# Danh tính của nhà vận hành trong DB khi QUYẾT PHIẾU: một dòng core.users KHÔNG có thành viên ở công ty nào (không đăng nhập được — tên miền .invalid,
# RFC 2606 — không nhận lead / phiếu), như người dùng dịch vụ của app từ xa. Có nó thì quyết định trên phiếu có người quyết thật (trigger tách bạch nhiệm
# vụ — migration base/053: khác người xin, không quyết hai bước), lệnh sau duyệt mang ``approved_by``; TÊN người vận hành + lý do ghi kèm quyết định.
SERVICE_EMAIL = "operator@operators.barista.invalid"


def by_field() -> Any:
    return field("Người vận hành", max_length=80, help="Ai làm — ghi vào nhật ký vận hành")


def why_field() -> Any:
    return field("Lý do", max_length=300, help="Vì sao làm (hợp đồng, yêu cầu của khách, thiết lập ban đầu…) — ghi vào nhật ký vận hành")


def require_who(inp: Any) -> None:
    """Lệnh của cửa nhà vận hành luôn ghi AI làm + VÌ SAO — thiếu một trong hai ⇒ không chạy (kiểm cả ở CLI lẫn ở thao tác)."""
    if not str(getattr(inp, "by", "")).strip() or not str(getattr(inp, "why", "")).strip():
        raise Invalid("Cần người vận hành (by) và lý do (why) — ghi vào nhật ký vận hành")


def note(by: str, why: str) -> str:
    """Dòng ghi chú trên phiếu / nhật ký: "Nhà vận hành <tên> — <lý do>"."""
    return f"{DECIDED_BY} {by.strip()} — {why.strip()}"
