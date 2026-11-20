"""CHE LIÊN HỆ TỚI KHI BẤM NHẬN (0214) — MỘT lối cho mọi chỗ crm đưa SĐT / liên hệ của khách ra NGOÀI (màn, ⌘K, thẻ buổi sáng, trợ lý, app ngoài).

Lead đang chờ bấm nhận theo luật chia có bật che (``leadflow.contact_gate`` — nguồn sự thật là trạng thái chia, không có cột trùng ở crm) ⇒ MỌI người
xem (kể cả người được chia, cả quản lý) chỉ thấy số đã che 4 chữ số cuối: bấm Nhận là cam kết, cam kết rồi mới có số. Che theo LEAD, không theo khách:
khách cũ có lead khác đã nhận thì người đang chăm lead đó vẫn thấy số ở lead đó.

Chỉ che chữ đưa ra; không chặn ghi (liên hệ thật — kể cả gọi qua tổng đài — được tính là đã nhận, như từ trước). ``tests/test_contact_gate.py`` bắt
mọi câu SQL mới của crm có đọc SĐT / liên hệ mà không đi qua đây (hoặc không khai lý do ở danh sách miễn)."""

from collections.abc import Iterable
from typing import Any

from barista.api import Ctx, phone_display

DOT = "•"
CONTACT_FIELDS = ("phone", "email")


async def gated(ctx: Ctx, lead_ids: Iterable[Any]) -> set[str]:
    """Lead nào (trong số người xem đã thấy) đang che liên hệ."""
    ids = list(dict.fromkeys(str(i) for i in lead_ids if i))
    if not ids:
        return set()
    out = await ctx.call_system("leadflow.contact_gate", lead_ids=ids)
    return set(out["gated"])


def mask_phone(p: str | None) -> str | None:
    """``+84912345678`` → ``091234••••``: dạng đọc theo quốc gia công ty (``phone_display``), che 4 chữ số cuối (đủ nhận ra số quen, không đủ để gọi)."""
    if not p:
        return p
    out, left = [], 4
    for ch in reversed(phone_display(p)):
        if left and ch.isdigit():
            out.append(DOT)
            left -= 1
        else:
            out.append(ch)
    return "".join(reversed(out))


def mask_value(kind: str, v: str | None) -> str | None:
    """Một liên hệ bất kỳ theo loại: SĐT che đuôi; email giữ chữ đầu + tên miền; kênh khác (Zalo, Facebook…) che hết."""
    if not v:
        return v
    if kind == "phone":
        return mask_phone(v)
    if kind == "email" and "@" in v:
        user, _, domain = v.partition("@")
        return f"{user[:1]}{DOT * 3}@{domain}"
    return DOT * 3


async def mask_rows(ctx: Ctx, rows: list[dict[str, Any]], *, lead: str = "id", fields: tuple[str, ...] = CONTACT_FIELDS) -> set[str]:
    """Che tại chỗ các trường liên hệ của những dòng mà lead (cột ``lead``) đang che; thêm ``contact_hidden`` = True cho dòng đó. Trả tập lead đã che."""
    g = await gated(ctx, (r.get(lead) for r in rows))
    for r in rows:
        if str(r.get(lead)) in g:
            for f in fields:
                if f in r:
                    r[f] = mask_value(f, r[f])
            r["contact_hidden"] = True
    return g
