"""GIỜ CHẤM (0150 §4.3): chấm luôn được ghi; trượt luật địa điểm ⇒ cờ cho quản lý xét. Bổ sung giờ quên chấm qua duyệt; nhập từ máy chấm / tệp.

IP của lần chấm do MÁY CHỦ đọc: đường HTTP riêng ``POST /v1/timekeeping/punch`` lấy địa chỉ của kết nối (bản cài chạy sau proxy tin cậy — uvicorn
``--proxy-headers``; BFF web chuyển tiếp địa chỉ trình duyệt CHỈ cho đường này) rồi gọi lệnh qua executor. Lệnh không có ô IP: gọi qua đường lệnh chung
⇒ không có IP ⇒ cờ "không đọc được địa chỉ mạng". Không lưu toạ độ, không theo dõi liên tục (BVDLCN — 0150 §8).
"""

import ipaddress
import re
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from psycopg import errors as pg

from barista.api import Ctx, Day, Denied, Input, Invalid, NoInput, NotFound, field, today, ui, web, zone
from barista.modules.timekeeping import DIRECTION, FLAGS, METHOD, OBSERVED_IP, m
from barista.modules.timekeeping.common import enrolments, member, names

PUNCH_ROUTE = "/v1/timekeeping/punch"


def _ip_or_none(ip: str | None) -> str | None:
    try:
        return str(ipaddress.ip_address((ip or "").split("%")[0]))
    except ValueError:
        return None


async def _flags_for(ctx: Ctx, user: UUID, ip: str | None, *, method: str) -> tuple[list[str], UUID | None, UUID | None]:
    team = await ctx.home_team(user)   # đội lúc CHẤM (không phải lúc gán chấm công): đổi đội ⇒ trưởng đội mới xét giờ từ đó (review 10/11)
    e = (await enrolments(ctx, today(), [user])).get(str(user))
    if e is None or e["mode"] == "off":
        return ["not_enrolled"], None, team
    if method == "import" and ip is None:
        return [], e["site_id"], team   # máy chấm đặt tại địa điểm: người nhập chịu trách nhiệm tệp (kiểm IP chỉ khi tệp có IP)
    # kiểm ở DB (hàm SECURITY DEFINER): người chấm không đọc được dải IP văn phòng, chỉ nhận kết quả
    flag = await ctx.val("SELECT timekeeping.site_check(%s, %s::inet)", (e["site_id"], ip))
    return ([flag] if flag else []), e["site_id"], team


class Punch(Input):
    direction: Literal["in", "out"] = field("Vào / ra", "in", options=DIRECTION)
    note: str = field("Ghi chú", "", max_length=300, example="Gặp khách ở công trình, vào văn phòng sau")


