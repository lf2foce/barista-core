"""BÁO CÁO QUẢN TRỊ (0166) — MỘT mục menu cho ban giám đốc, mỗi loại báo cáo là một tab con (màn); mỗi màn là một trang số liệu: ô số hàng trên, 2–3
biểu đồ cạnh nhau, bảng dưới (bố cục chia cột ``w`` + biểu đồ ``combo``).

Module này CHỈ khai phân hệ + quyền. Trang báo cáo do module SỞ HỮU con số góp vào bằng ``m.screens("mgmt", …)`` (giao dịch ⇒ module điều hành của gói ngành, dòng tiền ⇒
``acct``, quảng cáo ⇒ ``mkt``…) và đọc qua truy vấn / tập dữ liệu của chính module đó — không SQL chéo module. Gói tắt mặc định; công ty bật khi cần.
Số liệu toàn công ty ⇒ phạm vi cả công ty (``min_scope="all"`` ở từng màn)."""

from datetime import date, timedelta
from importlib import metadata
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic_core import to_jsonable_python

from barista.api import (
    Conflict,
    Ctx,
    Denied,
    Input,
    Invalid,
    Module,
    NoInput,
    NotFound,
    Pack,
    Workspace,
    field,
    menu_pages,
    today,
    ui,
)
from barista.api import __version__ as API_VERSION

Pack("mgmt", "Báo cáo quản trị",
     "Menu báo cáo cho ban giám đốc: mỗi loại báo cáo một tab (tổng quan tháng, doanh thu, dòng tiền, quảng cáo, tồn kho, nhân sự), các gói góp trang vào",
     kind="capability",
     grants=(("admin", "mgmt", "all", "read manage"), ("director", "mgmt", "all", "read manage")))

m = Module("mgmt", "Báo cáo quản trị", pack="mgmt", resources={"mgmt": "Báo cáo quản trị (số liệu toàn công ty)"})

# Chốt tháng (0213) HOÃN 14/11/2026 (anh Anh): lệnh + bảng giữ nguyên nhưng KHÔNG hiện trên menu, trợ lý không đề xuất. Mở lại = thêm màn
# Screen("mgmt_closings", …, "mgmt.closings", actions=(A("mgmt.close_month"),), open=Open("mgmt.snapshot", …)) và đổi agent của ba thao tác.
m.workspace(Workspace("mgmt", "Báo cáo quản trị", "chart.bar.doc.horizontal.fill", (), order=25))


# ── Dashboard tự tạo trong menu (0176): chủ app có quyền quản lý báo cáo quản trị đưa dashboard (app tự ghép) vào đây cho cả công ty ──
# Chỉ chia ĐỊNH NGHĨA: người xem mở bằng quyền chính mình (apps.open), khối nào cần phạm vi cả công ty mà người xem không có ⇒ báo rõ.

GONE_DAYS = 30


class PublishIn(Input):
    app_id: UUID = field("Dashboard (app của bạn)", hidden=True)
    publish: bool = field("Đưa vào Báo cáo quản trị", True, help="Tắt = gỡ khỏi menu; ai đã ghim sẽ thấy \"đã gỡ\"")


@m.command("publish", resource="mgmt", action="manage", title="Đưa dashboard vào Báo cáo quản trị", agent="propose")
async def publish(ctx: Ctx, inp: PublishIn) -> dict[str, Any]:
    """Chia một dashboard (app tự ghép) CỦA BẠN cho cả công ty trong menu Báo cáo quản trị — hoặc gỡ ra. Chỉ chia định nghĩa; mỗi người mở thấy số theo quyền
    của chính họ. Trợ lý chỉ đề xuất, người có quyền bấm áp dụng."""
    await ctx.call("apps.describe", app_id=str(inp.app_id))   # kiểm bằng quyền người gọi: chỉ chủ app mở được định nghĩa của nó
    r = await ctx.call_system("apps.set_published", app_id=str(inp.app_id), publish=inp.publish, by=str(ctx.p.user_id))
    verb = "Đã đưa" if inp.publish else "Đã gỡ"
    return {"app_id": inp.app_id, "summary": f"{verb} “{r['title']}” {'vào' if inp.publish else 'khỏi'} Báo cáo quản trị", "menu": True}


@m.query("dashboards", resource="mgmt", title="Dashboard trong Báo cáo quản trị (menu)", agent="deny")
async def dashboards(ctx: Ctx, inp: NoInput) -> list[dict[str, Any]]:
    """Màn cho menu: mỗi dashboard đang chia một màn (mở bằng quyền người xem); dashboard vừa gỡ chỉ còn hiện với ai đã GHIM nó, dưới tên "(đã gỡ)"."""
    pinned = {x["key"] for x in await ctx.call("core.pins") if x["workspace"] == "mgmt"}
    out = []
    for a in await ctx.call_system("apps.shared", gone_days=GONE_DAYS):
        key = f"dash-{a['app_id']}"
        if not a["published"] and key not in pinned:
            continue
        out.append({"key": key, "title": a["title"] if a["published"] else f"{a['title']} (đã gỡ)", "icon": a["icon"], "tint": a["tint"],
                    "query": "apps.open", "input": {"app_id": a["app_id"]}, "group": "Dashboard tự tạo",
                    "filters": a["filters"] if a["published"] else [], **({"filters_side": True} if a["published"] and a["filters_side"] else {})})
    return out


