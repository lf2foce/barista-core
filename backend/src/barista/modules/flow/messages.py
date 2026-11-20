"""TẠO VIỆC TỪ MỘT TIN NHẮN (0115, 0117 — ghi chú 07/11). ``flow`` cắm vào điểm ``talk.message_actions`` của Trao đổi: nút "Tạo việc" trên từng tin
(chỉ người tạo được việc) và dấu "Đã tạo việc" (chỉ người THẤY việc đó — RLS của ``flow.tasks`` lúc đọc). ``talk`` không biết ``flow``; ``flow`` đọc tin
qua hợp đồng hẹp ``talk.message_ref`` bằng quyền của đúng người đang hỏi.

* Việc mang đối tượng NGHIỆP VỤ của luồng (khách, giao dịch… ⇒ ``subject_*``; hồ sơ / kế hoạch ⇒ cột riêng). Phòng, tin riêng không có đối tượng
  nghiệp vụ ⇒ việc không có đối tượng. Việc nhớ tin gốc (``source_kind = 'talk.message'``) để dẫn ngược lại.
* Người nhận phải ĐỌC ĐƯỢC trao đổi đó (kiểm bằng quyền của chính họ, nạp lại từ DB) — giao việc không thành cửa sau để xem tin.
* Tiêu đề việc ai thấy việc cũng đọc được ⇒ chỉ điền sẵn chữ tin khi luồng nằm trên một đối tượng (việc mang chính đối tượng đó); tin riêng / phòng
  (người đọc là một danh sách) không điền sẵn — người tạo tự viết. Dấu "Đã tạo việc" trên tin đã thu hồi không mang tiêu đề việc.
* Không gắn bộ kiểm: việc người tạo không bao giờ cần bộ kiểm (0117, ghi chú 07/11).
* Trợ lý chỉ ĐỀ XUẤT (``agent="propose"``): thẻ đề xuất trong chat riêng của người hỏi, người bấm mới tạo; không gửi gì vào luồng chung (0046).
"""

import re
from typing import Any
from uuid import UUID

from barista.api import Ctx, Day, Input, Invalid, NotFound, field, ui
from barista.kernel import registry
from barista.modules.flow import PLAN_SUBJECT, TASK_STATUS, TASK_SUBJECT, _task_open, m
from barista.modules.flow.tasks import _assignee, _calendar_note, _check_subject, _new_task, _ranked, source_readable

SOURCE = "talk.message"
ASSIGNEE_CHECKS = 40   # trần số người kiểm "đọc được trao đổi" mỗi lần mở form
TITLE_MAX = 300


def _title(body: str) -> str:
    """Tiêu đề điền sẵn = chữ tin trên một dòng, cắt ở ``TITLE_MAX`` (người sửa trên form trước khi tạo)."""
    t = re.sub(r"\s+", " ", body).strip()
    return t if len(t) <= TITLE_MAX else t[:TITLE_MAX - 1].rstrip() + "…"


class MessageBrief(Input):
    id: int
    body: str
    recalled: bool


class MessagesIn(Input):
    """Cùng hình đầu vào của điểm cắm ``talk.message_actions`` (module chủ khai)."""
    subject_kind: str
    subject_id: UUID
    thread_id: UUID
    members_only: bool
    messages: list[MessageBrief]


