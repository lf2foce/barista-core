"""DÒNG THỜI GIAN CỦA KẾ HOẠCH (0222) — ngày bắt đầu, giai đoạn, phụ thuộc giữa các việc, và khối ``timeline`` (Gantt) do server dựng.

* Việc có ``start_on`` (trống = ngày tạo), ``phase`` (nhãn nhóm) và ``depends_on`` (chờ việc khác CÙNG kế hoạch). Mốc kế hoạch vẫn là ``flow.plans.due_on`` —
  không có bảng mốc / giai đoạn riêng.
* Phụ thuộc chỉ trong một kế hoạch, KHÔNG có chu trình: kiểm bằng code dưới khoá kế hoạch (``_lock_plan``) trên TOÀN BỘ đồ thị của kế hoạch (qua
  ``flow.plan_task_graph`` — người sửa có thể không thấy việc phòng khác, nhưng chu trình đi qua việc đó vẫn phải bị chặn).
* Khối ``timeline`` (``ui.b_timeline``) chỉ hiện việc người xem THẤY (RLS) có tên / người làm; việc phòng khác chỉ còn ngày + trạng thái, tên thay bằng
  "việc của <phòng>" — đúng như danh sách việc của thẻ kế hoạch. Mũi tên phụ thuộc chỉ vẽ giữa các việc người xem thấy.
"""

from datetime import date, timedelta
from typing import Any
from uuid import UUID

from barista.api import Ctx, Day, Input, Invalid, NotFound, business_calendar, field, today, ui, zone
from barista.modules.flow import TASK_STATUS, _plan_open, _task_open, m
from barista.modules.flow.schedule_rules import check_schedule, find_cycle
from barista.modules.flow.tasks import TaskRef, _calendar_of, _task_for_change

MAX_TL_PLANS = 30        # màn Kế hoạch: số kế hoạch tối đa trong góc nhìn dòng thời gian (kế hoạch rủi ro / sớm hạn lên đầu)
MAX_SPAN_DAYS = 540      # khoảng vẽ tối đa — kế hoạch dài hơn thì cắt đầu xa nhất, ghi chú rõ
SLUG = {"todo": "muted", "doing": "accent", "blocked": "bad", "done": "ok", "cancelled": "muted"}


# ── Hợp đồng đọc đồ thị phụ thuộc của CẢ kế hoạch ──

class PlanGraphIn(Input):
    plan_id: UUID = field("Kế hoạch", hidden=True)


@m.query("plan_task_graph", resource=None, title="Đồ thị phụ thuộc của kế hoạch", internal=True, agent="deny", callers=("flow",),
         why="set_task_timing / propose_plan kiểm phụ thuộc không tạo chu trình trên CẢ kế hoạch (người sửa có thể không thấy việc phòng khác, "
             "nhưng chu trình đi qua việc đó vẫn phải bị chặn), sau khi kiểm người gọi sửa được việc đó (RLS) và khoá kế hoạch. "
             "Trả mã việc + mã việc phải xong trước — KHÔNG tên việc, KHÔNG người làm, KHÔNG ngày. [id: người gọi nêu plan_id đã qua RLS của việc họ sửa]")
async def plan_task_graph(ctx: Ctx, inp: PlanGraphIn) -> list[dict[str, Any]]:
    return await ctx.all("SELECT id, depends_on FROM flow.tasks WHERE plan_id = %s", (inp.plan_id,))


class SetTaskTiming(Input):
    task_id: UUID = field("Việc", hidden=True)
    start_on: Day | None = field("Bắt đầu", None, placeholder="Giữ ngày hiện có")
    clear_start: bool = field("Bỏ ngày bắt đầu", False, help="Đưa về mặc định: thanh bắt đầu từ ngày tạo việc")
    phase: str | None = field("Giai đoạn", None, max_length=60, help="Nhãn nhóm trên dòng thời gian (vd “Chuẩn bị”). Trống = giữ; một dấu cách = bỏ nhóm")
    after: list[UUID] | None = field("Chờ các việc", None, options_query="flow.plan_task_options", options_with=("task_id",),
                                      help="Việc này chỉ bắt đầu sau khi các việc đã chọn xong (cùng kế hoạch, không vòng). Không chọn = giữ; chọn rỗng = bỏ hết")


