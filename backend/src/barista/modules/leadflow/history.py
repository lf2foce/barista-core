"""NẠP LỊCH SỬ CHIA LEAD từ hệ khác (quyết định 0168) — hai lệnh nhập hàng loạt, chỉ quản lý chia lead cả công ty, trợ lý không chạy:

* ``import_history``: MỘT dòng = một KHOẢNG GIỮ lead (ai giữ từ lúc nào tới lúc nào, vì sao bắt đầu / kết thúc) hoặc một sự kiện lẻ không gắn được
  vào khoảng nào. Ghi vào ``leadflow.history`` (bảng riêng, ``source = 'import'``) — KHÔNG ghi ``sla_events`` (sổ việc Barista tự làm theo luật của
  nó: chấm điểm, xoay vòng, sức khoẻ đọc sổ đó), không đổi người giữ, không thông báo, không vòng quét. Chống lặp theo (hệ nguồn, mã dòng nguồn).
* ``import_state``: trạng thái chia HIỆN TẠI của một lead (đang chờ gọi / đã liên hệ / hàng chờ / đóng, hạn gọi, số lần thu hồi, ai từng giữ) vào
  ``leadflow.lead_state``, đánh dấu ``imported_at``. Người giữ = người phụ trách bên Khách (MỘT nguồn — 0153), không lấy từ file; ô "người đang
  giữ" trong file chỉ để đối chiếu. Trạng thái nạp là THAM CHIẾU — lead ĐỖ (0185): vòng quét không chấm hạn, không nhắc, không báo trưởng nhóm, không
  thu hồi, không chia lại, dù hạn đã qua bao lâu (dữ liệu hệ cũ không khởi động việc hôm nay). Lead thành lead SỐNG khi Barista tự chia nó — quản lý
  giao từ hàng chờ / chuyển tay, đổi người phụ trách lead chưa liên hệ, khoá tài khoản người giữ (sự kiện thật): dấu ``imported_at`` xoá, hạn tính từ
  lúc đó. Không ghi đè trạng thái Barista đã tự chia.

Lead tìm theo mã Barista, hoặc theo (hệ nguồn, mã lead ở hệ nguồn) đã đăng ký ở ``leadflow.external_refs`` (cùng sổ mã ngoài với cửa nạp lead);
dòng có cả hai thì đăng ký mã ngoài cho lead đó. Giờ phải có múi giờ (ISO 8601 có ``+07:00`` / ``Z``) — hệ cũ lưu giờ không múi giờ thì lớp nạp đổi
trước (0168 §3)."""

import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import AwareDatetime

from barista.api import Conflict, Ctx, Denied, Input, Invalid, NotFound, add_working_minutes, field, today, ui
from barista.modules import leadflow as lf
from barista.modules.leadflow import _rule_for, clock_of, due_in, hours_of, m
from barista.modules.leadflow.routing import _lead_capable, _snapshot, put_imported

KINDS = {"held": "Khoảng giữ lead", "recalled": "Thu hồi", "assigned": "Được giao", "handed_over": "Chuyển người giữ", "queued": "Vào hàng chờ",
         "first_call_met": "Liên hệ lần đầu", "first_call_breached": "Quá hạn gọi", "followup_breached": "Quá hạn chăm sóc lại"}
STARTED = {"": "Không rõ", "rule": "Chia theo luật / xoay vòng", "designated": "Chỉ định người nhận", "manual": "Quản lý giao tay",
           "retry": "Chia lại người được chỉ định"}
ENDED = {"": "Đang giữ", "recalled": "Bị thu hồi", "handed_over": "Chuyển cho người khác", "ended": "Thôi giữ (hệ nguồn không ghi lý do)"}
STATES = {"waiting": "Chờ liên hệ lần đầu", "contacted": "Đã liên hệ", "queued": "Hàng chờ", "closed": "Đã đóng"}
QUEUE = {"": "—", "recall_limit": "Đã thu hồi hết số lần", "no_eligible": "Không có ai đủ điều kiện nhận", "no_rule": "Chưa có luật chia",
         "coordinator": "Chờ đội điều phối", "team": "Giao cho đội chỉ định"}
