"""Giới hạn tần suất cho đường KHÔNG đăng nhập (link gửi khách — quyết định 0064). Mã link 256 bit không dò được; giới hạn ở đây chặn LẠM DỤNG
một link đã lộ (gửi rác dồn đầy kho, gọi liên tục), tính theo từng link.

Đếm trong bộ nhớ của tiến trình (cửa sổ trượt): đúng với bản cài một tiến trình API như hiện nay; chạy nhiều bản API song song thì mỗi bản đếm
riêng (giới hạn thật = số bản × mức dưới) — khi đó chuyển sang đếm ở Postgres / reverse proxy.
"""

import time
from collections import deque

# loại → (số lần, trong bao nhiêu giây)
LIMITS: dict[str, tuple[int, float]] = {"view": (120, 600), "answer": (120, 600), "upload": (60, 3600),
                                        # gọi trực tiếp qua link mời (0114): ICE gom theo đợt, vài trăm tín hiệu / cuộc là nhiều; báo có mặt thưa
                                        "signal": (900, 600), "presence": (120, 600),
                                        # theo ghế (thành viên lẫn người ngoài): gửi tín hiệu; lấy mật khẩu TURN tạm
                                        "seat_signal": (900, 600), "turn": (40, 600),
                                        "mcp": (300, 600),   # ứng dụng AI ngoài (0093), theo người × ứng dụng
                                        # app từ xa (0140): gọi API theo app; xin token theo client_id (cả lần sai khoá)
                                        # lần xin SAI đếm theo (client_id, địa chỉ gọi) — kẻ dò khoá ở máy khác không khoá được app thật (review 08/11)
                                        "app": (1200, 600), "app_token": (120, 600), "app_token_fail": (30, 600),
                                        # webhook VÀO từ nhà cung cấp (0153): theo kết nối; lần sai chữ ký đếm theo (kết nối, địa chỉ gọi)
                                        "hook": (3000, 600), "hook_fail": (30, 600),
                                        # danh tính cho ứng dụng first-party (0205): theo token; lần token SAI theo địa chỉ gọi (chống dò token)
                                        "userinfo": (600, 600), "userinfo_fail": (60, 600),
                                        # đăng nhập Google ngay trên trang ứng dụng first-party (0205 §5b): theo client (mọi lượt đi từ server ứng dụng);
                                        # lượt SAI theo (client, địa chỉ gọi) — kẻ gửi rác ở máy khác không khoá được đăng nhập thật
                                        "signin": (600, 600), "signin_fail": (30, 600),
                                        # mã đăng nhập qua email (0205 §5c): xin mã theo email (chống gửi thư dồn) + theo địa chỉ gọi (web Barista); nhập SAI
                                        # theo địa chỉ gọi (web Barista) — mỗi mã còn tự giới hạn 5 lần sai trong DB
                                        "email_code": (5, 600), "email_code_ip": (30, 600), "email_verify_fail": (30, 600)}
UPLOAD_BYTES_PER_DAY = 300 * 1024 * 1024
MAX_KEYS = 20_000

_hits: dict[tuple[str, str], deque[float]] = {}
_bytes: dict[str, deque[tuple[float, int]]] = {}


def _prune() -> None:
    if len(_hits) > MAX_KEYS:   # chặn phình bộ nhớ: bỏ các khoá lâu không dùng nhất
        for k in sorted(_hits, key=lambda k: _hits[k][-1] if _hits[k] else 0)[: len(_hits) // 2]:
            _hits.pop(k, None)
    if len(_bytes) > MAX_KEYS:
        for k in sorted(_bytes, key=lambda k: _bytes[k][-1][0] if _bytes[k] else 0)[: len(_bytes) // 2]:
            _bytes.pop(k, None)


def allow(key: str, kind: str, *, now: float | None = None) -> bool:
    """Ghi một lượt ``kind`` của ``key``; False = vượt mức (lượt này KHÔNG được tính). Gọi TRƯỚC khi đọc thân yêu cầu."""
    t = time.monotonic() if now is None else now
    limit, window = LIMITS[kind]
    q = _hits.setdefault((key, kind), deque())
    while q and q[0] <= t - window:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(t)
    _prune()
    return True


def blocked(key: str, kind: str, *, now: float | None = None) -> bool:
    """Đã chạm mức chưa — KHÔNG tính lượt (đếm riêng lần thất bại bằng ``hit``)."""
    t = time.monotonic() if now is None else now
    limit, window = LIMITS[kind]
    q = _hits.get((key, kind))
    while q and q[0] <= t - window:
        q.popleft()
    return q is not None and len(q) >= limit


def hit(key: str, kind: str, *, now: float | None = None) -> None:
    _hits.setdefault((key, kind), deque()).append(time.monotonic() if now is None else now)
    _prune()


def take_bytes(key: str, size: int, *, now: float | None = None) -> bool:
    """Trừ ``size`` byte vào hạn mức nộp file / ngày của ``key``; False = vượt (không trừ)."""
    t = time.monotonic() if now is None else now
    b = _bytes.setdefault(key, deque())
    while b and b[0][0] <= t - 86400:
        b.popleft()
    if sum(n for _, n in b) + size > UPLOAD_BYTES_PER_DAY:
        return False
    b.append((t, size))
    return True


def reset() -> None:
    """Cho test."""
    _hits.clear()
    _bytes.clear()
