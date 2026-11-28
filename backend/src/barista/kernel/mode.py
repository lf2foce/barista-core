"""CHẾ ĐỘ CHẠY của lượt hiện tại — cờ theo ngữ cảnh, ở một module LÁ để cả ``executor`` (đặt / đọc) lẫn ``notify`` (nơi GHI thông báo cuối cùng) cùng đọc
mà không vòng import.

``HISTORICAL`` — NẠP LỊCH SỬ từ hệ khác (0185): cả chuỗi lệnh — gọi lồng, sự kiện phát ra và bên nghe của chúng — chỉ GHI dữ liệu tham chiếu: không thông
báo (``notify.send`` tự im — MỘT nơi, kể cả phiếu "xin duyệt" / "tự duyệt" của ``kernel/approvals``), không mở việc theo dõi, không gửi app từ xa. Chỉ mã trong
tiến trình bật qua ``executor`` (``run(historical=True)``, ``executor.historical()``); gói chỉ đọc ``ctx.historical``."""

from contextvars import ContextVar

HISTORICAL: ContextVar[bool] = ContextVar("barista_historical", default=False)
