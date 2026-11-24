"""MÀN NÀO ĐANG ĐƯỢC DÙNG (ADR 0260). Đọc bộ đếm lượt đọc (``core.usage_reads`` — ``kernel/usage.py``) theo TÊN TRUY VẤN, ghép với danh sách màn của công ty
(gói đã bật, nghiệp vụ còn bật) ⇒ mỗi màn: số lượt đọc, số người, lần gần nhất; màn ít dùng nhất lên đầu — chỗ để quyết định gộp / bỏ / viết giải thích
theo SỐ LIỆU. Chỉ đếm người thật (``via = human``); thẻ gọn tự tải của bàn việc và trợ lý không tính."""

from typing import Any

from barista.kernel import company_settings, ui
from barista.kernel.core_ops import m
from barista.kernel.executor import Ctx
from barista.kernel.fields import NoInput

WINDOW_DAYS = 30


@m.query("screen_usage", resource="core.admin", title="Màn nào đang được dùng", category="core", min_scope="all")
async def screen_usage(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Mỗi màn của công ty: lượt đọc và số người trong 30 ngày qua. Màn dùng chung một truy vấn (vd trang quản trị và trang chuyên môn cùng một số liệu)
    chia chung số đếm — không tách được ai mở trang nào."""
    from barista.workspaces import INBOX, workspaces  # nạp muộn: workspaces đọc sổ đăng ký, sổ đăng ký nạp module này

    rows = await ctx.all(
        """SELECT query, sum(queries)::int AS reads, count(DISTINCT user_id)::int AS users
           FROM core.usage_reads WHERE via = 'human' AND query <> '' AND day > core.today() - %s GROUP BY query""", (WINDOW_DAYS,))
    used = {r["query"]: r for r in rows}
    since = await ctx.val("SELECT min(day) FROM core.usage_reads WHERE query <> ''")
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for w in workspaces():
        if ctx.p.packs is not None and w.pack and w.pack not in ctx.p.packs:
            continue
        for s in (*w.screens, *((INBOX,) if w.key == "inbox" else ())):
            if (w.key, s.key) in seen or (s.feature and not company_settings.on(ctx.p.settings, s.feature)):
                continue
            seen.add((w.key, s.key))
            u = used.get(s.query)
            out.append({"id": f"{w.key}/{s.key}", "screen": s.title, "space": w.title, "group": s.group,
                        "reads": u["reads"] if u else 0, "users": u["users"] if u else 0, "state": "used" if u else "unused"})
    out.sort(key=lambda r: (r["reads"], r["space"], r["screen"]))
    never = sum(1 for r in out if not r["reads"])
    started = f"Đang đếm từ {since:%d/%m/%Y}. " if since else "Chưa có lượt đọc nào được ghi — số đếm bắt đầu từ khi bản này chạy. "
    return ui.table(out, [ui.col("screen", "Màn", width=240), ui.col("space", "Phân hệ"), ui.col("group", "Nhóm"), ui.col("reads", "Lượt đọc", "number"),
                          ui.col("users", "Số người", "number"), ui.col("state", "", "badge", labels={"unused": "Chưa ai mở", "used": ""}, tones={"unused": "orange"})],
                    title=f"Màn nào đang được dùng · {WINDOW_DAYS} ngày qua", limit=1000,
                    kpis=[ui.kpi("Màn của công ty", len(out)), ui.kpi("Chưa ai mở", never, tone="orange" if never else None)], empty="Chưa có màn nào.",
                    note=started + "Chỉ đếm người thật mở; không ghi nội dung hay điều họ xem. Màn dùng chung một truy vấn chia chung số đếm.")
