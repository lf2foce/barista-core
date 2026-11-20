"""KẾ HOẠCH & MỤC TIÊU (0073 / 0074 B) — lập kế hoạch nhiều phòng, tiến độ / rủi ro, mục tiêu đo bằng chỉ số, nhắc, nghiệm thu, gợi ý buổi sáng. Module ``flow`` (0088: package — ``m`` khai ở ``__init__``)."""

import math
from collections import Counter
from datetime import date, timedelta
from typing import Any, Literal
from uuid import UUID

from psycopg.types.json import Jsonb

from barista.api import Ctx, Day, Denied, Input, Invalid, Line, NoInput, NotFound, field, kpi, line, num, today, ui, zone
from barista.kernel import packs, registry
from barista.kernel.teams import led as led_teams
from barista.modules.flow import PLAN_SUBJECT, REQUESTS_OPEN, _plan_open, _task_item, _team_id, _team_leads, m
from barista.modules.flow.requests import _file_request
from barista.modules.flow.schedule_rules import check_schedule, find_cycle, fit_start
from barista.modules.flow.tasks import _calendar_note, _candidates, _check_subject, _done_when, _due, _lock_plan, _ranked
from barista.modules.flow.timeline import plan_timeline, portfolio_timeline

# ── Kế hoạch & giao việc (0073) ──
# Trợ lý LẬP kế hoạch từ một câu (một thẻ đề xuất: kế hoạch + nhiều việc), GIAO theo danh sách ứng viên CODE xếp hạng (không đoán ai rảnh), THEO
# DÕI bằng tín hiệu tiến độ CODE tính. Việc vẫn là flow.tasks (một bảng việc); kế hoạch chỉ gom + đo. Mức tự chủ = core.agent_policies (mặc định
# đề xuất; công ty đặt "làm thẳng" cho flow.propose_plan ⇒ trợ lý tự giao).

PLAN_STATUS = {"open": "Đang chạy", "done": "Xong", "cancelled": "Đã huỷ"}
RISK = {"ok": "Đúng tiến độ", "risk": "Có nguy cơ trễ", "late": "Trễ", "review": "Chờ nghiệm thu", "done": "Đã nghiệm thu", "cancelled": "Đã huỷ"}
RISK_TONE = {"ok": "green", "risk": "orange", "late": "red", "review": "blue", "done": "gray", "cancelled": "gray"}
MAX_PLAN_TASKS = 40
RATE_DAYS = 14                # tốc độ xong việc tính trên 14 ngày gần nhất


class PlanTask(Input):
    title: str = field("Việc", max_length=300, example="Chốt key visual dự án")
    notes: str = field("Ghi chú", "", kind="textarea", max_length=2000)
    assignee_email: str = field("Giao cho", "", options_query="flow.assignee_options", help="Xếp theo khối lượng việc — người ít việc lên đầu")
    due_on: Day | None = field("Hạn", None)
    start_on: Day | None = field("Bắt đầu", None, placeholder="Ngày tạo", help="Dòng thời gian vẽ thanh từ ngày này tới hạn")
    phase: str = field("Giai đoạn", "", max_length=60, help="Nhãn nhóm trên dòng thời gian (vd “Chuẩn bị”, “Chạy”) — trống = nhóm theo người làm")
    after: str = field("Chờ việc", "", max_length=600, help="Tên các việc KHÁC của kế hoạch này phải xong trước, cách nhau bằng dấu chấm phẩy (;)")
    request_team: str = field("Nhờ phòng", "", options_query="admin.team_options",
                              help="Người ngoài phạm vi giao việc của bạn ⇒ nhờ PHÒNG đó (trưởng phòng nhận, tự chia người) — để trống “Giao cho”")
    subject_kind: str = field("Loại đối tượng", "", hidden=True, help="Việc VỀ một khách / hồ sơ / kỳ sổ… — loại (vd crm.lead)")
    subject_id: UUID | None = field("Đối tượng", None, hidden=True)
    done_check: str = field("Xong khi", "", hidden=True, help="Bộ kiểm (flow.checker_options) đọc đối tượng của việc — việc tự đóng khi đạt", agent=False)
    done_args: str = field("Tham số bộ kiểm", "", hidden=True, max_length=200, agent=False)


class ProposePlan(Input):
    title: str = field("Kế hoạch", max_length=200, example="Chuẩn bị ra mắt [[offering|l]] mới")
    goal: str = field("Mục tiêu", "", kind="textarea", max_length=2000)
    due_on: Day | None = field("Mốc kế hoạch", None, help="Vd ngày mở bán — việc không được hạn trễ hơn")
    team_code: str = field("Phòng chủ trì", "", options_query="admin.team_options", placeholder="Đội chung của những người nhận việc", group="Thêm chi tiết")
    owner_email: str = field("Phụ trách", "", options_query="flow.assignee_options", placeholder="Trưởng đội của kế hoạch (không có ⇒ người lập)", group="Thêm chi tiết")
    goal_metric: str = field("Chỉ số mục tiêu", "", options_query="flow.metric_options", placeholder="Kế hoạch chỉ là danh sách việc", group="Mục tiêu đo được")
    goal_target: int | None = field("Đích", None, ge=1, help="Vd 50 (khách) — cần khi chọn chỉ số", group="Mục tiêu đo được")
    goal_context: str = field("Phạm vi", "", max_length=40, placeholder="Cả công ty",
                              help="Lọc chỉ số theo phạm vi (vd một dự án, một gian hàng)", group="Mục tiêu đo được")
    tasks: list[PlanTask] = field("Các việc")


