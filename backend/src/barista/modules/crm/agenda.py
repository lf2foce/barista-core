"""Lịch hẹn: hẹn gặp / tham quan / hẹn gọi, gộp với hạn gọi lại, việc đến hạn, đợt khách đóng tiền; nhắc trước giờ hẹn; gợi ý đặt lịch gọi.

0158: GIỜ + NGƯỜI của hẹn nằm trên LỊCH CHUNG (``schedule.events`` nguồn ``crm.appointment`` — qua hợp đồng ``schedule.book`` / ``times_of`` /
``finish`` / ``release``); ``crm.appointments`` giữ phần nghiệp vụ (loại hẹn, kết quả, lead, địa điểm, ghi chú). Mọi chỗ hiện giờ hẹn đọc từ lịch. Trùng giờ
với lịch khác của sale chỉ CẢNH BÁO (sale là người tham dự, không phải nguồn lực giữ cứng) — thay cho phép chặn ±60 phút cũ.
"""

import re
from datetime import date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from barista.api import Conflict, Ctx, Day, Event, Input, Invalid, NoInput, NotFound, can_run, field, fold, today, ui, zone
from barista.modules.crm import (
    APPT_CAL_KIND,
    APPT_KIND,
    APPT_MINUTES,
    APPT_SOURCE,
    APPT_STATUS,
    appointment_times,
    book_appointment,
    m,
)

WEEKDAY = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ nhật"]


def _at(day: date, hhmm: str) -> datetime:
    try:
        h, mi = (int(x) for x in hhmm.strip().replace("h", ":").split(":")[:2])
        return datetime(day.year, day.month, day.day, h, mi, tzinfo=zone())
    except (ValueError, TypeError) as e:
        raise Invalid("Giờ hẹn dạng 14:30") from e


class Schedule(Input):
    lead_id: UUID = field("Lead", hidden=True)
    kind: Literal["meet", "visit", "call"] = field("Loại hẹn", "meet", options=APPT_KIND)
    day: Day = field("Ngày", example="02/11/2026")
    time: str = field("Giờ", "09:00", kind="time", max_length=5, example="14:30", help="Giờ của công ty")
    minutes: int = field("Dài (phút)", 0, ge=0, le=600, agent=False, help="0 = mặc định theo loại hẹn (gặp 60, tham quan 90, gọi 15)")
    place: str = field("Địa điểm", "", max_length=200, example="Văn phòng trung tâm")
    note: str = field("Ghi chú", "", max_length=1000)


def _length(kind: str, minutes: int) -> timedelta:
    return timedelta(minutes=minutes or APPT_MINUTES[kind])


@m.command("schedule", resource="crm", title="Đặt lịch hẹn", agent="propose", promotable=True, risk="low", target="appointment_id",   # 0143
           undone_by=("crm.close_appointment:status=cancelled", "crm.move_appointment"))
