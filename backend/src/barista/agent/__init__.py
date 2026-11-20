"""Trợ lý (agent) — một NGƯỜI GỌI nữa của cùng cửa executor, bằng quyền của chính người hỏi (``Principal.as_agent``).

Pydantic AI chỉ lo model + vòng gọi công cụ. Quyền, duyệt, tiền, nhật ký nằm ở core — model không chạm được.
"""
