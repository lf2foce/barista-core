"""BẢNG CÔNG (tính lúc xem bằng ``sheet.py``), CHỐT THÁNG và XÁC NHẬN của người lao động (0150 §4.3).

Chốt (một đội hoặc cả công ty, người có quyền quản lý chấm công của phạm vi đó): chặn khi còn giờ chấm có cờ chưa xét hoặc đơn đang chờ trong tháng;
mỗi người một dòng tổng (``close_lines``, DB: một người một tháng một lần, người chốt không chốt chính mình) kèm mã băm đầu vào; rồi đẩy sang Lương
(``pay.record_work``, gói tắt ⇒ bỏ qua): ngày có lương ``days.paid``, ngày không lương ``days.unpaid``, giờ làm thêm ``ot.150/200/300``. Tháng đã chốt
của một người ⇒ DB không nhận thêm giờ chấm / đơn duyệt vào tháng đó (điều chỉnh: kỳ sau — lát 5).
"""

import hashlib
import json
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from barista.api import Ctx, Day, Denied, Input, Invalid, NoInput, NotFound, field, today, ui, zone
from barista.modules.timekeeping import MODES, m
from barista.modules.timekeeping.common import holidays, month_end, names, segments
from barista.modules.timekeeping.leave import balance
from barista.modules.timekeeping.punches import MonthIn, month_of_input
from barista.modules.timekeeping.sheet import month_sheet

STATUS = {"work": "Đi làm", "leave": "Nghỉ", "absent": "Vắng", "holiday": "Lễ", "off": "Nghỉ tuần", "incomplete": "Chưa đủ giờ chấm"}
DAY_FLAGS: dict[str, str] = {"unreviewed": "Chờ xét", "missing_out": "Thiếu giờ ra", "missing_in": "Thiếu giờ vào", "ot_not_worked": "Giờ làm thêm chưa đủ"}
OT_LABEL = {"ot.150": "Làm thêm ngày thường", "ot.200": "Làm thêm ngày nghỉ tuần", "ot.300": "Làm thêm ngày lễ"}


def _digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


async def sheets(ctx: Ctx, month: date, users: list[UUID] | None, *, until: date | None = None) -> dict[str, dict[str, Any]]:
    """Bảng công tháng của những người người xem thấy (RLS) — hoặc đúng ``users``: mọi dòng cách chấm hiệu lực trong tháng (đổi giữa tháng, vào làm /
    nghỉ việc giữa tháng — review 10/11), người có ít nhất một ngày được chấm công trong tháng. ``enrolment`` = dòng ĐANG CHẤM cuối cùng (đội, cách)."""
    first, last = month, month_end(month)
    segs = {k: v for k, v in (await segments(ctx, first, last, users)).items() if any(sg["mode"] != "off" for sg in v)}
    ids = [v[0]["user_id"] for v in segs.values()]
    if not ids:
        return {}
    punches = await ctx.all("""SELECT p.user_id,p.at,p.direction,p.flags,r.decision FROM timekeeping.punches p
                               LEFT JOIN timekeeping.punch_reviews r ON r.tenant_id=p.tenant_id AND r.punch_id=p.id
                               WHERE p.user_id=ANY(%s) AND (p.at AT TIME ZONE core.tz())::date BETWEEN %s AND %s ORDER BY p.at""",
                            (ids, first, last))
    leaves = await ctx.all("""SELECT r.user_id,r.from_day,r.to_day,r.half_day,r.leave_type,t.paid_by FROM timekeeping.leave_requests r
                              JOIN timekeeping.leave_types t ON t.tenant_id=r.tenant_id AND t.code=r.leave_type
                              WHERE r.user_id=ANY(%s) AND r.status='approved' AND r.from_day<=%s AND r.to_day>=%s""", (ids, last, first))
    ots = await ctx.all("""SELECT user_id,day,sum(minutes)::int AS minutes FROM timekeeping.overtime_requests WHERE user_id=ANY(%s) AND status='approved'
                           AND day BETWEEN %s AND %s GROUP BY 1,2""", (ids, first, last))
    hol = holidays(first, last)
    out: dict[str, dict[str, Any]] = {}
    for key, segs_ in segs.items():
        mine = [{"at": p["at"].astimezone(zone()), "direction": p["direction"], "flagged": bool(p["flags"]) and p["decision"] is None}
                for p in punches if str(p["user_id"]) == key and p["decision"] != "reject"]
        leave: dict[str, dict[str, Any]] = {}
        for r in (r for r in leaves if str(r["user_id"]) == key):
            d = max(r["from_day"], first)
            while d <= min(r["to_day"], last):
                leave[d.isoformat()] = {"type": r["leave_type"], "paid_by": r["paid_by"], "fraction": 0.5 if r["half_day"] else 1.0}
                d += timedelta(days=1)
        overtime = {r["day"].isoformat(): r["minutes"] for r in ots if str(r["user_id"]) == key}
        inputs = {"month": first.isoformat(), "segments": [{**sg, "from": sg["from"].isoformat()} for sg in segs_], "holidays": hol, "leave": leave,
                  "overtime": overtime, "punches": [{**p, "at": p["at"].isoformat()} for p in mine]}
        got = month_sheet(first, until=until or last, segments=segs_, holidays=hol, punches=mine, leave=leave, overtime=overtime)
        current = [sg for sg in segs_ if sg["mode"] != "off"][-1]
        out[key] = {"enrolment": current, **got, "inputs_hash": _digest(inputs)}
    return out


