"""NHẬP KẾ HOẠCH TỪ EXCEL (0222 §15/11) — ``flow.import_plan``: cả file kế hoạch là MỘT lần gọi (``ImportTable``), qua đúng màn nhập tự khớp (0226).

Phần hiểu file (WBS → giai đoạn, việc trước, số ngày → ngày, khớp việc đã có) là hàm thuần ở ``plan_logic``; ở đây chỉ nạp dữ liệu (lịch công ty, việc đã có, người giao được),
kiểm quyền, GHI (kế hoạch mới nếu chưa chọn kế hoạch, việc mới, việc cập nhật, phụ thuộc) và trả bản xem trước. Chạy thử = chính lệnh này trong điểm lưu.

* Nhập lại file đã sửa ⇒ CẬP NHẬT việc cũ (khớp theo mã dòng + tên, rồi tên, rồi mã WBS): đổi ngày / người / giai đoạn / việc trước / tên / ghi chú / trạng thái theo file
  (ô trống = giữ nguyên). Việc đã xong / huỷ giữ nguyên. Việc trong kế hoạch mà file không còn nhắc tới KHÔNG bị xoá — chỉ liệt kê.
* Ngày trong file giữ nguyên (kể cả đã qua — kế hoạch đang chạy có việc quá hạn là thật), KHÔNG dời theo ngày nghỉ; ngày thiếu mới tính, theo lịch làm việc công ty.
"""

from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import UUID

from barista.api import Ctx, ImportTable, Input, Invalid, NotFound, business_calendar, field, today, ui, zone
from barista.kernel import imports as kernel_imports
from barista.modules.flow import TASK_STATUS, _plan_open, m, plan_template
from barista.modules.flow.plan_logic import Built, Item, Row, WorkDays, build, match_existing, parse_iso
from barista.modules.flow.schedule_rules import find_cycle
from barista.modules.flow.tasks import _calendar_of, _candidates, _lock_plan
from barista.modules.flow.timeline import _frame, _off

MAX_IMPORT_TASKS = 500
# "Tải mẫu Excel" ở màn nhập: kế hoạch mẫu trọn vẹn (giai đoạn, WBS, việc trước, số ngày, mốc) thay mẫu tự sinh một dòng — người và trợ lý AI đọc là hiểu cách điền
kernel_imports.TEMPLATES["flow.import_plan"] = plan_template.build


class PlanImportRow(Input):
    line: int = field("Dòng", 0, hidden=True)
    code: str = field("STT / WBS", "", max_length=40, help="Mã dòng trong file: 1, 1.1, 1.2.3 — để nhập lại file đã sửa khớp đúng việc, và để hiểu cấp bậc",
                      aliases=("STT", "WBS", "TT", "Số TT", "No", "No.", "#", "Mã WBS", "Mã CV", "Mã công việc", "Item", "Số thứ tự"))
    row_id: str = field("ID", "", max_length=40, help="Số ID dòng của MS Project (cột Việc trước tham chiếu số này)", aliases=("ID", "Unique ID", "UID", "Task ID"))
    level: str = field("Cấp", "", max_length=10, help="Cấp bậc của dòng (MS Project: Outline Level) — dòng có dòng con là giai đoạn",
                       aliases=("Outline Level", "Cấp", "Level", "Cấp độ", "Cấp bậc"))
    title: str = field("Công việc", max_length=300, example="Chốt key visual")
    start_on: str = field("Bắt đầu", "", kind="date", help="Trống = tính từ việc trước + số ngày, hoặc để mặc định (ngày nhập)")
    due_on: str = field("Kết thúc / Hạn", "", kind="date", placeholder="Tính từ ngày bắt đầu + số ngày")
    duration: str = field("Số ngày", "", max_length=30, help="Số ngày làm việc: 5, 5 ngày, 2 tuần, 12h; 0 = mốc")
    assignee_email: str = field("Người phụ trách", "", options_query="flow.assignee_options",
                                help="Tên, email hoặc chữ viết tắt — máy khớp vào người trong công ty; để trống = chưa giao")
    after: str = field("Việc trước", "", max_length=300, help="STT / WBS / ID của việc phải xong trước: 3, 1.2, 3FS+2d, 5SS-1 — cách nhau dấu ; hoặc ,")
    percent: str = field("% hoàn thành", "", max_length=20, help="0–100 hoặc 0–1; 100 = đã xong")
    phase: str = field("Giai đoạn", "", max_length=60, help="Nếu file có cột giai đoạn riêng; không thì lấy từ dòng nhóm (WBS) cha")
    notes: str = field("Ghi chú", "", kind="textarea", max_length=4000)