@m.plug("talk.message_actions", "Tạo việc từ tin nhắn", resource="flow")
async def plug_message_actions(ctx: Ctx, inp: MessagesIn) -> list[dict[str, Any]] | None:
    """Bằng quyền NGƯỜI XEM: dấu "Đã tạo việc" cho tin có việc người này thấy (RLS — không thấy việc ⇒ không có dấu, không lộ là có); nút "Tạo việc"
    trên tin còn chữ khi người này tạo được việc."""
    ids = [str(x.id) for x in inp.messages]
    marks: dict[str, list[dict[str, Any]]] = {}
    recalled = {str(x.id) for x in inp.messages if x.recalled}
    for t in await ctx.all("""SELECT id, title, status, source_ref FROM flow.tasks WHERE source_kind = %s AND source_ref = ANY(%s)
                              ORDER BY created_at""", (SOURCE, ids)):
        # tin đã thu hồi: chỉ trạng thái — tiêu đề việc có thể chép từ chữ gốc (talk cũng bỏ chữ chi tiết của dấu trên tin thu hồi)
        detail = TASK_STATUS[t["status"]] if t["source_ref"] in recalled else f"{t['title']} · {TASK_STATUS[t['status']]}"
        marks.setdefault(t["source_ref"], []).append({"title": "Đã tạo việc", "detail": detail,
                                                      "tone": "ok" if t["status"] == "done" else "accent", "open": _task_open(t["id"])})
    can = ctx.p.can("flow", "write")
    out = []
    for x in inp.messages:
        # tiêu đề việc ai thấy việc cũng đọc được (người nhận, trưởng nhóm, giám đốc…). Luồng chỉ một danh sách người đọc (tin riêng, phòng) ⇒ KHÔNG
        # điền sẵn chữ tin: người tạo tự viết tiêu đề. Luồng trên đối tượng: việc mang chính đối tượng đó, điền sẵn để người tạo sửa.
        fill = None if inp.members_only else {"title": _title(x.body)}
        acts = ([ui.act("flow.task_from_message", "Tạo việc", {"message_id": x.id}, values=fill)]
                if can and not x.recalled and x.body.strip() else [])
        if acts or str(x.id) in marks:
            out.append({"message_id": x.id, "actions": acts, "marks": marks.get(str(x.id), [])})
    return out or None


async def _message(ctx: Ctx, mid: int, who: Any = None) -> dict[str, Any] | None:
    """Tin như ``who`` (mặc định người gọi) đọc được — None nếu không đọc được / gói Trao đổi tắt / người đó không có quyền Trao đổi."""
    try:
        out: dict[str, Any] | None = await ctx.call_as_optional("talk.message_ref", who or ctx.p, message_id=mid)
    except NotFound:
        return None
    return out


class TaskFromMessage(Input):
    message_id: int = field("Tin nhắn", ge=1, hidden=True)
    title: str = field("Việc", max_length=TITLE_MAX, example="Gọi lại chị Lan chốt lịch gặp",
                       help="Ai thấy việc đều đọc được tiêu đề. Tin trên hồ sơ: điền sẵn để sửa; tin riêng / phòng: tự viết, đừng chép nguyên tin")
    # tham chiếu người nhận giải ở server (``_assignee``) — hôm nay là email thành viên; thêm loại khác chỉ thêm một dòng ở danh sách chọn
    assignee: str = field("Giao cho", "", options_query="flow.message_assignee_options", options_with=("message_id",),
                          placeholder="Chính bạn", help="Chỉ người xem được trao đổi này")
    due_on: Day | None = field("Hạn", None)
    notes: str = field("Ghi chú", "", kind="textarea", max_length=4000)