def _ot_hours(t: dict[str, Any]) -> float:
    return round(sum(t["ot"].values()) / 60, 2)


@m.query("my_month", resource="timekeeping", title="Bảng công của tôi")
async def my_month(ctx: Ctx, inp: MonthIn) -> dict[str, Any]:
    """Bảng công tháng của CHÍNH người xem (tới hôm nay, hoặc cả tháng đã qua): ngày công có lương, đi muộn, vắng, nghỉ, làm thêm, phép năm còn lại,
    tình trạng chốt / xác nhận. Trợ lý trả lời "tháng này tôi đi muộn mấy buổi, còn mấy ngày phép" TỪ THẺ NÀY."""
    user = ctx.p.user_id
    if user is None:
        raise Denied("Cần đăng nhập")
    month = month_of_input(inp.month)
    if month > today():
        raise Invalid("Tháng chưa tới")
    got = (await sheets(ctx, month, [user], until=min(month_end(month), today()))).get(str(user))
    bal = await balance(ctx, user, "PHEP_NAM", month.year)
    line = await ctx.one("SELECT id,confirmed_at,dispute,confirm_by AS by FROM timekeeping.close_lines WHERE user_id=%s AND period=%s", (user, month))
    blocks: list[dict[str, Any]] = []
    if got is None:
        blocks.append(ui.b_callout("warn", "Chưa đăng ký chấm công", "Nhân sự chưa gán giờ làm cho anh/chị trong tháng này."))
        t: dict[str, Any] = {"paid_days": 0, "late_count": 0, "late_minutes": 0, "absent_days": 0, "leave": {}, "ot": {}, "unreviewed": 0, "missing_out": 0}
        days: list[dict[str, Any]] = []
    else:
        t, days = got["totals"], got["days"]
    if line and not line["confirmed_at"] and not line["dispute"]:
        blocks.append(ui.b_callout("accent", "Bảng công đã chốt — chờ anh/chị xác nhận", f"Xác nhận hoặc phản hồi trước {line['by']:%d/%m/%Y}."))
    elif line:
        blocks.append(ui.b_text("Đã xác nhận bảng công." if line["confirmed_at"] else f"Đã phản hồi: {line['dispute']}", "Chốt bảng công"))
    if t["unreviewed"] or t["missing_out"]:
        blocks.append(ui.b_callout("warn", "Cần chú ý", facts=[f for f in (f"{t['unreviewed']} ngày có giờ chấm chờ quản lý xét" if t["unreviewed"] else "",
                                                                         f"{t['missing_out']} ngày thiếu giờ chấm (chưa tính công) — xin bổ sung giờ" if t["missing_out"] else "") if f]))
    leave_used = sum(t["leave"].values())
    blocks.append(ui.b_kpis([ui.kpi("Ngày công có lương", t["paid_days"]), ui.kpi("Đi muộn (buổi)", t["late_count"], tone="orange" if t["late_count"] else None),
                             ui.kpi("Phút đi muộn", t["late_minutes"]), ui.kpi("Vắng", t["absent_days"], tone="red" if t["absent_days"] else None),
                             ui.kpi("Ngày nghỉ trong tháng", leave_used), ui.kpi("Làm thêm (giờ)", _ot_hours(t)),
                             ui.kpi("Phép năm còn lại", float(bal["balance"] - bal["pending"]), tone="green",
                                    hint=f"Đã trừ {float(bal['pending']):g} ngày đang chờ duyệt" if bal["pending"] else "")]))
    rows = [{"id": d["day"], "day": d["day"], "status": d["status"], "in": d["first_in"], "out": d["last_out"], "late": d["late_minutes"] or None,
             "leave": d["leave_type"], "ot": round(d["ot_minutes"] / 60, 2) or None,
             "flags": ", ".join(str(DAY_FLAGS.get(f, f)) for f in d["flags"])}
            for d in reversed(days)]
    blocks.append(ui.b_table(ui.table(rows, [ui.col("day", "Ngày", "date"), ui.col("status", "Công", "badge", labels=STATUS,
                                                                                  tones={"work": "green", "leave": "blue", "absent": "red", "holiday": "purple", "incomplete": "orange",
                                                                                         "off": "gray"}),
                                             ui.col("in", "Vào"), ui.col("out", "Ra"), ui.col("late", "Muộn (phút)", "number"), ui.col("leave", "Loại nghỉ"),
                                             ui.col("ot", "Làm thêm (giờ)", "number"), ui.col("flags", "Lưu ý")],
                                      title="Từng ngày", empty="Chưa có ngày nào")))
    acts = [ui.act("timekeeping.request_leave", "Xin nghỉ", style="primary"), ui.act("timekeeping.request_overtime", "Xin làm thêm"),
            ui.act("timekeeping.request_correction", "Bổ sung giờ")]
    if line and not line["confirmed_at"] and not line["dispute"]:
        acts.insert(0, ui.act("timekeeping.confirm_timesheet", "Xác nhận bảng công", {"month": month.isoformat()}, "primary"))
    return ui.card(f"Bảng công tháng {month:%m/%Y}", blocks, subtitle=f"Tính tới {min(month_end(month), today()):%d/%m/%Y}", icon="calendar", actions=acts)