class ImportPlan(Input):
    plan_id: UUID | None = field("Kế hoạch", None, hidden=True, placeholder="Tạo kế hoạch mới từ file")
    plan_title: str = field("Tên kế hoạch", "", hidden=True, max_length=200)
    tasks: list[PlanImportRow] = field("Các việc")


def _done_at(due: Any) -> datetime | None:
    """Giờ xong của việc file ghi ĐÃ XONG: cuối ngày hạn nếu hạn đã qua (việc xong từ trước, không phải lúc nhập — tính tốc độ / dự báo của kế hoạch theo
    ngày làm thật); hạn hôm nay / tương lai / không có ⇒ None (= lúc nhập)."""
    d = due if isinstance(due, date) else parse_iso(due) if isinstance(due, str) else None
    return datetime.combine(d, time(17, 0), tzinfo=zone()) if d and d < today() else None


def _fmt(d: date | None) -> str:
    return f"{d:%d/%m/%Y}" if d else "—"


async def _off_days(ctx: Ctx, cal: str, rows: list[Row]) -> WorkDays:
    ds = [d for r in rows for d in (parse_iso(r.start), parse_iso(r.due)) if d]
    lo = min([*ds, today()]) - timedelta(days=14)
    hi = max([*ds, today()]) + timedelta(days=900)
    try:
        off = {d for d, _ in business_calendar.non_working(cal, lo, hi)}
    except ValueError:   # lịch không còn trong bộ lịch: cuối tuần
        off = set()
        d = lo
        while d <= hi:
            if d.weekday() >= 5:
                off.add(d)
            d += timedelta(days=1)
    return WorkDays(lambda d: d in off or ((d < lo or d > hi) and d.weekday() >= 5))


def _row_of(r: PlanImportRow) -> Row:
    return Row(r.line, r.title, r.code, r.row_id, r.level, r.start_on, r.due_on, r.duration, r.assignee_email.strip().lower(), r.after, r.percent, r.phase, r.notes)


@m.command("import_plan", resource="flow", title="Nhập kế hoạch từ Excel", importable=True, agent="deny",
           import_table=ImportTable("tasks", preset=("plan_id", "plan_title")))