ORIGIN = r"^[a-z][a-z0-9_]{1,39}$"


class LeadRefIn(Input):
    lead_id: UUID | None = field("Mã lead (Barista)", None, help="Trống ⇒ tìm theo Hệ nguồn + Mã lead ở hệ nguồn đã đăng ký")
    origin: str = field("Hệ nguồn", max_length=40, example="he_cu", help="Mã ngắn của hệ cũ (chữ thường, số, _) — cùng mã cho mọi dòng")
    lead_ref: str = field("Mã lead ở hệ nguồn", "", max_length=120, example="1024",
                          help="Có cả mã Barista ⇒ đăng ký mã này cho lead (dòng sau chỉ cần mã nguồn)")


def _manager(ctx: Ctx) -> None:
    if not ctx.p.has_scope("leadflow", "all"):
        raise Denied("Nạp lịch sử chia lead là việc của quản lý chia lead cả công ty")


async def _lead(ctx: Ctx, inp: LeadRefIn) -> UUID:
    """Lead người nạp THẤY (RLS của lead_state / history kiểm lại lúc ghi): theo mã Barista (đăng ký mã nguồn nếu có), hoặc theo mã nguồn đã đăng ký."""
    if not re.fullmatch(ORIGIN, inp.origin):
        raise Invalid("Hệ nguồn: chữ thường không dấu, số, _ (2–40 ký tự), bắt đầu bằng chữ")
    if inp.lead_id is None:
        if not inp.lead_ref:
            raise Invalid("Cần Mã lead (Barista) hoặc Mã lead ở hệ nguồn")
        lid = await ctx.val("SELECT lead_id FROM leadflow.external_refs WHERE provider = %s AND ref = %s", (inp.origin, inp.lead_ref))
        if lid is None:
            raise NotFound(f"Chưa có lead nào mang mã {inp.lead_ref} của hệ {inp.origin}")
        return lid
    if not (await ctx.call_as("crm.lead_cards", ctx.p, lead_ids=[inp.lead_id])):
        raise NotFound("Không thấy lead này")
    if inp.lead_ref:
        got = await ctx.val("""INSERT INTO leadflow.external_refs (provider, ref, lead_id) VALUES (%s, %s, %s)
                               ON CONFLICT (tenant_id, provider, ref) DO NOTHING RETURNING lead_id""", (inp.origin, inp.lead_ref, inp.lead_id))
        if got is None:
            got = await ctx.val("SELECT lead_id FROM leadflow.external_refs WHERE provider = %s AND ref = %s", (inp.origin, inp.lead_ref))
        if got != inp.lead_id:
            raise Conflict(f"Mã {inp.lead_ref} của hệ {inp.origin} đã gắn với một lead khác")
    return inp.lead_id


async def _person(ctx: Ctx, email: str) -> UUID | None:
    """Người TỪNG là thành viên công ty (kể cả đã nghỉ — lịch sử vẫn là của họ)."""
    if not email:
        return None
    return await ctx.val("SELECT u.id FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant()"
                         " WHERE u.email = %s", (email.strip().lower(),))


def _past(at: datetime | None, what: str) -> None:
    if at is not None and at > lf.clock():
        raise Invalid(f"{what} ở tương lai — lịch sử chỉ nhận chuyện đã xảy ra")