class TeamMonth(Input):
    month: Day | None = field("Tháng", None, help="Ngày bất kỳ trong tháng; trống = tháng này")
    team_code: str = field("Đội", "", options_query="admin.team_options", help="Trống = mọi người anh/chị được xem")


async def _team(ctx: Ctx, code: str) -> UUID | None:
    if not code:
        return None
    tid = await ctx.val("SELECT id FROM core.teams WHERE code=%s", (code,))
    if tid is None:
        raise NotFound(f"Không có đội {code}")
    return tid


@m.query("timesheet", resource="timekeeping", min_scope="team", title="Bảng công đội / công ty")
async def timesheet(ctx: Ctx, inp: TeamMonth) -> dict[str, Any]:
    """Tổng công tháng từng người người xem được thấy (trưởng đội: đội mình; Nhân sự: cả công ty) — giờ, không tiền. Kèm tình trạng chốt / xác nhận."""
    month = month_of_input(inp.month)
    team = await _team(ctx, inp.team_code)
    got = await sheets(ctx, month, None, until=min(month_end(month), today()))
    closed = {str(r["user_id"]): r for r in await ctx.all("SELECT user_id,confirmed_at,dispute FROM timekeeping.close_lines WHERE period=%s", (month,))}
    who = await names(ctx, [s["enrolment"]["user_id"] for s in got.values()])
    rows = []
    for key, s in got.items():
        if team and s["enrolment"]["team_id"] != team:
            continue
        t, c = s["totals"], closed.get(key)
        rows.append({"id": key, "name": who.get(key, ""), "mode": s["enrolment"]["mode"], "paid": t["paid_days"], "unpaid": t["unpaid_days"],
                     "late": t["late_count"], "absent": t["absent_days"], "leave": sum(t["leave"].values()), "ot": _ot_hours(t),
                     "pending": t["unreviewed"], "missing": t["missing_out"],
                     "state": "open" if c is None else ("confirmed" if c["confirmed_at"] else "disputed" if c["dispute"] else "closed")})
    rows.sort(key=lambda r: r["name"])
    return ui.table(
        rows,
        [ui.col("name", "Người"), ui.col("mode", "Cách tính", "badge", labels=MODES), ui.col("paid", "Có lương", "number"),
         ui.col("unpaid", "Không lương", "number"), ui.col("late", "Đi muộn", "number"), ui.col("absent", "Vắng", "number"), ui.col("leave", "Nghỉ", "number"),
         ui.col("ot", "Làm thêm (giờ)", "number"), ui.col("pending", "Ngày chờ xét", "number"), ui.col("missing", "Thiếu giờ chấm", "number"),
         ui.col("state", "Chốt", "badge", labels={"open": "Chưa chốt", "closed": "Chờ xác nhận", "confirmed": "Đã xác nhận", "disputed": "Có phản hồi"},
                tones={"open": "gray", "closed": "orange", "confirmed": "green", "disputed": "red"})],
        title=f"Bảng công tháng {month:%m/%Y}", limit=2000, empty="Chưa ai được gán giờ làm",
        kpis=[ui.kpi("Người", len(rows)), ui.kpi("Ngày chờ xét", sum(r["pending"] for r in rows), tone="orange" if any(r["pending"] for r in rows) else None),
              ui.kpi("Chưa xác nhận", sum(r["state"] == "closed" for r in rows))],
        note="Giờ công, không tiền lương. Chốt tháng cần xét hết giờ chấm có cờ và xử lý đơn đang chờ",
    )


