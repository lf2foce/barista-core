"""KHÁCH — năng lực dùng chung (0110): khách hàng, lead (khách × sản phẩm quan tâm: lead → tiềm năng → giao dịch / mất), chăm sóc, lịch hẹn, nhắc việc.
Phần của ngành (danh mục hàng, giao dịch, đợt đóng tiền, gợi ý hàng) cắm vào qua điểm cắm ``crm.*``; mỗi khách có một định danh ở ``parties`` (0111).

Một SĐT = một khách trong công ty. Sale nhập trùng khách của người khác ⇒ báo "đã có chủ" mà KHÔNG lộ là của ai (tranh
khách là chuyện hằng ngày ở sàn — trưởng nhóm chuyển bằng ``crm.assign``).
Marketing không đọc bảng này; số đếm theo chiến dịch lấy qua ``crm.lead_stats`` (nội bộ, chỉ trả con số).

Mã (0088): ``__init__`` (gói, module, từ vựng, hằng dùng chung, lịch sử chặng) · ``identity`` (một SĐT một khách, định danh, ai thấy khách / lead,
tìm nhanh) · ``leads`` (thêm lead, danh sách, chăm sóc, nhu cầu, chặng, chuyển người, việc hôm nay) · ``deal_link`` (gắn khách với giao dịch) ·
``routing`` (hợp đồng cho chia lead) · ``reports`` (số đếm cho marketing / điều hành / sức khoẻ dữ liệu, chỉ số mục tiêu) · ``journey`` (thẻ khách:
hành trình, liên hệ, đồng ý) · ``morning`` (thẻ buổi sáng) · ``agenda`` (lịch hẹn, nhắc hẹn, gợi ý đặt lịch) · ``screens`` (màn hình) · ``calls``
(hợp đồng cho tổng đài: SĐT → lead, cuộc gọi thật thành chăm sóc — 0149) · ``imported`` (nạp chăm sóc từ hệ khác giữ giờ gốc — 0174) · ``my_month``
(tổng kết tháng của chính người hỏi — công cụ của kỹ năng trợ lý, 0198).
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from barista.api import Ctx, Hints, InfoGroup, Module, NotFound, Pack, Role, vnd_text

# Gói 'crm' tự khai (0069, 0110): năng lực dùng chung — ngành nào cũng có khách, lead, chăm sóc, lịch hẹn. Phần ngành cắm vào qua điểm cắm ``crm.*``.
Pack("crm", "Khách hàng",
     "Khách, lead, chăm sóc, lịch hẹn, chia lead và hạn xử lý — dùng được cho mọi ngành",
     kind="capability",
     requires=("directory", "schedule"),   # lịch hẹn nằm trên lịch chung (0158): giờ + người ở schedule, phần nghiệp vụ ở crm.appointments
     # 0239 phần 3: tên + SĐT khách là quyền đọc Khách & lead; số ĐẾM lead (Marketing, báo cáo) không cần quyền này
     info_groups=(InfoGroup("customers", "Khách & lead (tên, SĐT, lịch sử chăm sóc)", "crm",
                            "Mọi màn khách / lead và tên khách nghi trùng ở Sức khoẻ dữ liệu; số đếm lead trên màn Marketing / báo cáo không cần quyền này"),),
     roles={
         # bộ công cụ trợ lý mặc định (0218): công cụ việc hằng ngày của vai — nhóm khác trợ lý mở khi câu hỏi cần
         "team_lead": Role("Trưởng nhóm", ai_tier="manager",
                           tools=("crm.brief", "crm.reminders", "crm.leads", "crm.journey", "crm.assign", "leadflow.late", "leadflow.queue",
                                  "leadflow.overview", "leadflow.scorecard", "leadflow.explain", "leadflow.reassign", "flow.plans", "flow.plan",
                                  "flow.tasks", "flow.adjust_task", "schedule.week",
                                  # kế hoạch & giao việc bằng lời (0222): lập kế hoạch, giao một việc từ câu nói, xếp lịch, nhắc người chậm
                                  "flow.propose_plan", "flow.assignees", "flow.resolve_assignment", "flow.create_task", "flow.set_task_timing", "flow.remind_plan"),
                           home=("sales", ("leads",)),
                           picker=("Quản lý", "Chỉ thấy đội mình phụ trách (kể cả các đội bên dưới), không thấy tiền công ty.", "Đội tôi đang để rơi lead nào?"),
                           grants=(("core.ai_usage", "team", "read"), ("crm", "team", "read write manage"),
                                   ("ledger", "own", "read"), ("acct", "own", "read"), ("kb.common", "all", "read"),
                                   ("papers", "team", "read write"), ("work", "team", "read write"),
                                   ("leadflow", "team", "read write"), ("flow", "team", "read write"),
                                   ("flow.templates", "all", "read"), ("talk", "team", "read write"),
                                   ("training", "team", "read write"), ("training.manage", "team", "read write"),),
                           suggest=(("crm.reminders", "Đội tôi hôm nay có việc gì đến hạn?"),
                                    ("crm.leads", "Khách nào trong đội lâu chưa được chăm sóc?")),
                           showcase=(("Ai trong đội đang để khách quá hạn chăm sóc, và hồ sơ giao dịch nào của đội đang kẹt?",
                                      ("crm.brief", "flow.needs_action")),)),
         "sale": Role("Sale", ai_tier="basic", entry=True,
                      tools=("crm.brief", "crm.reminders", "crm.leads", "crm.journey", "crm.my_month", "crm.agenda", "crm.create_lead", "crm.log_activity",
                             "crm.update_interest", "crm.schedule", "crm.set_stage", "leadflow.mine", "leadflow.accept", "leadflow.scorecard", "flow.tasks"),
                      home=("sales", ("leads",)),
                      picker=("Nhân viên", "Chỉ thấy lead và giao dịch của chính mình.", "Hôm nay tôi nên gọi lead nào trước?"),
                      grants=(("crm", "own", "read write"),
                              ("ledger", "own", "read"), ("acct", "own", "read"), ("kb.common", "all", "read"),
                              ("papers", "own", "read write"), ("work", "own", "read write"),
                              ("leadflow", "own", "read write"), ("flow", "own", "read write"),
                              ("flow.templates", "all", "read"), ("talk", "own", "read write"),
                              ("training", "own", "read write"),),
                      suggest=(("crm.reminders", "Hôm nay tôi cần làm gì?"),
                               ("crm.leads", "Khách nào đến hạn gọi lại hôm nay?"))),
         # nhập liệu kinh doanh — KHÔNG quản trị hệ thống (sửa quyền / luật duyệt là của vai admin, 0029 / 0073)
         # quản lý giao dịch (0151): phân hệ "Quản lý giao dịch" của gói Hoa hồng lên đầu nếu công ty bật — không có thì bỏ qua, về Nhập liệu
         "sales_admin": Role("Sales admin", ai_tier="manager",
                             tools=("crm.leads", "crm.create_lead", "crm.assign", "leadflow.queue", "leadflow.assign", "leadflow.overview", "leadflow.explain"),
                             home=(("deal_admin", "admin"), ("to_confirm", "leads_entry")),
                             picker=("Chức năng", "Quản lý giao dịch (xác nhận người bán, tỷ lệ hoa hồng) và nhập liệu kinh doanh — không sửa quyền.",
                                     "Giao dịch nào đang chờ tôi xác nhận?"),
                             grants=(("crm", "all", "read write"), ("leadflow", "all", "read write"), ("kb.common", "all", "read"),
                                     ("papers", "team", "read write"), ("flow", "team", "read write"), ("flow.templates", "all", "read"),
                                     ("talk", "team", "read write"), ("training", "own", "read write"),)),
         "coordinator": Role("Điều phối lead", ai_tier="basic", tools=("leadflow", "crm.leads", "crm.create_lead"),
                             home=("sales", ("leads", "overview", "campaigns")),
                             picker=("Chức năng", "Team Digital: nạp lead nguồn ngoài, chia cho sale (có gợi ý người nhận) — không thấy tiền.", "Còn lead nào đang chờ tôi chia?"),
                             grants=(("crm", "team", "read write"),
                                     ("leadflow", "team", "read write"),
                                     ("kb.common", "all", "read"), ("work", "own", "read write"),
                                     ("flow", "own", "read write"), ("talk", "team", "read write"),
                                     ("training", "own", "read write"),),
                             suggest=(("leadflow.queue", "Còn lead nào đang chờ tôi chia?"),
                                      ("leadflow.queue", "Chia giúp tôi các lead đang chờ theo gợi ý"),
                                      ("crm.leads", "Lead tôi chia hôm qua đã được gọi chưa?"))),
     },
     hints={"*": Hints(suggest=(("crm.reminders", "Hôm nay tôi cần làm gì?"), ("crm.leads", "Khách nào đến hạn gọi lại hôm nay?")))},
     tools={"director": ("crm.ds_leads", "leadflow.overview")},   # góp vào bộ công cụ trợ lý của giám đốc (0218)
     grants=(("admin", "crm", "all", "read write manage"), ("admin", "leadflow", "all", "read write manage"),
             ("director", "crm", "all", "read manage"), ("director", "leadflow", "all", "read write manage"),),
     # chặng mặc định của lead (0153) — công ty đổi tên / thêm / tắt ở Khách hàng › Chặng; giai đoạn của từng chặng cố định
     records=tuple(("crm.save_stage", k, {"key": k, "label": label, "phase": phase, "sort": sort, "only_new": True}) for k, label, phase, sort in (
         ("new", "Mới", "lead", 10), ("contacting", "Đang liên hệ", "lead", 20), ("interested", "Quan tâm", "prospect", 30),
         ("met", "Đã gặp / tư vấn", "prospect", 40), ("negotiating", "Đang thương lượng", "prospect", 60), ("won", "Đã chốt giao dịch", "deal", 90),
         ("lost", "Mất", "lost", 100), ("unreachable", "Không liên lạc được", "lost", 110), ("not_interested", "Không còn nhu cầu", "lost", 120)))
     # loại lịch của hẹn khách (0158) — công ty đổi tên / tắt ở Lịch › Loại lịch
     + tuple(("schedule.save_kind", k, {"key": k, "label": label, "sort": sort, "tone": tone, "only_new": True}) for k, label, sort, tone in (
         ("customer_meeting", "Hẹn khách", 20, "blue"), ("site_visit", "Tham quan", 30, "green"), ("callback", "Hẹn gọi", 35, "orange"))),
     rules="""- Người dùng KỂ LẠI buổi gặp / cuộc gọi với khách ⇒ (1) chưa rõ khách nào thì core__find (tìm nhanh — nhóm "Khách"); (2) rồi trong CÙNG MỘT LƯỢT gọi song song mọi
  việc cần ghi: crm__log_activity (hình thức đúng, nội dung tóm tắt 1–2 câu, hẹn chăm sóc lại nếu có), crm__update_interest nếu khách
  nói nhu cầu mới (chỉ điền ô thay đổi; ý khác ghi vào "Mong muốn"), crm__schedule nếu có hẹn gặp / tham quan (ngày giờ cụ thể).