@m.command("propose_plan", resource="flow", title="Lập kế hoạch & giao việc", shows=("flow.plan", "plan_id"))
async def propose_plan(ctx: Ctx, inp: ProposePlan) -> dict[str, Any]:
    """Tạo MỘT kế hoạch + mọi việc của nó trong cùng một lần ghi (một thẻ đề xuất, bấm một lần). Người nhận phải thuộc danh sách giao được của người
    lập; hạn không trễ hơn mốc kế hoạch; hạn rơi ngày nghỉ ⇒ lùi về ngày làm việc liền trước. Mỗi người nhận một thông báo gộp."""
    if not 1 <= len(inp.tasks) <= MAX_PLAN_TASKS:
        raise Invalid(f"Kế hoạch cần 1–{MAX_PLAN_TASKS} việc")
    titles = [t.title.strip() for t in inp.tasks]
    if any(not t for t in titles) or len(set(titles)) != len(titles):
        raise Invalid("Mỗi việc cần tên riêng, không trùng")
    if inp.team_code and await _team_id(ctx, inp.team_code) is None:
        raise NotFound(f"Không có đội {inp.team_code}")
    asks = [t for t in inp.tasks if t.request_team.strip()]      # nhờ phòng khác (0074 C)
    direct = [t for t in inp.tasks if not t.request_team.strip()]
    if any(not t.assignee_email.strip() for t in direct):
        raise Invalid("Mỗi việc cần người nhận — hoặc “Nhờ phòng” khi người làm ngoài phạm vi giao việc của bạn")
    # Phòng chủ trì chỉ quyết kế hoạch THUỘC ai; ai nhận việc theo phạm vi giao việc của người lập (kế hoạch nhiều phòng)
    allowed = {r["email"]: r for r in await _candidates(ctx, "")}
    by_id = {str(r["id"]): r["email"] for r in allowed.values()}   # bảng "Ai nên nhận việc" có cả mã người — trợ lý lấy mã cũng nhận

    def email(x: str) -> str:
        return by_id.get(x.strip(), x).strip().lower()
    emails = [email(t.assignee_email) for t in direct]
    owner_email = email(inp.owner_email)
    outside = sorted((set(emails) | ({owner_email} if owner_email else set())) - set(allowed))
    if outside:
        raise Invalid(f"Không giao được cho {', '.join(outside)} — ngoài phạm vi giao việc của bạn hoặc không còn hoạt động. "
                      f"Chọn trong “Ai nên nhận việc” (flow.assignees)")
    if inp.due_on and inp.due_on < today():
        raise Invalid(f"Mốc kế hoạch {inp.due_on:%d/%m/%Y} đã qua")
    metric = inp.goal_metric.strip()
    if metric and metric not in registry.METRICS:
        raise Invalid(f"Không có chỉ số {metric} — chọn trong “Chỉ số mục tiêu” (flow.metrics)")
    if metric and not _metric_on(ctx, metric):
        raise Denied(f"“{registry.METRICS[metric].label}” là số toàn công ty — cần quyền xem cả công ty để gắn vào mục tiêu")
    if bool(metric) != (inp.goal_target is not None):
        raise Invalid("Mục tiêu cần cả chỉ số lẫn đích (vd chỉ số “Khách đặt lịch hẹn”, đích 50)")
    if metric and not inp.due_on:
        raise Invalid("Mục tiêu cần mốc (đạt bao nhiêu trước ngày nào)")
    checks = []
    for t in direct:   # đối tượng nghiệp vụ + điều kiện xong của từng việc: người lập phải thấy đối tượng (0117)
        await _check_subject(ctx, t.subject_kind, t.subject_id)
        checks.append(await _done_when(ctx, t.done_check, t.done_args, t.subject_kind, t.subject_id))
    if any(t.subject_kind or t.done_check for t in asks):
        raise Invalid("Việc nhờ phòng khác chưa gắn được đối tượng — trưởng phòng được nhờ gắn khi nhận")
    if any(t.start_on or t.phase.strip() or t.after.strip() for t in asks):
        raise Invalid("Việc nhờ phòng khác chưa đặt được ngày bắt đầu / giai đoạn / phụ thuộc — trưởng phòng được nhờ nhận rồi mới xếp lịch")
    # phụ thuộc theo TÊN việc (tên không trùng trong kế hoạch): phải trỏ việc khác cùng kế hoạch, không vòng
    after: dict[str, list[str]] = {}
    for t in direct:
        names = [x.strip() for x in t.after.split(";") if x.strip()]
        after[t.title.strip()] = list(dict.fromkeys(names))
    known = {t.title.strip() for t in direct}
    for name, deps in after.items():
        if missing := [d for d in deps if d not in known]:
            raise Invalid(f"“{name}” chờ việc không có trong kế hoạch: {', '.join(missing)} — tên phải trùng một việc khác của kế hoạch")
        if name in deps:
            raise Invalid(f"“{name}” không chờ chính nó")
    if find_cycle(after):
        raise Invalid("Các việc chờ nhau thành vòng (A chờ B, B lại chờ A) — bỏ bớt một phụ thuộc")
    people = [allowed[e]["id"] for e in emails]
    homes = {who: await ctx.home_team(who) for who in set(people)}
    shifted = 0
    warnings: list[str] = []
    dues: list[date | None] = []
    starts: list[date | None] = []
    for t, who in zip(direct, people, strict=True):
        d, moved, notices = await _due(ctx, t.due_on, homes[who], inp.due_on, f"“{t.title}”: ")
        warnings.extend(w for w in notices if w not in warnings)
        shifted += moved
        dues.append(d)
        starts.append(fit_start(t.start_on, t.due_on, d))
        check_schedule(starts[-1], d, f"“{t.title}”: ")
    # Kế hoạch thuộc ĐỘI làm việc (không phải đội người lập): giám đốc giao cho phòng marketing ⇒ trưởng phòng thấy và chịu trách nhiệm theo dõi
    # Phòng chủ trì trống ⇒ phòng nhận NHIỀU việc nhất (hoà ⇒ đội người lập)
    counts = Counter(homes[w] for w in people if homes[w])
    top = counts.most_common(2)
    most = top[0][0] if top and (len(top) == 1 or top[0][1] > top[1][1]) else None
    team = await _team_id(ctx, inp.team_code) or most or await ctx.home_team(ctx.p.user_id)
    ids = {r["id"] for r in allowed.values()}
    lead = await ctx.val("""SELECT tm.user_id FROM core.team_members tm JOIN core.memberships mb ON mb.tenant_id = tm.tenant_id AND mb.user_id = tm.user_id
                            WHERE tm.team_id = %s AND tm.is_lead AND mb.active ORDER BY tm.user_id LIMIT 1""", (team,)) if team else None
    owner = allowed[owner_email]["id"] if owner_email else (lead if lead in ids else ctx.p.user_id)
    pid = await ctx.val("""INSERT INTO flow.plans (title, goal, owner_id, team_id, due_on, created_by, goal_metric, goal_target, goal_context)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                        (inp.title.strip(), inp.goal.strip(), owner, team, inp.due_on, ctx.p.user_id, metric or None, inp.goal_target,
                         inp.goal_context.strip().upper()))
    origin = "assistant" if ctx.p.via == "agent" else "manual"
    per_person: dict[UUID, int] = {}
    checked: list[UUID] = []
    made: dict[str, UUID] = {}
    for t, d, s, who, (check, args) in zip(direct, dues, starts, people, checks, strict=True):
        tid = await ctx.val("""INSERT INTO flow.tasks (title, notes, assignee_id, team_id, due_on, plan_id, origin, created_by, subject_kind, subject_id,
                                                       done_check, done_args, start_on, phase)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                            (t.title.strip(), t.notes, who, homes[who], d, pid, origin, ctx.p.user_id, t.subject_kind or None, t.subject_id,
                             check, Jsonb(args), s, t.phase.strip()))
        made[t.title.strip()] = tid
        if check:
            checked.append(tid)
        per_person[who] = per_person.get(who, 0) + 1
    for name, deps in after.items():   # phụ thuộc: sau khi mọi việc đã có mã (cùng giao dịch; chu trình đã chặn ở trên)
        if deps:
            await ctx.exec("UPDATE flow.tasks SET depends_on = %s WHERE id = %s", ([made[d] for d in deps], made[name]))
    for tid in checked:   # dữ kiện đạt sẵn ⇒ đóng bằng bằng chứng ngay (cùng giao dịch)
        await ctx.call_system("flow.check_task", task_id=tid)
    for t in asks:
        await _file_request(ctx, title=t.title.strip(), notes=t.notes, team_code=t.request_team, due_on=t.due_on, plan_id=pid, milestone=inp.due_on)
    see = {"query": "flow.plan", "input": {"plan_id": str(pid)}}
    for who, n in per_person.items():
        await ctx.notify([who], kind="task", title=f"Kế hoạch “{inp.title.strip()}”: {n} việc giao cho anh/chị"
                         + (" — anh/chị phụ trách kế hoạch" if who == owner and owner != ctx.p.user_id else ""),
                         body=f"{ctx.p.name} giao", push=f"Anh/chị có {n} việc mới", open=see)
    if owner != ctx.p.user_id and owner not in per_person:
        await ctx.notify([owner], kind="task", title=f"Anh/chị phụ trách kế hoạch “{inp.title.strip()}”", body=f"{ctx.p.name} giao · {len(inp.tasks)} việc",
                         push="Anh/chị phụ trách một kế hoạch mới", open=see)
    ctx.emit("flow.plan_created", plan_id=pid, tasks=len(inp.tasks))
    return {"plan_id": pid, "tasks": len(inp.tasks), "people": len(per_person), "calendar_warnings": warnings,
            "summary": f"{inp.title.strip()}: {len(inp.tasks)} việc · {len(per_person)} người"
                       + (f" · {len(set(homes.values()))} phòng" if len(set(homes.values())) > 1 else "")
                       + (f" · nhờ phòng khác {len(asks)} việc" if asks else "")
                       + (f", mốc {inp.due_on:%d/%m/%Y}" if inp.due_on else "") + (f"; {shifted} hạn dời khỏi ngày nghỉ" if shifted else "")
                       + _calendar_note(warnings)}