class HistoryIn(LeadRefIn):
    ext_ref: str = field("Mã dòng ở hệ nguồn", max_length=120, example="a-88231", help="Duy nhất trong hệ nguồn — nạp lại cùng mã không nhân đôi")
    kind: Literal["held", "recalled", "assigned", "handed_over", "queued", "first_call_met", "first_call_breached", "followup_breached"] = field(
        "Loại", "held", options=KINDS)
    user_email: str = field("Người (email)", "", max_length=200, help="Người giữ / bị thu hồi / được giao — người đã nghỉ vẫn nhận nếu từng là thành viên")
    user_ref: str = field("Mã người ở hệ nguồn", "", max_length=120, help="Khi không có email khớp thành viên: giữ mã cũ, không bỏ dòng")
    at: AwareDatetime = field("Lúc", kind="text", example="2026-05-04T09:15:00+07:00", help="ISO 8601 CÓ múi giờ")
    until: AwareDatetime | None = field("Tới lúc", None, kind="text", help="Khoảng giữ: lúc thôi giữ; trống = đang giữ lúc nạp")
    started: Literal["", "rule", "designated", "manual", "retry"] = field("Bắt đầu vì", "", options=STARTED)
    ended: Literal["", "recalled", "handed_over", "ended"] = field("Kết thúc vì", "", options=ENDED)
    why: str = field("Lý do ở hệ nguồn", "", max_length=120, help="Mã / chữ lý do hệ cũ ghi (vd hết hạn gọi) — giữ nguyên để tra")
    import_ref: str = field("Lô nhập ở hệ nguồn", "", max_length=120, help="Lô nhập Excel của hệ cũ đã sinh ra lần giao này (Nhật ký nhập)")
    due: AwareDatetime | None = field("Hạn lúc đó", None, kind="text", help="Hạn liên hệ hệ nguồn đang áp khi chuyện xảy ra (vd hạn bị lỡ của lần thu hồi)")
    by_email: str = field("Người thao tác (email)", "", max_length=200, help="Quản lý giao tay / người nhập đã chỉ định — trống = hệ nguồn tự làm")


@m.command("import_history", resource="leadflow", action="manage", title="Nạp lịch sử chia lead từ hệ khác", category="setup", importable=True,
           agent="deny", min_scope="all")
async def import_history(ctx: Ctx, inp: HistoryIn) -> dict[str, Any]:
    """Ghi MỘT dòng lịch sử (khoảng giữ / sự kiện lẻ) vào ``leadflow.history`` — không thông báo, không đổi người giữ, không chạm sổ sự kiện
    Barista tự ghi. Nạp lại cùng (hệ nguồn, mã dòng) ⇒ bỏ qua."""
    _manager(ctx)
    if inp.kind == "held":
        if inp.until is not None and inp.until < inp.at:
            raise Invalid("Tới lúc trước Lúc")
        if inp.until is None and inp.ended:
            raise Invalid("Khoảng giữ đã kết thúc cần Tới lúc")
    elif inp.until is not None or inp.started or inp.ended:
        raise Invalid("Tới lúc / Bắt đầu vì / Kết thúc vì chỉ dùng cho khoảng giữ")
    _past(inp.at, "Lúc")
    _past(inp.until, "Tới lúc")
    lead_id = await _lead(ctx, inp)
    uid = await _person(ctx, inp.user_email)
    if inp.user_email and uid is None and not inp.user_ref:
        raise NotFound(f"Không có người {inp.user_email} trong công ty (điền Mã người ở hệ nguồn để giữ dòng)")
    by = await _person(ctx, inp.by_email)
    data: dict[str, Any] = {k: v for k, v in (("why", inp.why), ("due", inp.due and inp.due.isoformat()), ("by", by and str(by))) if v}
    new = await ctx.val(
        """INSERT INTO leadflow.history (lead_id, kind, user_id, user_ref, at, until, started, ended, origin, ext_ref, import_ref, data, imported_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (tenant_id, origin, ext_ref) DO NOTHING RETURNING id""",
        (lead_id, inp.kind, uid, "" if uid else inp.user_ref, inp.at, inp.until, inp.started, inp.ended, inp.origin, inp.ext_ref, inp.import_ref,
         Jsonb(data), ctx.p.user_id))
    return {"lead_id": lead_id, "inserted": new is not None, "matched_user": uid is not None}