@m.command("set_task_timing", resource="flow", title="Lịch của việc (bắt đầu · giai đoạn · chờ việc)")
async def set_task_timing(ctx: Ctx, inp: SetTaskTiming) -> dict[str, Any]:
    """Đặt ngày bắt đầu / giai đoạn / việc phải xong trước của MỘT việc — để dòng thời gian kế hoạch vẽ đúng. Ngày bắt đầu không sau hạn; phụ thuộc chỉ
    giữa các việc CÙNG kế hoạch và không tạo vòng. Chỉ đổi lịch, không đổi người làm / hạn (dùng "Giao lại / dời hạn")."""
    t = await _task_for_change(ctx, inp.task_id)   # khoá kế hoạch, kiểm kế hoạch còn mở, khoá dòng việc
    if t["status"] in ("done", "cancelled"):
        raise Invalid("Việc đã xong / đã huỷ — không cần đổi lịch")
    start = None if inp.clear_start else (inp.start_on or t["start_on"])
    check_schedule(start, t["due_on"], f"“{t['title']}”: ")
    phase = t["phase"] if inp.phase is None else inp.phase.strip()
    deps: list[UUID] = list(t["depends_on"])
    if inp.after is not None:
        ids = list(dict.fromkeys(inp.after))
        if ids and not t["plan_id"]:
            raise Invalid("Chỉ việc trong một kế hoạch mới chờ được việc khác")
        if t["id"] in ids:
            raise Invalid("Một việc không chờ chính nó")
        if ids:
            graph = await ctx.call_system("flow.plan_task_graph", plan_id=t["plan_id"])
            known = {g["id"] for g in graph}
            if set(ids) - known:
                raise Invalid("Chỉ chờ được việc CÙNG kế hoạch (bản đầu chưa hỗ trợ phụ thuộc chéo kế hoạch)")
            if find_cycle({**{g["id"]: list(g["depends_on"]) for g in graph}, t["id"]: ids}):
                raise Invalid("Phụ thuộc này tạo vòng (A chờ B, B lại chờ A) — bỏ bớt một mũi tên")
        deps = ids
    await ctx.exec("UPDATE flow.tasks SET start_on = %s, phase = %s, depends_on = %s, updated_at = now() WHERE id = %s",
                   (start, phase, deps, inp.task_id))
    bits = [f"bắt đầu {start:%d/%m}" if start else "bắt đầu từ ngày tạo", *([f"giai đoạn {phase}"] if phase else []),
            *([f"chờ {len(deps)} việc"] if deps else [])]
    return {"task_id": inp.task_id, "summary": f"“{t['title']}”: " + " · ".join(bits)}


@m.query("plan_task_options", resource="flow", title="Việc cùng kế hoạch")
async def plan_task_options(ctx: Ctx, inp: TaskRef) -> list[dict[str, str]]:
    """Ô "Chờ các việc" của form: các việc KHÁC trong cùng kế hoạch mà người xem thấy (RLS). Chỉ để chọn — lệnh kiểm lại."""
    t = await ctx.one("SELECT plan_id FROM flow.tasks WHERE id = %s", (inp.task_id,))
    if t is None:
        raise NotFound("Không thấy việc")
    if t["plan_id"] is None:
        return []
    return [{"value": str(r["id"]), "label": r["title"] + (f" · hạn {r['due_on']:%d/%m}" if r["due_on"] else "")}
            for r in await ctx.all("""SELECT id, title, due_on FROM flow.tasks WHERE plan_id = %s AND id <> %s AND status <> 'cancelled'
                                      ORDER BY due_on NULLS LAST, created_at LIMIT 100""", (t["plan_id"], inp.task_id))]


# ── Dựng khối timeline ──

def _local(ts: Any) -> date:
    return ts.astimezone(zone()).date()