@m.command("task_from_message", resource="flow", title="Tạo việc từ tin nhắn", agent="propose")
async def task_from_message(ctx: Ctx, inp: TaskFromMessage) -> dict[str, Any]:
    """Một tin trong trao đổi ⇒ một việc: đối tượng nghiệp vụ của luồng thành đối tượng của việc (phòng / tin riêng ⇒ không có), việc dẫn về tin gốc,
    tin hiện "Đã tạo việc" cho người thấy việc. Người nhận phải đọc được trao đổi. Không bộ kiểm. Trợ lý chỉ đề xuất: người bấm mới tạo."""
    ref = await _message(ctx, inp.message_id)
    if ref is None:
        raise NotFound("Không thấy tin nhắn (hoặc anh/chị không xem được trao đổi này)")
    if ref["recalled"]:
        raise Invalid("Tin đã thu hồi — không tạo việc từ tin này")
    if ref["members_only"] and _title(inp.title).casefold() == _title(ref["body"]).casefold():
        raise Invalid("Tin riêng / tin trong phòng chỉ người trong cuộc đọc được, còn tiêu đề việc thì ai thấy việc cũng đọc — viết tiêu đề riêng, "
                      "đừng chép nguyên tin")
    assignee = await _assignee(ctx, inp.assignee, None)
    # cùng luật với giao lại (``adjust_task``): người làm phải đọc được trao đổi chứa tin
    if assignee is not None and assignee != ctx.p.user_id and not await source_readable(ctx, SOURCE, str(inp.message_id), assignee):
        name = await ctx.val("SELECT name FROM core.users WHERE id = %s", (assignee,))
        raise Invalid(f"{name} không xem được trao đổi này — chỉ giao được cho người xem được (nhờ quản lý giao quyền nếu cần)")
    kind, sid = ref["about_kind"], ref["about_id"]
    case_id = plan_id = subject_id = None
    subject_kind, about = "", ""
    if kind == "flow.case":
        case_id = sid
    elif kind == PLAN_SUBJECT:
        plan_id = sid if await ctx.val("SELECT 1 FROM flow.plans WHERE id = %s AND status = 'open'", (sid,)) else None
    elif kind and kind != TASK_SUBJECT:   # việc không trỏ việc
        subj = await _check_subject(ctx, kind, sid)
        subject_kind, subject_id = kind, sid
        about = f" — {registry.SUBJECTS[kind][0].lower()} {subj['title']}" if subj else ""
    made = await _new_task(ctx, title=inp.title, notes=inp.notes, assignee=assignee, team=None, due_on=inp.due_on, case_id=case_id, plan_id=plan_id,
                           subject_kind=subject_kind, subject_id=subject_id, source=(SOURCE, str(inp.message_id)))
    # máy đang mở luồng vẽ lại (dấu "Đã tạo việc" tính lại bằng quyền từng người xem); không có quyền báo / gói tắt ⇒ lần tải sau mới thấy
    await ctx.call_optional("talk.cards_changed", thread_id=ref["thread_id"])
    return {"task_id": made["task_id"], "calendar_warnings": list(made["warnings"]),
            "summary": f"Việc “{inp.title}”{about} → {made['who']}{made['when']}" + _calendar_note(made["warnings"])}


class MessageIn(Input):
    message_id: int = field("Tin nhắn", ge=1, hidden=True)


@m.query("message_assignee_options", resource="flow", title="Người nhận việc từ tin nhắn")
async def message_assignee_options(ctx: Ctx, inp: MessageIn) -> list[dict[str, str]]:
    """Ô "Giao cho" của form: người bạn giao việc được (xếp theo khối lượng như "Ai nên nhận việc") VÀ đọc được trao đổi chứa tin — kiểm bằng quyền
    của từng người. Chỉ để chọn; lệnh kiểm lại."""
    if await _message(ctx, inp.message_id) is None:
        return []
    out = []
    checked = 0
    for r in await _ranked(ctx, ""):
        if r["id"] != ctx.p.user_id:
            # mỗi người một lần nạp quyền + đọc tin ⇒ chặn trần: công ty lớn chỉ xét ``ASSIGNEE_CHECKS`` người ít việc nhất (xếp sẵn), form vẫn
            # nhanh; lệnh luôn kiểm lại người được chọn
            if checked >= ASSIGNEE_CHECKS:
                continue
            checked += 1
            who = await ctx.principal_of(r["id"])
            if who is None or await _message(ctx, inp.message_id, who) is None:
                continue
        load = f"{r['open']} việc mở" + (f", {r['overdue']} quá hạn" if r["overdue"] else "")
        out.append({"value": r["email"], "label": " · ".join(x for x in (r["name"] + (" (bạn)" if r["id"] == ctx.p.user_id else ""), r["team"], load) if x)})
    return out
