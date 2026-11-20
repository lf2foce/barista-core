"""CHỈ dev / eval (quyết định 0095): khai thêm N công cụ NHIỄU như thật để mô phỏng danh mục lớn lên (nhân sự, chấm công, lương, kho, mua hàng,
pháp chế, chăm sóc khách, tài sản, xe…) — đo "đủ công cụ" so với "tìm theo câu hỏi" khi có thêm 100, 200… công cụ.

* Chỉ nạp khi ``settings.dev_auth`` VÀ ``settings.eval_noise_tools > 0`` (``http_api.py``). Bản thật không bao giờ có.
* Tất cả là TRUY VẤN rỗng (không ghi gì), ``resource=None`` ⇒ mọi vai đều thấy — đúng là thứ làm khó việc chọn công cụ.
* Mô tả cố ý dùng chung chữ với công cụ thật ("khách", "hợp đồng", "phí", "báo cáo", "phòng ban"…). Gọi trúng công cụ nhiễu = chọn nhầm
  (``eval_assistant.py`` đếm riêng).
"""

import itertools
from typing import Any

from barista.kernel import core_ops, ui
from barista.kernel.executor import Ctx
from barista.kernel.fields import NoInput

PREFIX = "noise_"

AREAS: dict[str, tuple[str, list[str]]] = {
    "hr": ("Nhân sự", ["hồ sơ nhân viên", "hợp đồng lao động", "nghỉ phép", "đánh giá nhân viên", "tuyển dụng ứng viên", "phòng ban"]),
    "att": ("Chấm công", ["chấm công", "ca làm việc", "đi muộn về sớm", "tăng ca", "bảng công tháng"]),
    "pay": ("Lương", ["bảng lương", "phụ cấp", "thuế thu nhập cá nhân", "bảo hiểm xã hội", "thưởng doanh số"]),
    "inv": ("Kho", ["nhập kho", "xuất kho", "tồn kho", "kiểm kê", "vật tư"]),
    "po": ("Mua hàng", ["đơn mua hàng", "nhà cung cấp", "báo giá nhà cung cấp", "thanh toán nhà cung cấp", "hợp đồng mua"]),
    "legal": ("Pháp chế", ["hợp đồng khách hàng", "điều khoản hợp đồng", "rủi ro pháp lý", "hồ sơ công chứng", "tranh chấp"]),
    "cs": ("Chăm sóc khách hàng", ["phiếu hỗ trợ khách", "khiếu nại khách", "đánh giá dịch vụ", "phản hồi khách", "lịch hẹn bảo hành"]),
    "asset": ("Tài sản", ["tài sản cố định", "khấu hao", "cấp phát thiết bị", "sửa chữa tài sản"]),
    "fleet": ("Xe", ["lịch xe", "chi phí xăng", "bảo dưỡng xe", "lái xe"]),
}
VERBS = ["Danh sách", "Báo cáo", "Chi tiết", "Thống kê theo tháng", "Đối chiếu", "Tổng hợp theo phòng ban"]


def _specs(n: int) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    pairs = [(area, title, thing) for area, (title, things) in AREAS.items() for thing in things]
    for verb, (area, title, thing) in itertools.product(VERBS, pairs):
        if len(out) >= n:
            break
        out.append((f"{PREFIX}{area}_{len(out)}", f"{verb} {thing}",
                    f"{verb} {thing} của công ty ({title}) — lọc theo phòng ban, người phụ trách, khách hàng, kỳ báo cáo, trạng thái, phí."))
    return out


def install(n: int) -> int:
    """Khai ``n`` công cụ nhiễu vào module lõi; trả số đã khai. Gọi MỘT lần lúc khởi động (dev)."""
    for name, title, doc in _specs(n):
        core_ops.m.query(name, resource=None, title=title)(_handler(title, doc))
    return n


def _handler(title: str, doc: str) -> Any:
    async def handler(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
        return ui.table([], [ui.col("x", "—")], title=title, empty="Không có dữ liệu (công cụ thử — eval)")

    handler.__doc__ = doc
    return handler
