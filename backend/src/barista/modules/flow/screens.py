"""MÀN HÌNH + TÌM NHANH ⌘K của module (0069 / 0087). Module ``flow`` (0088: package — ``m`` khai ở ``__init__``)."""

from typing import Any

from barista.api import Action as A
from barista.api import Ctx, Desk, Open, Screen, SearchIn, SearchTerms, Workspace
from barista.modules.flow import TASK_STATUS, m
from barista.modules.flow.plans import PLAN_STATUS, _attn_plans
from barista.modules.flow.requests import _attn_requests
from barista.modules.flow.tasks import _attn_needs_action, _attn_tasks, _attn_tasks_2

# ---- Màn hình (0069): module tự khai phân hệ / góp màn vào phân hệ khác; workspaces.py chỉ ráp và lọc theo quyền
m.workspace(Workspace("work", "Quy trình & kế hoạch", "rectangle.split.3x1", (
    Screen("tasks", "Việc của tôi", "checkmark.circle.fill", "flow.tasks", tint="green", actions=(A("flow.import_plan", "Nhập kế hoạch từ Excel"),),
           desk=Desk("do", say=lambda o: [_attn_tasks(o), _attn_tasks_2(o)], rank=120)),
    Screen("stuck", "Hồ sơ kẹt", "exclamationmark.triangle.fill", "flow.needs_action", tint="red", min_scope="team",
           desk=Desk("decide", say=_attn_needs_action, rank=110)),
    Screen("flow_board", "Quy trình đang chạy", "rectangle.split.3x1.fill", "flow.board", tint="indigo"),
    Screen("plans", "Kế hoạch", "list.bullet.clipboard.fill", "flow.plans", tint="purple", row_click="open", open=Open("flow.plan", {"plan_id": "id"}, "Kế hoạch"),
           desk=Desk("watch", say=_attn_plans, menu=True, rank=125),
           actions=(A("flow.propose_plan", "Lập kế hoạch", style="primary"), A("flow.import_plan", "Nhập kế hoạch từ Excel")),
           row_actions=(A("flow.remind_plan", "Nhắc người đang chậm", bind={"plan_id": "id"}, when={"remind": ["yes"]}),
                        A("flow.set_plan", "Nghiệm thu", bind={"plan_id": "id"}, preset={"status": "done"}, when={"risk": ["review"], "close": ["yes"]},
                          style="primary"))),
    Screen("requests", "Nhờ phòng / thuê ngoài", "arrow.left.arrow.right.square", "flow.requests", tint="orange",
           desk=Desk("decide", say=_attn_requests, rank=105),
           actions=(A("flow.request_team", "Nhờ phòng khác"), A("flow.request_vendor", "Thuê ngoài")),
           row_actions=(A("flow.accept_request", "Nhận & giao người", bind={"request_id": "id"}, when={"mine": ["yes"]}, style="primary"),
                        A("flow.decline_request", "Từ chối", bind={"request_id": "id"}, when={"mine": ["yes"]}, style="destructive"),
                        A("flow.send_brief", "Gửi đề bài (link)", bind={"request_id": "id"}, when={"brief": ["yes"]}, style="primary"))),
    Screen("work_calendars", "Ngày làm việc & nghỉ lễ", "calendar", "flow.work_calendars", tint="gray", needs_action=True, group="Thiết lập",   # rà vai 18/11/2026: màn THIẾT LẬP — chỉ người sửa được thấy trên menu (người đọc dùng kết quả ở màn việc)
           actions=(A("flow.set_work_calendar", "Đặt lịch", style="primary"),),
           row_actions=(A("flow.set_work_calendar", "Đổi lịch", bind={"team_code": "code"}),)),
), order=80))
m.screens("work", (
    Screen("flow_templates", "Mẫu quy trình", "list.bullet.rectangle", "flow.templates", needs_action=True, group="Thiết lập",   # rà vai 18/11/2026: màn THIẾT LẬP — chỉ người sửa được thấy trên menu (người đọc dùng kết quả ở màn việc)
           actions=(A("flow.set_template", "Thêm quy trình", style="primary"),), open=Open("flow.template", {"key": "key"}, "Xem quy trình")),
), rank=20)
# LUẬT TỰ ĐỘNG (0090: "Nếu… thì…" do quản trị đặt) — ở Quản trị › Hệ thống (anh Anh chốt 19/11/2026): luật chạy xuyên phân hệ, người đặt là quản trị; tên cũ
# "Tự động hoá" không nói được bên trong là gì. Khoá màn giữ nguyên.
m.screens("admin", (
    Screen("automations", "Luật tự động", "bolt.fill", "flow.automations", group="Hệ thống",
           actions=(A("flow.save_automation", "Đặt luật", style="primary"),), open=Open("flow.automation", {"automation_id": "id"}, "Luật")),
), rank=12)


# ── Tìm nhanh ⌘K (0087) ──

@m.search("search_plans", "Kế hoạch", resource="flow", rank=50)
async def search_plans(ctx: Ctx, inp: SearchIn) -> list[dict[str, Any]]:
    """Tên / mục tiêu kế hoạch — trong phạm vi người tìm xem được (RLS); có dấu hay không, từng từ; tên khớp hơn và kế hoạch đang mở lên trước."""
    t = SearchTerms(inp.q)
    rows = await ctx.all(f"""SELECT id, title, status, due_on FROM flow.plans WHERE {t.match("title", "goal")}
                             ORDER BY {t.rank("title")}, (status = 'open') DESC, due_on LIMIT %(_n)s""", t.params)
    return [{"title": r["title"], "subtitle": f"Hạn {r['due_on']:%d/%m/%Y}" if r["due_on"] else "", "badge": PLAN_STATUS.get(r["status"], r["status"]),
             "tone": "accent" if r["status"] == "open" else "muted", "open": {"query": "flow.plan", "input": {"plan_id": str(r["id"])}}}
            for r in rows]


@m.search("search_tasks", "Việc", resource="flow", rank=55)
async def search_tasks(ctx: Ctx, inp: SearchIn) -> list[dict[str, Any]]:
    """Tên việc — việc chưa xong lên trước, rồi tên khớp hơn; trong phạm vi người tìm xem được (RLS); có dấu hay không, từng từ."""
    t = SearchTerms(inp.q)
    rows = await ctx.all(f"""SELECT id, title, status, due_on FROM flow.tasks WHERE {t.match("title")}
                             ORDER BY (status = 'done'), {t.rank("title")}, due_on NULLS LAST LIMIT %(_n)s""", t.params)
    return [{"title": r["title"], "subtitle": f"Hạn {r['due_on']:%d/%m/%Y}" if r["due_on"] else "", "badge": TASK_STATUS.get(r["status"], r["status"]),
             "tone": "muted" if r["status"] == "done" else "accent", "open": {"query": "flow.task", "input": {"task_id": str(r["id"])}}}
            for r in rows]