class ClosePeriod(Input):
    month: Day = field("Tháng", example="01/10/2026", help="Ngày bất kỳ trong tháng đã qua")
    team_code: str = field("Đội", "", options_query="admin.team_options", help="Trống = cả công ty (cần quyền cả công ty)")
    confirm_days: int = field("Số ngày để người lao động xác nhận", 3, ge=1, le=15)


@m.command("close_period", resource="timekeeping", action="manage", title="Chốt bảng công tháng", agent="deny", risk="authority")
async def close_period(ctx: Ctx, inp: ClosePeriod) -> dict[str, Any]:
    """Chốt tháng cho đội / công ty: ghi tổng từng người (bất biến), đẩy công sang Lương, mời người lao động xác nhận. Người còn việc mở (giờ có cờ
    chưa xét, ngày thiếu giờ chấm, đơn đang chờ) chưa chốt — báo tên + việc; người khác vẫn chốt (chốt lại sau cho người còn lại). Trưởng đội chốt đội mình (RLS chỉ
    cho thấy đội mình); người chốt không chốt chính mình (DB) — bảng công của họ chờ người khác chốt."""
    month = month_of_input(inp.month)
    if month_end(month) >= today():
        raise Invalid("Chỉ chốt tháng đã qua")
    team = await _team(ctx, inp.team_code)
    if team is None and not ctx.p.has_scope("timekeeping", "all"):
        raise Denied("Chốt cả công ty cần quyền chấm công cả công ty — chọn đội của anh/chị")
    await ctx.exec("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"timekeeping.close:{ctx.p.tenant_id}:{month}",))
    got = await sheets(ctx, month, None)
    done = {str(r["user_id"]) for r in await ctx.all("SELECT user_id FROM timekeeping.close_lines WHERE period=%s", (month,))}
    todo = {k: s for k, s in got.items() if (team is None or s["enrolment"]["team_id"] == team) and k not in done and k != str(ctx.p.user_id)}
    if not todo:
        raise Invalid("Không còn ai chưa chốt trong phạm vi này" + (" (bảng công của chính anh/chị cần người khác chốt)" if str(ctx.p.user_id) in got else ""))
    ids = [s["enrolment"]["user_id"] for s in todo.values()]
    who = await names(ctx, ids)
    # việc còn mở của TỪNG người (review 10/11): người còn việc thì chưa chốt — những người khác của đội vẫn chốt được
    blocked: dict[str, list[str]] = {}
    for k, s in todo.items():
        if s["totals"]["unreviewed"]:
            blocked.setdefault(k, []).append(f"{s['totals']['unreviewed']} ngày có giờ chấm chưa xét")
        if s["totals"]["incomplete"]:   # chấm vào mà không ra (hoặc ngược lại): không tính công — bổ sung giờ có duyệt trước
            blocked.setdefault(k, []).append("thiếu giờ chấm ngày " + ", ".join(f"{date.fromisoformat(d):%d/%m}" for d in s["totals"]["incomplete"][:5]))
    pending = await ctx.all("""SELECT user_id,count(*) AS n FROM (
                                 SELECT user_id FROM timekeeping.leave_requests WHERE status='requested' AND from_day<=%(e)s AND to_day>=%(s)s
                                 UNION ALL SELECT user_id FROM timekeeping.overtime_requests WHERE status='requested' AND day BETWEEN %(s)s AND %(e)s
                                 UNION ALL SELECT user_id FROM timekeeping.corrections WHERE status='requested'
                                   AND (at AT TIME ZONE core.tz())::date BETWEEN %(s)s AND %(e)s) x
                               WHERE user_id=ANY(%(u)s) GROUP BY user_id""", {"s": month, "e": month_end(month), "u": ids})
    for r in pending:
        blocked.setdefault(str(r["user_id"]), []).append(f"{r['n']} đơn đang chờ duyệt")
    blockers = [f"{who.get(k, '')}: {'; '.join(v)}" for k, v in sorted(blocked.items(), key=lambda kv: who.get(kv[0], ""))]
    todo = {k: s for k, s in todo.items() if k not in blocked}
    if not todo:
        raise Invalid("Chưa chốt được — " + "; ".join(blockers[:8]) + (f" (và {len(blockers) - 8} người khác)" if len(blockers) > 8 else ""))
    ids = [s["enrolment"]["user_id"] for s in todo.values()]
    confirm_by = today() + timedelta(days=inp.confirm_days)
    cid = await ctx.val("INSERT INTO timekeeping.closes(period,team_id,confirm_by,closed_by) VALUES(%s,%s,%s,core.me()) RETURNING id", (month, team, confirm_by))
    pushed = 0
    for _key, s in sorted(todo.items()):
        t, e = s["totals"], s["enrolment"]
        lid = await ctx.val("""INSERT INTO timekeeping.close_lines(close_id,period,user_id,team_id,totals,inputs_hash,confirm_by,closed_by)
                               VALUES(%s,%s,%s,%s,%s::jsonb,%s,%s,core.me()) RETURNING id""",
                            (cid, month, e["user_id"], e["team_id"], json.dumps(t, ensure_ascii=False), s["inputs_hash"], confirm_by))
        work = [("std", "day", "days.std", t["std_days"]), ("paid", "day", "days.paid", t["paid_days"]), ("unpaid", "day", "days.unpaid", t["unpaid_days"])]
        work += [(cls.replace(".", ""), "hour", cls, round(mins / 60, 4)) for cls, mins in sorted(t["ot"].items())]
        for tag, unit, role, qty in work:
            if qty <= 0:
                continue
            got_ = await ctx.call_system_optional(
                "pay.record_work", source="timekeeping.close", source_ref=str(lid), key=f"timekeeping.close:{lid}:{tag}", user_id=e["user_id"],
                work_date=month_end(month), kind="qty", unit=unit, quantity=qty, role_key=role,
                evidence={"period": month.isoformat(), "late_count": t["late_count"], "absent_days": t["absent_days"], "leave": t["leave"]})
            pushed += got_ is not None
    await ctx.notify(ids, kind="timekeeping", title=f"Bảng công tháng {month:%m/%Y} đã chốt", body=f"Xác nhận hoặc phản hồi trước {confirm_by:%d/%m/%Y}",
                     push="Bảng công đã chốt — mời anh/chị xác nhận", open={"query": "timekeeping.my_month", "input": {"month": month.isoformat()}})
    return {"close_id": cid, "people": len(todo), "pushed": pushed, "left": blockers,
            "summary": f"Đã chốt bảng công {month:%m/%Y} cho {len(todo)} người" + (f", gửi {pushed} dòng công sang Lương" if pushed else "")
                       + (f" — chưa chốt {len(blockers)} người: " + "; ".join(blockers[:5]) if blockers else "")}


class Confirm(Input):
    month: Day = field("Tháng", example="01/10/2026")
    agree: bool = field("Đồng ý với bảng công", True)
    note: str = field("Phản hồi (nếu không đồng ý)", "", max_length=500, example="Ngày 15/10 tôi đi công tác, không phải vắng")


@m.command("confirm_timesheet", resource="timekeeping", title="Xác nhận bảng công", agent="propose", risk="low")
async def confirm_timesheet(ctx: Ctx, inp: Confirm) -> dict[str, Any]:
    """Người lao động xác nhận / phản hồi bảng công đã chốt của CHÍNH mình (DB: chỉ chính chủ, một lần). Phản hồi ⇒ báo người chốt; sửa = kỳ sau."""
    month = month_of_input(inp.month)
    if not inp.agree and not inp.note.strip():
        raise Invalid("Không đồng ý thì ghi phản hồi")
    row = await ctx.one("SELECT id,confirmed_at,dispute,closed_by FROM timekeeping.close_lines WHERE user_id=%s AND period=%s", (ctx.p.user_id, month))
    if not row:
        raise NotFound(f"Bảng công tháng {month:%m/%Y} của anh/chị chưa chốt")
    if row["confirmed_at"] or row["dispute"]:
        raise Invalid("Đã xác nhận / phản hồi bảng công tháng này")
    if inp.agree:
        await ctx.exec("UPDATE timekeeping.close_lines SET confirmed_at=now() WHERE id=%s", (row["id"],))
    else:
        await ctx.exec("UPDATE timekeeping.close_lines SET dispute=%s WHERE id=%s", (inp.note.strip(), row["id"]))
        await ctx.notify([row["closed_by"]], kind="timekeeping", title=f"Có phản hồi bảng công tháng {month:%m/%Y}", body=inp.note.strip()[:200],
                         open={"query": "timekeeping.timesheet", "input": {"month": month.isoformat()}})
    return {"close_line_id": row["id"], "summary": f"Đã {'xác nhận' if inp.agree else 'gửi phản hồi'} bảng công tháng {month:%m/%Y}"}


@m.query("pending_confirm", resource="timekeeping", title="Bảng công chờ tôi xác nhận")
async def pending_confirm(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await ctx.all("""SELECT id,period,confirm_by FROM timekeeping.close_lines WHERE user_id=core.me() AND confirmed_at IS NULL AND dispute=''
                            ORDER BY period""")
    for r in rows:
        r["label"] = f"Tháng {r['period']:%m/%Y}"
    return ui.table(rows, [ui.col("label", "Bảng công"), ui.col("confirm_by", "Xác nhận trước", "date")], title="Bảng công chờ tôi xác nhận",
                    empty="Không có bảng công nào chờ xác nhận", kpis=[ui.kpi("Chờ xác nhận", len(rows))])


@m.query("suggestions", resource="timekeeping", title="Gợi ý chấm công buổi sáng")
async def suggestions(ctx: Ctx, inp: NoInput) -> list[dict[str, Any]]:
    """Nguồn đề xuất buổi sáng (0074 A) cho CHÍNH người nhận: bảng công đã chốt chưa xác nhận ⇒ thẻ "Xác nhận bảng công" (người bấm mới chạy)."""
    rows = await ctx.all("""SELECT period,confirm_by FROM timekeeping.close_lines WHERE user_id=core.me() AND confirmed_at IS NULL AND dispute=''
                            ORDER BY period LIMIT 2""")
    return [{"command": "timekeeping.confirm_timesheet", "input": {"month": r["period"].isoformat(), "agree": True},
             "reason": f"Bảng công tháng {r['period']:%m/%Y} đã chốt — xác nhận trước {r['confirm_by']:%d/%m/%Y} (không đúng thì sửa thẻ, ghi phản hồi)",
             "key": f"timekeeping.confirm:{r['period']:%Y-%m}", "rank": 30} for r in rows]


m.suggests("timekeeping.suggestions", rank=35)

