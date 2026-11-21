"""SỨC KHOẺ DỮ LIỆU — họp khách 31/10 — docs/clients/<khách> (P0): "biết nguồn nào đã đồng bộ tới lúc nào, còn thiếu bao nhiêu, bản ghi nào lỗi; số đổi giải thích
được". Chị Phó tổng: "số liệu sai thì không dùng được".

Chỉ GOM + HIỂN THỊ (0078): mỗi module tự khai phần của mình bằng truy vấn nội bộ ``<module>.health_report`` — cùng một khuôn {rank, fresh: [{title,
subtitle, at, stale, tone?, badge?, why, open}], issues: [{key, n, title, subtitle?, subtitle_resource?, open, why}], notes}; ``subtitle_resource``: dòng phụ
có thông tin của người (vd tên khách nghi trùng) ⇒ chỉ hiện cho người xem ĐỌC được tài nguyên đó với phạm vi cả công ty (rà vai 18/11/2026 — vai Công
nghệ không thấy tên khách; số đếm vẫn hiện); module này không biết ngành nào, không đọc
bảng của ai. Kiểm xuyên hai gói nằm ở gói bắt buộc cả hai. Gói tắt ⇒ phần đó ghi "chưa kiểm" (không thành 0). Số 0 = ĐÃ kiểm và không có.
"""

from datetime import UTC, date, datetime
from typing import Any

from barista.api import Ctx, Module, NoInput, Screen, ui, zone
from barista.kernel import registry
from barista.kernel.executor import allowed

m = Module("health", "Sức khoẻ dữ liệu", pack="base",
           resources={"health": "Sức khoẻ dữ liệu (độ mới các nguồn, bản ghi lỗi / thiếu — số cả công ty)"})

REPORT = "health_report"


def _age(at: datetime | date | None, now: datetime) -> str:
    """"3 giờ trước" — ngày (date) tính từ đầu ngày giờ công ty."""
    if at is None:
        return "chưa có"
    if not isinstance(at, datetime):
        at = datetime(at.year, at.month, at.day, tzinfo=zone())
    hours = (now - at).total_seconds() / 3600
    if hours < 1:
        return "vừa xong"
    if hours < 48:
        return f"{int(hours)} giờ trước"
    return f"{int(hours // 24)} ngày trước"


def _when(at: datetime | date | None) -> str:
    if at is None:
        return "—"
    if isinstance(at, datetime):
        return f"{at.astimezone(zone()):%d/%m %H:%M}"
    return f"{at:%d/%m/%Y}"


async def _collect(ctx: Ctx) -> dict[str, Any]:
    """{module: phần của module | None (gói tắt)} — mọi module khai ``health_report`` + sự kiện tự động lỗi (lõi)."""
    reports = {op.module: await ctx.call_system_optional(op.name) for op in registry.OPS.values() if op.name == f"{op.module}.{REPORT}"}
    return {"reports": reports, "admin": await ctx.call_system("admin.delivery_health")}


