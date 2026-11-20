"""Thẻ khách: hành trình ghép từ dữ liệu thật (độ nóng, chỗ kẹt, dòng thời gian, phần gói ngành cắm vào), thêm liên hệ, ghi sự đồng ý."""

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from psycopg import errors as pg

from barista.api import Conflict, Ctx, Input, NotFound, SubjectRef, extra_fields, field, history, phone_display, today, ui, zone
from barista.api.lead_labels import SOURCES
from barista.modules.crm import ACTIVITY, APPT_KIND, APPT_STATUS, OUTCOMES, STAGES, appointment_times, interest_text, m, reveal
from barista.modules.crm.identity import _party_of_lead
from barista.modules.crm.leads import offering_names

TEMP = {"hot": "Nóng", "warm": "Ấm", "new": "Mới", "cold": "Nguội"}
TEMP_TONE = {"hot": "bad", "warm": "warn", "new": "accent", "cold": "muted"}


def _day(v: Any) -> date:
    return v.astimezone(zone()).date() if isinstance(v, datetime) else v


def temperature(lead: dict[str, Any], acts: list[dict[str, Any]]) -> str:
    """Nóng: tiềm năng + nói chuyện được trong 7 ngày. Ấm: có chăm sóc trong 30 ngày. Mới: ≤ 3 ngày tuổi. Còn lại: nguội."""
    t = today()
    talked = [a for a in acts if a["outcome"] == "connected" or a["kind"] in ("meet", "visit")]
    if lead["stage"] == "prospect" and talked and (t - _day(talked[-1]["at"])).days <= 7:
        return "hot"
    if acts and (t - _day(acts[-1]["at"])).days <= 30:
        return "warm"
    if (t - _day(lead["created_at"])).days <= 3:
        return "new"
    return "cold"