class PlanRemind(Input):
    plan_id: UUID = field("Kế hoạch", hidden=True)
    note: str = field("Lời nhắn", "", kind="textarea", max_length=500)


@m.command("remind_plan", resource="flow", title="Nhắc người đang chậm",
           description="Gửi mỗi người một thông báo về các việc quá hạn hoặc tới hạn trong 3 ngày. Người phụ trách kế hoạch nhắc được cả việc của "
                       "phòng khác; người khác chỉ nhắc được việc mình xem được.")
async def remind_plan(ctx: Ctx, inp: PlanRemind) -> dict[str, Any]:
    """Một thông báo / người cho các việc QUÁ HẠN hoặc tới hạn trong 3 ngày của kế hoạch. Người phụ trách / quản lý kế hoạch nhắc được MỌI việc
    của kế hoạch (cả việc phòng khác họ không xem được — nhắc chỉ là thông báo cho chính người nhận); người khác chỉ nhắc việc mình thấy (RLS)."""
    plan = await ctx.one("SELECT title, core.can_see('flow', owner_id, team_id) AS runs FROM flow.plans WHERE id = %s", (inp.plan_id,))
    if plan is None:
        raise NotFound("Không thấy kế hoạch")
    if plan["runs"]:   # cả việc phòng khác — tên việc / người nhận ở lại trong hợp đồng, bên gọi chỉ nhận số đếm
        n = await ctx.call_system("flow.remind_plan_tasks", plan_id=inp.plan_id, sender=ctx.p.name, note=inp.note)
    else:
        n = await _remind(ctx, inp.plan_id, plan["title"], ctx.p.name, inp.note,
                          await ctx.all("SELECT assignee_id, title, status, due_on FROM flow.tasks WHERE plan_id = %s", (inp.plan_id,)))
    return {**n, "summary": f"Nhắc {n['people']} người đang chậm ({n['tasks']} việc) — kế hoạch “{plan['title']}”"}