def assess(d: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Các phần của module ⇒ (độ mới, vấn đề, lý do số lệch, ghi chú ngưỡng). Hàm thuần — test được không cần thẻ."""
    fresh: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    why: list[str] = []
    notes: list[str] = []
    unchecked: list[str] = []
    for mod, r in sorted(d["reports"].items(), key=lambda kv: (kv[1] or {}).get("rank", 999)):
        if r is None:
            unchecked.append((registry.MODULES.get(mod) or mod).lower())
            continue
        for f in r["fresh"]:
            fresh.append({"title": f["title"], "subtitle": " · ".join(x for x in (f"Gần nhất {_when(f['at'])}" if f.get("at") else "", f.get("subtitle", ""))
                                                                       if x), "badge": f.get("badge") or _age(f.get("at"), now),
                          "tone": "warn" if f["stale"] else f.get("tone", "ok"), "stale": f["stale"], "open": f.get("open")})
            if f["stale"] and f.get("why"):
                why.append(f["why"])
        issues += r["issues"]
        notes += r.get("notes", [])
    ad = d["admin"]
    issues.append({"key": "dead_deliveries", "n": ad["dead"], "title": "sự kiện tự động đã dừng thử (lỗi)",
                   "subtitle": f"{ad['retrying']} đang thử lại · cũ nhất {_when(ad['oldest'])}" if ad["dead"] or ad["retrying"] else "",
                   "open": "admin.deliveries", "why": "{n} sự kiện lỗi ⇒ các phản ứng tự động (hoá đơn, việc theo dõi, thông báo…) có thể chưa chạy"})
    issues.append({"key": "system_errors", "n": ad.get("errors", 0), "title": "lỗi hệ thống trong 7 ngày", "subtitle": "",
                   "open": {"query": "admin.audit", "input": {"status": "error"}},
                   "why": "{n} thao tác gặp lỗi hệ thống (lỗi lập trình, không phải sai dữ liệu) ⇒ xem chi tiết trong Nhật ký, lọc “Lỗi hệ thống”"})
    why += [i["why"].format(n=i["n"]) for i in issues if i["n"]]
    issues.sort(key=lambda i: -(i["n"] > 0))
    return {"fresh": fresh, "issues": issues, "why": why, "notes": notes, "unchecked": unchecked}


def _open(ctx: Ctx, target: str | dict[str, Any] | None) -> dict[str, Any]:
    """Liên kết bấm sang màn sửa — CHỈ khi người xem mở được màn đó (admin không xem hoá đơn, giám đốc không xem sự kiện lỗi…)."""
    if not target:
        return {}
    name = target if isinstance(target, str) else target["query"]
    op = registry.get(name)
    inp = {} if isinstance(target, str) else target.get("input", {})   # mở sẵn bộ lọc (vd nhật ký chỉ lỗi hệ thống)
    return {"open": {"query": name, "input": inp}} if op is not None and allowed(ctx.p, op) else {}


def _subtitle(ctx: Ctx, issue: dict[str, Any]) -> str:
    """Dòng phụ của một loại vấn đề — dòng có thông tin của người (``subtitle_resource``) chỉ cho người đọc được tài nguyên đó cả công ty."""
    res = issue.get("subtitle_resource")
    if res and not (ctx.p.can(res, "read") and ctx.p.has_scope(res, "all")):
        return ""
    return str(issue.get("subtitle") or "")


def figures(r: dict[str, Any]) -> dict[str, int]:
    """Ba con số của Sức khoẻ dữ liệu từ kết quả ``assess`` — MỘT định nghĩa cho ô số của màn và cho ``health.summary``."""
    bad = [i for i in r["issues"] if i["n"]]
    return {"issue_kinds": len(bad), "records": sum(i["n"] for i in bad), "stale_sources": sum(1 for f in r["fresh"] if f["stale"])}


@m.query("summary", resource="health", title="Sức khoẻ dữ liệu — số tóm tắt", min_scope="all", internal=True)
async def summary(ctx: Ctx, inp: NoInput) -> dict[str, int]:
    """SỐ TÓM TẮT cho màn tổng hợp (bảng điều hành của gói): đúng ba con số của màn này {issue_kinds, records, stale_sources} — không dựng thẻ, không
    mã / tên. Gọi bằng QUYỀN NGƯỜI XEM (``ctx.call_as_optional("health.summary", ctx.p)``): người không xem được Sức khoẻ dữ liệu cả công ty ⇒ None,
    bên gọi bỏ dòng đó — như khi đọc cả màn trước đây."""
    return figures(assess(await _collect(ctx), datetime.now(UTC)))


@m.query("overview", resource="health", title="Sức khoẻ dữ liệu", min_scope="all")
async def overview(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Nguồn nào cập nhật tới đâu, bản ghi nào lỗi / thiếu, vì sao số trên các màn có thể lệch — số cả công ty."""
    r = assess(await _collect(ctx), datetime.now(UTC))
    fig = figures(r)
    bad = fig["issue_kinds"]
    stale = fig["stale_sources"]
    blocks: list[dict[str, Any]] = [ui.b_kpis([
        ui.kpi("Loại vấn đề cần sửa", bad, tone="orange" if bad else "green"),
        ui.kpi("Bản ghi cần sửa", fig["records"], tone="orange" if bad else None),
        ui.kpi("Nguồn cập nhật trễ", stale, tone="red" if stale else "green"),
    ])]
    if r["why"]:
        blocks.append(ui.b_callout("warn", "Vì sao số trên các màn có thể lệch", facts=r["why"]))
    else:
        blocks.append(ui.b_callout("ok", "Chưa thấy lý do nào làm số lệch", "Đã kiểm các nguồn và các loại lỗi bên dưới."))
    blocks.append(ui.b_list([{**{k: v for k, v in f.items() if k not in ("stale", "open")}, **_open(ctx, f.get("open"))} for f in r["fresh"]],
                            "Độ mới theo nguồn"))
    blocks.append(ui.b_list([{"title": f"{i['n']} {i['title']}", "subtitle": _subtitle(ctx, i), "badge": "Cần sửa" if i["n"] else "Ổn",
                              "tone": "bad" if i["n"] else "ok", **_open(ctx, i["open"])} for i in r["issues"]],
                            "Bản ghi cần sửa"))
    notes = [*r["notes"], "Số 0 = đã kiểm và không có. Lần nhập file chưa được ghi riêng nên chưa có mốc \"nhập file gần nhất\"."]
    if r["unchecked"]:
        notes.append("Chưa kiểm (gói chưa bật): " + ", ".join(r["unchecked"]) + ".")
    blocks.append(ui.b_callout("muted", "Cách đọc", facts=notes))
    return ui.card("Sức khoẻ dữ liệu", blocks, subtitle="Nguồn nào cập nhật tới đâu, bản ghi nào lỗi / thiếu — số cả công ty",
                   icon="stethoscope")


# ---- Màn hình (0069): module tự khai phân hệ / góp màn vào phân hệ khác; workspaces.py chỉ ráp và lọc theo quyền
m.screens("admin", (
    Screen("health", "Sức khoẻ dữ liệu", "stethoscope", "health.overview", group="Hệ thống", min_scope="all"),
), rank=20)