def _row(t: dict[str, Any], ids: set[str], by_id: dict[str, dict[str, Any]], t0: date) -> dict[str, Any]:
    """Một việc ⇒ một dòng. Bắt đầu = ``start_on`` hoặc ngày tạo; chưa có hạn ⇒ ``end`` None."""
    due = t["due_on"]
    start = t.get("start_on") or (_local(t["created_at"]) if t.get("created_at") else (due or t0))
    if due and start > due:
        start = due
    late = due is not None and due < t0 and t["status"] not in ("done", "cancelled")
    tone = "bad" if late else SLUG[t["status"]]
    after = [str(d) for d in (t.get("depends_on") or []) if str(d) in ids] if t["seen"] else []
    flag = ""
    for d in after:   # bắt đầu khi việc trước CHƯA xong và còn hạn sau ngày bắt đầu ⇒ cảnh báo (code tính)
        p = by_id[d]
        if p["status"] not in ("done", "cancelled") and p["due_on"] and p["due_on"] > start and p["seen"]:
            flag = f"Bắt đầu {start:%d/%m} nhưng “{p['title']}” mới xong ngày {p['due_on']:%d/%m}"
            break
    dates = (f"{start:%d/%m} – {due:%d/%m}" if due and due != start else f"{due:%d/%m}") if due else f"từ {start:%d/%m} · chưa có hạn"
    return ui.tl_item(str(t["id"]), t["title"], start, due, tone=tone, status=TASK_STATUS[t["status"]] + (" · quá hạn" if late else ""),
                      who=(t.get("who") or "") if t["seen"] else "", dates=dates, flag=flag, open=_task_open(t["id"]) if t["seen"] else None, after=after)


def _wbs(ref: str | None) -> tuple[int, tuple[int, ...]]:
    """Khoá xếp theo mã WBS ("1.2", "2.10"): việc có mã đứng trước theo số từng cấp; không mã ⇒ sau, rồi xếp theo ngày như cũ."""
    parts = tuple(int(x) for x in (ref or "").split(".") if x.strip().isdigit())
    return (0, parts) if parts else (1, ())


def _groups_of(rows: list[tuple[dict[str, Any], dict[str, Any]]]) -> list[dict[str, Any]]:
    """Nhóm trong MỘT kế hoạch: theo giai đoạn nếu có việc nào có, không thì theo người làm. Việc phòng khác (không thấy) gom riêng. Nhóm xếp theo
    ngày bắt đầu sớm nhất."""
    phased = any(t["seen"] and t.get("phase") for t, _ in rows)
    refs = {r["id"]: t.get("plan_ref") for t, r in rows}   # mã WBS của file kế hoạch (nhập từ Excel) ⇒ giữ đúng thứ tự file: 2.1 trước 2.2
    buckets: dict[str, list[dict[str, Any]]] = {}
    for t, r in rows:
        if not t["seen"]:
            key = f"Việc phòng khác ({t['team']})" if t.get("team") else "Việc phòng khác"
        elif phased:
            key = t.get("phase") or "Chưa xếp giai đoạn"
        else:
            key = t.get("who") or "Chưa ai nhận"
        buckets.setdefault(key, []).append(r)
    ordered = sorted(buckets.items(), key=lambda kv: (kv[0].startswith(("Chưa xếp", "Việc phòng khác")), min(x["start"] for x in kv[1])))
    return [ui.tl_group(f"g{i}", name, sorted(items, key=lambda x: (_wbs(refs.get(x["id"])), x["start"], x["end"] or "9999")),
                        subtitle="giai đoạn" if phased and not name.startswith(("Chưa xếp", "Việc phòng")) else "")
            for i, (name, items) in enumerate(ordered)]


async def _off(ctx: Ctx, start: date, end: date) -> list[tuple[date, str]]:
    try:
        return business_calendar.non_working(await _calendar_of(ctx, None), start, end)
    except ValueError:
        return []


def _frame(starts: list[date], ends: list[date], t0: date) -> tuple[date, date, str]:
    lo, hi = min([*starts, t0]), max([*ends, *starts, t0])
    lo, hi = lo - timedelta(days=1), hi + timedelta(days=2)
    if (hi - lo).days < 14:
        hi = lo + timedelta(days=14)
    note = ""
    if (hi - lo).days > MAX_SPAN_DAYS:
        lo = hi - timedelta(days=MAX_SPAN_DAYS)
        note = f"Khoảng thời gian dài hơn {MAX_SPAN_DAYS} ngày — đang vẽ từ {lo:%d/%m/%Y}."
    return lo, hi, note