@m.command("punch", resource="timekeeping", title="Chấm công", agent="deny", risk="authority")
async def punch(ctx: Ctx, inp: Punch) -> dict[str, Any]:
    """Chấm công của CHÍNH người bấm, giờ của máy chủ. Trợ lý không chấm thay (0150 §3 mục 8). Trượt luật ⇒ ghi + gắn cờ, không từ chối."""
    if ctx.p.user_id is None:
        raise Denied("Chấm công cần đăng nhập của chính người lao động")
    user = ctx.p.user_id
    await ctx.exec("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"timekeeping.punch:{ctx.p.tenant_id}:{user}",))
    last = await ctx.one("""SELECT id,at,direction,flags FROM timekeeping.punches WHERE user_id=%s AND at > now() - interval '2 minutes'
                            ORDER BY at DESC LIMIT 1""", (user,))
    if last and last["direction"] == inp.direction:   # bấm hai lần liền ⇒ một lần chấm
        return {"punch_id": last["id"], "replayed": True, "flags": list(last["flags"]),
                "summary": f"Đã {DIRECTION[inp.direction].lower()} lúc {last['at'].astimezone(zone()):%H:%M} (bấm lặp — không ghi thêm)"}
    ip = _ip_or_none(OBSERVED_IP.get())
    flags, site, team = await _flags_for(ctx, user, ip, method="web")
    row = await ctx.one("""INSERT INTO timekeeping.punches(user_id,team_id,at,direction,method,site_id,ip,flags,note,created_by)
                           VALUES(%s,%s,now(),%s,'web',%s,%s::inet,%s,%s,core.me()) RETURNING id,at""",
                        (user, team, inp.direction, site, ip, flags, inp.note.strip()))
    assert row is not None
    tail = f" — {', '.join(FLAGS[f].lower() for f in flags)}: quản lý sẽ xét" if flags else ""
    return {"punch_id": row["id"], "replayed": False, "flags": flags,
            "summary": f"Đã {DIRECTION[inp.direction].lower()} lúc {row['at'].astimezone(zone()):%H:%M}{tail}"}


@m.route("POST", PUNCH_ROUTE)
async def http_punch(request: web.Request, body: Annotated[dict[str, Any], web.Body()]) -> web.JSONResponse:
    """Nút "Vào ca / Ra ca": địa chỉ mạng ĐỌC TỪ KẾT NỐI (không từ thân request), đặt cho đúng lần gọi lệnh này rồi trả lại."""
    p = await web.caller(request)
    token = OBSERVED_IP.set(request.client.host if request.client else None)
    try:
        return web.result(await web.run(p, "timekeeping.punch", {k: body[k] for k in ("direction", "note") if k in body},
                                         key=request.headers.get("idempotency-key")))
    finally:
        OBSERVED_IP.reset(token)


def _parse_at(text: str) -> datetime:
    t = text.strip()
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(t, fmt).replace(tzinfo=zone())
        except ValueError:
            continue
    raise Invalid(f"“{t}”: giờ chấm dạng dd/mm/yyyy HH:MM")


class ImportPunch(Input):
    person: str = field("Người", max_length=200, example="nv.an@cong-ty-mau.demo", help="Email thành viên")
    at: str = field("Lúc", max_length=25, example="03/11/2026 08:02", help="Giờ của công ty, dd/mm/yyyy HH:MM")
    direction: Literal["in", "out"] = field("Vào / ra", "in", options=DIRECTION)
    ip: str = field("IP (nếu tệp có)", "", max_length=45, help="Trống = máy chấm đặt tại địa điểm (không kiểm IP)")
    note: str = field("Ghi chú", "", max_length=300)


@m.command("import_punch", resource="timekeeping", action="manage", title="Nhập giờ chấm từ máy chấm / tệp", importable=True, agent="deny",
           risk="authority")
async def import_punch(ctx: Ctx, inp: ImportPunch) -> dict[str, Any]:
    """Một dòng giờ chấm từ tệp xuất của máy chấm công (nhập cả tệp qua Nhập dữ liệu — đúng lệnh này từng dòng). Cùng người + lúc + vào/ra ⇒ không ghi
    hai lần. Người nhập không nhập cho chính mình (DB). Luật IP áp như chấm trên app khi tệp có IP."""
    if not ctx.p.has_scope("timekeeping", "all"):
        raise Denied("Cần quyền chấm công cả công ty")
    who = await member(ctx, inp.person)
    at = _parse_at(inp.at)
    if at > datetime.now(zone()) + timedelta(minutes=5):
        raise Invalid("Giờ chấm ở tương lai")
    old = await ctx.val("SELECT id FROM timekeeping.punches WHERE user_id=%s AND at=%s AND direction=%s", (who["id"], at, inp.direction))
    if old:
        return {"punch_id": old, "replayed": True, "summary": "Đã có giờ chấm này"}
    ip = _ip_or_none(inp.ip) if inp.ip.strip() else None
    if inp.ip.strip() and ip is None:
        raise Invalid(f"“{inp.ip}” không phải địa chỉ IP")
    flags, site, team = await _flags_for(ctx, who["id"], ip, method="import")
    try:
        pid = await ctx.val("""INSERT INTO timekeeping.punches(user_id,team_id,at,direction,method,site_id,ip,flags,note,created_by)
                               VALUES(%s,%s,%s,%s,'import',%s,%s::inet,%s,%s,core.me()) RETURNING id""",
                            (who["id"], team, at, inp.direction, site, ip, flags, inp.note.strip()))
    except pg.IntegrityError as e:   # trigger / ràng buộc của DB: tháng đã chốt / tự nhập cho mình (lỗi khác nổi lên là lỗi hệ thống)
        raise Denied(str(getattr(getattr(e, "diag", None), "message_primary", "") or e)) from e
    return {"punch_id": pid, "replayed": False, "flags": flags, "summary": f"{who['name']}: {DIRECTION[inp.direction].lower()} {at:%d/%m %H:%M}"}


class Review(Input):
    punch_id: UUID = field("Giờ chấm", hidden=True)
    decision: Literal["accept", "reject"] = field("Xét", "accept", options={"accept": "Nhận — tính công", "reject": "Bỏ — không tính"})
    note: str = field("Ghi chú", "", max_length=300, example="Đi gặp khách theo lịch đã báo")


@m.command("review_punch", resource="timekeeping", action="manage", title="Xét giờ chấm có cờ", agent="deny", risk="authority")
async def review_punch(ctx: Ctx, inp: Review) -> dict[str, Any]:
    """Quản lý (của đội — RLS) nhận hoặc bỏ một giờ chấm có cờ. Không xét giờ của chính mình (DB); xét một lần, chỉ thêm."""
    row = await ctx.one("""SELECT p.id,p.user_id,p.at,p.flags,r.id AS reviewed FROM timekeeping.punches p
                           LEFT JOIN timekeeping.punch_reviews r ON r.tenant_id=p.tenant_id AND r.punch_id=p.id WHERE p.id=%s""", (inp.punch_id,))
    if not row:
        raise NotFound("Không thấy giờ chấm")
    if not row["flags"] or row["reviewed"]:
        raise Invalid("Giờ chấm không có cờ, hoặc đã xét")
    if row["user_id"] == ctx.p.user_id:
        raise Denied("Không tự xét giờ chấm của chính mình — nhờ quản lý khác")
    if inp.decision == "reject" and not inp.note.strip():
        raise Invalid("Bỏ một giờ chấm cần ghi lý do (người lao động sẽ thấy)")
    try:
        await ctx.exec("INSERT INTO timekeeping.punch_reviews(punch_id,decision,note,reviewed_by) VALUES(%s,%s,%s,core.me())",
                       (inp.punch_id, inp.decision, inp.note.strip()))
    except pg.IntegrityError as e:   # trigger của DB: xét giờ của chính mình / xét hai lần (lỗi khác nổi lên là lỗi hệ thống)
        raise Denied(str(getattr(getattr(e, "diag", None), "message_primary", "") or e)) from e
    who = (await names(ctx, [row["user_id"]])).get(str(row["user_id"]), "")
    if inp.decision == "reject":
        await ctx.notify([row["user_id"]], kind="timekeeping", title=f"Giờ chấm {row['at'].astimezone(zone()):%d/%m %H:%M} không được tính",
                         body=inp.note.strip(), open={"query": "timekeeping.my_month", "input": {}})
    return {"punch_id": inp.punch_id, "summary": f"{who}: giờ chấm {row['at'].astimezone(zone()):%d/%m %H:%M} — {'đã nhận' if inp.decision == 'accept' else 'đã bỏ'}"}


class RequestCorrection(Input):
    person: str = field("Cho ai", "", options_query="admin.member_options",
                        help="Trống = cho chính mình. Quản lý ghi thay người trong đội (người lao động quên xin) — người khác duyệt")
    day: Day = field("Ngày", example="06/11/2026")
    clock: str = field("Giờ thật", kind="time", max_length=5, example="17:35", help="Giờ của công ty")
    direction: Literal["in", "out"] = field("Vào / ra", "out", options=DIRECTION)
    reason: str = field("Lý do", max_length=300, example="Quên bấm ra ca, về lúc 17:35")


@m.command("request_correction", resource="timekeeping", title="Xin bổ sung giờ chấm", agent="propose", risk="low")
async def request_correction(ctx: Ctx, inp: RequestCorrection) -> dict[str, Any]:
    """Quên chấm ⇒ xin bổ sung cho chính mình — hoặc quản lý ghi thay người trong đội (người đó quên xin, đội chờ chốt) — qua chuỗi ``time_correction``
    (trưởng đội của NGƯỜI XIN duyệt; quản lý ghi thay ⇒ cấp trên của quản lý duyệt — không ai tự duyệt). Duyệt ⇒ giờ chấm mới mang tên người duyệt."""
    if ctx.p.user_id is None:
        raise Denied("Cần đăng nhập")
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", inp.clock):
        raise Invalid("Giờ dạng HH:MM")
    if not inp.reason.strip():
        raise Invalid("Cần lý do")
    at = datetime.combine(inp.day, datetime.strptime(inp.clock, "%H:%M").time(), zone())
    if at > datetime.now(zone()) or inp.day < today() - timedelta(days=45):
        raise Invalid("Chỉ bổ sung giờ đã qua, trong 45 ngày gần đây")
    user = ctx.p.user_id
    if inp.person.strip():   # quản lý ghi thay người trong đội: phải quản lý chấm công người đó (RLS: thấy dòng chấm công của họ) — không cho chính mình
        other = await member(ctx, inp.person)
        if other["id"] != user:
            if not ctx.p.can("timekeeping", "manage") or not (await enrolments(ctx, inp.day, [other["id"]])).get(str(other["id"])):
                raise Denied("Chỉ quản lý chấm công của người này mới ghi thay được")
            user = other["id"]
    cid = await ctx.val("""INSERT INTO timekeeping.corrections(user_id,team_id,at,direction,reason,requested_by) VALUES(%s,%s,%s,%s,%s,core.me())
                           RETURNING id""", (user, await ctx.home_team(user), at, inp.direction, inp.reason.strip()))
    aid = await ctx.request_approval(chain="time_correction", command="timekeeping.apply_correction", input={"correction_id": str(cid)},
                                     summary=f"Bổ sung giờ {DIRECTION[inp.direction].lower()} {at:%d/%m %H:%M}: {inp.reason.strip()[:80]}",
                                     on_reject="timekeeping.reject_correction")
    await ctx.exec("UPDATE timekeeping.corrections SET approval_id=%s WHERE id=%s", (aid, cid))
    return {"correction_id": cid, "approval_id": aid, "summary": f"Đã gửi xin bổ sung giờ {DIRECTION[inp.direction].lower()} {at:%d/%m/%Y %H:%M}"}


class CorrectionRef(Input):
    correction_id: UUID


@m.command("apply_correction", resource="timekeeping", title="Ghi giờ bổ sung sau duyệt", internal=True, agent="deny", risk="authority")
async def apply_correction(ctx: Ctx, inp: CorrectionRef) -> dict[str, Any]:
    if not ctx.p.approved_by:
        raise Denied("Giờ bổ sung cần người khác duyệt")
    c = await ctx.one("SELECT * FROM timekeeping.corrections WHERE id=%s FOR UPDATE", (inp.correction_id,))
    if not c or c["status"] != "requested":
        raise Invalid("Đề nghị không còn chờ ghi")
    pid = await ctx.val("""INSERT INTO timekeeping.punches(user_id,team_id,at,direction,method,flags,note,approved_by,created_by)
                           VALUES(%s,%s,%s,%s,'correction','{}',%s,%s,%s) RETURNING id""",
                        (c["user_id"], c["team_id"], c["at"], c["direction"], c["reason"], list(ctx.p.approved_by), c["requested_by"]))
    await ctx.exec("UPDATE timekeeping.corrections SET status='approved',punch_id=%s WHERE id=%s", (pid, c["id"]))
    return {"punch_id": pid}


@m.command("reject_correction", resource="timekeeping", title="Bổ sung giờ bị từ chối", internal=True, agent="deny", risk="low")
async def reject_correction(ctx: Ctx, inp: CorrectionRef) -> None:
    await ctx.exec("UPDATE timekeeping.corrections SET status='rejected' WHERE id=%s AND status='requested'", (inp.correction_id,))


# ── Xem ──

@m.query("today", resource="timekeeping", title="Chấm công hôm nay")
async def today_card(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Màn ĐIỆN THOẠI của chính người xem: hai nút lớn "Vào ca" / "Ra ca" (đường riêng của gói — máy chủ đọc IP), giờ đã chấm hôm nay, cờ nếu có."""
    user = ctx.p.user_id
    rows = await ctx.all("""SELECT p.at,p.direction,p.flags,r.decision FROM timekeeping.punches p
                            LEFT JOIN timekeeping.punch_reviews r ON r.tenant_id=p.tenant_id AND r.punch_id=p.id
                            WHERE p.user_id=%s AND (p.at AT TIME ZONE core.tz())::date=%s ORDER BY p.at""", (user, today()))
    e = (await enrolments(ctx, today(), [user] if user else [])).get(str(user))
    nxt = "out" if rows and rows[-1]["direction"] == "in" else "in"
    items = [{"title": f"{DIRECTION[r['direction']]} {r['at'].astimezone(zone()):%H:%M}",
              "subtitle": ", ".join(FLAGS[f] for f in r["flags"]) + (" — quản lý đã nhận" if r["decision"] == "accept" else
                                                                      " — không tính" if r["decision"] == "reject" else " — chờ quản lý xét" if r["flags"] else ""),
              "tone": "warn" if r["flags"] and r["decision"] != "accept" else "ok"} for r in rows]
    blocks: list[dict[str, Any]] = []
    if e is None or e["mode"] == "off":
        blocks.append(ui.b_callout("warn", "Chưa đăng ký chấm công", "Nhân sự chưa gán giờ làm / địa điểm cho anh/chị — lần chấm vẫn được ghi, gắn cờ."))
    else:
        blocks.append(ui.b_text(f"{e['pattern_name']}: {e['starts']}–{e['ends']}" + (f" · {e['site_name']} (kiểm theo mạng văn phòng)" if e["site_name"] else "")
                                + (" · đủ công theo lịch, chỉ trừ nghỉ / vắng" if e["mode"] == "exception" else ""), "Giờ làm"))
    blocks.append(ui.b_list(items, "Hôm nay") if items else ui.b_text("Chưa chấm lần nào hôm nay.", "Hôm nay"))
    acts = []
    for d in (nxt, "out" if nxt == "in" else "in"):
        # một chạm (không ô nào để điền ⇒ chạy ngay, nút to trên điện thoại); nút nên bấm tiếp theo đứng trước
        a = ui.act("timekeeping.punch", DIRECTION[d] + (" (tiếp theo)" if d == nxt else ""), {"direction": d, "note": ""}, "instant")
        a["route"] = PUNCH_ROUTE   # chỉ THÊM trường (bất biến 7): app biết route thì gửi qua đường riêng (máy chủ đọc IP); app cũ gửi lệnh chung ⇒ cờ "không đọc được"
        acts.append(a)
    acts.append(ui.act("timekeeping.request_correction", "Quên chấm? Xin bổ sung"))
    return ui.card("Chấm công", blocks, subtitle=f"{today():%d/%m/%Y}", icon="clock", actions=acts)


class FlaggedIn(Input):
    days: int = field("Trong bao nhiêu ngày gần đây", 7, ge=1, le=62)
    pending_only: bool = field("Chỉ giờ chưa xét", False)


@m.query("flagged", resource="timekeeping", title="Giờ chấm có cờ (ngoài văn phòng, không đọc được mạng…)")
async def flagged(ctx: Ctx, inp: FlaggedIn) -> dict[str, Any]:
    """Giờ chấm trượt luật địa điểm trong N ngày — mỗi người thấy của mình, trưởng đội của đội, Nhân sự cả công ty (RLS). Xét ở đây (nhận / bỏ)."""
    rows = await ctx.all("""SELECT p.id,p.user_id,p.at,p.direction,p.flags,host(p.ip) AS ip,p.method,s.name AS site,r.decision,p.note
                            FROM timekeeping.punches p LEFT JOIN timekeeping.punch_reviews r ON r.tenant_id=p.tenant_id AND r.punch_id=p.id
                            LEFT JOIN timekeeping.sites s ON s.tenant_id=p.tenant_id AND s.id=p.site_id
                            WHERE p.flags<>'{}' AND p.at >= now() - make_interval(days => %s) AND (NOT %s OR r.id IS NULL)
                            ORDER BY p.at DESC LIMIT 1000""", (inp.days, inp.pending_only))
    who = await names(ctx, list({r["user_id"] for r in rows}))
    # địa chỉ IP là dữ liệu cá nhân (BVDLCN): chỉ Nhân sự (quyền chính sách chấm công) và chính người chấm thấy; quản lý thấy CỜ, không thấy IP
    see_ip = ctx.p.has_scope("timekeeping.policy", "all")
    for r in rows:
        if not see_ip and r["user_id"] != ctx.p.user_id:
            r["ip"] = ""
        r["name"] = who.get(str(r["user_id"]), "")
        r["when"] = f"{r['at'].astimezone(zone()):%d/%m %H:%M}"
        r["what"] = ", ".join(FLAGS[f] for f in r.pop("flags"))
        r["state"] = r.pop("decision") or "pending"
        r["at"] = r["at"].astimezone(zone()).isoformat()
    pending = sum(r["state"] == "pending" for r in rows)
    out = ui.table(
        rows,
        [ui.col("name", "Người"), ui.col("when", "Lúc"), ui.col("direction", "Vào / ra", "badge", labels=DIRECTION), ui.col("what", "Cờ"),
         ui.col("ip", "IP"), ui.col("site", "Địa điểm"), ui.col("method", "Cách chấm", "badge", labels=METHOD), ui.col("note", "Ghi chú"),
         ui.col("state", "Xét", "badge", labels={"pending": "Chờ xét", "accept": "Đã nhận", "reject": "Đã bỏ"},
                tones={"pending": "orange", "accept": "green", "reject": "gray"})],
        title=f"Giờ chấm có cờ — {inp.days} ngày gần đây", limit=1000, empty="Không có giờ chấm nào trượt luật địa điểm",
        kpis=[ui.kpi("Chờ xét", pending, tone="orange" if pending else "green"), ui.kpi("Người", len({r["user_id"] for r in rows})),
              ui.kpi("Ngoài mạng văn phòng", sum("Ngoài mạng" in r["what"] for r in rows))],
        note="Giờ chấm có cờ vẫn được ghi; chốt bảng công cần xét hết giờ có cờ của tháng",
    )
    return out


class MonthIn(Input):
    month: Day | None = field("Tháng", None, help="Ngày bất kỳ trong tháng; trống = tháng này")


def month_of_input(d: date | None) -> date:
    return (d or today()).replace(day=1)