async def import_plan(ctx: Ctx, inp: ImportPlan) -> dict[str, Any]:
    """Nhập một bảng việc (WBS, hạng mục, bắt đầu / kết thúc, số ngày, người phụ trách, việc trước, % hoàn thành) thành kế hoạch — hoặc cập nhật kế hoạch đã chọn từ
    file đã sửa. Không bao giờ xoá việc. Người nhận phải thuộc phạm vi giao việc của người nhập."""
    if not 1 <= len(inp.tasks) <= MAX_IMPORT_TASKS:
        raise Invalid(f"File cần 1–{MAX_IMPORT_TASKS} dòng việc")
    plan: dict[str, Any] | None = None
    plan_id = inp.plan_id
    if plan_id is None and inp.plan_title.strip():   # bấm đúp / nhập lại ngay: kế hoạch cùng tên chính người này vừa tạo ⇒ cập nhật nó, không sinh bản thứ hai
        plan_id = await ctx.val("""SELECT id FROM flow.plans WHERE created_by = %s AND status = 'open' AND lower(title) = lower(%s)
                                   AND created_at > now() - interval '10 minutes' ORDER BY created_at DESC LIMIT 1""", (ctx.p.user_id, inp.plan_title.strip()[:200]))
    if plan_id:
        await _lock_plan(ctx, plan_id)
        plan = await ctx.one("SELECT id, title, due_on, team_id, owner_id, status FROM flow.plans WHERE id = %s", (plan_id,))
        if plan is None:
            raise NotFound("Không thấy kế hoạch")
        if plan["status"] != "open":
            raise Invalid("Kế hoạch đã đóng / huỷ — mở lại trước khi nhập")
    cal = await _calendar_of(ctx, None)
    rows = [_row_of(r) for r in inp.tasks]
    built: Built = build(rows, await _off_days(ctx, cal, rows))
    if built.blocking:
        raise Invalid(built.blocking)

    allowed = {r["email"].lower(): r for r in await _candidates(ctx, "")}
    existing = await ctx.all(
        """SELECT t.id, t.title, t.plan_ref, t.status, t.start_on, t.due_on, t.assignee_id, t.team_id, t.phase, t.depends_on, t.notes, u.email
           FROM flow.tasks t LEFT JOIN core.users u ON u.id = t.assignee_id WHERE t.plan_id = %s AND t.status <> 'cancelled'""", (plan["id"],)) if plan else []
    out_rows: list[dict[str, Any]] = []   # dòng KHÔNG nhập được (lỗi từng dòng) — dòng nhập được báo ở ``_report``
    for t in built.tasks:
        if t.error:
            out_rows.append({"line": t.line, "action": "create", "status": "invalid", "error": f"“{t.title[:40]}”: {t.error}"})
        elif t.row.assignee and t.row.assignee not in allowed:
            out_rows.append({"line": t.line, "action": "create", "status": "invalid",
                             "error": f"“{t.title[:40]}”: không giao được cho {t.row.assignee} — ngoài phạm vi giao việc của bạn hoặc không còn hoạt động"})
            t.error = "người nhận ngoài phạm vi"
    ok_tasks = [t for t in built.tasks if not t.error]
    matched, missing = match_existing(ok_tasks, existing)

    # ---- quyết từng việc: mới / cập nhật / giữ nguyên ----
    ids: dict[int, UUID | None] = {id(t): (matched[id(t)]["id"] if id(t) in matched else None) for t in ok_tasks}
    by_item = {id(t): t for t in ok_tasks}
    plan_ops: list[dict[str, Any]] = []   # việc sẽ ghi: {item, existing|None, action, set: {cột: giá trị}, diff, deps}
    for t in ok_tasks:
        op = _decide(t, matched.get(id(t)), allowed, ids, by_item)
        if t.error:   # dòng khớp việc đã có nhưng ngày mới làm hỏng ràng buộc (bắt đầu sau hạn)
            out_rows.append({"line": t.line, "action": "update", "status": "invalid", "error": f"“{t.title[:40]}”: {t.error}"})
            continue
        plan_ops.append(op)

    # ---- chu trình trên CẢ đồ thị kế hoạch (kể cả việc ngoài file / việc người nhập không thấy) ----
    graph: dict[Any, list[Any]] = {}
    if plan:
        for g in await ctx.call_system("flow.plan_task_graph", plan_id=plan["id"]):
            graph[g["id"]] = list(g["depends_on"])
    label: dict[Any, str] = {e["id"]: f"“{e['title'][:40]}”" for e in existing}
    for op in plan_ops:
        t: Item = op["item"]
        key = op["existing"]["id"] if op["existing"] else ("new", id(t))
        label[key] = t.label()
        if op["deps"] is not None:
            graph[key] = op["deps"]
        elif key not in graph:
            graph[key] = []
    if cyc := find_cycle(graph):
        raise Invalid("Các việc chờ nhau thành vòng: " + " → ".join(label.get(x, "việc khác") for x in cyc) + ". Sửa cột “Việc trước” rồi chọn lại file.")

    # ---- ghi ----
    people = {r["email"].lower(): r for r in allowed.values()}
    due_all = [t.due for t in ok_tasks if t.due]
    moved_milestone: tuple[date | None, date] | None = None
    if plan is None:
        homes = [await ctx.home_team(people[t.row.assignee]["id"]) for t in ok_tasks if t.row.assignee]
        top = max(set(homes), key=homes.count) if homes else None
        team = top or await ctx.home_team(ctx.p.user_id)
        # người phụ trách = NGƯỜI NHẬP file (họ cầm kế hoạch này — vd quản lý dự án nhập kế hoạch nhiều phòng), không phải trưởng nhóm của phòng
        # có nhiều việc nhất (27/11: nhập kế hoạch triển khai, cột "Phụ trách" ra một trưởng nhóm không liên quan). Đổi người phụ trách sau: sửa kế hoạch.
        owner = ctx.p.user_id
        title = inp.plan_title.strip() or "Kế hoạch nhập từ Excel"
        pid = await ctx.val("INSERT INTO flow.plans (title, goal, owner_id, team_id, due_on, created_by) VALUES (%s, '', %s, %s, %s, %s) RETURNING id",
                            (title[:200], owner, team, max(due_all) if due_all else None, ctx.p.user_id))
        plan = {"id": pid, "title": title, "due_on": max(due_all) if due_all else None, "team_id": team, "owner_id": owner}
        is_new = True
    else:
        pid, is_new = plan["id"], False
        if due_all and (plan["due_on"] is None or max(due_all) > plan["due_on"]):
            if plan["due_on"] is not None:
                moved_milestone = (plan["due_on"], max(due_all))
            await ctx.exec("UPDATE flow.plans SET due_on = %s, updated_at = now() WHERE id = %s", (max(due_all), pid))
    origin = "assistant" if ctx.p.via == "agent" else "manual"
    new_ids: dict[int, UUID] = {}
    homes_cache: dict[UUID, UUID | None] = {}
    for op in plan_ops:
        if op["action"] != "create":
            continue
        t = op["item"]
        c = op["set"]
        who = people[t.row.assignee]["id"] if t.row.assignee else None
        team_id = None
        if who is not None:
            if who not in homes_cache:
                homes_cache[who] = await ctx.home_team(who)
            team_id = homes_cache[who]
        if who is None:
            team_id = plan["team_id"] or await ctx.home_team(ctx.p.user_id)
            if team_id is None:
                who = ctx.p.user_id   # chưa giao + kế hoạch không thuộc đội ⇒ người nhập giữ (ràng buộc: việc cần người hoặc đội)
        tid = await ctx.val(
            """INSERT INTO flow.tasks (title, notes, assignee_id, team_id, due_on, start_on, plan_id, origin, created_by, phase, plan_ref, status, done_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CASE WHEN %s = 'done' THEN coalesce(%s, now()) END) RETURNING id""",
            (c["title"], c.get("notes", ""), who, team_id, c.get("due_on"), c.get("start_on"), pid, origin, ctx.p.user_id, c.get("phase", ""),
             c.get("plan_ref"), c.get("status", "todo"), c.get("status", "todo"), _done_at(c.get("due_on"))))
        new_ids[id(t)] = tid
        op["id"] = tid
    for op in plan_ops:
        if op["action"] != "update":
            continue
        sets, args = [], []
        for col, val in op["set"].items():
            if col == "assignee_email":
                who = people[val]["id"]
                sets += ["assignee_id = %s", "team_id = %s"]
                if who not in homes_cache:
                    homes_cache[who] = await ctx.home_team(who)
                args += [who, homes_cache[who]]
            elif col == "status":
                sets += ["status = %s", "done_at = CASE WHEN %s = 'done' THEN coalesce(done_at, %s, now()) ELSE NULL END"]
                args += [val, val, _done_at(op["set"].get("due_on") or (op.get("existing") or {}).get("due_on"))]
            else:
                sets.append(f"{col} = %s")
                args.append(val)
        if sets:
            await ctx.exec(f"UPDATE flow.tasks SET {', '.join(sets)}, updated_at = now() WHERE id = %s", (*args, op["existing"]["id"]))
        op["id"] = op["existing"]["id"]
    real: dict[int, UUID] = {**new_ids, **{id(op["item"]): op["existing"]["id"] for op in plan_ops if op["existing"]}}
    for op in plan_ops:   # phụ thuộc: sau khi mọi việc đã có mã (cùng giao dịch; vòng đã chặn ở trên)
        if op["deps"] is None:
            continue
        mine = [real[x[1]] if isinstance(x, tuple) else x for x in op["deps"]]
        await ctx.exec("UPDATE flow.tasks SET depends_on = %s, updated_at = now() WHERE id = %s", (mine, real[id(op["item"])]))

    # ---- thông báo gộp mỗi người một lần ----
    per_new: dict[UUID, int] = {}
    per_changed: dict[UUID, int] = {}
    for op in plan_ops:
        t = op["item"]
        email = t.row.assignee or (op["existing"] or {}).get("email") or ""
        if not email or email not in people or people[email]["id"] == ctx.p.user_id:
            continue
        uid = people[email]["id"]
        if op["action"] == "create":
            per_new[uid] = per_new.get(uid, 0) + 1
        elif op["action"] == "update" and op["moves"]:
            per_changed[uid] = per_changed.get(uid, 0) + 1
    for uid, n in per_new.items():
        await ctx.notify([uid], kind="task", title=f"Kế hoạch “{plan['title']}”: {n} việc giao cho anh/chị", body=f"{ctx.p.name} nhập từ file kế hoạch",
                         push=f"Anh/chị có {n} việc mới", open=_plan_open(pid))
    for uid, n in per_changed.items():
        await ctx.notify([uid], kind="task", title=f"Kế hoạch “{plan['title']}”: {n} việc của anh/chị đổi lịch", body=f"{ctx.p.name} cập nhật từ file kế hoạch",
                         open=_plan_open(pid))
    if is_new:
        ctx.emit("flow.plan_created", plan_id=pid, tasks=len(plan_ops))

    return await _report(ctx, built, plan_ops, out_rows, missing, plan, is_new, cal, moved_milestone, {e: p['name'] for e, p in people.items()})


