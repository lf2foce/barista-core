"""KHOÁ CHỐNG LẶP — hạn giữ (quyết định 0177 §2). ``core.idempotency`` ghi mỗi lệnh có ``Idempotency-Key`` (và mỗi dòng nhập file): cùng khoá + cùng nội dung ⇒ trả
kết quả cũ, cùng khoá khác nội dung ⇒ ``conflict``. Khoá chỉ có nghĩa trong CỬA SỔ phát lại — client gửi lại khi mất phản hồi, người nhập lại cùng file —
nên công ty đặt cửa sổ (mặc định 90 ngày, cùng hạn giữ nội dung dòng nhập — 0174 §4), quá hạn vòng quét xoá khoá. Không bao giờ chạm nhật ký thao tác
(``core.audit_log``) hay khoá chống lặp của sổ cái (``ledger.journals.idempotency_key`` — mãi mãi, bất biến 4)."""

from typing import Any

from barista.kernel import ui
from barista.kernel.core_ops import m
from barista.kernel.executor import Ctx
from barista.kernel.fields import Input, NoInput, field

PURGE_ROWS = 20_000   # mỗi lượt quét xoá tối đa chừng ấy khoá của một công ty (lượt sau làm tiếp) — giao dịch ngắn, không khoá bảng lâu


@m.query("idempotency_retention", resource="core.admin", title="Chống ghi trùng khi gửi lại", category="core")
async def idempotency_retention(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Một dòng: cửa sổ phát lại (ngày) của công ty + bao nhiêu khoá còn giữ, bao nhiêu sẽ xoá ở lượt quét tới."""
    keep = await ctx.val("SELECT idempotency_keep_days FROM core.tenants")
    got = await ctx.one(
        """SELECT count(*) AS kept, count(*) FILTER (WHERE %(k)s::int IS NOT NULL AND created_at < now() - make_interval(days => %(k)s::int)) AS due
           FROM core.idempotency""", {"k": keep})
    g = got or {}
    return ui.table([{"id": "idempotency", "keep": f"{keep} ngày" if keep else "Giữ mãi", "kept": g.get("kept", 0), "due": g.get("due", 0)}],
                    [ui.col("keep", "Cửa sổ phát lại"), ui.col("kept", "Khoá đang giữ", "number"), ui.col("due", "Sẽ xoá ở lượt quét tới", "number")],
                    title="Chống ghi trùng khi gửi lại",
                    note="Khoá chống lặp giúp gửi lại / nhập lại cùng dữ liệu không ghi hai lần — chỉ cần trong cửa sổ này. Quá hạn khoá bị xoá: nhập lại một "
                         "file cũ hơn cửa sổ sẽ chạy lại từng dòng (lệnh có khoá tự nhiên như mã dòng ngoài thì vẫn chặn trùng). Sổ cái và nhật ký thao "
                         "tác không bị xoá. Công ty nhập lại cùng file thường xuyên: đặt “Giữ mãi”.")


class SetIdempotencyRetention(Input):
    keep_days: int = field("Giữ khoá chống ghi trùng (ngày)", 90, ge=7, le=3650)
    keep_forever: bool = field("Giữ mãi", False, help="Bật: không xoá khoá chống lặp (bỏ qua số ngày)")


@m.command("set_idempotency_retention", resource="core.admin", action="manage", title="Đặt hạn giữ khoá chống lặp", agent="deny", category="setup")
async def set_idempotency_retention(ctx: Ctx, inp: SetIdempotencyRetention) -> dict[str, Any]:
    """Đổi cửa sổ phát lại của CHÍNH công ty. Rút ngắn ⇒ lượt quét kế tiếp xoá khoá đã quá cửa sổ mới — không lấy lại được. Trợ lý không đổi thay."""
    keep = None if inp.keep_forever else inp.keep_days
    old = await ctx.val("SELECT idempotency_keep_days FROM core.tenants")
    await ctx.exec("UPDATE core.tenants SET idempotency_keep_days = %s WHERE id = core.tenant()", (keep,))
    return {"keep_days": keep, "previous": old, "summary": f"Chống ghi trùng khi gửi lại — giữ khoá: {f'{keep} ngày' if keep else 'mãi'}"}


@m.command("purge_idempotency", resource="core.admin", title="Xoá khoá chống lặp quá cửa sổ phát lại", internal=True,
           sweep="""SELECT t.id AS tenant_id FROM core.tenants t WHERE t.idempotency_keep_days IS NOT NULL
                    AND EXISTS (SELECT 1 FROM core.idempotency i WHERE i.tenant_id = t.id
                                AND i.created_at < now() - make_interval(days => t.idempotency_keep_days))""")
async def purge_idempotency(ctx: Ctx, inp: NoInput) -> dict[str, int]:
    """Vòng quét: xoá tối đa ``PURGE_ROWS`` khoá cũ hơn cửa sổ của công ty (khoá cũ nhất trước). Chỉ bảng ``core.idempotency`` của chính công ty (RLS)."""
    keep = await ctx.val("SELECT idempotency_keep_days FROM core.tenants")
    if keep is None:
        return {"keys": 0}
    n = await ctx.exec(
        """DELETE FROM core.idempotency WHERE ctid = ANY(ARRAY(
               SELECT ctid FROM core.idempotency WHERE created_at < now() - make_interval(days => %s) ORDER BY created_at LIMIT %s))""", (keep, PURGE_ROWS))
    return {"keys": n}