def _journey_steps(lead: dict[str, Any], evs: list[dict[str, Any]], acts: list[dict[str, Any]], parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Các mốc từ dữ liệu THẬT: phần chung của mọi ngành + mốc gói ngành cắm vào (``crm.lead_story``). Mốc đầu chưa đạt = hiện tại; lead mất = dừng."""
    first_touch = next((a for a in acts if a["kind"] in ("call", "zalo", "meet", "visit", "email")), None)
    talked = next((a for a in acts if a["outcome"] == "connected" or a["kind"] in ("meet", "visit")), None)
    interest = next((e for e in evs if e["kind"] == "stage" and e["to_stage"] == "prospect"), None)
    steps: list[dict[str, Any]] = [
        {"label": "Lead về", "at": lead["created_at"], "note": SOURCES.get(lead["source"], ""), "owner": ""},
        {"label": "Gọi lần đầu", "at": first_touch and first_touch["at"], "note": "", "owner": ""},
        {"label": "Nói chuyện được", "at": talked and talked["at"], "note": "", "owner": ""},
        {"label": "Quan tâm", "at": interest and interest["at"], "note": "", "owner": ""},
        *(st for p in parts for st in p.get("steps", ())),
    ]
    cur_set = False
    for st in steps:
        if st["at"]:
            st["state"] = "done"
        elif not cur_set:
            st["state"], cur_set = "current", True
        else:
            st["state"] = "todo"
    stop = _lost_text(lead.get("lost_reason_label"), lead["lost_reason"]) or "Đã mất" if lead["stage"] == "lost" else \
        next((p["stop"] for p in parts if p.get("stop")), None)
    if stop:
        for st in steps:
            if st["state"] == "current":
                st["state"], st["note"] = "stopped", stop
    return steps


def _lost_text(label: str | None, note: str | None) -> str:
    """Lý do mất cho người đọc: tên lý do trong danh mục + ghi chú (ADR 0246); lead mất trước khi có danh mục chỉ có chữ người đã gõ."""
    label, note = (label or "").strip(), (note or "").strip()
    return " — ".join(x for x in (label, note if note.lower() != label.lower() else "") if x)


def _blocker(lead: dict[str, Any], acts: list[dict[str, Any]], parts: list[dict[str, Any]],
             idle: dict[str, Any] | None = None) -> tuple[str, str] | None:
    """Cái đang KẸT, ai cần làm, từ khi nào — luật đầu tiên khớp thắng; chưa kẹt ở chăm sóc thì hỏi gói ngành. ``idle``: dòng nằm im của lead
    (``idle.idle_rows`` — ADR 0246), quá ngưỡng của chặng thì là chỗ kẹt."""
    t = today()
    if lead["stage"] == "lost":
        return None
    if lead["stage"] in ("lead", "prospect"):
        if lead["next_follow_up"] and lead["next_follow_up"] < t:
            return "Quá hạn gọi lại", f"Hẹn gọi từ {lead['next_follow_up']:%d/%m} ({(t - lead['next_follow_up']).days} ngày) — {lead['owner']} cần gọi."
        if not acts and (t - _day(lead["created_at"])).days >= 1:
            return "Chưa ai gọi", f"Lead về {_day(lead['created_at']):%d/%m} mà chưa có lần chăm sóc nào — {lead['owner']} cần gọi."
        if idle and idle["over"]:
            return (f"Nằm im {idle['days']} ngày", f"Ở chặng “{idle['stage_label']}” từ {_day(idle['since']):%d/%m} không chuyển chặng, không chăm sóc "
                                                    f"(ngưỡng {idle['limit_days']} ngày) — {lead['owner']} cần chăm sóc hoặc chuyển chặng.")
        if acts and (t - _day(acts[-1]["at"])).days >= 14:
            return "Khách đang nguội", f"Lần chăm sóc cuối {_day(acts[-1]['at']):%d/%m} ({(t - _day(acts[-1]['at'])).days} ngày trước)."
    return next((tuple(p["blocker"]) for p in parts if p.get("blocker")), None)


def _draft(sender: str, lead: dict[str, Any], kind: str, product: str | None = None) -> str:
    """Tin soạn sẵn GỬI KHÁCH — CHỈ dùng dữ kiện có trong dữ liệu (tên, thứ khách quan tâm, nhu cầu khách đã nói). Không bịa ưu đãi / lịch.
    Không viết "em thấy anh/chị vừa xem…" (không biết ai xem). Sale tự gửi; hệ thống không gửi gì.
    ``product``: TÊN thứ khách quan tâm (``leads.offering_names``) — KHÔNG BAO GIỜ mã nội bộ (``lead["offering_code"]``: ``GOI-A``, ``HSR``…) vì khách không biết;
    không có tên ⇒ bỏ phần đó, câu vẫn trọn."""
    who = lead["customer"].split()[-1]
    proj = f" {product}" if product else ""
    need = interest_text(lead["interest"])
    if kind == "uncalled":
        return f"Chào anh/chị {who}, em {sender} đây ạ. Anh/chị có quan tâm{proj or ' tới bên em'} nên em xin phép liên hệ hỗ trợ mình. Anh/chị tiện nghe máy lúc nào ạ?"
    if lead["last_activity_at"] is None:  # chưa từng liên hệ ⇒ không được nói "hôm trước mình có trao đổi"
        return f"Chào anh/chị {who}, em {sender} đây ạ. Anh/chị có để lại thông tin quan tâm{proj}, em xin phép hỗ trợ mình. Anh/chị tiện nghe máy lúc nào ạ?"
    # không có tên sản phẩm thì nói "nhu cầu của anh/chị" — tránh câu cụt "Hôm trước mình có trao đổi (≤ 5 tỷ)"
    about = f" về{proj}" if proj else (" về nhu cầu của anh/chị" if need else " với nhau")
    return (f"Chào anh/chị {who}, em {sender} đây ạ. Hôm trước mình có trao đổi{about}" + (f" ({need})" if need else "") +
            ". Em gọi lại để hỗ trợ mình thêm — anh/chị tiện nghe máy lúc nào ạ?")


class JourneyIn(Input):
    lead_id: UUID = field("Lead", hidden=True)


@m.subject("crm.lead", "Khách / lead")
async def subject_lead(ctx: Ctx, inp: SubjectRef) -> dict[str, Any]:
    """Khách như một ĐỐI TƯỢNG (trao đổi gắn vào khách — 0046). Gọi bằng quyền người hỏi ⇒ không thấy lead thì NotFound."""
    lead = await ctx.one("""SELECT l.id, l.owner_id, l.team_id, l.offering_code, c.name FROM crm.leads l JOIN crm.customers c ON c.id = l.customer_id
                            WHERE l.id = %s""", (inp.subject_id,))
    if lead is None:
        raise NotFound("Không thấy lead")
    return {"title": f"{lead['name']} · {lead['offering_code'] or 'chưa chọn [[offering|l]]'}", "owner_id": lead["owner_id"], "team_id": lead["team_id"],
            "state": "active", "open": {"query": "crm.journey", "input": {"lead_id": str(lead["id"])}}}


@m.query("journey", resource="crm", title="Hành trình khách")
async def journey(ctx: Ctx, inp: JourneyIn) -> dict[str, Any]:
    """Hành trình một khách, ghép từ lịch sử chuyển chặng, chăm sóc và phần gói ngành cắm vào — kèm cái đang kẹt và việc cần làm tiếp.
    Mọi mốc là dữ liệu thật (không do model khai)."""
    lead = await ctx.one(
        """SELECT l.*, c.name AS customer, c.phone, c.party_id, coalesce(u.name, 'Chưa chia') AS owner FROM crm.leads l JOIN crm.customers c ON c.id = l.customer_id
           LEFT JOIN core.users u ON u.id = l.owner_id WHERE l.id = %s""", (inp.lead_id,))
    if lead is None:
        raise NotFound("Không thấy lead")
    raw_phone = lead["phone"]
    hidden = bool(await reveal.mask_rows(ctx, [lead]))   # chờ bấm nhận theo luật có che ⇒ mọi liên hệ trên thẻ đã che (0214)
    evs = await ctx.all("SELECT e.*, u.name AS by_name FROM crm.stage_events e LEFT JOIN core.users u ON u.id = e.by_user WHERE e.lead_id = %s ORDER BY e.at, e.id", (inp.lead_id,))
    acts = await ctx.all("SELECT a.*, u.name AS by_name FROM crm.activities a LEFT JOIN core.users u ON u.id = a.by_user WHERE a.lead_id = %s ORDER BY a.at", (inp.lead_id,))
    parts: list[dict[str, Any]] = await ctx.plugs("crm.lead_story", lead_id=inp.lead_id)   # bằng quyền người xem: phần nào không được xem thì tự bỏ
    from barista.modules.crm import idle as idle_mod  # nạp muộn: giữ thứ tự thao tác trong sổ đăng ký
    from barista.modules.crm.lost_reasons import label_of

    lead["lost_reason_label"] = await label_of(ctx, lead.get("lost_reason_key"))
    idle = next(iter(await idle_mod.idle_rows(ctx, lead_ids=[lead["id"]], over_only=False)), None)
    steps = _journey_steps(lead, evs, acts, parts)
    blocker = _blocker(lead, acts, parts, idle)
    temp = temperature(lead, acts)
    t = today()

    rows: list[dict[str, Any]] = []  # (lúc, tiêu đề, phụ đề, tông)
    names = {r["key"]: r["label"] for r in await ctx.all("SELECT key, label FROM crm.stages")} if any(e["kind"] == "pipeline" for e in evs) else {}
    for e in evs:
        if e["kind"] == "created":
            rows.append({"at": e["at"], "title": "Lead về", "subtitle": SOURCES.get((e["data"] or {}).get("source", ""), ""), "tone": "accent"})
        elif e["kind"] == "stage":
            why = _lost_text((e["data"] or {}).get("lost_reason_label"), e["reason"]) if e["to_stage"] == "lost" else e["reason"]
            rows.append({"at": e["at"], "title": f"Chuyển chặng: {STAGES[e['from_stage']]} → {STAGES[e['to_stage']]}",
                         "subtitle": " · ".join(x for x in (why, e["by_name"] or "") if x), "tone": "muted" if e["to_stage"] == "lost" else "accent"})
        elif e["kind"] == "pipeline":   # chặng của công ty trong cùng giai đoạn (0153); Mất → Mất có lý do = ghi lại lý do mất (ADR 0246)
            lost = "lost_reason_label" in (e["data"] or {})
            why = _lost_text(e["data"]["lost_reason_label"], e["reason"]) if lost else ""
            title = "Ghi lại lý do mất" if lost and e["from_stage"] == e["to_stage"] else \
                f"Chuyển chặng: {names.get(e['from_stage'] or '', e['from_stage'] or '—')} → {names.get(e['to_stage'] or '', e['to_stage'] or '—')}"
            rows.append({"at": e["at"], "title": title, "subtitle": " · ".join(x for x in (why, e["by_name"] or "") if x), "tone": "muted" if lost else "accent"})
        elif e["kind"] == "assigned":
            rows.append({"at": e["at"], "title": "Đổi người phụ trách", "subtitle": " · ".join(x for x in (e["reason"], e["by_name"] or "") if x),
                         "tone": "muted"})
    for a in acts:
        out = OUTCOMES.get(a["outcome"], "") if a["outcome"] else ""
        rows.append({"at": a["at"], "title": f"{ACTIVITY[a['kind']]}{' — ' + out if out else ''}",
                     "subtitle": " · ".join(x for x in (a["note"], a["by_name"] or "") if x), "tone": "ok" if a["outcome"] == "connected" else ("warn" if a["outcome"] in ("no_answer", "busy") else "default")})
    appts = await ctx.all("SELECT id, kind, place, note, status, created_at FROM crm.appointments WHERE lead_id = %s", (inp.lead_id,))
    times = await appointment_times(ctx, [ap["id"] for ap in appts], cancelled=True)   # giờ hẹn từ lịch chung (0158), kể cả hẹn đã huỷ
    for ap in appts:
        rows.append({"at": times[str(ap["id"])]["starts_at"] if str(ap["id"]) in times else ap["created_at"], "title": f"{APPT_KIND[ap['kind']]} — {APPT_STATUS[ap['status']].lower()}",
                     "subtitle": " · ".join(x for x in (ap["place"], ap["note"]) if x),
                     "tone": "accent" if ap["status"] == "scheduled" else ("bad" if ap["status"] in ("cancelled", "missed") else "ok")})
    for p in parts:
        rows += p.get("rows", [])
    rows.sort(key=lambda r: r["at"], reverse=True)

    blocks: list[dict[str, Any]] = [ui.b_kpis([
        ui.kpi("Độ nóng", TEMP[temp], "text", tone=TEMP_TONE[temp] if temp in ("hot", "warm") else None),
        ui.kpi("Tuổi lead", f"{(t - _day(lead['created_at'])).days} ngày", "text"),
        ui.kpi("Hẹn gọi", lead["next_follow_up"].strftime("%d/%m") if lead["next_follow_up"] else "Chưa hẹn", "text",
               tone="bad" if lead["next_follow_up"] and lead["next_follow_up"] < t else None),
        # ADR 0246: chỉ khi chặng hiện tại có ngưỡng nằm im
        *([ui.kpi("Nằm im", f"{idle['days']} ngày", "text", tone="bad" if idle["over"] else None, hint=f"Ngưỡng của chặng: {idle['limit_days']} ngày")]
          if idle else [])])]
    if hidden:
        mine = lead["owner_id"] == ctx.p.user_id
        blocks.append(ui.b_callout("accent", "Chưa nhận lead — số khách đang che",
                                   "Bấm Nhận lead để xem đủ số và liên hệ khách. Quá hạn bấm nhận thì lead chuyển cho người khác." if mine
                                   else "Người được chia chưa bấm nhận — liên hệ của khách đang che với mọi người cho tới lúc đó.",
                                   actions=[ui.act("leadflow.accept", "Nhận lead", {"lead_id": str(lead["id"])}, "primary")] if mine else []))
    if blocker:
        blocks.append(ui.b_callout("bad", f"Đang kẹt: {blocker[0]}", blocker[1]))
    blocks.append(ui.b_steps(steps, "Hành trình"))
    if lead["stage"] in ("lead", "prospect") and lead["owner_id"] == ctx.p.user_id and not hidden:   # tin soạn sẵn chỉ cho người đang phụ trách, đã nhận
        sender = ctx.p.name.split()[-1] if ctx.p.name else "em"
        blocks.append(ui.b_callout("accent", "Tin nhắn gợi ý — anh/chị tự gửi", draft=_draft(sender, lead, "uncalled" if lead["last_activity_at"] is None else "follow",
                                                                                          (await offering_names(ctx, [lead["offering_code"]])).get(lead["offering_code"] or "")),
                                   actions=[ui.act("crm.log_activity", "Ghi đã gọi", {"lead_id": str(lead["id"]), "kind": "call"}, "primary")]))
    need = interest_text(lead["interest"])
    if need:
        blocks.append(ui.b_text(f"**Nhu cầu:** {need}", ""))
    own = extra_fields.shown(ctx.p.fields, "lead", lead.get("extra"))   # trường công ty tự thêm (0060) — chỉ trường đang dùng, có giá trị
    if own:
        plain = str.maketrans({c: f"\\{c}" for c in "\\`*_[]()<>#|"})   # chữ người gõ không thành định dạng / link trên thẻ
        blocks.append(ui.b_text(" · ".join(f"**{label.translate(plain)}:** {value.translate(plain)}" for label, value in own), ""))
    card = await ctx.call_system("parties.contact_card", party_id=lead["party_id"])
    others = [f"{CONTACT_LABEL.get(c['kind'], c['kind'])}: {reveal.mask_value(c['kind'], c['value']) if hidden else (phone_display(c['value']) if c['kind'] == 'phone' else c['value'])}"
              for c in card["contacts"] if not (c["kind"] == "phone" and c["value"] == raw_phone)]
    if others:
        blocks.append(ui.b_text("**Liên hệ khác:** " + " · ".join(others), ""))
    if card["consents"]:
        blocks.append(ui.b_text("**Đồng ý:** " + " · ".join(f"{CONSENT_LABEL.get(c['purpose'], c['purpose'])}: {c['state_label'].lower()}" for c in card["consents"]), ""))
    blocks.append(ui.b_list([{"title": r["title"], "subtitle": r["subtitle"], "tone": r["tone"], "badge": f"{r['at'].astimezone(zone()):%d/%m %H:%M}"}
                             for r in rows[:40]], "Dòng thời gian"))
    blocks += [b for p in parts for b in p.get("blocks", ())]   # khối riêng của gói cắm vào (vd cuộc gọi tổng đài — 0149)
    if talk := await ctx.call_as_optional("talk.block", ctx.p, subject_kind="crm.lead", subject_id=lead["id"]):   # gói Trao đổi (0046)
        blocks.append(talk)
    if changes := await history.block(ctx, "crm.lead", lead["id"]):   # lịch sử thay đổi theo trường (ADR 0246)
        blocks.append(changes)
    actions = []
    if lead["stage"] in ("lead", "prospect"):
        actions.append(ui.act("crm.log_activity", "Ghi chăm sóc", {"lead_id": str(lead["id"])}, "primary"))
        actions.append(ui.act("crm.schedule", "Đặt lịch hẹn", {"lead_id": str(lead["id"])}))
        open_ = [ap for ap in appts if ap["status"] == "scheduled" and str(ap["id"]) in times]
        for ap in sorted(open_, key=lambda x: times[str(x["id"])]["starts_at"])[:2]:
            at = times[str(ap["id"])]["starts_at"]
            actions.append(ui.act("crm.close_appointment", f"Chốt hẹn {at.astimezone(zone()):%H:%M %d/%m}", {"appointment_id": str(ap["id"])}))
        if lead["stage"] == "lead":
            actions.append(ui.act("crm.set_stage", "Khách có quan tâm", {"lead_id": str(lead["id"]), "stage": "prospect"}))
        actions.append(ui.act("crm.add_contact", "Thêm liên hệ", {"lead_id": str(lead["id"])}))
        actions.append(ui.act("crm.record_consent", "Ghi sự đồng ý", {"lead_id": str(lead["id"])}))
        actions += [a for p in parts for a in p.get("actions", ())]
        actions.append(ui.act("crm.set_stage", "Mất khách", {"lead_id": str(lead["id"]), "stage": "lost"}, "destructive"))
    who = " · ".join(x for x in (phone_display(lead["phone"]), lead["offering_code"] or "chưa chọn [[offering|l]]", STAGES[lead["stage"]], lead["owner"]) if x)
    return ui.card(lead["customer"], blocks, subtitle=who, icon="person.crop.circle", actions=actions,
                   open=[o for p in parts for o in p.get("open", ())],
                   ask={"title": "Hỏi trợ lý về khách này", "prompt": f"Về khách {lead['customer']}: "})


CONTACT_LABEL = {"phone": "SĐT", "email": "Email", "zalo": "Zalo", "facebook": "Facebook", "other": "Khác"}
CONSENT_LABEL = {"marketing": "Nhận tiếp thị", "care": "Chăm sóc & nhắc lịch", "data_processing": "Xử lý dữ liệu cá nhân"}


class AddContact(Input):
    lead_id: UUID = field("Lead", hidden=True)
    kind: Literal["phone", "email", "zalo", "facebook", "other"] = field("Loại liên hệ", "zalo", options=CONTACT_LABEL)
    value: str = field("Giá trị", max_length=200)


@m.command("add_contact", resource="crm", title="Thêm liên hệ cho khách", agent="propose")
async def add_contact(ctx: Ctx, inp: AddContact) -> dict[str, Any]:
    """Liên hệ thuộc người (0111). Liên hệ đang thuộc người khác ⇒ không chuyển, ghi vào danh sách nghi trùng. Khách chưa có SĐT thì SĐT này thành số chính."""
    c = await _party_of_lead(ctx, inp.lead_id)
    got = await ctx.call_system("parties.attach_contact", party_id=c["party_id"], kind=inp.kind, value=inp.value, source="crm")
    if got.get("conflict"):
        return {"lead_id": inp.lead_id, "conflict": True, "summary": f"{CONTACT_LABEL[inp.kind]} này đang thuộc một người khác: chưa thêm cho {c['name']}, đã ghi vào danh sách nghi trùng"}
    if inp.kind == "phone" and not c["phone"]:
        try:
            async with ctx.conn.transaction():
                await ctx.exec("UPDATE crm.customers SET phone = %s WHERE id = %s", (got["value"], c["id"]))
        except pg.UniqueViolation as e:
            raise Conflict("SĐT này đã là khách của người khác trong công ty — báo trưởng nhóm nếu cần chuyển khách") from e
    return {"lead_id": inp.lead_id, "summary": f"Thêm {CONTACT_LABEL[inp.kind]} cho {c['name']}"}


class ContactConsent(Input):
    lead_id: UUID = field("Lead", hidden=True)
    purpose: Literal["marketing", "care", "data_processing"] = field("Mục đích", "marketing", options=CONSENT_LABEL)
    granted: bool = field("Đồng ý", True, help="Bỏ chọn = rút / từ chối. Lần ghi mới nhất là trạng thái hiện tại")
    source: str = field("Ghi nhận qua", "", max_length=120, example="Cuộc gọi ngày 07/11")
    note: str = field("Ghi chú", "", max_length=500)


@m.command("record_consent", resource="crm", title="Ghi sự đồng ý của khách", agent="propose")
async def record_consent(ctx: Ctx, inp: ContactConsent) -> dict[str, Any]:
    """Chỉ thêm (0111): rút đồng ý là một dòng mới; ghi lại ai ghi, ghi qua đâu. Chỉ khách người gọi được xem."""
    c = await _party_of_lead(ctx, inp.lead_id)
    await ctx.call_system("parties.record_contact_consent", party_id=c["party_id"], purpose=inp.purpose, granted=inp.granted, source=inp.source, note=inp.note)
    return {"lead_id": inp.lead_id, "summary": f"{c['name']}: {CONSENT_LABEL[inp.purpose].lower()} — {'đồng ý' if inp.granted else 'không đồng ý'}"}