def _decide(t: Item, e: dict[str, Any] | None, allowed: dict[str, dict[str, Any]], ids: dict[int, UUID | None], by_item: dict[int, Item]) -> dict[str, Any]:
    """Một việc trong file: giá trị sẽ ghi + khác biệt so với việc đã có. ``deps`` = None (không đổi) hoặc danh sách mã (mã thật hoặc ("new", id(Item)))."""
    code = t.row.code.strip().rstrip(".")[:60] or None
    deps: list[Any] | None = None
    if t.deps:
        want = []
        for d in t.deps:
            if d.target.error or id(d.target) not in by_item:
                t.warnings.append(f"Việc trước {d.target.label()} không nhập được — bỏ mũi tên này")
                continue
            want.append(ids[id(d.target)] if ids.get(id(d.target)) else ("new", id(d.target)))
        deps = want
    if e is None:
        c: dict[str, Any] = {"title": t.title[:300], "notes": t.row.notes.strip(), "start_on": t.start, "due_on": t.due, "phase": t.phase, "plan_ref": code,
                             "status": t.status or "todo"}
        return {"item": t, "existing": None, "action": "create", "set": c, "diff": [], "deps": deps or None, "moves": False}
    if e["status"] in ("done", "cancelled"):
        return {"item": t, "existing": e, "action": "skip", "set": {}, "diff": [], "deps": None, "moves": False, "why": "việc đã xong — giữ nguyên"}
    diff: list[dict[str, Any]] = []
    setv: dict[str, Any] = {}
    moves = False

    def chg(col: str, label: str, old: Any, new: Any, show: Any = None, move: bool = False) -> None:
        nonlocal moves
        diff.append({"field": label, "old": show[0] if show else old, "new": show[1] if show else new, "applied": True})
        setv[col] = new
        moves = moves or move

    if t.title != e["title"]:
        chg("title", "Tên việc", e["title"], t.title[:300])
    if t.start and t.start != e["start_on"]:
        chg("start_on", "Bắt đầu", e["start_on"], t.start, (_fmt(e["start_on"]) if e["start_on"] else None, _fmt(t.start)), True)
    if t.due and t.due != e["due_on"]:
        chg("due_on", "Hạn", e["due_on"], t.due, (_fmt(e["due_on"]) if e["due_on"] else None, _fmt(t.due)), True)
    if t.row.assignee and t.row.assignee != (e.get("email") or "").lower():
        chg("assignee_email", "Người làm", e.get("email"), t.row.assignee, (e.get("email"), allowed[t.row.assignee]["name"]), True)
    if t.phase and t.phase != e["phase"]:
        chg("phase", "Giai đoạn", e["phase"], t.phase, (e["phase"] or None, t.phase))
    if t.row.notes.strip() and t.row.notes.strip() != e["notes"]:
        chg("notes", "Ghi chú", e["notes"], t.row.notes.strip())
    if t.status and t.status != e["status"] and (t.status == "done" or e["status"] != "blocked"):
        chg("status", "Trạng thái", e["status"], t.status, (TASK_STATUS.get(e["status"], e["status"]), TASK_STATUS.get(t.status, t.status)))
    if code and code != e["plan_ref"]:
        setv["plan_ref"] = code
    new_deps_changed = False
    if deps is not None:
        old = set(e["depends_on"])
        new = {x for x in deps if not isinstance(x, tuple)}
        new_deps_changed = old != new or any(isinstance(x, tuple) for x in deps)
        if new_deps_changed:
            diff.append({"field": "Việc trước", "old": f"{len(old)} việc" if old else None, "new": f"{len(deps)} việc", "applied": True})
    final_start, final_due = setv.get("start_on", e["start_on"]), setv.get("due_on", e["due_on"])
    if final_start and final_due and final_start > final_due:
        t.error = f"bắt đầu {_fmt(final_start)} sau hạn {_fmt(final_due)}"
    if not diff and not setv and not new_deps_changed:
        return {"item": t, "existing": e, "action": "skip", "set": {}, "diff": [], "deps": None, "moves": False, "why": "không đổi"}
    return {"item": t, "existing": e, "action": "update", "set": setv, "diff": diff, "deps": deps if new_deps_changed else None, "moves": moves}