""")

m = Module("crm", "Khách hàng", pack="crm", resources={"crm": "Khách hàng & lead"})
m.consumes("plug")
# Từ vựng (0124): MỘT khái niệm cho "thứ khách quan tâm" — chữ hiện ra do công ty đặt (gói ngành mang nhãn mặc định). Cột / khoá vẫn tên cũ (N2 mới đổi).
m.term("offering", "Sản phẩm", "thứ khách quan tâm và công ty bán")
m.trigger("crm.lead_created", "Có lead mới", {"source": "Nguồn", "offering_code": "[[offering]] quan tâm"}, open=("crm.journey", {"lead_id": "lead_id"}))
# Sự kiện công bố cho app từ xa (0140): chỉ mang mã lead — app gọi crm.lead_status bằng quyền của nó để đọc
m.webhook("lead.assigned", "crm.lead_assigned", ids=("lead_id",), resource="crm", label="Lead được giao người phụ trách")
m.webhook("lead.stage_changed", "crm.lead_stage_changed", ids=("lead_id",), resource="crm", label="Lead đổi chặng")

STAGES = {"lead": "Lead", "prospect": "Tiềm năng", "deal": "Giao dịch", "lost": "Mất"}
STAGE_TONES = {"lead": "blue", "prospect": "purple", "deal": "green", "lost": "gray"}
ACTIVITY = {"call": "Gọi điện", "meet": "Gặp", "zalo": "Zalo", "email": "Email", "visit": "Tham quan",
            "send_profile": "Gửi tài liệu", "note": "Ghi chú"}


OUTCOMES = {"": "—", "connected": "Nói chuyện được", "no_answer": "Không nghe máy", "busy": "Máy bận", "wrong_number": "Sai số"}
# Lịch hẹn: thẻ khách, thẻ buổi sáng và lịch cùng dùng
APPT_KIND = {"meet": "Hẹn gặp", "visit": "Tham quan", "call": "Hẹn gọi"}
APPT_STATUS = {"scheduled": "Sắp tới", "done": "Đã xong", "cancelled": "Đã huỷ", "missed": "Khách không tới"}
# Lịch hẹn trên LỊCH CHUNG (0158): giờ + người ở schedule.events (nguồn crm.appointment), phần nghiệp vụ (loại, kết quả, lead, địa điểm, ghi chú) ở đây
APPT_SOURCE = "crm.appointment"
APPT_CAL_KIND = {"meet": ("customer_meeting", "Hẹn khách"), "visit": ("site_visit", "Tham quan"), "call": ("callback", "Hẹn gọi")}
APPT_MINUTES = {"meet": 60, "visit": 90, "call": 15}   # độ dài mặc định khi người đặt không nêu


async def book_appointment(ctx: Ctx, appt: dict[str, Any], starts: datetime, ends: datetime, *, by: UUID | None = None) -> dict[str, Any]:
    """Giữ giờ của MỘT hẹn trên lịch chung (khai báo — gọi lại là cập nhật): người tham dự = sale đang phụ trách lead (chưa ai ⇒ người đặt), bên ngoài
    = định danh của khách, riêng tư (tên khách trong tiêu đề chỉ người trong lịch thấy; người khác thấy "Bận"). Trùng giờ với lịch khác của sale chỉ
    là CẢNH BÁO (``warnings``) — sale là người tham dự, không phải nguồn lực giữ cứng. Bên gọi đã đọc hẹn / lead bằng RLS của người làm."""
    lead = await ctx.one("""SELECT l.owner_id, l.team_id, c.name, c.party_id FROM crm.leads l JOIN crm.customers c ON c.id = l.customer_id WHERE l.id = %s""",
                         (appt["lead_id"],))
    if lead is None:
        raise NotFound("Không thấy lead của lịch hẹn")
    who = lead["owner_id"] or by or ctx.p.user_id
    kind, label = APPT_CAL_KIND[appt["kind"]]
    return await ctx.call_system("schedule.book", source=APPT_SOURCE, source_ref=str(appt["id"]), title=f"{APPT_KIND[appt['kind']]} — {lead['name']}",
                                 starts_at=starts, ends_at=ends, team_id=lead["team_id"], kind=kind, kind_label=label,
                                 attendee_user_ids=[who] if who else [], accepted_user_ids=[who] if who and who == ctx.p.user_id else [],
                                 party_ids=[lead["party_id"]] if lead["party_id"] else [], visibility="private", notify=False)


async def appointment_times(ctx: Ctx, ids: list[Any], *, since: datetime | None = None, until: datetime | None = None,
                            cancelled: bool = False) -> dict[str, dict[str, Any]]:
    """GIỜ của các hẹn — đọc từ lịch chung (một nguồn), cho đúng các hẹn bên gọi đã đọc bằng RLS. ``since`` / ``until``: chỉ hẹn chạm khoảng đó."""
    if not ids:
        return {}
    got = await ctx.call_system("schedule.times_of", source=APPT_SOURCE, refs=[str(i) for i in ids], since=since, until=until, include_cancelled=cancelled)
    return got["times"]


async def _stage_event(ctx: Ctx, lead_id: Any, kind: str, frm: str | None = None, to: str | None = None, reason: str = "",
                       data: dict[str, Any] | None = None) -> None:
    """Lịch sử chuyển chặng CHỈ THÊM — ai, lúc nào, từ đâu tới đâu, vì sao (migration 110)."""
    await ctx.exec("INSERT INTO crm.stage_events (lead_id, kind, from_stage, to_stage, by_user, reason, data) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                   (lead_id, kind, frm, to, ctx.p.user_id, reason, Jsonb(data or {})))
    if kind == "assigned":   # khách đổi người phụ trách ⇒ thứ gắn vào khách (hồ sơ quy trình, mục nhớ — 0091) cập nhật theo (quy ước m.subject)
        ctx.emit("subject.changed", kind="crm.lead", id=lead_id)
        if (data or {}).get("to"):   # có người nhận (về hàng chờ thì không) — MỘT chỗ ghi lịch sử giao ⇒ mọi đường giao đều phát
            ctx.emit("crm.lead_assigned", lead_id=lead_id, owner_id=(data or {})["to"])
    elif kind == "stage":
        ctx.emit("crm.lead_stage_changed", lead_id=lead_id, from_stage=frm, to_stage=to)


async def _set_owner(ctx: Ctx, lead_id: Any, owner: UUID | None, *, team: UUID | None = None, reason: str = "",
                     data: dict[str, Any] | None = None) -> UUID | None:
    """MỘT chỗ duy nhất ĐỔI người phụ trách một lead (0153 — "ai giữ lead" có một nguồn: ``crm.leads.owner_id``). Khách của lead theo người phụ
    trách mới (lead về hàng chờ: chỉ đặt đội cho khách chưa có chủ), lịch sử chặng ghi "assigned". Trạng thái chia lead (``leadflow.lead_state``)
    KHÔNG tự đổi ở đây: bên gọi là chia lead (đã ghi phần của mình) hoặc đã gọi ``leadflow.follow_owner`` trong CÙNG giao dịch, TRƯỚC khi gọi hàm
    này (thứ tự khoá: trạng thái chia rồi mới tới lead — không khoá chéo với vòng quét). ``tests/test_lead_safety.py`` chặn chỗ ghi thứ hai."""
    prev = await ctx.one("SELECT owner_id FROM crm.leads WHERE id = %s FOR UPDATE", (lead_id,))
    if prev is None:
        raise NotFound("Không thấy lead")
    team = await ctx.home_team(owner) if owner else team
    cid = await ctx.val("UPDATE crm.leads SET owner_id = %s, team_id = %s, updated_at = now() WHERE id = %s RETURNING customer_id", (owner, team, lead_id))
    if cid is None:
        raise NotFound("Không thấy lead")
    if owner:
        await ctx.exec("UPDATE crm.customers SET owner_id = %s, team_id = %s WHERE id = %s", (owner, team, cid))
    else:   # lead về hàng chờ: khách chưa có chủ thì cho đội hàng chờ thấy khách (không thì người điều phối thấy lead mà không thấy tên)
        await ctx.exec("UPDATE crm.customers SET team_id = %s WHERE id = %s AND owner_id IS NULL", (team, cid))
    was = prev["owner_id"]
    if owner != was:   # lịch hẹn đi theo khách (0158): hẹn sắp tới chuyển sang lịch của người phụ trách mới, giữ nguyên giờ
        appts = await ctx.all("SELECT id, lead_id, kind FROM crm.appointments WHERE lead_id = %s AND status = 'scheduled'", (lead_id,))
        times = await appointment_times(ctx, [a["id"] for a in appts], since=datetime.now(UTC) - timedelta(days=1))
        for a in appts:
            if (t := times.get(str(a["id"]))) and t["status"] == "booked":
                await book_appointment(ctx, a, t["starts_at"], t["ends_at"])
    await _stage_event(ctx, lead_id, "assigned", reason=reason,
                       data={"from": str(was) if was else None, "to": str(owner) if owner else None, **(data or {})})
    return was


def interest_text(i: dict[str, Any]) -> str:
    """Nhu cầu thành một câu. ``detail``: phần gói ngành đã viết sẵn thành chữ (CRM không cần biết từng thuộc tính của ngành)."""
    bits = []
    if i.get("detail"):
        bits.append(i["detail"])
    if i.get("budget_vnd"):
        bits.append("≤ " + vnd_text(i["budget_vnd"]))
    if i.get("wishes"):
        bits.append(i["wishes"])
    return " · ".join(bits)




# Thứ tự nạp = thứ tự thao tác trong sổ đăng ký (danh mục công cụ trợ lý / app giữ như khi crm còn là một file) — không sắp lại
# isort: off
from barista.modules.crm import identity, leads, deal_link, routing, reports, journey, morning, agenda, screens, calls, stages, imported, my_month, findings  # noqa: E402, F401
# isort: on
from barista.modules import leadflow  # noqa: E402, F401 — module thứ hai của gói crm (điểm vào gói — 0130)