async def _remind(ctx: Ctx, plan_id: UUID, plan_title: str, sender: str, note: str, tasks: list[dict[str, Any]]) -> dict[str, int]:
    """Một thông báo / người cho việc quá hạn hoặc tới hạn trong 3 ngày — người nhận đọc tên việc CỦA CHÍNH MÌNH."""
    soon = today() + timedelta(days=3)
    rows = sorted((t for t in tasks if t["assignee_id"] and t["status"] in ("todo", "doing", "blocked") and t["due_on"] and t["due_on"] <= soon),
                  key=lambda t: t["due_on"])
    if not rows:
        raise Invalid("Không có việc nào quá hạn hay tới hạn trong 3 ngày để nhắc")
    by: dict[UUID, list[dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(r["assignee_id"], []).append(r)
    t0 = today()
    for who, ts in by.items():
        lines = "; ".join(f"{t['title']} ({'quá hạn' if t['due_on'] < t0 else 'hạn'} {t['due_on']:%d/%m})" for t in ts)
        await ctx.notify([who], kind="task", title=f"Nhắc: {len(ts)} việc của kế hoạch “{plan_title}” cần xong",
                         body=sender + (f": {note.strip()}" if note.strip() else "") + f" — {lines}",
                         push=f"Nhắc việc: {len(ts)} việc sắp / đã quá hạn", open=_plan_open(plan_id))
    return {"people": len(by), "tasks": len(rows)}


class PlanRemindAll(Input):
    plan_id: UUID = field("Kế hoạch", hidden=True)
    sender: str = field("Người nhắc", "", max_length=200)
    note: str = field("Lời nhắn", "", max_length=500)


@m.command("remind_plan_tasks", resource=None, title="Nhắc mọi việc chậm của kế hoạch", internal=True, agent="deny", callers=("flow",),
           why="remind_plan, sau khi kiểm người gọi phụ trách / quản lý kế hoạch (core.can_see): nhắc TỪNG người làm việc chậm, kể cả phòng khác "
               "— mỗi người chỉ nhận tên việc của mình. Trả số người / số việc; tên việc và người nhận không về bên gọi.")
async def remind_plan_tasks(ctx: Ctx, inp: PlanRemindAll) -> dict[str, int]:
    title = await ctx.val("SELECT title FROM flow.plans WHERE id = %s", (inp.plan_id,))
    return await _remind(ctx, inp.plan_id, title or "", inp.sender, inp.note,
                         await ctx.all("SELECT assignee_id, title, status, due_on FROM flow.tasks WHERE plan_id = %s", (inp.plan_id,)))


class PlanNotifyIn(Input):
    plan_id: UUID = field("Kế hoạch", hidden=True)
    title: str = field("Tiêu đề", max_length=300)
    body: str = field("Nội dung", "", max_length=1000)
    skip: UUID | None = field("Trừ người", None, hidden=True)


@m.command("notify_plan", resource=None, title="Báo người làm của kế hoạch", internal=True, agent="deny", callers=("flow",),
           why="set_plan, sau khi kiểm người gọi có quyền GHI kế hoạch (khoá dòng qua RLS): báo mọi người đang có việc trong kế hoạch (cả phòng "
               "khác) rằng kế hoạch đã đóng / mở lại. Trả số người được báo — danh sách người không về bên gọi.")
async def notify_plan(ctx: Ctx, inp: PlanNotifyIn) -> int:
    people = [r["assignee_id"] for r in await ctx.all(
        """SELECT DISTINCT assignee_id FROM flow.tasks WHERE plan_id = %s AND assignee_id IS NOT NULL AND assignee_id IS DISTINCT FROM %s
           ORDER BY assignee_id""", (inp.plan_id, inp.skip))]
    if people:
        await ctx.notify(people, kind="task", title=inp.title, body=inp.body, open=_plan_open(inp.plan_id))
    return len(people)


class SetPlan(Input):
    plan_id: UUID = field("Kế hoạch", hidden=True)
    status: Literal["done", "cancelled", "open"] = field("Đổi thành", "done", options={"done": "Nghiệm thu — kế hoạch xong", "cancelled": "Huỷ kế hoạch",
                                                                                  "open": "Mở lại"})
    note: str = field("Ghi chú", "", kind="textarea", max_length=500)


@m.command("set_plan", resource="flow", title="Nghiệm thu / huỷ / mở lại kế hoạch")
async def set_plan(ctx: Ctx, inp: SetPlan) -> dict[str, Any]:
    """Kế hoạch CHỈ đóng khi người phụ trách (hoặc quản lý có quyền ghi) quyết — hết việc là "Chờ nghiệm thu", không tự đóng. Nghiệm thu khi
    mọi việc đã xong / huỷ (tính trên CẢ kế hoạch); huỷ ⇒ huỷ luôn các việc chưa xong và báo người nhận; mở lại giữ nguyên trạng thái từng việc."""
    await _lock_plan(ctx, inp.plan_id)
    p = await ctx.one("SELECT * FROM flow.plans WHERE id = %s FOR UPDATE", (inp.plan_id,))
    if p is None:   # thấy kế hoạch qua việc của mình (0073, mig 168) không đủ để đóng / mở — cần quyền ghi kế hoạch (phụ trách / quản lý)
        raise Denied("Chỉ người phụ trách hoặc quản lý kế hoạch mới nghiệm thu, huỷ hay mở lại")
    if p["status"] == inp.status:
        raise Invalid(f"Kế hoạch đã ở trạng thái {PLAN_STATUS[inp.status]}")
    tasks = await ctx.call_system("flow.plan_tasks", plan_ids=[p["id"]])
    left = [t for t in tasks if t["status"] in ("todo", "doing", "blocked")]
    if inp.status == "done" and left:
        raise Invalid(f"Còn {len(left)} việc chưa xong — xong hoặc huỷ từng việc trước, hay huỷ cả kế hoạch")
    asked = await ctx.call_system("flow.plan_requests", plan_ids=[p["id"]])
    if inp.status == "done" and asked:
        raise Invalid(f"Còn {len(asked)} việc đang nhờ phòng khác, chưa ai nhận — chờ nhận / bị từ chối, hay huỷ cả kế hoạch")
    if inp.status == "done" and (goal := await _goal(ctx, p)) and goal["status"] != "met":
        raise Invalid(f"Chưa đạt mục tiêu: {goal['value']}/{goal['target']} {goal['unit']} — huỷ kế hoạch nếu dừng, không nghiệm thu khi chưa đạt")
    if inp.status == "cancelled" and asked:   # việc nhờ còn treo theo kế hoạch đã huỷ ⇒ đóng, báo phòng được nhờ
        await ctx.call_system("flow.close_plan_requests", plan_id=p["id"], note="Kế hoạch đã huỷ")
    cancelled: list[UUID] = await ctx.call_system("flow.cancel_plan_tasks", plan_id=p["id"]) if inp.status == "cancelled" else []
    await ctx.exec("UPDATE flow.plans SET status = %s, closed_at = CASE WHEN %s = 'open' THEN NULL ELSE now() END, closed_note = %s WHERE id = %s",
                   (inp.status, inp.status, inp.note.strip(), p["id"]))
    word = {"done": "đã nghiệm thu", "cancelled": "đã huỷ", "open": "được mở lại"}[inp.status]
    await ctx.call_system("flow.notify_plan", plan_id=p["id"], skip=ctx.p.user_id, title=f"Kế hoạch “{p['title']}” {word}",
                          body=ctx.p.name + (f": {inp.note.strip()}" if inp.note.strip() else "") + (f" — {len(cancelled)} việc chưa xong đã huỷ" if cancelled else ""))
    ctx.emit("flow.plan_closed" if inp.status != "open" else "flow.plan_reopened", plan_id=p["id"], status=inp.status)
    return {"plan_id": p["id"], "status": inp.status,
            "summary": f"Kế hoạch “{p['title']}” → {PLAN_STATUS[inp.status]}" + (f" (huỷ {len(cancelled)} việc chưa xong)" if cancelled else "")}


class PlanCancelIn(Input):
    plan_id: UUID = field("Kế hoạch", hidden=True)


@m.command("cancel_plan_tasks", resource=None, title="Huỷ việc chưa xong của kế hoạch", internal=True, agent="deny", callers=("flow",),
           why="set_plan huỷ kế hoạch, sau khi kiểm người gọi có quyền GHI kế hoạch (khoá dòng qua RLS): huỷ việc chưa xong của đúng kế hoạch đó, "
               "kể cả việc phòng khác. Trả id việc đã huỷ.")
async def cancel_plan_tasks(ctx: Ctx, inp: PlanCancelIn) -> list[UUID]:
    """Hợp đồng hẹp cho ``set_plan``: người huỷ kế hoạch có thể không thấy việc của phòng khác (RLS) nhưng việc thuộc kế hoạch phải huỷ theo."""
    rows = await ctx.all("""UPDATE flow.tasks SET status = 'cancelled', updated_at = now()
                            WHERE plan_id = %s AND status IN ('todo', 'doing', 'blocked') RETURNING id""", (inp.plan_id,))
    return [r["id"] for r in rows]


GOAL_BEHIND = 0.8   # đo được < 80% đường cần đạt tới hôm nay ⇒ "có nguy cơ"


async def _goal(ctx: Ctx, plan: dict[str, Any]) -> dict[str, Any] | None:
    """Đo mục tiêu (0074 B) bằng chỉ số module công bố — MỘT con số trong bộ lọc, đếm từ ngày lập kế hoạch. So với đường thẳng từ 0 lúc lập tới
    đích lúc mốc: chậm hơn 80% ⇒ có nguy cơ; qua mốc chưa đạt ⇒ trễ; đạt ⇒ đạt. Code tính, trợ lý chỉ đọc."""
    if not plan.get("goal_metric"):
        return None
    m = registry.METRICS.get(plan["goal_metric"])
    if m is None:
        return None
    start = plan["created_at"].astimezone(zone()).date()
    out = await ctx.call_system(m.key, since=start.isoformat(), context_code=plan.get("goal_context") or "")
    value, target, due, t0 = int(out["value"]), int(plan["goal_target"]), plan["due_on"], today()
    span = max(1, (due - start).days) if due else 1
    expected = round(target * min(1.0, max(0, (t0 - start).days) / span))
    where = f" ({plan['goal_context']})" if plan.get("goal_context") else ""
    if value >= target:
        status, why = "met", f"Đã đạt {value}/{target} {m.unit}"
    elif due and t0 > due:
        status, why = "late", f"Qua mốc {due:%d/%m}: mới {value}/{target} {m.unit}"
    elif (t0 - start).days >= 2 and value < expected * GOAL_BEHIND:
        status, why = "risk", f"{m.label}{where}: {value}/{target} — tới hôm nay cần khoảng {expected}, thiếu {expected - value} {m.unit}"
    else:
        status, why = "ok", f"{m.label}{where}: {value}/{target} {m.unit}, đúng đường (cần khoảng {expected} tới hôm nay)"
    return {"value": value, "target": target, "expected": expected, "label": m.label, "unit": m.unit, "status": status, "why": why}


def _with_goal(g: dict[str, Any], goal: dict[str, Any] | None) -> dict[str, Any]:
    """Rủi ro chung = xấu hơn giữa VIỆC và MỤC TIÊU; xong hết việc mà chưa đạt đích thì chưa phải "chờ nghiệm thu"."""
    if goal is None or g["risk"] in ("done", "cancelled"):
        return g
    order = {"ok": 0, "review": 0, "met": 0, "risk": 1, "late": 2}
    if goal["status"] in ("risk", "late") and order[goal["status"]] >= order.get(g["risk"], 0):
        why = goal["why"] + (f" · {g['why']}" if g["risk"] in ("risk", "late") else "")
        return {**g, "risk": goal["status"], "why": why}
    if g["risk"] == "review" and goal["status"] != "met":
        return {**g, "risk": "ok", "why": f"Đã xong mọi việc nhưng mục tiêu chưa đạt — {goal['why']}"}
    return g


def _progress(plan: dict[str, Any], tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Tín hiệu tiến độ — CODE tính, trợ lý chỉ đọc và diễn giải: % xong, quá hạn, tốc độ 14 ngày, dự báo ngày xong, cờ rủi ro + lý do."""
    t0 = today()
    live = [t for t in tasks if t["status"] != "cancelled"]
    done = [t for t in live if t["status"] == "done"]
    remaining = len(live) - len(done)
    overdue = [t for t in live if t["status"] != "done" and t["due_on"] and t["due_on"] < t0]
    how = Counter(t.get("done_by") or "self" for t in done)   # 0117: tách đóng bằng bằng chứng / tự báo / ngoại lệ
    # việc có bộ kiểm bị huỷ (qua duyệt / huỷ cả kế hoạch) không làm mà không còn chặn tiến độ ⇒ hiện rõ, không lặng lẽ rơi khỏi tổng
    dropped = sum(1 for t in tasks if t["status"] == "cancelled" and t.get("checked"))
    recent = sum(1 for t in done if t["done_at"] and t["done_at"].astimezone(zone()).date() > t0 - timedelta(days=RATE_DAYS))
    started = plan["created_at"].astimezone(zone()).date()
    days = max(1, min(RATE_DAYS, (t0 - started).days))
    rate = recent / days
    forecast = (t0 + timedelta(days=math.ceil(remaining / rate))) if remaining and rate > 0 else (t0 if not remaining else None)
    due = plan["due_on"]
    if plan["status"] != "open":
        closed = f" {plan['closed_at'].astimezone(zone()):%d/%m/%Y}" if plan.get("closed_at") else ""
        risk, why = plan["status"], ("Đã nghiệm thu" if plan["status"] == "done" else "Đã huỷ") + closed + (f" — {plan['closed_note']}" if plan.get("closed_note") else "")
    elif not remaining:
        risk, why = "review", "Đã xong mọi việc — người phụ trách nghiệm thu để đóng kế hoạch"
    elif overdue:
        risk, why = "late", f"{len(overdue)} việc quá hạn: " + ", ".join(t["title"] for t in overdue[:3])
    elif due and t0 > due:
        risk, why = "late", f"Đã qua mốc {due:%d/%m}, còn {remaining} việc"
    elif due and forecast and forecast > due:
        risk, why = "risk", f"Tốc độ {recent} việc / {days} ngày ⇒ dự báo xong {forecast:%d/%m}, sau mốc {due:%d/%m}"
    elif due and forecast is None and (due - t0).days <= 7:
        risk, why = "risk", f"Chưa xong việc nào mà còn {(due - t0).days} ngày tới mốc {due:%d/%m}"
    else:
        risk, why = "ok", (f"Dự báo xong {forecast:%d/%m}" if forecast else "Chưa đủ dữ liệu để dự báo") + (f", mốc {due:%d/%m}" if due else "")
    return {"total": len(live), "done": len(done), "remaining": remaining, "overdue": len(overdue), "forecast": forecast, "risk": risk, "why": why,
            "verified": how["evidence"], "self": how["self"], "override": how["override"], "dropped": dropped}


def _proof(g: dict[str, Any]) -> str:
    """"3 có bằng chứng · 2 tự báo · 1 huỷ (có bộ kiểm)" — tiến độ TÁCH việc đóng bằng bộ kiểm khỏi việc người tự báo (0117)."""
    return " · ".join(x for x in (f"{g['verified']} có bằng chứng" if g["verified"] else "", f"{g['self']} tự báo" if g["self"] else "",
                                  f"{g['override']} ngoại lệ" if g["override"] else "",
                                  f"{g['dropped']} huỷ (có bộ kiểm)" if g["dropped"] else "") if x)


class PlanRef(Input):
    plan_id: UUID = field("Kế hoạch", hidden=True)


@m.subject(PLAN_SUBJECT, "Kế hoạch")
async def subject_plan(ctx: Ctx, inp: registry.SubjectRef) -> dict[str, Any]:
    """Kế hoạch như một ĐỐI TƯỢNG (0117): trao đổi của kế hoạch là luồng trên chính nó — thấy kế hoạch (phạm vi, hoặc có một việc trong đó) mới
    đọc được, không phải "phòng dự án" có danh sách thành viên tự quản. Gọi bằng quyền người hỏi ⇒ không thấy thì NotFound."""
    p = await ctx.one("SELECT id, title, owner_id, team_id, status FROM flow.plans WHERE id = %s", (inp.subject_id,))
    if p is None:
        raise NotFound("Không thấy kế hoạch")
    return {"title": p["title"], "owner_id": p["owner_id"], "team_id": p["team_id"],
            "state": {"done": "done", "cancelled": "cancelled"}.get(p["status"], "active"), "open": _plan_open(p["id"])}


class PlanCloseRequestsIn(Input):
    plan_id: UUID = field("Kế hoạch", hidden=True)
    note: str = field("Ghi chú", "", max_length=500)


class PlanIds(Input):
    plan_ids: list[UUID] = field("Kế hoạch", hidden=True)


@m.query("plan_requests", resource=None, title="Việc kế hoạch đang nhờ phòng khác", internal=True, agent="deny", callers=("flow",),
         why="Người đã THẤY kế hoạch (RLS) cần biết kế hoạch còn chờ phòng nào — yêu cầu chỉ người gửi / phòng được nhờ thấy. Trả tên việc nhờ, "
             "hạn, tên phòng của yêu cầu ĐANG MỞ — không người gửi, không ghi chú.")
async def plan_requests(ctx: Ctx, inp: PlanIds) -> list[dict[str, Any]]:
    """Việc nhờ phòng khác CHƯA được nhận của các kế hoạch — người xem kế hoạch thấy kế hoạch còn chờ ai, dù yêu cầu do người khác gửi."""
    return await ctx.all("""SELECT r.plan_id, r.title, r.due_on, t.name AS team FROM flow.requests r JOIN core.teams t ON t.id = r.to_team
                            WHERE r.plan_id = ANY(%s) AND r.kind = 'team' AND r.status = 'open' ORDER BY r.created_at""", (inp.plan_ids,))


@m.command("close_plan_requests", resource=None, title="Đóng việc nhờ của kế hoạch đã huỷ", internal=True, agent="deny", callers=("flow",),
           why="set_plan huỷ kế hoạch, sau khi kiểm quyền GHI kế hoạch: đóng yêu cầu còn mở của đúng kế hoạch đó và báo trưởng phòng được nhờ. "
               "Trả số yêu cầu đã đóng.")
async def close_plan_requests(ctx: Ctx, inp: PlanCloseRequestsIn) -> int:
    """Hợp đồng hẹp cho ``set_plan``: người huỷ kế hoạch không thấy yêu cầu do người khác gửi (RLS) nhưng yêu cầu của kế hoạch phải đóng theo."""
    rows = await ctx.all("""UPDATE flow.requests SET status = 'declined', decided_at = now(), decision_note = %s
                            WHERE plan_id = %s AND status = 'open' RETURNING to_team, title""", (inp.note, inp.plan_id))
    for r in rows:
        if r["to_team"]:
            await ctx.notify(await _team_leads(ctx, r["to_team"]), kind="task", title=f"Không cần làm nữa: {r['title']}", body=inp.note,
                             open=REQUESTS_OPEN)
    return len(rows)


@m.query("plan_state", resource=None, title="Trạng thái + mốc kế hoạch", internal=True, agent="deny", callers=("flow",),
         why="accept_request, sau khi kiểm người gọi được nhận yêu cầu (trưởng phòng được nhờ): kế hoạch còn mở không, mốc ngày nào — người "
             "nhận chưa thấy kế hoạch. Trả status + due_on.")
async def plan_state(ctx: Ctx, inp: PlanRef) -> dict[str, Any] | None:
    """Cho đường nhận việc phòng khác nhờ: trưởng phòng được nhờ CHƯA thấy kế hoạch (chưa có việc nào của mình) mà phải kiểm kế hoạch còn mở
    và mốc của nó."""
    return await ctx.one("SELECT status, due_on FROM flow.plans WHERE id = %s", (inp.plan_id,))


@m.query("plan_tasks", resource=None, title="Trạng thái việc của kế hoạch (để tính tiến độ)", internal=True, agent="deny", callers=("flow",),
         why="Người đã THẤY kế hoạch (RLS) có thể chỉ thấy một phần việc; tiến độ / rủi ro / nghiệm thu phải tính trên CẢ kế hoạch, nếu không "
             "phòng chủ trì thấy \"đúng tiến độ\" khi phòng khác đã trễ. Trả trạng thái, hạn, ngày xong, cách đóng (bằng chứng / tự báo / ngoại lệ, "
             "mất căn cứ), ngày bắt đầu / ngày tạo (dựng dòng thời gian), tên phòng — KHÔNG tên việc, KHÔNG người làm.")
async def plan_tasks(ctx: Ctx, inp: PlanIds) -> list[dict[str, Any]]:
    return await ctx.all("""SELECT t.id, t.plan_id, t.status, t.due_on, t.start_on, t.created_at, t.done_at, t.done_by, t.done_check IS NOT NULL AS checked, tm.name AS team
                            FROM flow.tasks t
                            LEFT JOIN core.teams tm ON tm.id = t.team_id WHERE t.plan_id = ANY(%s)""", (inp.plan_ids,))


async def _plan_view(ctx: Ctx, plan_ids: list[UUID]) -> list[dict[str, Any]]:
    """Mọi việc của các kế hoạch cho người đang xem: việc người đó thấy (RLS) giữ tên + người làm; việc phòng khác chỉ còn trạng thái / hạn,
    tên thay bằng "việc của <phòng>"."""
    if not plan_ids:
        return []
    seen = {r["id"]: r for r in await ctx.all("""SELECT t.id, t.title, t.assignee_id, t.phase, t.depends_on, u.name AS who FROM flow.tasks t
                                                 LEFT JOIN core.users u ON u.id = t.assignee_id WHERE t.plan_id = ANY(%s)""", (plan_ids,))}
    return [{**t, **seen[t["id"]], "seen": True} if t["id"] in seen
            else {**t, "title": f"việc của {t['team'] or 'phòng khác'}", "assignee_id": None, "who": None, "phase": "", "depends_on": [], "seen": False}
            for t in await ctx.call_system("flow.plan_tasks", plan_ids=plan_ids)]


@m.query("plan", resource="flow", title="Kế hoạch")
async def plan(ctx: Ctx, inp: PlanRef) -> dict[str, Any]:
    """Một kế hoạch: tiến độ, quá hạn, dự báo ngày xong, cờ rủi ro kèm lý do (code tính) + từng việc theo người. Trợ lý đề xuất nhắc / dời hạn /
    giao lại dựa trên thẻ này, không tự suy số."""
    p = await ctx.one("""SELECT pl.*, u.name AS owner, core.can_see('flow', pl.owner_id, pl.team_id) AS runs FROM flow.plans pl
                         LEFT JOIN core.users u ON u.id = pl.owner_id WHERE pl.id = %s""", (inp.plan_id,))
    if p is None:
        raise NotFound("Không thấy kế hoạch (hoặc bạn không có quyền xem)")
    rows = await ctx.all("""SELECT t.*, u.name AS who, tm.name AS team FROM flow.tasks t LEFT JOIN core.users u ON u.id = t.assignee_id
                            LEFT JOIN core.teams tm ON tm.id = t.team_id WHERE t.plan_id = %s ORDER BY t.due_on NULLS LAST, t.created_at""",
                         (inp.plan_id,))
    full = await _plan_view(ctx, [p["id"]])
    goal = await _goal(ctx, p)
    pending = await ctx.call_system("flow.plan_requests", plan_ids=[p["id"]])
    g = _with_goal(_progress(p, full), goal)
    if pending and g["risk"] == "review":   # chưa ai nhận thì chưa xong
        g = {**g, "risk": "ok", "why": f"Còn {len(pending)} việc đang nhờ phòng khác, chưa ai nhận"}
    # Kế hoạch nhiều phòng: tiến độ TỪNG PHÒNG — phòng nào kéo chậm thấy ngay (trưởng phòng đó cũng thấy kế hoạch qua việc của phòng mình)
    teams: dict[str, list[dict[str, Any]]] = {}
    for r in full:
        teams.setdefault(r["team"] or "Chưa thuộc đội", []).append(r)
    by_team = []
    for name, ts in teams.items():
        live = [t for t in ts if t["status"] != "cancelled"]
        n_done = sum(t["status"] == "done" for t in live)
        late = sum(t["status"] != "done" and t["due_on"] is not None and t["due_on"] < today() for t in live)
        by_team.append({"title": name, "subtitle": f"{n_done}/{len(live)} xong" + (f" · {late} quá hạn" if late else ""),
                        "badge": "Xong" if live and n_done == len(live) else ("Trễ" if late else "Đang làm"),
                        "tone": "ok" if live and n_done == len(live) else ("bad" if late else "accent")})
    items = [_task_item(r) for r in rows]
    if len(by_team) > 1:
        for it, r in zip(items, rows, strict=True):
            it["subtitle"] = " · ".join(x for x in (it["subtitle"], r["team"]) if x)
    tone = {"ok": "ok", "risk": "warn", "late": "bad", "review": "accent", "done": "ok", "cancelled": "muted"}[g["risk"]]
    # Nút xử lý do CODE chọn từ tín hiệu: có việc chậm ⇒ nhắc cả loạt + giao lại / dời hạn từng việc chậm nhất (form mở sẵn việc đó)
    fixes: list[dict[str, Any]] = []
    if g["risk"] in ("risk", "late") and ctx.p.can("flow", "write"):
        soon = today() + timedelta(days=3)
        def lagging(ts: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [r for r in ts if r["status"] in ("todo", "doing", "blocked") and r["due_on"] and r["due_on"] <= soon]
        slow = lagging(rows)
        # người phụ trách / quản lý kế hoạch nhắc được cả việc phòng khác (remind_plan) — giao lại / dời hạn thì chỉ việc mình thấy
        if lagging(full if p["runs"] else rows):
            fixes.append(ui.act("flow.remind_plan", "Nhắc người đang chậm", {"plan_id": str(p["id"])}, "primary"))
        fixes += [ui.act("flow.adjust_task", f"Giao lại / dời hạn: {r['title'][:40]}", {"task_id": str(r["id"])}) for r in slow[:3]]
    if g["risk"] == "review" and ctx.p.can("flow", "write"):
        fixes.append(ui.act("flow.set_plan", "Nghiệm thu kế hoạch", {"plan_id": str(p["id"]), "status": "done"}, "primary"))
    # trao đổi của kế hoạch = luồng trên chính kế hoạch (0117) — gói Trao đổi tắt / không có quyền ⇒ thẻ vẫn mở, không có khối
    talk = await ctx.call_as_optional("talk.block", ctx.p, subject_kind=PLAN_SUBJECT, subject_id=p["id"])
    tl = await plan_timeline(ctx, p, full)
    return ui.card(p["title"], [
        ui.b_kpis([*([ui.kpi("Mục tiêu", f"{goal['value']}/{goal['target']} {goal['unit']}", "text",
                             tone="green" if goal["status"] == "met" else ("red" if goal["status"] in ("risk", "late") else None),
                             hint=f"{goal['label']} · cần khoảng {goal['expected']} tới hôm nay")] if goal else []),
                   ui.kpi("Tiến độ", f"{g['done']}/{g['total']} việc", "text", hint=_proof(g)),
                   ui.kpi("Quá hạn", g["overdue"], tone="red" if g["overdue"] else None),
                   ui.kpi("Dự báo xong", f"{g['forecast']:%d/%m/%Y}" if g["forecast"] else "Chưa đủ dữ liệu", "text"),
                   ui.kpi("Mốc", f"{p['due_on']:%d/%m/%Y}" if p["due_on"] else "—", "text")]),
        ui.b_callout(tone, RISK[g["risk"]], g["why"], actions=fixes),
        *([tl] if tl else []),
        *([ui.b_text(p["goal"], "Mục tiêu")] if p["goal"] else []),
        *([ui.b_tiles(by_team, "Theo phòng")] if len(by_team) > 1 else []),
        *([ui.b_list([{"title": r["title"], "subtitle": f"Nhờ {r['team']}" + (f" · hạn {r['due_on']:%d/%m}" if r["due_on"] else ""),
                       "badge": "Chờ nhận", "tone": "warn"} for r in pending], "Đang nhờ phòng khác")] if pending else []),
        ui.b_list(items or [{"title": "Chưa có việc", "subtitle": ""}],
                  "Các việc" if len(rows) == len(full) else f"Các việc bạn được xem ({len(rows)}/{len(full)} — còn lại của phòng khác)"),
        *([talk] if talk else []),
    ], subtitle=f"{PLAN_STATUS[p['status']]} · phụ trách {p['owner'] or '—'}", icon="list.bullet.clipboard",
        actions=([ui.act("flow.create_task", "Thêm việc vào kế hoạch", {"plan_id": str(p["id"])}),
                  ui.act("flow.import_plan", "Nhập / cập nhật từ Excel", {"plan_id": str(p["id"])}, import_only=True),
                  ui.act("flow.set_plan", "Huỷ kế hoạch", {"plan_id": str(p["id"]), "status": "cancelled"}, "destructive")] if p["status"] == "open"
                 else [ui.act("flow.set_plan", "Mở lại kế hoạch", {"plan_id": str(p["id"]), "status": "open"})]) if ctx.p.can("flow", "write") else [],
        ask={"title": "Hỏi trợ lý về kế hoạch này", "prompt": f"Kế hoạch “{p['title']}”: "})


@m.query("plans", resource="flow", title="Kế hoạch")
async def plans(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Các kế hoạch đang chạy trong phạm vi xem: tiến độ, quá hạn, dự báo, cờ rủi ro — kế hoạch có nguy cơ / trễ lên đầu."""
    ps = await ctx.all("""SELECT pl.*, u.name AS owner, core.can_see('flow', pl.owner_id, pl.team_id) AS runs FROM flow.plans pl
                          LEFT JOIN core.users u ON u.id = pl.owner_id
                          WHERE pl.status = 'open' ORDER BY pl.due_on NULLS LAST, pl.created_at DESC LIMIT 200""")
    tasks = await _plan_view(ctx, [p["id"] for p in ps])
    soon = today() + timedelta(days=3)
    rows = []
    for p in ps:
        ts = [t for t in tasks if t["plan_id"] == p["id"]]
        g = _with_goal(_progress(p, ts), await _goal(ctx, p))
        # nút theo dòng chỉ hiện khi bấm được: người phụ trách / quản lý nhắc mọi việc; người khác chỉ nhắc việc chậm mình xem được (như remind_plan)
        mine_late = any(t["seen"] and t["assignee_id"] and t["status"] in ("todo", "doing", "blocked") and t["due_on"] and t["due_on"] <= soon
                        for t in ts)
        rows.append({"id": p["id"], "title": p["title"], "owner": p["owner"], "due_on": p["due_on"], "progress": f"{g['done']}/{g['total']}",
                     "overdue": g["overdue"], "forecast": g["forecast"], "risk": g["risk"], "why": g["why"],
                     "remind": "yes" if g["risk"] in ("risk", "late") and (p["runs"] or mine_late) else "",
                     "close": "yes" if p["runs"] else ""})
    rows.sort(key=lambda r: ({"late": 0, "risk": 1, "review": 2}.get(r["risk"], 3), r["due_on"] or today() + timedelta(days=3650)))
    bad = sum(1 for r in rows if r["risk"] in ("late", "risk"))
    review = sum(1 for r in rows if r["risk"] == "review")
    order = {r["id"]: i for i, r in enumerate(rows)}
    summary = {r["id"]: {"line": f"{r['owner'] or '—'} · {r['progress']} xong · {RISK[r['risk']]}"} for r in rows}
    timeline = await portfolio_timeline(ctx, sorted(ps, key=lambda p: order[p["id"]]), tasks, summary) if rows else None
    return ui.table(rows, [
        ui.col("risk", "", "badge", labels=RISK, tones=RISK_TONE), ui.col("title", "Kế hoạch"), ui.col("owner", "Phụ trách"),
        ui.col("progress", "Xong"), ui.col("overdue", "Quá hạn", "number"), ui.col("forecast", "Dự báo xong", "date"),
        ui.col("due_on", "Mốc", "date"), ui.col("why", "Vì sao", width=320),
    ], kpis=[ui.kpi("Đang chạy", len(rows) - review), ui.kpi("Có nguy cơ / trễ", bad, tone="red" if bad else None),
             ui.kpi("Chờ nghiệm thu", review)],
        empty="Chưa có kế hoạch nào — nói với trợ lý: “Lên kế hoạch … giao cho đội …”", limit=200,
        views=ui.views(board=ui.board_view("risk", "title", "owner"), calendar=ui.calendar_view("due_on", "title", "owner"), timeline=timeline))   # chỉ xem


@m.query("metrics", resource="flow", title="Chỉ số đặt mục tiêu được")
async def metrics(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Các chỉ số module công bố để giao MỤC TIÊU (0074 B): khoá, ý nghĩa, đơn vị. Lập kế hoạch có mục tiêu ⇒ chọn đúng khoá ở đây (goal_metric),
    không tự đặt công thức. Chỉ số của gói công ty chưa bật không có ở đây."""
    rows = [{"id": k, "key": k, "label": m.label, "unit": m.unit} for k, m in registry.METRICS.items() if _metric_on(ctx, k)]
    return ui.table(rows, [ui.col("key", "Khoá"), ui.col("label", "Chỉ số"), ui.col("unit", "Đơn vị")], empty="Chưa có chỉ số nào")


def _metric_on(ctx: Ctx, key: str) -> bool:
    """Chỉ số người này được gắn vào mục tiêu: gói đang bật VÀ có phạm vi cả công ty trên tài nguyên chỉ số đếm — số tổng là quyền được cấp rõ,
    không tự có vì lập được kế hoạch. Người được giao kế hoạch thấy con số vì người CÓ quyền đã gắn nó vào (chia sẻ có chủ đích)."""
    op, m = registry.get(key), registry.METRICS.get(key)
    return (op is not None and m is not None and ctx.p.has_scope(m.resource, "all")
            and (ctx.p.packs is None or packs.missing(ctx.p.packs, op) is None))


@m.query("metric_options", resource=None, title="Chỉ số mục tiêu")
async def metric_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return [{"value": k, "label": f"{m.label} ({m.unit})"} for k, m in registry.METRICS.items() if _metric_on(ctx, k)]


# đo mục tiêu mỗi giờ cho kế hoạch đang chạy; chậm ⇒ báo NGƯỜI GIAO mục tiêu (không phải người phụ trách — người đó thấy trên thẻ / buổi sáng),
# tối đa 3 ngày một lần
@m.command("check_goals", resource="flow", title="Đo mục tiêu kế hoạch", internal=True, agent="deny",
           sweep="""SELECT DISTINCT tenant_id FROM flow.plans WHERE status = 'open' AND goal_metric IS NOT NULL
                    AND (goal_measured_at IS NULL OR goal_measured_at < now() - interval '1 hour')""")
async def check_goals(ctx: Ctx, inp: NoInput) -> dict[str, int]:
    ps = await ctx.all("""SELECT * FROM flow.plans WHERE status = 'open' AND goal_metric IS NOT NULL
                          AND (goal_measured_at IS NULL OR goal_measured_at < now() - interval '1 hour')""")
    alerted = 0
    for p in ps:
        goal = await _goal(ctx, p)
        if goal is None:
            continue
        late = goal["status"] in ("risk", "late")
        alert = late and p["created_by"] and p["created_by"] != p["owner_id"] and (p["goal_alerted_on"] is None
                                                                                    or p["goal_alerted_on"] <= today() - timedelta(days=3))
        await ctx.exec("UPDATE flow.plans SET goal_value = %s, goal_measured_at = now(), goal_alerted_on = CASE WHEN %s THEN core.today() "
                       "ELSE goal_alerted_on END WHERE id = %s", (goal["value"], bool(alert), p["id"]))
        if alert:
            await ctx.notify([p["created_by"]], kind="task", title=f"Mục tiêu “{p['title']}” đang chậm", body=goal["why"],
                             push="Một mục tiêu anh/chị giao đang chậm", open=_plan_open(p["id"]))
            alerted += 1
    return {"measured": len(ps), "alerted": alerted}


@m.query("suggestions", resource="flow", title="Gợi ý buổi sáng (việc & kế hoạch)", agent="deny")
async def suggestions(ctx: Ctx, inp: NoInput) -> list[dict[str, Any]]:
    """Nguồn gợi ý buổi sáng (0074 A), code chọn: kế hoạch MÌNH PHỤ TRÁCH có việc chậm ⇒ nhắc; đã xong mọi việc ⇒ nghiệm thu; việc chung của
    đội mình quản lý chưa ai nhận quá 1 ngày ⇒ giao cho người đang ít việc nhất."""
    out: list[dict[str, Any]] = []
    ps = await ctx.all("SELECT * FROM flow.plans WHERE status = 'open' AND owner_id = core.me() ORDER BY due_on NULLS LAST LIMIT 20")
    tasks = await _plan_view(ctx, [p["id"] for p in ps])   # lý do trên thẻ không lộ tên việc phòng khác
    soon = today() + timedelta(days=3)
    for p in ps:
        ts = [t for t in tasks if t["plan_id"] == p["id"]]
        g = _progress(p, ts)
        lag = [t for t in ts if t["status"] in ("todo", "doing", "blocked") and t["due_on"] and t["due_on"] <= soon]
        goal = await _goal(ctx, p)
        g = _with_goal(g, goal)
        if goal and goal["status"] in ("risk", "late") and not lag and p["due_on"] and p["due_on"] >= today():
            # mục tiêu chậm mà việc vẫn đúng hạn ⇒ việc chưa đủ để đạt đích: thêm một việc bù cho người ít việc nhất của đội kế hoạch
            team_code = await ctx.val("SELECT code FROM core.teams WHERE id = %s", (p["team_id"],)) or ""
            best = next((r for r in await _ranked(ctx, team_code)), None)
            gap = max(1, goal["expected"] - goal["value"])
            if best:
                out.append({"command": "flow.create_task", "key": f"goal:{p['id']}", "rank": 60,
                            "input": {"title": f"Bù mục tiêu “{p['title']}”: thêm {gap} {goal['unit']} trong 3 ngày", "assignee_email": best["email"],
                                      "plan_id": str(p["id"]), "due_on": min(p["due_on"], today() + timedelta(days=3)).isoformat(),
                                      "notes": goal["why"]},
                            "reason": f"{goal['why']} — việc hiện có vẫn đúng hạn nhưng chưa đủ; {best['name']} đang ít việc nhất"})
            continue
        if lag and g["risk"] in ("risk", "late"):
            out.append({"command": "flow.remind_plan", "input": {"plan_id": str(p["id"])}, "key": f"remind:{p['id']}", "rank": 50 if g["risk"] == "late" else 120,
                        "reason": f"Kế hoạch “{p['title']}” {RISK[g['risk']].lower()}: {g['why']}"})
        elif g["risk"] == "review":
            out.append({"command": "flow.set_plan", "input": {"plan_id": str(p["id"]), "status": "done"}, "key": f"review:{p['id']}", "rank": 200,
                        "reason": f"Kế hoạch “{p['title']}” đã xong mọi việc — nghiệm thu để đóng"})
    # việc chung chỉ gợi ý cho TRƯỞNG TRỰC TIẾP của đội (không phải mọi người có phạm vi rộng — giám đốc không nhận trùng thẻ của trưởng nhóm)
    led = list(await led_teams(ctx.conn, ctx.p.tenant_id, ctx.p.user_id))
    if led:
        free = await ctx.all("""SELECT t.id, t.title, t.created_at, tm.code, tm.name AS team FROM flow.tasks t JOIN core.teams tm ON tm.id = t.team_id
                                WHERE t.assignee_id IS NULL AND t.status = 'todo' AND t.team_id = ANY(%s) AND t.created_at < now() - interval '1 day'
                                ORDER BY t.due_on NULLS LAST, t.created_at LIMIT 2""", (led,))
        asked = await ctx.all("""SELECT r.id, r.title, r.created_at, t.code, t.name AS team, u.name AS who FROM flow.requests r
                                 JOIN core.teams t ON t.id = r.to_team JOIN core.users u ON u.id = r.requested_by
                                 WHERE r.kind = 'team' AND r.status = 'open' AND r.to_team = ANY(%s) ORDER BY r.created_at LIMIT 2""", (led,))
        for r in asked:
            best = next((x for x in await _ranked(ctx, r["code"]) if x["id"] != ctx.p.user_id), None)
            if best:
                out.append({"command": "flow.accept_request", "input": {"request_id": str(r["id"]), "assignee_email": best["email"]},
                            "key": f"accept:{r['id']}", "rank": 70,
                            "reason": f"{r['who']} nhờ {r['team']} từ {r['created_at'].astimezone(zone()):%d/%m} — {best['name']} đang ít việc nhất ({best['reason']})"})
        for t in free:
            best = next((r for r in await _ranked(ctx, t["code"]) if r["id"] != ctx.p.user_id), None)
            if best:
                out.append({"command": "flow.adjust_task", "input": {"task_id": str(t["id"]), "assignee_email": best["email"]},
                            "key": f"assign:{t['id']}", "rank": 150,
                            "reason": f"Việc chung của {t['team']} chưa ai nhận từ {t['created_at'].astimezone(zone()):%d/%m} — {best['name']} đang ít việc nhất ({best['reason']})"})
    return out


m.suggests("flow.suggestions", rank=10)


def _attn_plans(o: Any) -> Line | None:
    return (line("warn", f"{num(kpi(o, 'Có nguy cơ / trễ'))} kế hoạch có nguy cơ trễ", "Xem việc nào kéo chậm, đề xuất nhắc / dời / giao lại", "Kế hoạch nào đang có nguy cơ trễ, nên làm gì?") if kpi(o, "Có nguy cơ / trễ") else None)