m.personal_screens("mgmt", "mgmt.dashboards")


# ── Chốt tháng (0213): chụp ĐẦU RA của từng trang chính thức trong menu, bằng quyền NGƯỜI CHỐT — không quyền hệ thống, không chép công thức. Trang app vốn do
# server mô tả nên bản chụp là đúng trang app vẽ lại được. Chỉ thêm: chốt lại = lần mới; xem tháng đã chốt = bản chụp, không tự thay bằng số sống.

OWN = frozenset({"mgmt_closings"})


def _month(text: str) -> date:
    """"2026-10" / "10/2026" ⇒ ngày đầu tháng; trống = tháng trước (việc chốt thường làm đầu tháng sau)."""
    t = (text or "").strip()
    if not t:
        first = today().replace(day=1)
        return (first - timedelta(days=1)).replace(day=1)
    try:
        y, mo = (t.split("/", 1)[::-1] if "/" in t else t.split("-", 1))
        d = date(int(y), int(mo), 1)
    except ValueError:
        raise Invalid(f"Tháng “{t}” không hiểu — viết dạng 2026-10") from None
    if d > today().replace(day=1):
        raise Invalid("Không chốt tháng chưa tới")
    return d


def _undrill(x: Any) -> Any:
    """Bỏ đường bấm ra dòng gốc trong bản chụp (bấm vào đó sẽ chạy số SỐNG, không phải số đã chốt); giá trị đưa về dạng JSON như app nhận."""
    x = to_jsonable_python(x)
    return _strip(x)


def _strip(x: Any) -> Any:
    if isinstance(x, dict):
        return {k: _strip(v) for k, v in x.items() if k not in ("drill", "row_actions", "actions")}
    if isinstance(x, list):
        return [_strip(v) for v in x]
    return x


def _pack_versions() -> dict[str, str]:
    """Phiên bản bản phân phối của từng gói đang nạp (điểm vào ``barista.packs``) — gói trong lõi mang phiên bản của lõi."""
    out: dict[str, str] = {}
    for ep in metadata.entry_points(group="barista.packs"):
        out[ep.name] = ep.dist.version if ep.dist else ""
    return dict(sorted(out.items()))


class CloseIn(Input):
    period: str = field("Tháng chốt", "", kind="month", example="2026-10", help="Trống = tháng trước")
    note: str = field("Ghi chú", "", max_length=500, help="Vd \"số đã họp giao ban 05/11\" hoặc lý do chốt lại")


@m.command("close_month", resource="mgmt", action="manage", title="Chốt tháng (chụp Báo cáo quản trị)", agent="deny", consistent=True)
async def close_month(ctx: Ctx, inp: CloseIn) -> dict[str, Any]:
    """Chụp mọi trang chính thức của menu Báo cáo quản trị (trang của các module + dashboard đã đưa vào menu) cho một tháng, bằng quyền của bạn. Chốt lại cùng tháng = lần chốt mới, bản cũ giữ nguyên.
    Thiếu quyền xem một trang ⇒ không chốt (báo tên trang). Trang của gói chưa bật thì bỏ qua và ghi vào nguồn gốc bản chốt."""
    if not ctx.p.has_scope("mgmt", "all"):
        raise Denied("Chốt tháng là việc của cả công ty — cần quyền quản lý Báo cáo quản trị phạm vi cả công ty")
    period = _month(inp.period)
    version = int(await ctx.val("SELECT coalesce(max(version), 0) + 1 FROM mgmt.snapshots WHERE period = %s", (period,)))
    as_of = await ctx.val("SELECT now()")   # mốc lấy số: giờ bắt đầu giao dịch chốt — mọi trang đọc CÙNG ảnh dữ liệu này (consistent=True)
    pages: list[tuple[str, str, str, str, dict[str, Any], Any]] = []   # (khoá, tên, nhóm, truy vấn, đầu vào, trang)
    denied, skipped = [], []
    targets = [(s.key, s.title, s.group or "", s.query, dict(s.input)) for s in menu_pages("mgmt") if s.key not in OWN]
    targets += [(f"dash-{a['app_id']}", a["title"], "Dashboard tự tạo", "apps.open", {"app_id": a["app_id"]})
                for a in await ctx.call_system("apps.shared", gone_days=0) if a["published"]]   # dashboard đã đưa vào menu (0176)
    for key, title, grp, query, args in targets:
        try:
            pages.append((key, title, grp, query, args, await ctx.call(query, **args)))   # quyền người chốt; RLS như khi họ mở trang
        except Denied:
            denied.append(title)
        except (NotFound, Invalid, Conflict) as e:   # gói chưa bật (NotFound), trang cần đầu vào… ⇒ bỏ qua, ghi lại
            skipped.append(f"{title} ({e})")
    if denied:   # thiếu quyền một trang ⇒ bản chốt sẽ thiếu trang đó mà không ai biết ⇒ không chốt
        raise Denied("Người chốt phải xem được mọi trang của Báo cáo quản trị — bạn chưa xem được: " + ", ".join(denied))
    source = {"api": API_VERSION, "packs": _pack_versions(), "as_of": as_of.isoformat(), "skipped": skipped}
    for key, title, grp, query, args, page in pages:
        await ctx.exec("""INSERT INTO mgmt.snapshots (period, version, screen, title, grp, page, note, closed_by, source)
                          VALUES (%s, %s, %s, %s, %s, %s, %s, core.me(), %s)""",
                       (period, version, key, title, grp, Jsonb(_undrill(page)), inp.note.strip(),
                        Jsonb({**source, "query": query, "input": to_jsonable_python(args)})))
    taken = [t for _, t, *_ in pages]
    if not taken:
        raise Invalid("Không chụp được trang nào — " + ("; ".join(skipped) or "menu Báo cáo quản trị chưa có trang"))
    return {"period": f"{period:%Y-%m}", "version": version, "pages": len(taken), "skipped": skipped,
            "summary": f"Đã chốt {period:%m/%Y} (lần {version}): {len(taken)} trang" + (f", bỏ qua {len(skipped)}" if skipped else "")}