class StateIn(LeadRefIn):
    status: Literal["waiting", "contacted", "queued", "closed"] = field("Trạng thái chia", options=STATES)
    assigned_at: AwareDatetime | None = field("Tới tay người giữ lúc", None, kind="text", help="ISO 8601 có múi giờ — mốc tính hạn gọi đầu")
    first_call_due: AwareDatetime | None = field("Hạn liên hệ lần đầu", None, kind="text",
                                                 help="Trống ⇒ tính theo luật chia hiện hành từ lúc tới tay (phút làm việc)")
    first_call_at: AwareDatetime | None = field("Liên hệ lần đầu lúc", None, kind="text")
    followup_due: AwareDatetime | None = field("Hạn chăm sóc lại", None, kind="text")
    recalls: int = field("Số lần đã thu hồi", 0, ge=0, le=100000)
    holders: list[str] = field("Người từng giữ (email)", [], kind="multiselect", help="Cách nhau dấu phẩy — luật không giao lại cho họ")
    holder_email: str = field("Người đang giữ ở hệ nguồn (email)", "", max_length=200,
                              help="Chỉ để ĐỐI CHIẾU: người giữ ở Barista luôn là người phụ trách lead bên Khách")
    queue_reason: Literal["", "recall_limit", "no_eligible", "no_rule", "coordinator", "team"] = field("Lý do hàng chờ", "", options=QUEUE)
    take_holder: bool = field("Người phụ trách = người đang giữ ở hệ nguồn", False,
                              help="Bật: lead đang chờ gọi / đã liên hệ mà người phụ trách bên Khách khác người đang giữ ở hệ nguồn ⇒ chuyển người "
                                   "phụ trách sang người đang giữ (lịch sử chuyển tay ghi lý do, không thông báo) — khi người đó còn làm được lead")


@m.command("import_state", resource="leadflow", action="manage", title="Nạp trạng thái chia lead từ hệ khác", category="setup", importable=True,
           agent="deny", min_scope="all")
async def import_state(ctx: Ctx, inp: StateIn) -> dict[str, Any]:
    """Trạng thái chia HIỆN TẠI của một lead nạp từ hệ khác. Người giữ = người phụ trách bên Khách; lead đã sang giao dịch / mất ⇒ đóng (giữ số lần
    thu hồi + người từng giữ). Hàng chờ ⇒ bỏ người phụ trách bên Khách (lead về hàng chờ có chủ, có tuổi — 0153). ``take_holder`` (0174): người phụ
    trách bên Khách chuyển sang người đang giữ ở hệ nguồn trước (người đó phải còn làm được lead). Không thông báo ai."""
    _manager(ctx)
    for at, what in ((inp.assigned_at, "Tới tay lúc"), (inp.first_call_at, "Liên hệ lần đầu lúc")):
        _past(at, what)
    lead_id = await _lead(ctx, inp)
    card = (await ctx.call_as("crm.lead_cards", ctx.p, lead_ids=[lead_id]))[str(lead_id)]
    info = (await ctx.call_as("crm.contact_info", ctx.p, lead_ids=[lead_id], since=[inp.assigned_at]))[str(lead_id)]
    prev = await ctx.one("SELECT imported_at FROM leadflow.lead_state WHERE lead_id = %s FOR UPDATE", (lead_id,))
    if prev is not None and prev["imported_at"] is None:
        raise Conflict("Lead này đã được Barista chia — không ghi đè bằng trạng thái hệ cũ")
    status = inp.status if info["stage"] in ("lead", "prospect") else "closed"
    owner: UUID | None = info["owner_id"]
    held = [u for e in inp.holders if (u := await _person(ctx, e)) is not None]
    src = await _person(ctx, inp.holder_email)
    rule = await _rule_for(ctx, card["offering_code"], card["source"], today())
    taken = False
    if inp.take_holder and status in ("waiting", "contacted") and src is not None and src != owner:
        # MỘT nguồn "ai giữ lead" (0153) vẫn là người phụ trách bên Khách: đổi nó trước bằng quyền người nạp (lịch sử chuyển tay), rồi trạng thái đi theo
        if src in await _lead_capable(ctx, card["offering_code"]):
            await ctx.call_as("crm.place", ctx.p, lead_id=lead_id, owner_id=src, reason="Nạp từ hệ cũ: người đang giữ ở vòng chia")
            owner, taken = src, True
    if status in ("waiting", "contacted"):
        if rule is None:
            raise Invalid(f"Chưa có luật chia cho [[offering|l]] {card['offering_code'] or '(trống)'} — thêm luật trước khi nạp lead đang chờ / đã liên hệ")
        if owner is None:
            raise Invalid("Lead chưa có người phụ trách bên Khách — nạp trạng thái hàng chờ, hoặc giao người trước")
        if inp.assigned_at is None:
            raise Invalid("Lead đang có người giữ cần Tới tay người giữ lúc")
    if status == "queued" and owner is not None:
        await ctx.call_as("crm.place", ctx.p, lead_id=lead_id, owner_id=None, team_id=rule and rule["team_id"],
                          reason="Nạp từ hệ cũ: lead nằm hàng chờ")
        held.append(owner)
        owner = None
    holder = owner if status != "queued" else None
    holders = list(dict.fromkeys([*held, *([holder] if holder else [])]))
    due = inp.first_call_due
    if status == "waiting" and due is None and rule is not None and inp.assigned_at is not None:
        due = due_in(inp.assigned_at, rule["first_call_min"], clock_of(rule, False))
    fdue = inp.followup_due
    if status == "contacted" and fdue is None and rule is not None:
        fdue = add_working_minutes(inp.first_call_at or inp.assigned_at or lf.clock(), rule["followup_hours"] * 60, hours_of(rule))
    ok = (inp.first_call_at <= due) if (inp.first_call_at and due) else None
    now = lf.clock()
    await put_imported(ctx, {"lead_id": lead_id, "offering_code": card["offering_code"], "source": card["source"], "status": status, "holder_id": holder,
                             "holders": holders, "rule_id": rule and rule["id"], "rule": Jsonb(_snapshot(rule) if rule else {}),
                             "assigned_at": inp.assigned_at, "first_call_due": due if status == "waiting" else None, "first_call_at": inp.first_call_at,
                             "first_call_ok": ok, "recalls": inp.recalls, "followup_due": fdue if status == "contacted" else None,
                             "queue_reason": (inp.queue_reason or "recall_limit") if status == "queued" else "",
                             "queued_at": now if status == "queued" else None,
                             "queue_team_id": rule["team_id"] if status == "queued" and rule else None})
    return {"lead_id": lead_id, "status": status, "holder_id": holder, "holder_matches": None if not inp.holder_email else src == holder, "holder_taken": taken,
            "past_due": status == "waiting" and due is not None and due < now}


