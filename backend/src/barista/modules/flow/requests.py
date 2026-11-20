"""NHỜ PHÒNG KHÁC / THUÊ NGOÀI (0074 C) — yêu cầu cho phòng, nhận / từ chối, đẩy lên sếp chung, thuê ngoài qua duyệt + link đề bài. Module ``flow`` (0088: package — ``m`` khai ở ``__init__``)."""

from datetime import date, timedelta
from typing import Any
from uuid import UUID

from barista.api import (
    Ctx,
    Day,
    Denied,
    Event,
    Input,
    Invalid,
    Line,
    Money,
    NoInput,
    NotFound,
    SubjectRef,
    field,
    kpi,
    line,
    num,
    today,
    ui,
    vnd_text,
)
from barista.modules.flow import REQUESTS_OPEN, TASKS_OPEN, _plan_open, _team_id, _team_leads, m
from barista.modules.flow.tasks import _candidates, _due, _lock_plan


async def _file_request(ctx: Ctx, *, title: str, notes: str, team_code: str, due_on: date | None, plan_id: UUID | None,
                        milestone: date | None) -> UUID:
    """NHỜ PHÒNG (0074 C): ghi yêu cầu cho PHÒNG (không chỉ định người), hạn theo lịch của phòng đó + mốc kế hoạch, báo trưởng phòng."""
    team = await _team_id(ctx, team_code)
    if team is None:
        raise NotFound(f"Không có phòng {team_code}")
    due, _, _ = await _due(ctx, due_on, team, milestone, f"“{title}”: ")
    rid: UUID = await ctx.val("""INSERT INTO flow.requests (kind, plan_id, title, notes, requested_by, to_team, due_on)
                                 VALUES ('team', %s, %s, %s, %s, %s, %s) RETURNING id""", (plan_id, title, notes, ctx.p.user_id, team, due))
    when = f", hạn {due:%d/%m}" if due else ""
    await ctx.notify(await _team_leads(ctx, team), kind="task", title=f"Phòng khác nhờ: {title}", body=f"{ctx.p.name} nhờ{when}",
                     push="Có việc phòng khác nhờ phòng anh/chị", open=REQUESTS_OPEN)
    return rid


class RequestTeam(Input):
    title: str = field("Việc cần nhờ", max_length=300, example="Gọi mời 30 khách cũ tham dự mở bán")
    notes: str = field("Ghi chú", "", kind="textarea", max_length=2000)
    team_code: str = field("Nhờ phòng", options_query="admin.team_options")
    due_on: Day | None = field("Hạn", None)
    plan_id: UUID | None = field("Kế hoạch", None, hidden=True)


@m.command("request_team", resource="flow", title="Nhờ phòng khác")
async def request_team(ctx: Ctx, inp: RequestTeam) -> dict[str, Any]:
    """Việc cần người của phòng khác mà mình không giao được ⇒ nhờ PHÒNG: trưởng phòng nhận và tự chia người, hoặc từ chối kèm lý do."""
    milestone = None
    if inp.plan_id:
        plan = await ctx.one("SELECT due_on FROM flow.plans WHERE id = %s AND status = 'open'", (inp.plan_id,))
        if plan is None:
            raise NotFound("Không thấy kế hoạch đang chạy")
        milestone = plan["due_on"]
    rid = await _file_request(ctx, title=inp.title.strip(), notes=inp.notes, team_code=inp.team_code, due_on=inp.due_on, plan_id=inp.plan_id,
                              milestone=milestone)
    return {"request_id": rid, "summary": f"Nhờ phòng {inp.team_code}: {inp.title.strip()}"}


