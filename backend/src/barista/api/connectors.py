"""KẾT NỐI RA HỆ NGOÀI (ADR 0244, api 1.65) — bề mặt cho GÓI KẾT NỐI. Lõi giữ kết nối, khoá (mã hoá), lịch kéo có con trỏ + thử lại, cửa webhook, nhật ký,
màn "Kết nối ngoài", sức khoẻ; gói viết:

1. **Bộ nối** — lớp con ``Connector``: ``key``, ``label``, ``secrets`` (``Secret``), ``every_min``, ``webhook``; ``check()`` · ``pull(cursor) → Batch`` ·
   ``hook_parse(payload) → Hook``. Chỉ đổi dữ liệu hệ ngoài về MỤC (dict JSON) của gói — không chạm DB, không biết quyền. HTTP qua ``client()``; link hệ
   ngoài trả về qua ``check_public_url`` trước khi tải. Lỗi ⇒ ``ConnectorError(kind, câu người đọc được)`` — không mang khoá / thân phản hồi.
2. **Lệnh nhận** — ``@m.connector(BộNối)`` trên ``async (ctx, inp: Delivery)``: một MỤC mỗi lần, quyền hệ thống của công ty, điểm lưu riêng. Ghi bằng lệnh
   của module sở hữu dữ liệu (vd ``leadflow.intake``), chống lặp theo mã của hệ ngoài. Từ chối một mục (``Invalid``…) ⇒ mục vào hàng thử lại của lõi.
3. **Form tạo kết nối** — lệnh của gói (đầu vào có kiểu, ô khoá ``audit=False``) gọi ``await connectors.save(ctx, BộNối, label=…, settings=…, secrets=…)``.
   Gói khai ``m.consumes("connector")``.
4. **Đẩy ra hệ ngoài** (đăng bài, tạo vận đơn, gọi ra) — ở BƯỚC CHUẨN BỊ của lệnh (``prepare=``, ngoài giao dịch): ``inst = await connectors.open(prep, id)``
   rồi gọi hàm của bộ nối. Không gọi mạng trong handler.
"""

from barista.kernel.connectors import (
    Batch,
    Connector,
    ConnectorError,
    Delivery,
    Hook,
    Secret,
    check_public_url,
    check_url_setting,
    client,
    client_for,
    connections,
    open,
    save,
    use_transport,
)

__all__ = ["Batch", "Connector", "ConnectorError", "Delivery", "Hook", "Secret", "check_public_url", "check_url_setting", "client", "client_for", "connections", "open", "save",
           "use_transport"]
