"""NHÃN CỦA FORM THÊM LEAD — mã nguồn lead và kênh liên hệ khác SĐT. Một bộ mã cho MỌI form / cột nhập file tạo lead: form chung của gói Khách hàng
(``crm.create_lead``), luật chia lead, và form của gói ngành chuyển nguyên dòng cho ``crm.create_lead`` (cột nhập file phải khớp đúng các mã này, nếu
không file lead đang dùng mất cột). Gói lấy qua ``barista.api.lead_labels`` (1.18); đổi mã ở đây = đổi cho cả lõi lẫn gói."""

SOURCES = {"ads": "Quảng cáo", "telesales": "Telesales", "self": "Tự khai thác", "referral": "Giới thiệu",
           "event": "Sự kiện", "import": "Nhập file"}
CONTACT_KIND = {"": "—", "zalo": "Zalo", "facebook": "Facebook", "other": "Khác"}