@m.query("teams", resource="flow", title="Phòng / đội")
async def teams_directory(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Danh mục phòng / đội của công ty (mã, tên, trưởng) — để nhờ đúng phòng (request_team = mã) hay chọn phòng chủ trì."""
    rows = await ctx.all("""SELECT t.id, t.code, t.name, p.name AS parent, string_agg(u.name, ', ' ORDER BY u.name) FILTER (WHERE tm.is_lead) AS leads
                            FROM core.teams t LEFT JOIN core.teams p ON p.id = t.parent_id LEFT JOIN core.team_members tm ON tm.team_id = t.id
                            LEFT JOIN core.users u ON u.id = tm.user_id GROUP BY t.id, p.name ORDER BY t.code""")
    return ui.table(rows, [ui.col("code", "Mã"), ui.col("name", "Phòng / đội"), ui.col("leads", "Trưởng"), ui.col("parent", "Thuộc")])


class AcceptRequest(Input):
    request_id: UUID = field("Yêu cầu", hidden=True)
    assignee_email: str = field("Giao cho", options_query="flow.assignee_options", help="Người trong phòng bạn — xếp theo khối lượng việc")
    due_on: Day | None = field("Hạn", None, placeholder="Hạn phòng nhờ đưa")


async def _open_request(ctx: Ctx, request_id: UUID) -> dict[str, Any]:
    """Yêu cầu nhờ phòng còn mở mà người gọi QUẢN LÝ phòng được nhờ (trưởng phòng / cấp trên) — người xin không tự nhận thay."""
    r = await ctx.one("""SELECT r.*, t.code AS team_code, t.name AS team FROM flow.requests r JOIN core.teams t ON t.id = r.to_team
                         WHERE r.id = %s AND r.kind = 'team' FOR UPDATE OF r""", (request_id,))
    if r is None:
        raise NotFound("Không thấy yêu cầu (hoặc không phải gửi phòng bạn)")
    if r["status"] != "open":
        raise Invalid("Yêu cầu đã được xử lý")
    if r["to_team"] not in ctx.p.managed_teams:
        raise Denied("Chỉ trưởng phòng được nhờ (hoặc cấp trên) nhận / từ chối")
    return r


@m.command("accept_request", resource="flow", title="Nhận việc phòng khác nhờ")
async def accept_request(ctx: Ctx, inp: AcceptRequest) -> dict[str, Any]:
    """Trưởng phòng nhận: giao cho người trong phòng (theo phạm vi của mình) ⇒ thành việc thật (vào kế hoạch nếu có), báo người nhờ + người nhận."""
    r = await _open_request(ctx, inp.request_id)
    milestone = None
    if r["plan_id"]:   # cùng khoá với đóng / mở kế hoạch — không nhận việc vào kế hoạch vừa đóng
        await _lock_plan(ctx, r["plan_id"])
        state = await ctx.call_system("flow.plan_state", plan_id=r["plan_id"])
        if not state or state["status"] != "open":
            raise Invalid("Kế hoạch của yêu cầu này đã đóng / huỷ — không cần nhận nữa")
        milestone = state["due_on"]
    allowed = {x["email"]: x for x in await _candidates(ctx, r["team_code"])}
    who = allowed.get(inp.assignee_email.strip().lower())
    if who is None:
        raise Invalid(f"{inp.assignee_email} không thuộc phòng {r['team']} trong phạm vi của bạn")
    home = await ctx.home_team(who["id"])
    due = (await _due(ctx, inp.due_on, home, milestone, ""))[0] if inp.due_on else r["due_on"]
    if due and milestone and due > milestone:
        raise Invalid(f"Hạn {due:%d/%m/%Y} trễ hơn mốc kế hoạch {milestone:%d/%m/%Y}")
    tid = await ctx.val("""INSERT INTO flow.tasks (title, notes, assignee_id, team_id, due_on, plan_id, origin, created_by)
                           VALUES (%s, %s, %s, %s, %s, %s, 'manual', %s) RETURNING id""",
                        (r["title"], r["notes"], who["id"], home, due, r["plan_id"], ctx.p.user_id))
    await ctx.exec("UPDATE flow.requests SET status = 'accepted', task_id = %s, decided_by = %s, decided_at = now() WHERE id = %s",
                   (tid, ctx.p.user_id, r["id"]))
    when = f", hạn {due:%d/%m}" if due else ""
    see = _plan_open(r["plan_id"]) if r["plan_id"] else TASKS_OPEN
    await ctx.notify([r["requested_by"]], kind="task", title=f"{r['team']} đã nhận: {r['title']}", body=f"{who['name']} làm{when}", open=see)
    await ctx.notify([who["id"]], kind="task", title=f"Việc mới (phòng khác nhờ): {r['title']}{when}", body=f"{ctx.p.name} giao",
                     push=f"Anh/chị có việc mới{when}", open=see)
    return {"task_id": tid, "summary": f"Nhận “{r['title']}” → {who['name']}{when}"}


class DeclineRequest(Input):
    request_id: UUID = field("Yêu cầu", hidden=True)
    note: str = field("Lý do", kind="textarea", max_length=500, help="Người nhờ cần biết để tìm cách khác (thuê ngoài, dời mốc…)")


@m.command("decline_request", resource="flow", title="Từ chối việc phòng khác nhờ")
async def decline_request(ctx: Ctx, inp: DeclineRequest) -> dict[str, Any]:
    if len(inp.note.strip()) < 3:
        raise Invalid("Ghi lý do từ chối để người nhờ tìm cách khác")
    r = await _open_request(ctx, inp.request_id)
    await ctx.exec("UPDATE flow.requests SET status = 'declined', decided_by = %s, decided_at = now(), decision_note = %s WHERE id = %s",
                   (ctx.p.user_id, inp.note.strip(), r["id"]))
    await ctx.notify([r["requested_by"]], kind="task", title=f"{r['team']} từ chối: {r['title']}", body=f"{ctx.p.name}: {inp.note.strip()}",
                     open=_plan_open(r["plan_id"]) if r["plan_id"] else REQUESTS_OPEN)
    return {"summary": f"Đã từ chối “{r['title']}”"}


VENDOR_STEPS = (("Chọn đơn vị / freelancer, chốt báo giá trong ngân sách", 0.2), ("Gửi đề bài, nhận bản nháp", 0.6),
                ("Nghiệm thu sản phẩm", 1.0), ("Đề nghị thanh toán theo nghiệm thu", 1.0))


class RequestVendor(Input):
    title: str = field("Việc thuê ngoài", max_length=300, example="Dựng phim 3D giới thiệu dự án")
    brief: str = field("Đề bài", kind="textarea", max_length=4000)
    budget_vnd: Money = field("Ngân sách tối đa", ge=1, example="30.000.000")
    due_on: Day = field("Hạn bàn giao")
    vendor_name: str = field("Đơn vị / freelancer (nếu đã có)", "", max_length=200)
    plan_id: UUID | None = field("Kế hoạch", None, hidden=True)


@m.command("request_vendor", resource="flow", title="Thuê ngoài")
async def request_vendor(ctx: Ctx, inp: RequestVendor) -> dict[str, Any]:
    """Thuê ngoài (0074 C): là CAM KẾT CHI ⇒ đi qua chuỗi duyệt công ty đặt cho lệnh này (mặc định giám đốc). Chạy (sau duyệt) ⇒ ghi yêu cầu +
    sinh các việc của quy trình thuê cho người xin, rải tới hạn bàn giao. Tiền không ghi ở đây — chỉ ghi qua sổ cái khi có hoá đơn / nghiệm thu."""
    milestone = None
    if inp.plan_id:
        plan = await ctx.one("SELECT due_on FROM flow.plans WHERE id = %s AND status = 'open'", (inp.plan_id,))
        if plan is None:
            raise NotFound("Không thấy kế hoạch đang chạy")
        milestone = plan["due_on"]
    home = await ctx.home_team(ctx.p.user_id)
    due, _, _ = await _due(ctx, inp.due_on, home, milestone, "")
    assert due is not None
    rid = await ctx.val("""INSERT INTO flow.requests (kind, plan_id, title, notes, requested_by, due_on, status, decided_at, budget_vnd, vendor_name)
                           VALUES ('vendor', %s, %s, %s, %s, %s, 'accepted', now(), %s, %s) RETURNING id""",
                        (inp.plan_id, inp.title.strip(), inp.brief, ctx.p.user_id, due, inp.budget_vnd, inp.vendor_name.strip()))
    span = max(1, (due - today()).days)
    for step, frac in VENDOR_STEPS:
        d = min(due, today() + timedelta(days=max(1, round(span * frac))))
        await ctx.exec("""INSERT INTO flow.tasks (title, notes, assignee_id, team_id, due_on, plan_id, origin, created_by)
                          VALUES (%s, %s, %s, %s, %s, %s, 'manual', %s)""",
                       (f"{step} — {inp.title.strip()}", inp.brief if frac < 1 else "", ctx.p.user_id, home, d, inp.plan_id, ctx.p.user_id))
    return {"request_id": rid, "summary": f"Thuê ngoài “{inp.title.strip()}” · ngân sách tối đa {vnd_text(inp.budget_vnd)} · hạn {due:%d/%m}"
                                          f" · {len(VENDOR_STEPS)} việc cho người phụ trách"}


REQUEST_SUBJECT = "flow.request"
# hai mục bên thuê ngoài nộp qua link — ``ref`` để nhận lại đúng mục khi họ nộp
BRIEF_ITEMS = ({"kind": "text", "prompt": "Báo giá (đồng) và thời gian hoàn thành", "options": [], "ref": "quote"},
               {"kind": "file", "prompt": "Bản nháp / sản phẩm bàn giao", "options": [], "ref": "delivery"})


@m.subject(REQUEST_SUBJECT, "Việc nhờ / thuê ngoài")
async def subject_request(ctx: Ctx, inp: SubjectRef) -> dict[str, Any]:
    r = await ctx.one("SELECT id, title, requested_by, to_team, status FROM flow.requests WHERE id = %s", (inp.subject_id,))
    if r is None:
        raise NotFound("Không thấy việc nhờ / thuê ngoài")
    return {"title": r["title"], "owner_id": r["requested_by"], "team_id": r["to_team"],
            "state": "active" if r["status"] in ("open", "accepted") else "done", "open": REQUESTS_OPEN}


class SendBrief(Input):
    request_id: UUID = field("Việc thuê ngoài", hidden=True)
    recipient: str = field("Gửi ai", "", max_length=200, example="Anh Minh — studio dựng 3D", placeholder="Đơn vị đã ghi khi xin thuê ngoài")
    note: str = field("Lời nhắn kèm", "", kind="textarea", max_length=1000)
    days: int = field("Link dùng được (ngày)", 14, ge=1, le=60)


async def _upload_shelves(ctx: Ctx) -> list[dict[str, Any]]:
    """Kệ người gọi thêm tài liệu được (bằng QUYỀN NGƯỜI ĐÓ) — file bên ngoài nộp qua link vào đây; không có ⇒ không mở link."""
    me = await ctx.principal_of(ctx.p.user_id) if ctx.p.user_id else None
    try:
        return list(await ctx.call_as("kb.upload_shelves", me) or []) if me else []
    except Denied:
        return []


@m.command("send_brief", resource="flow", title="Gửi đề bài thuê ngoài (link)", shows=("asks.request", "once_link"))
async def send_brief(ctx: Ctx, inp: SendBrief) -> dict[str, Any]:
    """Link một lần cho bên thuê ngoài (qua Zalo / email): đề bài + hạn; họ gửi báo giá và nộp bàn giao qua link (asks, 0075), người xin được
    báo khi có. NGÂN SÁCH TỐI ĐA là số nội bộ — không gửi ra ngoài. Chỉ việc thuê ngoài đã duyệt chi."""
    r = await ctx.one("SELECT * FROM flow.requests WHERE id = %s AND kind = 'vendor'", (inp.request_id,))
    if r is None:
        raise NotFound("Không thấy việc thuê ngoài")
    if r["status"] != "accepted":
        raise Invalid("Việc thuê ngoài chưa được duyệt chi")
    who = inp.recipient.strip() or r["vendor_name"]
    if not await _upload_shelves(ctx):
        raise Invalid("Bạn chưa thêm được tài liệu vào kệ nào — file bàn giao không có chỗ lưu. Nhờ quản trị cấp quyền “thêm tài liệu” trong kho")
    out = await ctx.call_system("asks.open", subject_kind=REQUEST_SUBJECT, subject_id=str(r["id"]), subject_title=r["title"],
                                title=f"Đề bài: {r['title']}", recipient=who, items=list(BRIEF_ITEMS), days=inp.days,
                                note=r["notes"] + (f"\n\n{inp.note.strip()}" if inp.note.strip() else ""),
                                due_on=r["due_on"].isoformat() if r["due_on"] else None)
    return {**out, "summary": f"Link đề bài “{r['title']}”" + (f" cho {who}" if who else "") + " — link chỉ hiện một lần, gửi ngay qua Zalo / email"}


@m.on("asks.item_answered")
async def brief_answered(ctx: Ctx, event: Event) -> None:
    """Bên thuê ngoài gửi báo giá / nộp bàn giao qua link ⇒ báo người xin (mở yêu cầu xem nội dung, file vào kho theo quyền người xem)."""
    e = event.payload
    if e["subject_kind"] != REQUEST_SUBJECT or e["ref"] not in ("quote", "delivery"):
        return
    what = "Báo giá" if e["ref"] == "quote" else "Bàn giao"
    body = (e.get("answer") or "")[:2000] or ("Đã nộp file — mở yêu cầu để xem" if e.get("document_id") else "")
    await ctx.call_system("asks.notify_owner", request_id=e["request_id"], title=f"{what} thuê ngoài đã về", body=body)


@m.query("requests", resource="flow", title="Nhờ phòng / thuê ngoài")
async def requests(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Yêu cầu nguồn lực mình thấy: mình nhờ / phòng mình được nhờ / thuê ngoài. Việc chờ phòng mình nhận lên đầu."""
    rows = await ctx.all("""SELECT r.id, r.kind, r.title, r.status, r.due_on, r.budget_vnd, r.vendor_name, r.decision_note, r.to_team, r.requested_by,
                                   u.name AS who, t.name AS team, a.name AS assignee FROM flow.requests r JOIN core.users u ON u.id = r.requested_by
                            LEFT JOIN core.teams t ON t.id = r.to_team LEFT JOIN flow.tasks k ON k.id = r.task_id
                            LEFT JOIN core.users a ON a.id = k.assignee_id ORDER BY (r.status = 'open') DESC, r.created_at DESC LIMIT 200""")
    managed = set(ctx.p.managed_teams)
    out = [{**r, "to": r["team"] if r["kind"] == "team" else (r["vendor_name"] or "Thuê ngoài"),
            "kind_label": "Nhờ phòng" if r["kind"] == "team" else "Thuê ngoài",
            "mine": "yes" if r["kind"] == "team" and r["status"] == "open" and r["to_team"] in managed else "no",
            "brief": "yes" if r["kind"] == "vendor" and r["status"] == "accepted" and r["requested_by"] == ctx.p.user_id else "no"} for r in rows]
    waiting_me = sum(1 for r in out if r["mine"] == "yes")
    waiting_others = sum(1 for r in out if r["status"] == "open" and r["requested_by"] == ctx.p.user_id)
    return ui.table(out, [
        ui.col("status", "", "badge", labels=REQUEST_STATUS, tones=REQUEST_TONE), ui.col("kind_label", "Loại"), ui.col("title", "Việc"),
        ui.col("who", "Người nhờ"), ui.col("to", "Phòng / đơn vị"), ui.col("assignee", "Người làm"), ui.col("due_on", "Hạn", "date"),
        ui.col("budget_vnd", "Ngân sách", "money"), ui.col("decision_note", "Ghi chú"),
    ], kpis=[ui.kpi("Chờ phòng bạn nhận", waiting_me, tone="red" if waiting_me else None), ui.kpi("Bạn đang nhờ, chờ nhận", waiting_others)],
        empty="Chưa có việc nhờ / thuê ngoài nào", limit=200)


REQUEST_STATUS = {"open": "Chờ nhận", "accepted": "Đã nhận", "declined": "Từ chối"}
REQUEST_TONE = {"open": "orange", "accepted": "green", "declined": "gray"}


def _attn_requests(o: Any) -> Line | None:
    n = kpi(o, "Chờ phòng bạn nhận")
    return line("warn", f"{num(n)} việc phòng khác nhờ phòng anh/chị", "Nhận và chia người, hoặc từ chối kèm lý do",
                "Việc phòng khác nhờ, nên giao cho ai?") if n else None


# nhờ phòng quá 2 ngày chưa ai nhận ⇒ báo trưởng của đội CHA CHUNG gần nhất của hai phòng (một lần)
@m.command("escalate_requests", resource="flow", title="Đẩy việc nhờ chưa ai nhận", internal=True, agent="deny",
           sweep="""SELECT DISTINCT tenant_id FROM flow.requests WHERE kind = 'team' AND status = 'open' AND escalated_at IS NULL
                    AND created_at < now() - interval '2 days'""")
async def escalate_requests(ctx: Ctx, inp: NoInput) -> dict[str, int]:
    rows = await ctx.all("""SELECT r.id, r.title, r.to_team, r.requested_by, t.name AS team FROM flow.requests r JOIN core.teams t ON t.id = r.to_team
                            WHERE r.kind = 'team' AND r.status = 'open' AND r.escalated_at IS NULL AND r.created_at < now() - interval '2 days'""")
    parent = {r["id"]: r["parent_id"] for r in await ctx.all("SELECT id, parent_id FROM core.teams")}

    def up(t: UUID | None) -> list[UUID]:
        out: list[UUID] = []
        while t is not None and t not in out:
            out.append(t)
            t = parent.get(t)
        return out
    sent = 0
    for r in rows:
        mine = set(up(await ctx.home_team(r["requested_by"])))
        boss_team = next((t for t in up(parent.get(r["to_team"])) if t in mine), None)
        bosses = await _team_leads(ctx, boss_team) if boss_team else []
        await ctx.exec("UPDATE flow.requests SET escalated_at = now() WHERE id = %s", (r["id"],))
        if bosses:
            await ctx.notify(bosses, kind="task", title=f"Việc nhờ {r['team']} 2 ngày chưa ai nhận: {r['title']}",
                             body="Phòng được nhờ chưa nhận / từ chối — cần anh/chị điều phối", open=REQUESTS_OPEN)
            sent += 1
    return {"escalated": sent}