def _bounds(items: list[dict[str, Any]]) -> tuple[list[date], list[date]]:
    starts = [date.fromisoformat(i["start"]) for i in items]
    return starts, [date.fromisoformat(i["end"]) for i in items if i["end"]]


async def plan_timeline(ctx: Ctx, plan: dict[str, Any], tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Khối dòng thời gian của MỘT kế hoạch. ``tasks`` = ``_plan_view`` (việc người xem thấy + việc phòng khác ẩn tên). Không có việc ⇒ None."""
    live = [t for t in tasks if t["status"] != "cancelled"]
    if not live:
        return None
    t0 = today()
    ids = {str(t["id"]) for t in live}
    by_id = {str(t["id"]): t for t in live}
    rows = [(t, _row(t, ids, by_id, t0)) for t in live]
    items = [r for _, r in rows]
    starts, ends = _bounds(items)
    mile = [ui.tl_milestone(plan["due_on"], "Mốc kế hoạch", "bad" if plan["due_on"] < t0 and plan["status"] == "open" else "accent")] if plan["due_on"] else []
    lo, hi, note = _frame(starts, [*ends, *([plan["due_on"]] if plan["due_on"] else [])], t0)
    return ui.b_timeline(_groups_of(rows), today=t0, start=lo, end=hi, title="Dòng thời gian", milestones=mile, off=await _off(ctx, lo, hi), note=note)


async def portfolio_timeline(ctx: Ctx, plans: list[dict[str, Any]], tasks: list[dict[str, Any]], summary: dict[Any, dict[str, Any]]) -> dict[str, Any]:
    """Góc nhìn dòng thời gian của màn Kế hoạch: mỗi kế hoạch MỘT nhóm (gấp sẵn, hàng tiêu đề vẽ thanh cả kế hoạch + mốc), mở ra thấy từng việc.
    ``plans`` đã xếp (kế hoạch trễ / nguy cơ lên đầu); ``summary`` = {plan_id: {"line": chữ phụ, "tone": …}}."""
    t0 = today()
    groups: list[dict[str, Any]] = []
    all_items: list[dict[str, Any]] = []
    extra: list[date] = []
    shown = plans[:MAX_TL_PLANS]
    for p in shown:
        live = [t for t in tasks if t["plan_id"] == p["id"] and t["status"] != "cancelled"]
        ids = {str(t["id"]) for t in live}
        by_id = {str(t["id"]): t for t in live}
        items = sorted((_row(t, ids, by_id, t0) for t in live), key=lambda x: (x["start"], x["end"] or "9999"))
        all_items += items
        starts, ends = _bounds(items)
        if p["due_on"]:
            extra.append(p["due_on"])
        s = min(starts) if starts else _local(p["created_at"])
        span_ends: list[date] = [*ends, *([p["due_on"]] if p["due_on"] else [])]
        e = max(span_ends) if span_ends else s
        groups.append(ui.tl_group(str(p["id"]), p["title"], items, subtitle=summary.get(p["id"], {}).get("line", ""), start=s, end=max(s, e),
                                  milestones=[ui.tl_milestone(p["due_on"], "Mốc", "bad" if p["due_on"] < t0 else "accent")] if p["due_on"] else [],
                                  collapsed=True, open=_plan_open(p["id"])))
    starts, ends = _bounds(all_items) if all_items else ([], [])
    starts += [date.fromisoformat(g["start"]) for g in groups if g.get("start")]
    lo, hi, note = _frame(starts, [*ends, *extra], t0)
    cut = f"Đang hiện {len(shown)}/{len(plans)} kế hoạch đầu (trễ / nguy cơ trước)." if len(plans) > len(shown) else ""
    return ui.b_timeline(groups, today=t0, start=lo, end=hi, title="Dòng thời gian các kế hoạch", off=await _off(ctx, lo, hi), note=" ".join(x for x in (cut, note) if x))
