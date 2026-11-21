"""NGHỈ PHÉP và LÀM THÊM GIỜ (0150 §4.3): đơn của CHÍNH người lao động qua chuỗi duyệt (trưởng đội), sổ phép chỉ thêm (số dư = tổng dòng), trần làm
thêm là quy định ``vn-labour`` (vượt ⇒ từ chối lúc xin, kèm lý do — không tự cắt giờ). Trợ lý soạn đơn cho chính người hỏi (thẻ người bấm)."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from barista.api import Ctx, Day, Denied, Input, Invalid, NoInput, NotFound, field, today, ui, zone
from barista.modules.timekeeping import REQUEST_STATUS, m
from barista.modules.timekeeping.common import enrolments, holidays, labour_rule, member, names
from barista.modules.timekeeping.sheet import _minutes, working_days


def _me(ctx: Ctx) -> UUID:
    if ctx.p.user_id is None:
        raise Denied("Cần đăng nhập của chính người lao động")
    return ctx.p.user_id


async def balance(ctx: Ctx, user: UUID, leave_type: str, year: int) -> dict[str, Decimal]:
    """Số dư phép = tổng sổ phép của năm; ``pending`` = đơn đang chờ duyệt (giữ chỗ khi xin đơn mới)."""
    have = await ctx.val("SELECT coalesce(sum(days),0) FROM timekeeping.leave_ledger WHERE user_id=%s AND leave_type=%s AND year=%s",
                         (user, leave_type, year))
    pending = await ctx.val("""SELECT coalesce(sum(days),0) FROM timekeeping.leave_requests WHERE user_id=%s AND leave_type=%s AND status='requested'
                               AND extract(year FROM from_day)=%s""", (user, leave_type, year))
    return {"balance": Decimal(have), "pending": Decimal(pending)}


class RequestLeave(Input):
    leave_type: str = field("Loại nghỉ", "PHEP_NAM", options_query="timekeeping.leave_type_options")
    from_day: Day = field("Từ ngày", example="13/11/2026")
    to_day: Day | None = field("Đến ngày", None, example="13/11/2026", help="Trống = nghỉ một ngày")
    half_day: bool = field("Nửa ngày", False, help="Chỉ khi nghỉ một ngày")
    reason: str = field("Lý do", "", max_length=300, example="Việc gia đình")


@m.command("request_leave", resource="timekeeping", title="Xin nghỉ", agent="propose", risk="low")
async def request_leave(ctx: Ctx, inp: RequestLeave) -> dict[str, Any]:
    """TRƯỚC KHI soạn: gọi timekeeping.leave_types để lấy đúng MÃ loại nghỉ (ốm, việc riêng, không lương… — đừng đoán; mặc định PHEP_NAM chỉ là phép năm)
    và timekeeping.my_month để xem phép còn lại. Đơn nghỉ CỦA CHÍNH người bấm: số ngày = ngày làm việc theo lịch (bỏ ngày nghỉ tuần, ngày lễ); loại trừ số dư ⇒ cần đủ số dư (đã trừ đơn đang
    chờ). Qua chuỗi ``leave`` — người xin không tự duyệt. Không ghi chẩn đoán bệnh, chỉ loại nghỉ."""
    user = _me(ctx)
    to = inp.to_day or inp.from_day
    if to < inp.from_day or (to - inp.from_day).days > 92:
        raise Invalid("Khoảng ngày không hợp lệ (tối đa 3 tháng một đơn)")
    if inp.half_day and to != inp.from_day:
        raise Invalid("Nửa ngày chỉ khi nghỉ một ngày")
    lt = await ctx.one("SELECT code,name,paid_by,uses_balance FROM timekeeping.leave_types WHERE code=%s AND active", (inp.leave_type.strip().upper(),))
    if not lt:
        raise NotFound("Không có loại nghỉ này")
    e = (await enrolments(ctx, inp.from_day, [user])).get(str(user))
    weekdays = list(e["weekdays"]) if e else [1, 2, 3, 4, 5]
    days = working_days(inp.from_day, to, weekdays=weekdays, holidays=holidays(inp.from_day, to))
    n = Decimal("0.5") if inp.half_day else Decimal(len(days))
    if not days:
        raise Invalid("Khoảng này không có ngày làm việc nào (ngày nghỉ tuần / ngày lễ)")
    if await ctx.val("""SELECT 1 FROM timekeeping.leave_requests WHERE user_id=%s AND status IN ('requested','approved')
                        AND daterange(from_day,to_day,'[]') && daterange(%s,%s,'[]')""", (user, inp.from_day, to)):
        raise Invalid("Đã có đơn nghỉ trùng ngày (đang chờ hoặc đã duyệt)")
    if lt["uses_balance"]:
        b = await balance(ctx, user, lt["code"], inp.from_day.year)
        if b["balance"] - b["pending"] < n:
            raise Invalid(f"Không đủ phép: còn {b['balance'] - b['pending']:g} ngày (sau đơn đang chờ), xin {n:g} ngày — chọn loại nghỉ khác")
    rid = await ctx.val("""INSERT INTO timekeeping.leave_requests(user_id,team_id,leave_type,from_day,to_day,half_day,days,reason,requested_by)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,core.me()) RETURNING id""",
                        (user, await ctx.home_team(user), lt["code"], inp.from_day, to, inp.half_day, n, inp.reason.strip()))
    span = f"{inp.from_day:%d/%m}" + (f"–{to:%d/%m}" if to != inp.from_day else "") + f"/{to:%Y}"
    aid = await ctx.request_approval(chain="leave", command="timekeeping.approve_leave", input={"request_id": str(rid)},
                                     summary=f"{lt['name']} {n:g} ngày ({span})", on_reject="timekeeping.reject_leave")
    await ctx.exec("UPDATE timekeeping.leave_requests SET approval_id=%s WHERE id=%s", (aid, rid))
    return {"request_id": rid, "approval_id": aid, "days": float(n), "summary": f"Đã gửi đơn {lt['name'].lower()} {n:g} ngày ({span}) — chờ quản lý duyệt"}


class RequestRef(Input):
    request_id: UUID


@m.command("approve_leave", resource="timekeeping", title="Ghi đơn nghỉ đã duyệt", internal=True, agent="deny", risk="authority")
async def approve_leave(ctx: Ctx, inp: RequestRef) -> dict[str, Any]:
    """Sau duyệt: đơn thành ``approved`` (DB: người duyệt khác người xin, tháng chưa chốt) + dòng sổ phép âm nếu loại trừ số dư."""
    if not ctx.p.approved_by:
        raise Denied("Đơn nghỉ cần người khác duyệt")
    r = await ctx.one("""SELECT r.*,t.uses_balance,t.name AS type_name FROM timekeeping.leave_requests r
                         JOIN timekeeping.leave_types t ON t.tenant_id=r.tenant_id AND t.code=r.leave_type WHERE r.id=%s FOR UPDATE OF r""", (inp.request_id,))
    if not r or r["status"] != "requested":
        raise Invalid("Đơn không còn chờ duyệt")
    await ctx.exec("UPDATE timekeeping.leave_requests SET status='approved',approved_by=%s WHERE id=%s", (list(ctx.p.approved_by), r["id"]))
    if r["uses_balance"]:
        await ctx.exec("""INSERT INTO timekeeping.leave_ledger(user_id,team_id,leave_type,year,days,kind,request_id,key,created_by)
                          VALUES(%s,%s,%s,%s,%s,'taken',%s,%s,%s)""",
                       (r["user_id"], r["team_id"], r["leave_type"], r["from_day"].year, -r["days"], r["id"], f"leave:{r['id']}", r["requested_by"]))
    s, e = await _leave_span(ctx, r)
    # 0158: nghỉ đã duyệt hiện trên LỊCH của người đó (bận; chi tiết riêng tư — người khác thấy "Bận"). Gói Lịch tắt ⇒ bỏ qua, đơn vẫn duyệt.
    await ctx.call_system_optional("schedule.book", source="timekeeping.leave", source_ref=str(r["id"]), title=r["type_name"] + (" (nửa ngày)" if r["half_day"] else ""),
                                   starts_at=s, ends_at=e, team_id=r["team_id"], kind="leave", kind_label="Nghỉ", attendee_user_ids=[r["user_id"]],
                                   accepted_user_ids=[r["user_id"]], visibility="private", notify=False)
    await ctx.notify([r["user_id"]], kind="timekeeping", title=f"Đơn nghỉ {r['from_day']:%d/%m} đã được duyệt",
                     open={"query": "timekeeping.my_month", "input": {}})
    return {"request_id": r["id"]}


async def _leave_span(ctx: Ctx, r: dict[str, Any]) -> tuple[datetime, datetime]:
    """Khoảng giờ của đơn nghỉ trên lịch: cả ngày (00:00 ngày đầu → 00:00 sau ngày cuối, giờ công ty); nửa ngày ⇒ nửa đầu giờ làm theo cách chấm
    công (không có ⇒ 08:00–12:00). Đơn không ghi buổi sáng / chiều nên nửa ngày luôn là nửa đầu."""
    day0 = datetime.combine(r["from_day"], time(0), zone())
    if not r["half_day"]:
        return day0, datetime.combine(r["to_day"] + timedelta(days=1), time(0), zone())
    en = (await enrolments(ctx, r["from_day"], [r["user_id"]])).get(str(r["user_id"]))
    a, b = (_minutes(en["starts"]), _minutes(en["ends"])) if en else (8 * 60, 16 * 60)
    return day0 + timedelta(minutes=a), day0 + timedelta(minutes=a + max((b - a) // 2, 60))


@m.command("reject_leave", resource="timekeeping", title="Đơn nghỉ bị từ chối", internal=True, agent="deny", risk="low")
async def reject_leave(ctx: Ctx, inp: RequestRef) -> None:
    await ctx.exec("UPDATE timekeeping.leave_requests SET status='rejected' WHERE id=%s AND status='requested'", (inp.request_id,))


class CancelLeave(Input):
    request_id: UUID = field("Đơn", hidden=True)


@m.command("cancel_leave",
           purpose="Huỷ đơn nghỉ đang chờ duyệt khi không còn nghỉ nữa.", irreversible=True, effect="Đơn nghỉ bị huỷ và người duyệt không còn thấy; muốn nghỉ phải gửi đơn mới.",
           resource="timekeeping", title="Huỷ đơn nghỉ đang chờ", agent="propose", risk="low")
async def cancel_leave(ctx: Ctx, inp: CancelLeave) -> dict[str, Any]:
    user = _me(ctx)
    n = await ctx.exec("UPDATE timekeeping.leave_requests SET status='cancelled' WHERE id=%s AND user_id=%s AND status='requested'", (inp.request_id, user))
    if not n:
        raise Invalid("Chỉ huỷ được đơn của chính mình đang chờ duyệt")
    return {"request_id": inp.request_id, "summary": "Đã huỷ đơn nghỉ"}


class RequestOvertime(Input):
    day: Day = field("Ngày làm thêm", example="14/11/2026")
    hours: float = field("Số giờ", 2, gt=0, le=12)
    reason: str = field("Việc gì", max_length=300, example="Chốt hồ sơ khách gấp")


async def overtime_room(ctx: Ctx, user: UUID, day: date, minutes: int, *, exclude: UUID | None = None) -> str:
    """Kiểm trần làm thêm (ngày / tháng / năm — ``vn-labour:OT_CAPS``) gồm đơn đã duyệt + đang chờ. Trả "" nếu còn trong trần, không thì lý do."""
    caps = labour_rule("OT_CAPS", day)
    if caps is None:
        return ""
    e = (await enrolments(ctx, day, [user])).get(str(user))
    span = (_minutes(e["ends"]) - _minutes(e["starts"]) - int(e["break_minutes"])) if e else 480
    if minutes > span * int(caps["day_ratio_bps"]) // 10000 and e and day.isoweekday() in e["weekdays"]:
        return f"Vượt trần một ngày ({span * int(caps['day_ratio_bps']) // 10000 // 60:g} giờ = {int(caps['day_ratio_bps']) / 100:g}% giờ làm bình thường)"
    used = await ctx.one("""SELECT coalesce(sum(minutes) FILTER (WHERE date_trunc('month',day)=date_trunc('month',%(d)s::date)),0) AS m,
                                   coalesce(sum(minutes) FILTER (WHERE extract(year FROM day)=extract(year FROM %(d)s::date)),0) AS y
                            FROM timekeeping.overtime_requests WHERE user_id=%(u)s AND status IN ('requested','approved') AND id IS DISTINCT FROM %(x)s""",
                         {"d": day, "u": user, "x": exclude})
    assert used is not None
    if used["m"] + minutes > int(caps["month_hours"]) * 60:
        return f"Vượt trần tháng {caps['month_hours']} giờ (đã có {used['m'] / 60:g} giờ)"
    if used["y"] + minutes > int(caps["year_hours"]) * 60:
        return f"Vượt trần năm {caps['year_hours']} giờ (đã có {used['y'] / 60:g} giờ)"
    return ""


@m.command("request_overtime", resource="timekeeping", title="Xin làm thêm giờ", agent="propose", risk="low")
async def request_overtime(ctx: Ctx, inp: RequestOvertime) -> dict[str, Any]:
    """Làm thêm cần người lao động đồng ý (BLLĐ Đ107) ⇒ chính người đó xin; quản lý duyệt (chuỗi ``overtime``). Trần ngày / tháng / năm theo
    ``vn-labour`` — vượt ⇒ từ chối kèm lý do. Hệ số tiền (150 / 200 / 300%) do bảng công xếp theo loại ngày, tiền do gói Lương."""
    user = _me(ctx)
    minutes = round(inp.hours * 60)
    if minutes < 15:
        raise Invalid("Tối thiểu 15 phút")
    if inp.day < today() - timedelta(days=45):
        raise Invalid("Chỉ xin làm thêm cho ngày trong 45 ngày gần đây hoặc sắp tới")
    if why := await overtime_room(ctx, user, inp.day, minutes):
        raise Invalid(f"{why} — quy tắc {labour_rule('OT_CAPS', inp.day)['ref']}")   # type: ignore[index]
    if not inp.reason.strip():
        raise Invalid("Cần ghi việc gì")
    rid = await ctx.val("""INSERT INTO timekeeping.overtime_requests(user_id,team_id,day,minutes,reason,requested_by) VALUES(%s,%s,%s,%s,%s,core.me())
                           RETURNING id""", (user, await ctx.home_team(user), inp.day, minutes, inp.reason.strip()))
    aid = await ctx.request_approval(chain="overtime", command="timekeeping.approve_overtime", input={"request_id": str(rid)},
                                     summary=f"Làm thêm {inp.hours:g} giờ ngày {inp.day:%d/%m/%Y}: {inp.reason.strip()[:80]}",
                                     on_reject="timekeeping.reject_overtime")
    await ctx.exec("UPDATE timekeeping.overtime_requests SET approval_id=%s WHERE id=%s", (aid, rid))
    return {"request_id": rid, "approval_id": aid, "summary": f"Đã gửi xin làm thêm {inp.hours:g} giờ ngày {inp.day:%d/%m/%Y} — chờ quản lý duyệt"}


@m.command("approve_overtime", resource="timekeeping", title="Ghi làm thêm đã duyệt", internal=True, agent="deny", risk="authority")
async def approve_overtime(ctx: Ctx, inp: RequestRef) -> dict[str, Any]:
    if not ctx.p.approved_by:
        raise Denied("Làm thêm cần người khác duyệt")
    r = await ctx.one("SELECT * FROM timekeeping.overtime_requests WHERE id=%s FOR UPDATE", (inp.request_id,))
    if not r or r["status"] != "requested":
        raise Invalid("Đơn không còn chờ duyệt")
    if why := await overtime_room(ctx, r["user_id"], r["day"], r["minutes"], exclude=r["id"]):
        raise Invalid(why)
    await ctx.exec("UPDATE timekeeping.overtime_requests SET status='approved',approved_by=%s WHERE id=%s", (list(ctx.p.approved_by), r["id"]))
    return {"request_id": r["id"]}


@m.command("reject_overtime", resource="timekeeping", title="Làm thêm bị từ chối", internal=True, agent="deny", risk="low")
async def reject_overtime(ctx: Ctx, inp: RequestRef) -> None:
    await ctx.exec("UPDATE timekeeping.overtime_requests SET status='rejected' WHERE id=%s AND status='requested'", (inp.request_id,))


class GrantLeave(Input):
    person: str = field("Người", options_query="admin.member_options")
    leave_type: str = field("Loại nghỉ", "PHEP_NAM", options_query="timekeeping.leave_type_options")
    year: int = field("Năm", ge=2000, le=2100, example="2026")
    days: float = field("Số ngày", gt=-60, le=60, example="12", help="Âm = điều chỉnh giảm")
    note: str = field("Lý do", max_length=300, example="Cấp phép năm 2026 theo hợp đồng")


@m.command("grant_leave",
           purpose="Cấp hoặc điều chỉnh số ngày phép của một người, có lý do, để sổ phép đúng thực tế.", reversible="Điều chỉnh ngược cùng số ngày bằng chính lệnh này (sổ phép chỉ thêm, có lý do)",
           resource="timekeeping", action="manage", title="Cấp / điều chỉnh phép", agent="deny", risk="authority")
async def grant_leave(ctx: Ctx, inp: GrantLeave) -> dict[str, Any]:
    """Dòng sổ phép cấp / điều chỉnh (chỉ thêm, có lý do). Không tự cấp cho mình (DB)."""
    if not ctx.p.has_scope("timekeeping", "all"):
        raise Denied("Cần quyền chấm công cả công ty")
    who = await member(ctx, inp.person)
    if who["id"] == ctx.p.user_id:
        raise Denied("Không tự cấp phép cho chính mình")
    if not inp.note.strip() or not inp.days:
        raise Invalid("Cần số ngày khác 0 và lý do")
    lt = await ctx.val("SELECT code FROM timekeeping.leave_types WHERE code=%s AND uses_balance", (inp.leave_type.strip().upper(),))
    if not lt:
        raise NotFound("Loại nghỉ này không có số dư")
    lid = await ctx.val("""INSERT INTO timekeeping.leave_ledger(user_id,team_id,leave_type,year,days,kind,note,key,created_by)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,'grant:' || gen_random_uuid(),core.me()) RETURNING id""",
                        (who["id"], await ctx.home_team(who["id"]), lt, inp.year, round(inp.days * 2) / 2, "grant" if inp.days > 0 else "adjust",
                         inp.note.strip()))
    return {"ledger_id": lid, "summary": f"{who['name']}: {inp.days:+g} ngày {lt} năm {inp.year}"}


class AnnualIn(Input):
    year: int = field("Năm", ge=2000, le=2100, example="2026")


@m.command("grant_annual_leave",
           purpose="Cấp phép năm cho mọi người đang chấm công để sổ phép đầu năm đủ số ngày theo luật.", irreversible=True, effect="Cấp đủ số ngày phép năm cho mọi người đang chấm công vào sổ phép (sổ chỉ thêm); sửa phải điều chỉnh từng người.",
           resource="timekeeping", action="manage", title="Cấp phép năm cho mọi người", agent="deny", risk="authority")
async def grant_annual_leave(ctx: Ctx, inp: AnnualIn) -> dict[str, Any]:
    """Phép năm theo ``vn-labour:ANNUAL_LEAVE`` cho mọi người đang chấm công: cấp ĐỦ số ngày của năm. Người vào làm giữa năm (theo tỉ lệ tháng —
    BLLĐ Đ113.2), thâm niên (+1 ngày / 5 năm), chế độ riêng của công ty: điều chỉnh bằng "Cấp / điều chỉnh phép" (ngày vào làm nằm ở hồ sơ làm việc,
    gói này không đọc). Gọi lại cùng năm không cấp hai lần."""
    if not ctx.p.has_scope("timekeeping", "all"):
        raise Denied("Cần quyền chấm công cả công ty")
    rule = labour_rule("ANNUAL_LEAVE", date(inp.year, 12, 31))
    if rule is None:
        raise Invalid("Chưa có quy định phép năm cho năm này")
    if not await ctx.val("SELECT 1 FROM timekeeping.leave_types WHERE code='PHEP_NAM' AND uses_balance"):
        raise Invalid("Chưa có loại nghỉ PHEP_NAM trừ số dư — cài loại nghỉ thường gặp trước")
    n = 0
    for e in (await enrolments(ctx, date(inp.year, 12, 31))).values():
        if e["mode"] == "off" or e["user_id"] == ctx.p.user_id:
            continue
        n += await ctx.exec("""INSERT INTO timekeeping.leave_ledger(user_id,team_id,leave_type,year,days,kind,note,key,created_by)
                               VALUES(%s,%s,'PHEP_NAM',%s,%s,'grant',%s,%s,core.me()) ON CONFLICT (tenant_id,key) DO NOTHING""",
                            (e["user_id"], e["team_id"], inp.year, int(rule["days"]), f"Phép năm {inp.year} — {rule['ref']}",
                             f"annual:{e['user_id']}:{inp.year}"))
    return {"granted": n, "summary": f"Đã cấp phép năm {inp.year} cho {n} người (người đã cấp giữ nguyên; người cấp không tự cấp cho mình)"}


class BalancesIn(Input):
    year: int | None = field("Năm", None, ge=2000, le=2100, help="Trống = năm nay")


@m.query("balances", resource="timekeeping", title="Số dư phép")
async def balances(ctx: Ctx, inp: BalancesIn) -> dict[str, Any]:
    """Số dư phép theo người × loại (RLS: của mình / đội / cả công ty) = tổng sổ phép; kèm ngày đang chờ duyệt."""
    year = inp.year or today().year
    rows = await ctx.all("""SELECT l.user_id,l.leave_type,t.name,sum(l.days) FILTER (WHERE l.days>0) AS granted,-coalesce(sum(l.days) FILTER (WHERE l.days<0),0) AS used,
                                   sum(l.days) AS balance FROM timekeeping.leave_ledger l
                            LEFT JOIN timekeeping.leave_types t ON t.tenant_id=l.tenant_id AND t.code=l.leave_type
                            WHERE l.year=%s GROUP BY l.user_id,l.leave_type,t.name ORDER BY l.user_id,l.leave_type""", (year,))
    pend = {(str(r["user_id"]), r["leave_type"]): r["d"] for r in await ctx.all(
        """SELECT user_id,leave_type,sum(days) AS d FROM timekeeping.leave_requests WHERE status='requested' AND extract(year FROM from_day)=%s
           GROUP BY 1,2""", (year,))}
    who = await names(ctx, list({r["user_id"] for r in rows}))
    for r in rows:
        r["id"] = f"{r['user_id']}:{r['leave_type']}"
        r["person"] = who.get(str(r["user_id"]), "")
        r["pending"] = pend.get((str(r["user_id"]), r["leave_type"]), 0)
        for k in ("granted", "used", "balance", "pending"):
            r[k] = float(r[k] or 0)
    return ui.table(rows, [ui.col("person", "Người"), ui.col("name", "Loại"), ui.col("granted", "Được cấp", "number"), ui.col("used", "Đã nghỉ", "number"),
                           ui.col("pending", "Đang chờ duyệt", "number"), ui.col("balance", "Còn lại", "number")],
                    title=f"Số dư phép năm {year}", limit=2000, empty="Chưa cấp phép cho ai năm này")


class RequestsIn(Input):
    status: Literal["requested", "all"] = field("Trạng thái", "requested", options={"requested": "Đang chờ duyệt", "all": "Tất cả"})


@m.query("requests", resource="timekeeping", title="Đơn nghỉ, làm thêm, bổ sung giờ")
async def requests(ctx: Ctx, inp: RequestsIn) -> dict[str, Any]:
    """Đơn của mình / đội / cả công ty (RLS). Duyệt ở hộp duyệt (chuỗi ``leave`` / ``overtime`` / ``time_correction``) — không duyệt ở đây."""
    only = inp.status == "requested"
    rows = await ctx.all(
        """SELECT * FROM (
             SELECT r.id,'leave' AS kind,r.user_id,r.from_day AS day,r.to_day,t.name || ' ' || r.days::text || ' ngày' AS what,r.reason,r.status,r.created_at
               FROM timekeeping.leave_requests r LEFT JOIN timekeeping.leave_types t ON t.tenant_id=r.tenant_id AND t.code=r.leave_type
             UNION ALL
             SELECT id,'overtime',user_id,day,day,'Làm thêm ' || round(minutes/60.0,2)::text || ' giờ',reason,status,created_at FROM timekeeping.overtime_requests
             UNION ALL
             SELECT id,'correction',user_id,(at AT TIME ZONE core.tz())::date,(at AT TIME ZONE core.tz())::date,
                    'Bổ sung giờ ' || CASE direction WHEN 'in' THEN 'vào ' ELSE 'ra ' END || to_char(at AT TIME ZONE core.tz(),'HH24:MI'),reason,status,
                    created_at FROM timekeeping.corrections) x
           WHERE NOT %s OR status='requested' ORDER BY created_at DESC LIMIT 500""", (only,))
    who = await names(ctx, list({r["user_id"] for r in rows}))
    for r in rows:
        r["person"] = who.get(str(r["user_id"]), "")
    return ui.table(rows, [ui.col("person", "Người"), ui.col("kind", "Loại", "badge", labels={"leave": "Nghỉ", "overtime": "Làm thêm", "correction": "Bổ sung giờ"}),
                           ui.col("what", "Nội dung"), ui.col("day", "Ngày", "date"), ui.col("to_day", "Đến", "date"), ui.col("reason", "Lý do"),
                           ui.col("status", "Trạng thái", "badge", labels=REQUEST_STATUS,
                                  tones={"requested": "orange", "approved": "green", "rejected": "gray", "cancelled": "gray"})],
                    title="Đơn đang chờ duyệt" if only else "Đơn nghỉ, làm thêm, bổ sung giờ", limit=500,
                    empty="Không có đơn nào đang chờ" if only else "Chưa có đơn nào",
                    kpis=[ui.kpi("Đang chờ duyệt", sum(r["status"] == "requested" for r in rows))])


@m.query("leave_types", resource="timekeeping", title="Loại nghỉ đang dùng")
async def leave_types(ctx: Ctx, inp: NoInput) -> list[dict[str, Any]]:
    """Các loại nghỉ công ty đang cho xin: MÃ + tên + ai trả lương + có trừ phép năm không. Gọi TRƯỚC khi soạn đơn xin nghỉ để chọn đúng mã loại
    (nghỉ ốm, việc riêng, không lương, phép năm…) — đừng đoán mã."""
    return await ctx.all("SELECT code,name,paid_by,uses_balance FROM timekeeping.leave_types WHERE active ORDER BY code")



# ── Điểm cắm của chia lead (0152): người có đơn nghỉ ĐÃ DUYỆT phủ hôm nay không nhận lead mới, lead đang chờ gọi của họ chuyển ngay ──

class AwayIn(Input):
    user_ids: list[UUID]
    on: date


@m.plug("leadflow.away", "Đang nghỉ phép (đơn nghỉ đã duyệt)")
async def leadflow_away(ctx: Ctx, inp: AwayIn) -> dict[str, dict[str, Any]]:
    """Chỉ người trong ``user_ids`` có đơn nghỉ đã duyệt CẢ NGÀY phủ ngày ``on`` (nghỉ nửa ngày vẫn nhận lead). Trả ngày về + tên loại nghỉ — không
    lý do, không số dư."""
    if not inp.user_ids:
        return {}
    rows = await ctx.all("""SELECT r.user_id, max(r.to_day) AS until, min(t.name) AS label FROM timekeeping.leave_requests r
                            JOIN timekeeping.leave_types t ON t.tenant_id = r.tenant_id AND t.code = r.leave_type
                            WHERE r.status = 'approved' AND NOT r.half_day AND r.from_day <= %s AND r.to_day >= %s AND r.user_id = ANY(%s)
                            GROUP BY r.user_id""", (inp.on, inp.on, inp.user_ids))
    return {str(r["user_id"]): {"until": r["until"].isoformat(), "label": r["label"]} for r in rows}


# ── Giờ làm là RẢNH để hẹn, không phải lịch (0158): tìm giờ của Lịch hỏi ở đây — ca / giờ làm cố định không thành sự kiện ──

class WorkHoursIn(Input):
    user_ids: list[UUID]
    on: date


@m.query("work_hours", resource="timekeeping", title="Giờ làm theo cách chấm công", internal=True, agent="deny", callers=("schedule",),
         why="schedule.find_time: người tìm giờ hẹn cho nhiều người cần biết giờ làm của từng người để không gợi ý ngoài giờ. Trả thứ làm việc + giờ vào / "
             "ra của cách chấm công hiệu lực ngày ``on``; không trả giờ chấm, nghỉ, làm thêm, lương."
             " [id: người gọi thấy — user_ids là thành viên đang hoạt động người tìm giờ chọn trong ô chọn người (danh mục thành viên ai trong công ty cũng "
             "thấy), schedule đã đối chiếu email với thành viên đang hoạt động]")
async def work_hours(ctx: Ctx, inp: WorkHoursIn) -> dict[str, dict[str, Any]]:
    return {u: {"weekdays": list(r["weekdays"]), "starts": r["starts"], "ends": r["ends"]} for u, r in (await enrolments(ctx, inp.on, inp.user_ids)).items()}