async def _preview_gantt(ctx: Ctx, built: Built, by_item: dict[int, dict[str, Any]], plan: dict[str, Any], names: dict[str, str],
                         missing: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Gantt của kế hoạch SAU khi nhập (bản xem trước): đúng khối ``timeline`` của thẻ kế hoạch — nhóm theo giai đoạn, mũi tên việc trước, mốc kế hoạch, ngày nghỉ.
    Thanh tô theo việc sẽ làm gì: thêm mới / cập nhật (nhãn trạng thái nói rõ), lỗi đỏ, giữ nguyên xám."""
    t0 = today()
    groups: dict[str, list[dict[str, Any]]] = {}
    for t in built.tasks:
        op = by_item.get(id(t))
        action = "invalid" if t.error or op is None else op["action"]
        e = (op or {}).get("existing") or {}
        t_start, t_due = t.start or e.get("start_on"), t.due or e.get("due_on")   # kế hoạch SAU khi nhập: ô file bỏ trống thì giữ ngày đang có
        start = t_start or t_due or t0
        tone = {"create": "accent", "update": "ok", "skip": "muted", "invalid": "bad"}[action]
        what = {"create": "Thêm mới", "update": "Cập nhật: " + ", ".join(d["field"] for d in (op or {}).get("diff", [])[:3]), "skip": "Giữ nguyên", "invalid": "Lỗi"}[action]
        how = " · ngày tính từ số ngày / việc trước" if t.computed else ""
        when = (f"{start:%d/%m} – {t_due:%d/%m}" if t_due and t_due != start else f"{t_due:%d/%m}") if t_due else (f"từ {start:%d/%m} · chưa có hạn" if t_start else "chưa có ngày")
        item = ui.tl_item(f"l{t.line}", (t.row.code.strip() + " " if t.row.code.strip() else "") + t.title, start, t_due, tone=tone, status=what,
                          who=names.get(t.row.assignee, "") if t.row.assignee else "", dates=when + how, flag=(t.error or (t.warnings[0] if t.warnings else "")),
                          after=[f"l{d.target.line}" for d in t.deps if not d.target.error])
        if (t.due and t.start is None and t.duration == 0) or (t.duration == 0 and t.due):
            item["kind"] = "milestone"
            item["start"] = t.due.isoformat() if t.due else item["start"]
        groups.setdefault(t.phase or "Chưa xếp giai đoạn", []).append(item)
    for n, e in enumerate(missing[:60]):   # việc còn trong kế hoạch mà file không nhắc tới: vẫn nằm đó (xám), không bị xoá
        s0 = e["start_on"] or e["due_on"] or t0
        item = ui.tl_item(f"x{n}", e["title"], s0, e["due_on"], tone="muted", status="Không còn trong file — giữ nguyên",
                          who=names.get((e.get("email") or "").lower(), ""), dates=f"hạn {e['due_on']:%d/%m}" if e["due_on"] else "chưa có hạn")
        groups.setdefault(e["phase"] or "Chưa xếp giai đoạn", []).append(item)
    if not groups:
        return None
    dated = [date.fromisoformat(i["start"]) for g in groups.values() for i in g]
    ends = [date.fromisoformat(i["end"]) for g in groups.values() for i in g if i["end"]]
    mile = [ui.tl_milestone(plan["due_on"], "Mốc kế hoạch")] if plan.get("due_on") else []
    lo, hi, note = _frame(dated, [*ends, *([plan["due_on"]] if plan.get("due_on") else [])], t0)
    # giữ THỨ TỰ CỦA FILE (cột tên có mã WBS: 2.1 trước 2.2), không xếp lại theo ngày — xếp theo ngày làm 2.2 nhảy lên trước 2.1, trông như nhập lộn
    gs = [ui.tl_group(f"g{i}", name, items, subtitle="giai đoạn" if not name.startswith("Chưa xếp") else "")
          for i, (name, items) in enumerate(groups.items())]
    return ui.b_timeline(gs, today=t0, start=lo, end=hi, title="Kế hoạch sau khi nhập", milestones=mile, off=await _off(ctx, lo, hi), note=note)


async def _report(ctx: Ctx, built: Built, ops: list[dict[str, Any]], bad: list[dict[str, Any]], missing: list[dict[str, Any]], plan: dict[str, Any], is_new: bool, cal: str,
                  moved: tuple[date | None, date] | None, names: dict[str, str]) -> dict[str, Any]:
    rows = list(bad)
    by_item = {id(op["item"]): op for op in ops}
    for op in ops:
        t = op["item"]
        r: dict[str, Any] = {"line": t.line, "action": op["action"], "status": "ok", "error": op.get("why") if op["action"] == "skip" else None,
                             "diff": op["diff"], "note": "; ".join(t.warnings[:2]), "who": t.title}
        rows.append(r)
    n_new = sum(1 for op in ops if op["action"] == "create")
    n_dates = sum(1 for op in ops if op["action"] == "update" and any(d["field"] in ("Bắt đầu", "Hạn") for d in op["diff"]))
    n_other = sum(1 for op in ops if op["action"] == "update") - n_dates
    n_same = sum(1 for op in ops if op["action"] == "skip")
    bits = []
    if n_new:
        bits.append(f"{n_new} việc mới")
    if n_dates:
        bits.append(f"{n_dates} việc đổi ngày")
    if n_other:
        bits.append(f"{n_other} việc đổi nội dung khác")
    if missing:
        bits.append(f"{len(missing)} việc không còn trong file — giữ nguyên, không xoá")
    notes = [f"Ngày còn thiếu được tính theo lịch làm việc “{cal}” của công ty (nghỉ cuối tuần và ngày lễ); ngày có sẵn trong file giữ nguyên."]
    computed = sum(1 for t in built.tasks if t.computed)
    if computed:
        notes.append(f"{computed} việc có ngày do máy tính từ số ngày / việc trước — bấm vào dòng để xem.")
    if moved:
        notes.append(f"Mốc kế hoạch dời từ {_fmt(moved[0])} sang {_fmt(moved[1])} vì file có việc hạn muộn hơn.")
    notes += built.notes
    phases: dict[str, dict[str, Any]] = {}
    tasks_view = []
    line_of = {id(t): t.line for t in built.tasks}
    for t in built.tasks:
        op = by_item.get(id(t))
        action = "invalid" if t.error or op is None else op["action"]
        tasks_view.append({"line": t.line, "code": t.row.code.strip(), "title": t.title, "phase": t.phase, "start": t.start.isoformat() if t.start else None,
                           "end": t.due.isoformat() if t.due else None, "computed": sorted(t.computed), "who": t.row.assignee, "action": action,
                           "after": [line_of[id(d.target)] for d in t.deps if id(d.target) in line_of], "warnings": t.warnings[:4],
                           "diff": [d["field"] for d in op["diff"]] if op else []})
        if t.phase:
            ph = phases.setdefault(t.phase, {"title": t.phase, "count": 0, "start": None, "end": None})
            ph["count"] += 1
            for k, v in (("start", t.start), ("end", t.due)):
                if v and (ph[k] is None or (v < date.fromisoformat(ph[k]) if k == "start" else v > date.fromisoformat(ph[k]))):
                    ph[k] = v.isoformat()
    starts = [t["start"] or t["end"] for t in tasks_view if t["start"] or t["end"]]
    ends = [t["end"] or t["start"] for t in tasks_view if t["end"] or t["start"]]
    warn_total = sum(len(t.warnings) for t in built.tasks)
    view = {"target": {"plan_id": str(plan["id"]), "title": plan["title"], "new": is_new},
            "summary": " · ".join(bits) if bits else "Không có gì thay đổi",
            "start": min(starts) if starts else None, "end": max(ends) if ends else None, "phases": list(phases.values()), "tasks": tasks_view,
            "missing": [{"title": e["title"], "due": e["due_on"].isoformat() if e["due_on"] else None} for e in missing[:50]],
            "calendar": cal, "warnings": warn_total, "unchanged": n_same, "groups": [g.title for g in built.groups],
            "timeline": await _preview_gantt(ctx, built, by_item, plan, names, missing)}
    if warn_total:
        notes.append(f"{warn_total} lưu ý trên các dòng (việc trước không tìm thấy, ngày không đọc được…) — xem “Chi tiết từng việc” và dấu cảnh báo trên Gantt.")
    return {"plan_id": plan["id"], "tasks": len(ops), "summary": f"{plan['title']}: " + (" · ".join(bits) or "không đổi"),
            "import": {"rows": rows, "notes": notes, "view": view}}