async def schedule(ctx: Ctx, inp: Schedule) -> dict[str, Any]:
    """Đặt hẹn gặp, đi xem (tham quan) hay hẹn gọi cho khách của mình — giờ trên lịch chung của sale phụ trách. Sale đã có lịch khác cùng giờ ⇒ vẫn
    đặt, kèm cảnh báo."""
    # trợ lý đặt lên lịch của NGƯỜI KHÁC ⇒ luôn thành đề xuất, kể cả khi công ty cho làm thẳng (schedule.book — 0158). Docstring giữ ngắn: là mô tả
    # công cụ của trợ lý, dài ra đẩy công cụ khỏi trần ký tự của phần cố định (tests/test_meeting_note.py)
    lead = await ctx.one("""SELECT l.id, l.owner_id, l.stage, c.name FROM crm.leads l JOIN crm.customers c ON c.id = l.customer_id
                            WHERE l.id = %s FOR UPDATE OF l""", (inp.lead_id,))
    if lead is None:
        raise NotFound("Không thấy lead")
    if lead["stage"] in ("lost", "deal"):
        raise Invalid("Khách đã mất / đã thành giao dịch — không đặt hẹn mới")
    at = _at(inp.day, inp.time)
    if at < datetime.now(zone()) - timedelta(minutes=5):
        raise Invalid("Giờ hẹn đã qua")
    aid = await ctx.val("INSERT INTO crm.appointments (lead_id, kind, place, note, created_by) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                        (inp.lead_id, inp.kind, inp.place, inp.note, ctx.p.user_id))   # không ghi ``at``: giờ ở lịch chung (0158; crm/074 cho cột rỗng)
    got = await book_appointment(ctx, {"id": aid, "lead_id": inp.lead_id, "kind": inp.kind}, at, at + _length(inp.kind, inp.minutes))
    when = f"{at:%H:%M %d/%m}"
    await ctx.notify([lead["owner_id"]], kind="appointment", title=f"Lịch hẹn mới: {lead['name']} lúc {when}", body=inp.place or APPT_KIND[inp.kind],
                     push=f"Anh/chị có lịch hẹn mới lúc {when}", open={"query": "crm.agenda", "input": {}})
    return {"appointment_id": aid, "event_id": got["event_id"], "warnings": got["warnings"],
            "summary": f"Hẹn {lead['name']} — {APPT_KIND[inp.kind].lower()} lúc {when}" + (f" tại {inp.place}" if inp.place else "")
                       + (f". Lưu ý: {'; '.join(got['warnings'])}" if got["warnings"] else ""),
            # HOÀN TÁC (0144): huỷ chính hẹn vừa đặt, chỉ khi hẹn chưa bị dời
            "undo": {"command": "crm.close_appointment", "title": f"Hoàn tác: huỷ hẹn {lead['name']} lúc {when}",
                     "input": {"appointment_id": str(aid), "status": "cancelled", "note": "hoàn tác", "if_at": at.isoformat()}}}


class CloseAppt(Input):
    appointment_id: UUID = field("Lịch hẹn", hidden=True)
    status: Literal["done", "cancelled", "missed"] = field("Kết quả", "done", options={k: APPT_STATUS[k] for k in ("done", "cancelled", "missed")})
    note: str = field("Ghi chú kết quả", "", max_length=1000, help="Gặp xong: ghi lại khách nói gì — vào lịch sử chăm sóc")
    next_follow_up: Day | None = field("Hẹn chăm sóc lại", None)
    if_at: datetime | None = field("Chỉ khi giờ hẹn vẫn là", None, hidden=True, agent=False,
                                   help="Hoàn tác (0144): hẹn đã được dời sau đó ⇒ từ chối, không huỷ đè lên lịch mới")


@m.command("close_appointment", resource="crm", title="Chốt kết quả lịch hẹn", agent="propose")
async def close_appointment(ctx: Ctx, inp: CloseAppt) -> dict[str, Any]:
    """Gặp / tham quan xong ⇒ tự ghi vào chăm sóc (đã liên hệ) để hạn gọi, thẻ buổi sáng và chia lead cùng thấy."""
    # trên lịch chung (0158): đã gặp / khách không tới ⇒ lịch đã xong (giờ đó đã dùng); huỷ ⇒ nhả giờ
    a = await ctx.one("SELECT * FROM crm.appointments WHERE id = %s AND status = 'scheduled' FOR UPDATE", (inp.appointment_id,))
    if a is None:
        raise NotFound("Không thấy lịch hẹn (hoặc đã chốt rồi)")
    if inp.if_at is not None:   # khoá của Hoàn tác (0144): hẹn đã dời (trên lịch) sau lần trợ lý đặt
        now_at = (await appointment_times(ctx, [a["id"]])).get(str(a["id"]), {}).get("starts_at")
        if now_at is not None and now_at != inp.if_at:
            raise Conflict(f"Lịch hẹn đã được dời sang {now_at.astimezone(zone()):%H:%M %d/%m} sau đó — không huỷ đè lên lịch mới. Mở lịch hẹn để xem.")
    await ctx.exec("UPDATE crm.appointments SET status = %s, closed_at = now(), note = CASE WHEN %s = '' THEN note ELSE %s END WHERE id = %s",
                   (inp.status, inp.note, inp.note, a["id"]))
    if inp.status == "cancelled":
        await ctx.call_system("schedule.release", source=APPT_SOURCE, source_ref=str(a["id"]), reason=inp.note[:200] or "Huỷ hẹn")
    else:
        await ctx.call_system("schedule.finish", source=APPT_SOURCE, source_ref=str(a["id"]))
    if inp.status == "done":
        kind = "visit" if a["kind"] == "visit" else ("call" if a["kind"] == "call" else "meet")
        await ctx.exec("INSERT INTO crm.activities (lead_id, kind, outcome, note, by_user) VALUES (%s, %s, %s, %s, %s)",
                       (a["lead_id"], kind, "connected" if kind == "call" else "", inp.note or APPT_KIND[a["kind"]], ctx.p.user_id))
        was = await ctx.val("UPDATE crm.leads SET last_activity_at = now(), next_follow_up = %s, updated_at = now() WHERE id = %s"
                            " RETURNING old.next_follow_up", (inp.next_follow_up, a["lead_id"]))
        await ctx.track("crm.lead", a["lead_id"], "next_follow_up", was, inp.next_follow_up)   # lịch sử thay đổi (ADR 0246)
    elif inp.next_follow_up:
        was = await ctx.val("UPDATE crm.leads SET next_follow_up = %s, updated_at = now() WHERE id = %s RETURNING old.next_follow_up",
                            (inp.next_follow_up, a["lead_id"]))
        await ctx.track("crm.lead", a["lead_id"], "next_follow_up", was, inp.next_follow_up)
    return {"appointment_id": a["id"], "status": inp.status}


class MoveAppt(Input):
    appointment_id: UUID = field("Lịch hẹn", hidden=True)
    day: Day = field("Ngày mới")
    time: str = field("Giờ mới", "09:00", kind="time", max_length=5)


@m.command("move_appointment", resource="crm", title="Dời lịch hẹn", agent="propose")
async def move_appointment(ctx: Ctx, inp: MoveAppt) -> dict[str, Any]:
    """Dời hẹn, giữ độ dài. Sale bận giờ mới ⇒ vẫn dời, kèm cảnh báo."""
    a = await ctx.one("""SELECT a.*, l.owner_id FROM crm.appointments a JOIN crm.leads l ON l.id = a.lead_id
                         WHERE a.id = %s AND a.status = 'scheduled' FOR UPDATE OF a""", (inp.appointment_id,))
    if a is None:
        raise NotFound("Không thấy lịch hẹn (hoặc đã chốt rồi)")
    at = _at(inp.day, inp.time)
    if at < datetime.now(zone()) - timedelta(minutes=5):
        raise Invalid("Giờ hẹn đã qua")
    t = (await appointment_times(ctx, [a["id"]])).get(str(a["id"]))
    length = t["ends_at"] - t["starts_at"] if t else _length(a["kind"], 0)
    got = await book_appointment(ctx, a, at, at + length)
    return {"appointment_id": a["id"], "warnings": got["warnings"], "summary": f"Dời hẹn sang {at:%H:%M %d/%m}"
            + (f". Lưu ý: {'; '.join(got['warnings'])}" if got["warnings"] else "")}


@m.on("schedule.booking_changed")
async def on_booking_changed(ctx: Ctx, event: Event) -> None:
    """Người dời / huỷ hẹn ngay trên LỊCH (không qua thẻ khách) ⇒ phần nghiệp vụ của hẹn khớp theo: huỷ ⇒ hẹn thành "đã huỷ" (dời thì giờ đã ở lịch — không còn bản chép ở hẹn)."""
    p = event.payload
    if p.get("source") != APPT_SOURCE:
        return
    a = await ctx.one("SELECT id, status FROM crm.appointments WHERE id = %s FOR UPDATE", (UUID(str(p["source_ref"])),))
    if a is None or a["status"] != "scheduled":
        return
    if p.get("change") == "cancelled":
        await ctx.exec("UPDATE crm.appointments SET status = 'cancelled', closed_at = now() WHERE id = %s", (a["id"],))


class AgendaIn(Input):
    days: int = field("Số ngày tới", 7, ge=1, le=60)
    who: Literal["", "team"] = field("Xem", "", options={"": "Khách của tôi", "team": "Cả đội (khách tôi được xem)"})


def _day_label(d: date) -> str:
    if d == today():
        return f"Hôm nay · {d:%d/%m}"
    return "Ngày mai · " + f"{d:%d/%m}" if d == today() + timedelta(days=1) else f"{WEEKDAY[d.weekday()]} {d:%d/%m}"


async def open_appointments(ctx: Ctx, lo: datetime, hi: datetime, *, mine: bool) -> list[dict[str, Any]]:
    """Hẹn sắp tới chạm khoảng [lo, hi) của khách người xem thấy (RLS của lead; ``mine`` = khách tôi phụ trách), GIỜ đọc từ lịch chung."""
    rows = await ctx.all(
        """SELECT a.id, a.kind, a.place, a.note, c.name, l.id AS lead_id, l.offering_code, u.name AS owner
           FROM crm.appointments a JOIN crm.leads l ON l.id = a.lead_id JOIN crm.customers c ON c.id = l.customer_id
           LEFT JOIN core.users u ON u.id = l.owner_id
           WHERE a.status = 'scheduled' AND (NOT %s OR l.owner_id = core.me()) LIMIT 5000""", (mine,))
    times = await appointment_times(ctx, [r["id"] for r in rows], since=lo, until=hi)
    out = []
    for r in rows:
        if (t := times.get(str(r["id"]))) and t["status"] == "booked" and lo <= t["starts_at"] < hi:
            out.append({**r, "at": t["starts_at"], "ends_at": t["ends_at"]})
    return sorted(out, key=lambda r: r["at"])[:300]


@m.query("agenda", resource="crm", title="Lịch hẹn khách")
async def agenda(ctx: Ctx, inp: AgendaIn) -> dict[str, Any]:
    """Lịch gộp bằng code từ dữ liệu thật, theo ngày: hẹn gặp / tham quan, hẹn gọi lại, việc đến hạn, đợt khách đóng tiền. Không bịa mốc.
    "Của tôi" = khách tôi giữ + việc giao tôi; "cả đội" = mọi khách tôi được xem (RLS)."""
    # giờ hẹn đọc từ lịch chung (0158 — open_appointments)
    t0 = today()
    end = t0 + timedelta(days=inp.days)
    mine = inp.who != "team"
    items: list[tuple[datetime, str, str, str, str, dict[str, Any]]] = []   # (lúc, loại, tiêu đề, phụ đề, tông, mở)
    for r in await open_appointments(ctx, datetime.combine(t0, datetime.min.time(), zone()), datetime.combine(end, datetime.min.time(), zone()), mine=mine):
        sub = " · ".join(x for x in [r["place"], r["offering_code"] or "", "" if mine else (r["owner"] or "")] if x)
        items.append((r["at"], "appt", f"{r['name']} — {APPT_KIND[r['kind']].lower()}", sub, "accent",
                      {"query": "crm.journey", "input": {"lead_id": str(r["lead_id"])}, "appointment_id": str(r["id"])}))
    for r in await ctx.all(
            """SELECT l.id, l.next_follow_up, l.offering_code, c.name, u.name AS owner FROM crm.leads l JOIN crm.customers c ON c.id = l.customer_id
               LEFT JOIN core.users u ON u.id = l.owner_id WHERE l.stage IN ('lead', 'prospect') AND l.next_follow_up >= %s AND l.next_follow_up < %s
                 AND (NOT %s OR l.owner_id = core.me()) ORDER BY l.next_follow_up LIMIT 300""", (t0, end, mine)):
        items.append((datetime.combine(r["next_follow_up"], datetime.min.time(), zone()), "callback", f"Gọi lại {r['name']}",
                      " · ".join(x for x in [r["offering_code"] or "", "" if mine else (r["owner"] or "")] if x), "muted",
                      {"query": "crm.journey", "input": {"lead_id": str(r["id"])}}))
    for r in (await ctx.call_optional("flow.due_between", since=t0.isoformat(), until=end.isoformat(), mine=mine)) or []:
        items.append((datetime.combine(r["due_on"], datetime.min.time(), zone()), "task", r["title"], r["who"] or "đội", "warn",
                      {"query": "flow.case", "input": {"case_id": str(r["case_id"])}} if r["case_id"] else {"query": "flow.tasks", "input": {}}))
    for part in await ctx.plugs("crm.customer_due", days=inp.days, mine=mine):   # vai không có quyền phần đó: bỏ nhóm, lịch vẫn mở
        for r in part:
            if r["due_on"] >= t0:
                items.append((datetime.combine(r["due_on"], datetime.min.time(), zone()), "pay", f"{r['customer']} đóng {r['label']}",
                              r["detail"], "ok", r["open"]))
    items.sort(key=lambda x: (x[0], x[1] != "appt"))
    label = {"appt": "Hẹn", "callback": "Gọi lại", "task": "Việc", "pay": "Đóng tiền"}
    blocks: list[dict[str, Any]] = [ui.b_kpis([
        ui.kpi("Hẹn gặp", sum(1 for x in items if x[1] == "appt")), ui.kpi("Gọi lại", sum(1 for x in items if x[1] == "callback")),
        ui.kpi("Việc đến hạn", sum(1 for x in items if x[1] == "task")), ui.kpi("Khách đóng tiền", sum(1 for x in items if x[1] == "pay"))])]
    by_day: dict[date, list[tuple[datetime, str, str, str, str, dict[str, Any]]]] = {}
    for it in items:
        by_day.setdefault(it[0].astimezone(zone()).date(), []).append(it)
    for d in sorted(by_day):
        blocks.append(ui.b_list([{"title": x[2], "subtitle": x[3], "tone": x[4],
                                  "badge": f"{x[0].astimezone(zone()):%H:%M}" if x[1] == "appt" else label[x[1]],
                                  "open": {"query": x[5]["query"], "input": x[5]["input"]}} for x in by_day[d]], _day_label(d)))
    if not items:
        blocks.append(ui.b_callout("muted", f"Không có gì trong {inp.days} ngày tới", "Đặt lịch hẹn từ hành trình của từng khách."))
    return ui.card("Lịch hẹn khách" if mine else "Lịch hẹn khách cả đội", blocks, subtitle=f"{t0:%d/%m} → {(end - timedelta(days=1)):%d/%m}", icon="calendar",
                   ask={"title": "Nhờ trợ lý xếp lịch", "prompt": "Giúp tôi xếp lịch tuần này: "})


@m.command("remind_appointments", resource="crm", title="Nhắc lịch hẹn sắp tới", internal=True,
           sweep="""SELECT DISTINCT tenant_id FROM crm.appointments WHERE status = 'scheduled'""")
async def remind_appointments(ctx: Ctx, inp: NoInput) -> dict[str, int]:
    """Vòng quét (quyền hệ thống): nhắc người phụ trách trước giờ hẹn 60 phút, MỘT lần cho mỗi hẹn (khoá chống lặp). Giờ hẹn đọc từ lịch chung."""
    now = datetime.now(zone())
    soon = (await ctx.call_system("schedule.times_of", source=APPT_SOURCE, since=now, until=now + timedelta(minutes=60)))["times"]
    starting = {ref: t["starts_at"] for ref, t in soon.items() if t["status"] == "booked" and now < t["starts_at"] <= now + timedelta(minutes=60)}
    sent = 0
    for a in await ctx.all(
            """SELECT a.id, a.kind, a.place, c.name, l.owner_id, l.id AS lead_id FROM crm.appointments a JOIN crm.leads l ON l.id = a.lead_id
               JOIN crm.customers c ON c.id = l.customer_id WHERE a.status = 'scheduled' AND a.id::text = ANY(%s)""", (list(starting),)):
        at = starting[str(a["id"])]
        sent += await ctx.notify([a["owner_id"]], kind="appointment", title=f"Sắp tới giờ hẹn {a['name']} — {at.astimezone(zone()):%H:%M}",
                                 body=a["place"] or APPT_KIND[a["kind"]], push=f"Còn chưa đầy 1 giờ nữa tới lịch hẹn ({at.astimezone(zone()):%H:%M})",
                                 open={"query": "crm.journey", "input": {"lead_id": str(a["lead_id"])}}, key=f"appt:{a['id']}")
    return {"sent": sent}


@m.command("heal_appointments", resource="crm", title="Đưa hẹn chưa có trên lịch chung lên lịch", internal=True, agent="deny",
           sweep="""SELECT DISTINCT tenant_id FROM crm.appointments WHERE created_at > now() - interval '30 days'""")
async def heal_appointments(ctx: Ctx, inp: NoInput) -> dict[str, int]:
    """Vòng quét (quyền hệ thống), chạy lại an toàn — vá khe lúc chuyển (0158): bản cũ còn chạy sau migration crm/069 tạo / chốt hẹn mà không chạm lịch
    chung. Hẹn chưa có lịch ⇒ đặt lịch theo giờ đã ghi ở hẹn (cột ``at`` — NGỪNG dùng từ crm/074, chỉ còn dòng do BẢN CŨ ghi trong cửa sổ chuyển; dòng bản mới
    để ``at`` rỗng và luôn có lịch cùng giao dịch ⇒ không có gì để vá), rồi chốt / nhả theo kết quả;
    hẹn đã chốt mà lịch còn đang đặt ⇒ chốt / nhả lịch. Hẹn đã có lịch không đụng (giờ của nó ở lịch)."""
    rows = await ctx.all("""SELECT id, lead_id, kind, at, status, created_by FROM crm.appointments WHERE created_at > now() - interval '30 days'
                            ORDER BY created_at LIMIT 1000""")
    times = await appointment_times(ctx, [r["id"] for r in rows], cancelled=True)
    made = closed = 0
    for a in rows:
        t = times.get(str(a["id"]))
        try:
            async with ctx.conn.transaction():
                if t is None:
                    if a["at"] is None:
                        continue   # không giờ để đặt (dòng bản mới đáng ra đã có lịch) — để người xử lý, không đoán giờ
                    await book_appointment(ctx, a, a["at"], a["at"] + _length(a["kind"], 0), by=a["created_by"])
                    made += 1
                elif a["status"] == "scheduled" or t["status"] != "booked":
                    continue
                else:
                    closed += 1
                if a["status"] == "cancelled":
                    await ctx.call_system("schedule.release", source=APPT_SOURCE, source_ref=str(a["id"]), reason="Huỷ hẹn")
                elif a["status"] in ("done", "missed"):
                    await ctx.call_system("schedule.finish", source=APPT_SOURCE, source_ref=str(a["id"]))
        except (Conflict, Invalid, NotFound):
            continue   # lead đã mất người phụ trách lẫn người đặt, … — để lần sau / người xử lý; không làm hỏng các hẹn khác
    return {"made": made, "closed": closed}


def _next_call_slot(now: datetime) -> tuple[date, str]:
    """Giờ gọi gợi ý: đầu giờ kế tiếp + 1 (sớm nhất 9:00); sau 17 giờ ⇒ 9:00 sáng mai."""
    h = max(9, now.hour + 2)
    return (now.date(), f"{h:02d}:00") if h <= 17 else (now.date() + timedelta(days=1), "09:00")


@m.query("suggestions", resource="crm", title="Gợi ý buổi sáng (khách)", agent="deny")
async def suggestions(ctx: Ctx, inp: NoInput) -> list[dict[str, Any]]:
    """Nguồn gợi ý buổi sáng (0074 A): khách CỦA MÌNH quá hẹn gọi lại lâu nhất ⇒ đặt lịch gọi trong hôm nay (lệnh thật, chạy thử thành thẻ)."""
    rows = await ctx.all("""SELECT l.id, c.name, l.next_follow_up FROM crm.leads l JOIN crm.customers c ON c.id = l.customer_id
                            WHERE l.owner_id = core.me() AND l.stage IN ('lead', 'prospect') AND l.next_follow_up < core.today()
                              AND NOT EXISTS (SELECT 1 FROM crm.appointments a WHERE a.lead_id = l.id AND a.status = 'scheduled')
                            ORDER BY l.next_follow_up, c.name LIMIT 2""")
    day, time = _next_call_slot(datetime.now(zone()))
    t0 = today()
    return [{"command": "crm.schedule", "input": {"lead_id": str(r["id"]), "kind": "call", "day": day.isoformat(), "time": time,
                                                  "note": "Gọi lại theo hẹn chăm sóc"},
             "reason": f"{r['name']} quá hẹn gọi lại {(t0 - r['next_follow_up']).days} ngày (hẹn {r['next_follow_up']:%d/%m}) — chưa có lịch nào",
             "key": f"call:{r['id']}", "rank": 100 + i * 10} for i, r in enumerate(rows)]


m.suggests("crm.suggestions", rank=20)


# ── Mốc hạn cho lịch chung (0158): hạn gọi lại khách + đợt khách đóng tiền — KHÔNG là lịch (không chiếm giờ), màn lịch vẽ chồng ──

class MarkersIn(Input):
    since: date
    until: date
    mine: bool = True


@m.plug("schedule.markers", "Hạn gọi lại khách, đợt khách đóng tiền", resource="crm")
async def plug_markers(ctx: Ctx, inp: MarkersIn) -> list[dict[str, Any]]:
    """Bằng quyền người xem (RLS của lead): hạn gọi lại của khách đang chăm trong [since, until); đợt khách đóng tiền (điểm cắm của gói bán hàng)."""
    out: list[dict[str, Any]] = [
        {"on": r["next_follow_up"], "kind": "callback", "label": "Gọi lại", "title": f"Gọi lại {r['name']}", "ref": str(r["id"]), "detail": r["offering_code"] or "",
         "open": {"query": "crm.journey", "input": {"lead_id": str(r["id"])}}}
        for r in await ctx.all("""SELECT l.id, l.next_follow_up, l.offering_code, c.name FROM crm.leads l JOIN crm.customers c ON c.id = l.customer_id
                                  WHERE l.stage IN ('lead', 'prospect') AND l.next_follow_up >= %s AND l.next_follow_up < %s
                                    AND (NOT %s OR l.owner_id = core.me()) ORDER BY l.next_follow_up LIMIT 1000""", (inp.since, inp.until, inp.mine))]
    days = (inp.until - today()).days
    if days > 0:
        for part in await ctx.plugs("crm.customer_due", days=min(days, 92), mine=inp.mine):
            out += [{"on": r["due_on"], "kind": "installment", "label": "Đóng tiền", "title": f"{r['customer']} đóng {r['label']}", "ref": r["label"],
                     "detail": r["detail"], "open": r["open"]} for r in part if inp.since <= r["due_on"] < inp.until]
    return out



# ── Màn Lịch (0240): hẹn khách hiện đủ tên khách / địa điểm / nút mở hồ sơ cho người được xem lead (RLS của crm), dời bằng lệnh của crm; tạo nhanh từ ô giờ ──

class CalItemsIn(Input):
    since: datetime
    until: datetime
    parties: dict[str, list[UUID]] | None = None   # mã lịch → bên ngoài của lịch (lịch người xem đã đọc được chi tiết): bên ngoài là khách ⇒ nút mở hồ sơ khách


@m.plug("schedule.calendar_items", "Hẹn khách trên màn Lịch: tên khách, địa điểm, mở hồ sơ, dời hẹn", resource="crm")
async def plug_calendar_items(ctx: Ctx, inp: CalItemsIn) -> list[dict[str, Any]]:
    """Bằng quyền người xem: hẹn của khách người xem thấy qua RLS của lead (sale: khách mình; trưởng nhóm: khách của đội) chạm [since, until). Lịch hẹn là
    lịch riêng tư (0158) — người không ở trong lịch mà vẫn thấy khách thì vốn đã thấy hẹn ở Lịch hẹn khách, nên màn Lịch hiện đúng điều đó thay cho "Bận".
    Mã lịch lấy từ hợp đồng ``schedule.times_of`` (chỉ giờ + mã), tên khách / địa điểm đọc ở đây."""
    out: list[dict[str, Any]] = []
    if wanted := sorted({p for ps in (inp.parties or {}).values() for p in ps}):
        leads: dict[UUID, dict[str, Any]] = {}
        for r in await ctx.all("""SELECT DISTINCT ON (c.party_id) c.party_id, c.name, l.id AS lead_id, l.offering_code FROM crm.customers c
                                  JOIN crm.leads l ON l.customer_id = c.id WHERE c.party_id = ANY(%s) ORDER BY c.party_id, l.updated_at DESC""", (wanted,)):
            leads[r["party_id"]] = r
        for eid, ps in (inp.parties or {}).items():
            if got := [leads[p] for p in ps if p in leads]:
                out.append({"event_id": eid, "subtitle": " · ".join(r["offering_code"] for r in got if r["offering_code"]),
                            "links": [{"title": f"Hồ sơ khách {r['name']}", "query": "crm.journey", "input": {"lead_id": str(r["lead_id"])}} for r in got]})
    times = (await ctx.call_system("schedule.times_of", source=APPT_SOURCE, since=inp.since, until=inp.until))["times"]
    if not times:
        return out
    rows = await ctx.all("""SELECT a.id, a.kind, a.place, l.id AS lead_id, l.offering_code, c.name FROM crm.appointments a
                            JOIN crm.leads l ON l.id = a.lead_id JOIN crm.customers c ON c.id = l.customer_id
                            WHERE a.id = ANY(%s) AND a.status = 'scheduled'""", ([UUID(k) for k in times],))
    movable = can_run(ctx.p, "crm.move_appointment")
    for r in rows:
        t = times[str(r["id"])]
        lead = {"query": "crm.journey", "input": {"lead_id": str(r["lead_id"])}, "title": r["name"]}
        out.append({"event_id": str(t["event_id"]), "title": f"{APPT_KIND[r['kind']]} — {r['name']}", "kind": APPT_CAL_KIND[r["kind"]][0],
                    "subtitle": " · ".join(x for x in (r["place"], r["offering_code"] or "") if x), "open": lead,
                    "links": [{"title": f"Hồ sơ khách {r['name']}", "query": lead["query"], "input": lead["input"]}],
                    "move": {"command": "crm.move_appointment", "preset": {"appointment_id": str(r["id"])}, "fields": {"day": "day", "start": "time"}}
                    if movable else None})
    return out


# từ gợi ý loại hẹn trong câu người gõ ("Thứ 5 2h dẫn khách A xem Sun Feliza") — thứ tự là ưu tiên: đi xem trước gặp
APPT_WORDS = {"visit": ("dan khach", "tham quan", "di xem", "xem nha", "xem can", "xem du an", "nha mau", "xem mau", "xem dat", "site"),
              "call": ("goi lai", "goi cho", "goi dien", "goi ", "call"),
              "meet": ("gap", "hen", "cafe", "ca phe", "ky hd", "ky hop dong", "tu van")}


HONORIFICS = "anh|chi|em|co|chu|bac|ong|ba|a|c|mr|ms"


class PickIn(Input):
    q: str = field("Tìm khách", "", max_length=100)


async def _my_leads(ctx: Ctx) -> list[dict[str, Any]]:
    return await ctx.all("""SELECT l.id, l.stage, l.offering_code, c.name, l.owner_id = core.me() AS mine FROM crm.leads l
                            JOIN crm.customers c ON c.id = l.customer_id WHERE l.stage IN ('lead', 'prospect')
                            ORDER BY l.owner_id = core.me() DESC, l.updated_at DESC LIMIT 3000""")


def _lead_option(r: dict[str, Any]) -> dict[str, str]:
    return {"value": str(r["id"]), "label": r["name"], "hint": " · ".join(x for x in (r["offering_code"] or "", "" if r["mine"] else "khách của đội") if x)}


@m.query("lead_pick", resource="crm", title="Chọn khách cho lịch hẹn")
async def lead_pick(ctx: Ctx, inp: PickIn) -> list[dict[str, str]]:
    """Ô chọn khách khi tạo nhanh trên màn Lịch (0225 — chọn, không gõ mã): khách đang chăm người chọn thấy (RLS), của mình trước, khớp tên không dấu."""
    q = fold(inp.q.strip())
    rows = [r for r in await _my_leads(ctx) if not q or q in fold(r["name"])]
    return [_lead_option(r) for r in rows[:20]]


class QuickIn(Input):
    text: str = ""


@m.plug("schedule.quick_add", "Đặt hẹn khách từ màn Lịch", resource="crm", action="write")
async def plug_quick_add(ctx: Ctx, inp: QuickIn) -> dict[str, Any]:
    """Cách tạo của crm trên màn Lịch: ba loại hẹn khách, mỗi loại là lệnh ``crm.schedule`` (khách chọn từ danh sách). Có câu người gõ ⇒ đoán khách (tên
    khách của mình xuất hiện trong câu, dài nhất thắng) và [[offering]] (tên / mã có trong câu ⇒ địa điểm) — chỉ là gợi ý, người xác nhận."""
    creators = [{"key": cal, "label": label, "kind": cal, "command": "crm.schedule", "preset": {"kind": k},
                 "fields": {"day": "day", "start": "time", "minutes": "minutes", "title": "note"}, "place": "place", "words": list(APPT_WORDS[k]),
                 "subject": {"field": "lead_id", "label": "Khách", "search": "crm.lead_pick", "required": True}}
                for k in APPT_WORDS for cal, label in (APPT_CAL_KIND[k],)]
    out: dict[str, Any] = {"creators": creators}
    text = f" {fold(inp.text)} "
    if inp.text.strip():
        best = None
        for r in await _my_leads(ctx):
            n = fold(r["name"]).strip()
            # tên đầy đủ trong câu; hoặc tên gọi (từ cuối, ≥ 3 chữ) ngay sau một từ xưng hô — "chị Lan" khớp "Nguyễn Thị Lan", còn "tuần sau" không khớp "Tuấn"
            last = n.split()[-1] if n else ""
            hit = len(n) >= 3 and f" {n} " in text and len(n)
            if not hit and len(last) >= 3 and re.search(rf"\b(?:{HONORIFICS}) {re.escape(last)}\b", text):
                hit = len(last)
            if hit and (best is None or hit > best[0]):
                best = (hit, r)
        if best:
            out["subject"] = _lead_option(best[1])
        for part in await ctx.plugs("crm.offering_options"):   # nhãn "MÃ — Tên dự án": khớp tên hoặc mã
            for o in sorted(part, key=lambda o: -len(str(o.get("label", "")))):
                name = str(o.get("label") or o.get("value") or "").split(" — ", 1)[-1]
                if any(len(x) >= 3 and f" {x} " in text for x in {fold(name).strip(), fold(str(o.get("value", ""))).strip()}):
                    out["place"] = name
                    break
            if "place" in out:
                break
    return out
