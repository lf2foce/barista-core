"""Chuẩn hoá chữ dùng chung (không phụ thuộc module nào)."""

import re
import unicodedata


def fold(s: str) -> str:
    """Chữ thường, bỏ dấu, đ→d — so khớp không phụ thuộc gõ có dấu hay không."""
    t = unicodedata.normalize("NFD", s.replace("đ", "d").replace("Đ", "D"))
    return "".join(ch for ch in t if not unicodedata.combining(ch)).lower()


def code_from_name(name: str, taken: set[str] | frozenset[str] = frozenset(), limit: int = 30) -> str:
    """MÃ do máy đặt từ TÊN (0225 — không bắt người dùng nghĩ ra mã): chữ HOA không dấu, số, gạch nối ("Kinh doanh Đông" → "KINH-DOANH-DONG"), cắt ở ranh
    giới từ cho vừa ``limit``; trùng mã đã có (``taken``) ⇒ thêm -2, -3… Tên không có chữ / số nào ⇒ "MA"."""
    words = re.findall(r"[a-z0-9]+", fold(name))
    base = ""
    for w in words:
        nxt = f"{base}-{w}" if base else w
        if len(nxt) > limit - 3:   # chừa chỗ cho hậu tố -NN
            break
        base = nxt
    base = (base or (words[0][: limit - 3] if words else "ma")).upper()
    code, i = base, 2
    while code in taken:
        code, i = f"{base}-{i}", i + 1
    return code


def snake_from_name(name: str, taken: set[str] | frozenset[str] = frozenset(), limit: int = 40, *, letters_only: bool = False) -> str:
    """Khoá chữ thường không dấu, số, gạch dưới do máy đặt từ TÊN (0225) cho khoá dạng ``phi_dich_vu`` (vai, chuỗi duyệt, mục khoản thu / chi, loại thuế…).
    ``taken`` so không phân biệt hoa / thường; bắt đầu bằng số ⇒ thêm ``m_`` (khoá phải bắt đầu bằng chữ). ``letters_only``: bảng chỉ nhận ``[a-z_]`` (vai,
    chuỗi duyệt) ⇒ bỏ chữ số, trùng thì thêm ``_a``, ``_b``…"""
    if letters_only:
        base = re.sub(r"_+", "_", re.sub(r"[^a-z_]", "", code_from_name(name, limit=limit - 2).lower().replace("-", "_"))).strip("_")
        base = base if len(base) >= 2 else "khoa"
        return next(k for k in (base, *(f"{base}_{c}" for c in "abcdefghijklmnopqrstuvwxyz")) if k not in taken)
    held = {t.upper().replace("_", "-") for t in taken}
    key = code_from_name(name, held, limit - 2).lower().replace("-", "_")
    return key if key[0].isalpha() else "m_" + key


_BLOCK_START = re.compile(r"^\s*([-•*–]|\d{1,3}[.)]\s|[a-zđ][.)]\s|#)")


def reflow(text: str) -> str:
    """Nối dòng bị ngắt CỨNG giữa câu (văn bản bóc từ PDF / gõ xuống dòng theo khổ giấy) để HIỂN THỊ liền mạch. Giữ xuống dòng khi dòng trước
    kết thúc câu (. : ; ! ? …), dòng sau mở gạch đầu dòng / đánh số / đề mục, hoặc có dòng trống. Chỉ dùng khi vẽ — câu trích / bằng chứng
    vẫn so trên chữ gốc đã lưu."""
    out: list[str] = []
    for line in text.split("\n"):
        cur = line.strip()
        prev = out[-1] if out else ""
        if out and prev and cur and not prev.endswith((".", ":", ";", "!", "?", "…", ")")) and not _BLOCK_START.match(cur):
            out[-1] = f"{prev} {cur}"
        else:
            out.append(cur)
    return "\n".join(out)


def name_key(s: str) -> str:
    """Khoá so khớp TÊN: bỏ dấu, chữ thường, bỏ mọi khoảng trắng / dấu câu — "Hồ Sen Residence", "HO SEN residence", "hosen-residence"
    cùng một khoá."""
    return re.sub(r"[^a-z0-9]+", "", fold(s))


def vnd_text(n: int | None) -> str:
    """Tiền gọn kiểu Việt (4,5 tỷ · 90 triệu · 125.000 đ) — dùng chung, không thuộc ngành nào."""
    if n is None:
        return ""
    if n >= 10**9:
        return f"{n / 10**9:.2f}".rstrip("0").rstrip(".").replace(".", ",") + " tỷ"
    if n >= 10**6:
        return f"{n / 10**6:.0f} triệu"
    return f"{n:,}".replace(",", ".") + " đ"


_D = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]


def _three(n: int, full: bool) -> str:
    h, t, u = n // 100, n // 10 % 10, n % 10
    out = []
    if h or full:
        out.append(f"{_D[h]} trăm")
    if t == 0:
        if u:
            out.append(("lẻ " if (h or full) else "") + _D[u])
    elif t == 1:
        out.append("mười" + ("" if u == 0 else " lăm" if u == 5 else f" {_D[u]}"))
    else:
        tail = "" if u == 0 else " mốt" if u == 1 else " tư" if u == 4 else " lăm" if u == 5 else f" {_D[u]}"
        out.append(f"{_D[t]} mươi{tail}")
    return " ".join(out)


def vnd_words(n: int) -> str:
    """168000000 → 'Một trăm sáu mươi tám triệu đồng'."""
    if n == 0:
        return "Không đồng"
    groups = []
    x = abs(n)
    while x:
        groups.append(x % 1000)
        x //= 1000
    parts = []
    for i in range(len(groups) - 1, -1, -1):
        if groups[i] == 0:
            continue
        unit = ["", " nghìn", " triệu"][i % 3] + " tỷ" * (i // 3)
        parts.append(_three(groups[i], full=i != len(groups) - 1) + unit)
    s = " ".join(parts)
    return ("Âm " if n < 0 else "") + s[0].upper() + s[1:] + " đồng"


def money(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def dong(n: int) -> str:
    """Số tiền ĐỦ từng đồng (125.700.000 đ) — công nợ, giấy tờ: không làm tròn "126 triệu" như ``vnd_text``."""
    return money(n) + " đ"


def like(q: str) -> str:
    """Mẫu ILIKE "chứa" cho tìm nhanh — bỏ ký tự đại diện người gõ (%, _) để không thành truy vấn rộng ngoài ý."""
    return "%" + q.replace("%", "").replace("_", "") + "%"