@m.query("closings", resource="mgmt", title="Bản chốt tháng", min_scope="all", agent="deny")
async def closings(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Các lần chốt: mỗi dòng một trang đã chụp (mới nhất trước). Bấm mở đúng trang lúc chốt."""
    rows = await ctx.all("""SELECT s.id, to_char(s.period, 'MM/YYYY') AS month, s.version, s.title, s.grp, s.note, s.closed_at,
                                   coalesce(u.name, u.email) AS closed_by,
                                   s.version = max(s.version) OVER (PARTITION BY s.period) AS latest
                            FROM mgmt.snapshots s JOIN core.users u ON u.id = s.closed_by
                            ORDER BY s.period DESC, s.version DESC, s.grp, s.title LIMIT 2000""")
    for r in rows:
        r["latest"] = "yes" if r["latest"] else "no"
    return ui.table(rows, [
        ui.col("month", "Tháng"), ui.col("version", "Lần chốt", "number"), ui.col("title", "Trang"), ui.col("grp", "Nhóm"),
        ui.col("latest", "Bản", "badge", labels={"yes": "Mới nhất", "no": "Đã thay"}, tones={"yes": "green", "no": "gray"}),
        ui.col("closed_by", "Người chốt"), ui.col("closed_at", "Lúc chốt", "datetime"), ui.col("note", "Ghi chú"),
    ], title="Bản chốt tháng", limit=2000, empty="Chưa chốt tháng nào — bấm “Chốt tháng” để chụp Báo cáo quản trị của tháng trước",
        note="Bản chốt là số lúc chốt; dữ liệu nhập trễ sau đó không đổi bản chốt (xem số mới ở chính trang trong menu). Chốt lại = lần mới, bản cũ còn.")


class SnapshotIn(Input):
    snapshot_id: UUID = field("Bản chốt", hidden=True)


@m.query("snapshot", resource="mgmt", title="Một trang trong bản chốt", min_scope="all", agent="deny")
async def snapshot(ctx: Ctx, inp: SnapshotIn) -> dict[str, Any]:
    """Đúng trang lúc chốt, kèm dòng ghi tháng / lần chốt / người chốt — không chạy lại số."""
    r = await ctx.one("""SELECT s.page, s.title, s.period, s.version, s.closed_at, s.source, coalesce(u.name, u.email) AS by
                         FROM mgmt.snapshots s JOIN core.users u ON u.id = s.closed_by WHERE s.id = %s""", (inp.snapshot_id,))
    if not r:
        raise NotFound("Không có bản chốt này")
    page = dict(r["page"]) if isinstance(r["page"], dict) else {"kind": "table", "rows": [], "columns": []}
    src = r["source"] or {}
    stamp = (f"Bản chốt {r['period']:%m/%Y} · lần {r['version']} · {r['by']} chốt lúc {r['closed_at']:%d/%m/%Y %H:%M} — số lúc chốt, không cập nhật"
             + (f" (lõi {src['api']})" if src.get("api") else "") + ".")
    page["note"] = f"{stamp} {page.get('note') or ''}".strip()
    if page.get("title"):
        page["title"] = f"{page['title']} (bản chốt {r['period']:%m/%Y})"
    return page
