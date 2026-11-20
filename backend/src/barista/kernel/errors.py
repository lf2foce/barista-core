"""Lỗi nghiệp vụ handler ĐƯỢC PHÉP ném — executor đổi thành trạng thái trả về (và ghi nhật ký).

Lỗi Python thường (ValueError, KeyError…) KHÔNG được coi là lỗi nghiệp vụ: đó là bug ⇒ trạng thái ``error``, ghi log.
"""


class OpError(Exception):
    status = "error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class Invalid(OpError):
    """Dữ liệu sai / trái luật nghiệp vụ (422)."""

    status = "invalid"


class NotFound(OpError):
    """Không có (hoặc không được thấy — RLS không phân biệt hai trường hợp này, và cũng không nên) (404)."""

    status = "not_found"


class Denied(OpError):
    """Không có quyền (403)."""

    status = "denied"


class Conflict(OpError):
    """Trùng / xung đột trạng thái (409)."""

    status = "conflict"
