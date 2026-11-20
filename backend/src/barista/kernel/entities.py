"""Pháp nhân (quyết định 0099) — CHIỀU trên bút toán và chứng từ tiền, không phải công ty riêng. Một nơi tra mã → pháp nhân, cho module dùng
khi lập chứng từ (dự án → pháp nhân ghi doanh thu, tài khoản ngân hàng → pháp nhân nhận tiền…). Trống = pháp nhân mặc định của công ty."""

from typing import Any
from uuid import UUID

from barista.kernel.errors import NotFound
from barista.kernel.executor import Ctx


async def default(ctx: Ctx) -> UUID:
    eid = await ctx.val("SELECT core.default_entity()")
    if eid is None:
        raise NotFound("Công ty chưa có pháp nhân mặc định — quản trị cài mặc định (admin.install_defaults)")
    return eid


async def resolve(ctx: Ctx, code: str | None) -> UUID:
    """Mã pháp nhân (vd "RBS") → id; trống ⇒ mặc định. Pháp nhân đã ngừng không nhận chứng từ mới."""
    if not (code or "").strip():
        return await default(ctx)
    eid = await ctx.val("SELECT id FROM core.legal_entities WHERE code = upper(%s) AND active", ((code or "").strip(),))
    if eid is None:
        raise NotFound(f"Không có pháp nhân đang hoạt động mã {code} — xem Quản trị › Pháp nhân")
    return eid


async def names(ctx: Ctx) -> dict[str, dict[str, Any]]:
    """id → {code, name} — để hiện trên bảng / chứng từ."""
    return {str(r["id"]): r for r in await ctx.all("SELECT id, code, name FROM core.legal_entities ORDER BY is_default DESC, code")}


async def many(ctx: Ctx) -> bool:
    """Công ty có hơn một pháp nhân ⇒ màn / xuất MISA hiện cột pháp nhân."""
    return int(await ctx.val("SELECT count(*) FROM core.legal_entities WHERE active") or 0) > 1