class LeadHistoryIn(Input):
    lead_id: UUID = field("Lead", hidden=True)


@m.query("lead_history", resource="leadflow", title="Lịch sử chia lead (nạp từ hệ khác)")
async def lead_history(ctx: Ctx, inp: LeadHistoryIn) -> dict[str, Any]:
    """Các khoảng giữ / sự kiện của một lead đã nạp từ hệ khác — tách khỏi sổ việc Barista tự làm (Vì sao lead tới tay ai)."""
    rows = await ctx.all(
        """SELECT h.id, h.kind, h.at, h.until, h.started, h.ended, h.origin, h.data, coalesce(u.name, nullif(h.user_ref, '')) AS who
           FROM leadflow.history h LEFT JOIN core.users u ON u.id = h.user_id WHERE h.lead_id = %s ORDER BY h.at, h.id LIMIT 1000""", (inp.lead_id,))
    for r in rows:
        r["why"] = (r.pop("data") or {}).get("why", "")
        r["minutes"] = int((r["until"] - r["at"]).total_seconds() // 60) if r["until"] else None
    held = [r for r in rows if r["kind"] == "held"]
    return ui.table(rows, [
        ui.col("at", "Từ", "datetime"), ui.col("until", "Tới", "datetime"), ui.col("who", "Người"),
        ui.col("kind", "Loại", "badge", labels=KINDS), ui.col("started", "Bắt đầu vì", "badge", labels=STARTED),
        ui.col("ended", "Kết thúc vì", "badge", labels=ENDED), ui.col("minutes", "Giữ (phút)", "number"), ui.col("why", "Lý do ở hệ nguồn"),
    ], title="Lịch sử chia lead (nạp từ hệ khác)", kpis=[
        ui.kpi("Lượt giữ", len(held)), ui.kpi("Người khác nhau", len({r["who"] for r in held if r["who"]})),
        ui.kpi("Bị thu hồi", sum(1 for r in held if r["ended"] == "recalled"))],
        empty="Lead này không có lịch sử nạp từ hệ khác")

