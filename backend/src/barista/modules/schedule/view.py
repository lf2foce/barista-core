"""Màn Lịch kiểu app lịch (0240): Ngày / Tuần / Lịch biểu trên MỘT nguồn lịch (``schedule.events``) + mốc hạn; người cạnh nhau theo phạm vi quyền; màu
theo loại lịch (dữ liệu công ty); tạo nhanh từ ô giờ trống bằng lệnh của module sở hữu; thêm bằng câu tự nhiên (máy đoán, người xác nhận).

Hai điểm cắm của module này (module khác góp, bằng quyền người xem — thiếu quyền ⇒ bỏ phần đó):
* ``schedule.calendar_items`` (since, until, parties: {mã lịch: [mã bên ngoài]}) ⇒ [{event_id, title, subtitle, kind, open, links, move}] — module NGUỒN của lịch (vd hẹn khách) cho biết người
  xem thấy gì thêm về lịch của nó (tên khách, mở hồ sơ) và dời bằng lệnh nào của nó.
* ``schedule.quick_add`` (text) ⇒ {creators: [cách tạo], subject?, place?} — cách tạo mục từ ô giờ (lệnh của module đó + ánh xạ trường), và với câu người gõ:
  đoán đối tượng (khách) / địa điểm. Lõi Lịch góp cách tạo của chính nó (``schedule.create``) cho các loại không module nào nhận.
"""

import re
from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from uuid import UUID

from barista.api import Ctx, Day, Denied, Input, Invalid, business_calendar, can_run, field, fold, today, ui, zone, zone_label
from barista.modules.schedule import (
    BUSY,
    MAYBE,
    MORE,
    REPEAT,
    RSVP,
    VISIBILITY,
    WEEKDAY,
    _agent_guard,
    _create,
    _event,
    _kind_ok,
    _may_change,
    _members_by_email,
    _new_series,
    _owns,
    _soft_clash,
    _team,
    _tell,
    m,
    rule_of,
    span,
    when,
)

VIEWS = {"day": "Ngày", "week": "Tuần", "agenda": "Lịch biểu"}
WHO = {"me": "Của tôi", "team": "Cả đội"}
WHO_ALL = {"me": "Của tôi", "team": "Theo đơn vị"}   # phạm vi cả công ty: chọn chi nhánh / phòng; chưa chọn ⇒ các trưởng nhóm
PALETTE = ("blue", "green", "orange", "purple", "teal", "pink", "indigo", "red")   # loại chưa chọn màu ⇒ theo thứ tự loại
AGENDA_DAYS = 14
MAX_LANES = 16
MAX_PEOPLE = 1500


class CalIn(Input):
    view: Literal["day", "week", "agenda"] = field("Xem theo", "day", options=VIEWS, hidden=True)
    on: Day | None = field("Ngày", None, hidden=True, help="Trống = hôm nay")
    who: Literal["", "me", "team"] = field("Lịch của", "", options={"": "Mặc định theo vai", **WHO}, hidden=True)
    team_code: str = field("Đơn vị", "", options_query="admin.team_options", hidden=True, help="Chọn ở ô Đơn vị trên lịch; trống = đội bạn quản lý")
    member_email: str = field("Một người", "", options_query="admin.member_options", group=MORE,
                              help="Lịch bạn được xem hiện đủ, lịch khác của người đó chỉ hiện “Bận”")


def _range(view: str, d: date) -> tuple[date, date]:
    if view == "week":
        mon = d - timedelta(days=d.isoweekday() - 1)
        return mon, mon + timedelta(days=6)
    if view == "agenda":
        return d, d + timedelta(days=AGENDA_DAYS - 1)
    return d, d


def _step(view: str, d: date, sign: int) -> date:
    return d + timedelta(days=sign * {"day": 1, "week": 7, "agenda": AGENDA_DAYS}[view])


def day_label(d: date) -> str:
    return f"{WEEKDAY[d.isoweekday()]} {d:%d/%m}"


def _iso(t: datetime) -> str:
    return t.astimezone(zone()).isoformat(timespec="minutes")


async def _people(ctx: Ctx, inp: CalIn, who: str) -> tuple[list[UUID], str]:
    """Người có lịch trên màn: một người được chọn, chính tôi, hoặc cả đội (đội chọn ở bộ lọc / đội tôi quản lý / đội chính của người phạm vi cả
    công ty). Chỉ là người CẦN XEM — đọc được gì vẫn do RLS + khối "Bận"."""
    me = ctx.p.user_id
    if inp.member_email.strip():
        got = await _members_by_email(ctx, [inp.member_email])
        return list(got), ""
    if who == "me":
        return [me] if me else [], ""
    team = await _team(ctx, inp.team_code)
    if not team and ctx.p.has_scope("schedule", "all"):
        # phạm vi cả công ty, chưa chọn đơn vị: tôi + các trưởng nhóm (người điều phối từng đội) — chọn chi nhánh / phòng để xem cả đơn vị đó
        rows = await ctx.all("""SELECT DISTINCT tm.user_id FROM core.team_members tm JOIN core.memberships mb ON mb.user_id = tm.user_id AND mb.active
                                WHERE tm.is_lead LIMIT %s""", (MAX_PEOPLE,))
        ids = [r["user_id"] for r in rows if r["user_id"] != me]
        return ([me] if me else []) + ids, "Các trưởng nhóm"
    teams = [team] if team else list(ctx.p.managed_teams) or ([t] if (t := await ctx.home_team(me)) else [])
    rows = await ctx.all("""SELECT DISTINCT tm.user_id FROM core.team_members tm JOIN core.memberships mb ON mb.user_id = tm.user_id AND mb.active
                            WHERE tm.team_id = ANY(%s) LIMIT %s""", (teams, MAX_PEOPLE))
    names = await ctx.all("SELECT name FROM core.teams WHERE id = ANY(%s) ORDER BY name", (teams,))
    ids = [r["user_id"] for r in rows]
    if me and me not in ids:
        ids.insert(0, me)   # làn của tôi luôn có (tạo lịch của mình ngay cả khi đang xem đơn vị khác)
    return ids, ", ".join(r["name"] for r in names)


async def _kinds(ctx: Ctx) -> dict[str, dict[str, str]]:
    rows = await ctx.all("SELECT key, label, tone FROM schedule.kinds WHERE active ORDER BY sort, label")
    return {r["key"]: {"key": r["key"], "label": r["label"], "tone": r["tone"] or PALETTE[i % len(PALETTE)]} for i, r in enumerate(rows)}


@m.query("calendar", resource="schedule", title="Lịch")
async def calendar(ctx: Ctx, inp: CalIn) -> dict[str, Any]:
    """Màn Lịch: Ngày (người cạnh nhau — mỗi người một cột giờ), Tuần (7 ngày, gộp lịch của những người đang xem), Lịch biểu (14 ngày dạng danh sách).
    Lịch người xem đọc được chi tiết hiện đủ; lịch khác của người trong phạm vi hiện "Bận" (0158). Lịch do module nguồn đặt hiện thêm tên đối tượng /
    nút mở hồ sơ nếu người xem thấy khách đó (điểm cắm ``schedule.calendar_items``). Mốc hạn (gọi lại, hạn việc…) ở hàng cả ngày của chính tôi."""
    d = inp.on or today()
    d0, d1 = _range(inp.view, d)
    lo, hi = datetime.combine(d0, time(0), zone()), datetime.combine(d1 + timedelta(days=1), time(0), zone())
    narrow = ctx.p.scope("schedule") == "own"
    who = "me" if narrow else (inp.who or ("team" if inp.view == "day" else "me"))
    people, team_name = await _people(ctx, inp, who)
    me = ctx.p.user_id
    kinds = await _kinds(ctx)

    # lịch đọc được chi tiết (RLS) chạm những người đang xem
    rows = await ctx.all(
        """SELECT e.id, e.title, e.starts_at, e.ends_at, e.status, e.source, e.kind, e.team_id, e.series_id IS NOT NULL AS repeats, e.created_by,
                  coalesce((SELECT a.rsvp FROM schedule.attendees a WHERE a.event_id = e.id AND a.user_id = core.me()), '') AS my_rsvp,
                  coalesce((SELECT string_agg(b.resource_name, ', ' ORDER BY b.resource_name) FROM schedule.bookings b
                            WHERE b.event_id = e.id AND b.released_at IS NULL AND b.user_id IS NULL), '') AS rooms,
                  ARRAY(SELECT a.user_id FROM schedule.attendees a WHERE a.event_id = e.id AND a.rsvp <> 'declined'
                        UNION SELECT b.user_id FROM schedule.bookings b WHERE b.event_id = e.id AND b.released_at IS NULL AND b.user_id IS NOT NULL) AS who,
                  ARRAY(SELECT p.party_id FROM schedule.event_parties p WHERE p.event_id = e.id) AS parties,
                  coalesce((SELECT u.name FROM core.users u WHERE u.id = e.created_by), '') AS organizer,
                  coalesce((SELECT jsonb_agg(jsonb_build_object('u', a.user_id, 'n', u.name, 'r', a.rsvp) ORDER BY u.name)
                            FROM schedule.attendees a JOIN core.users u ON u.id = a.user_id WHERE a.event_id = e.id), '[]'::jsonb) AS att,
                  (SELECT count(*) FROM schedule.bookings b WHERE b.event_id = e.id AND b.released_at IS NULL) AS held
           FROM schedule.events e
           WHERE tstzrange(e.starts_at, e.ends_at) && tstzrange(%(lo)s, %(hi)s) AND e.status <> 'cancelled'
           ORDER BY e.starts_at, e.title LIMIT 2000""", {"lo": lo, "hi": hi})
    wanted = set(people)
    seen: set[UUID] = set()
    items_raw: list[dict[str, Any]] = []
    for r in rows:
        whose = [u for u in (r["who"] or [r["created_by"]]) if u in wanted]
        if not whose:
            continue
        seen.add(r["id"])
        items_raw.append({"id": r["id"], "title": r["title"], "s": r["starts_at"], "e": r["ends_at"], "kind": r["kind"], "busy": False,
                          "status": r["status"], "repeats": r["repeats"], "whose": whose, "subtitle": r["rooms"], "team_id": r["team_id"], "source": r["source"],
                          "created_by": r["created_by"], "organizer": r["organizer"], "att": r["att"], "held": r["held"], "my_rsvp": r["my_rsvp"]})
    # khối "Bận": lịch của người đang xem mà tôi không được đọc chi tiết — chỉ giờ (0158)
    if people:
        busy: dict[UUID, dict[str, Any]] = {}
        for b in await ctx.all("SELECT * FROM schedule.blocks(%s::uuid[], %s::uuid[], tstzrange(%s, %s), NULL)", ([], people, lo, hi)):
            if b["event_id"] in seen or b["user_id"] is None:
                continue
            x = busy.setdefault(b["event_id"], {"id": b["event_id"], "title": MAYBE, "s": b["starts_at"], "e": b["ends_at"], "kind": "", "busy": True,
                                                "status": "maybe", "repeats": False, "whose": [], "subtitle": "", "team_id": None, "source": "",
                                                "created_by": None, "organizer": "", "att": [], "held": 0, "my_rsvp": ""})
            if b["hard"] or b["rsvp"] == "accepted":
                x["title"], x["status"] = BUSY, "busy"
            if b["user_id"] not in x["whose"]:
                x["whose"].append(b["user_id"])
        items_raw += busy.values()

    # module nguồn kể thêm về lịch của nó (tên khách, mở hồ sơ, dời bằng lệnh của nó) — bằng quyền người xem
    extra: dict[str, dict[str, Any]] = {}
    parties = {str(r["id"]): [str(x) for x in r["parties"]] for r in rows if r["parties"] and r["id"] in seen}
    for part in await ctx.plugs("schedule.calendar_items", since=lo, until=hi, parties=parties):
        for x in part:
            extra[str(x["event_id"])] = x
    can_move = can_run(ctx.p, "schedule.create")   # quyền ĐẶT lịch ở đội (ghi) — dời lịch của người khác; lịch của chính mình xét riêng (_owns)
    wide = ctx.p.has_scope("schedule", "all")

    items: list[dict[str, Any]] = []
    for r in items_raw:
        x = extra.get(str(r["id"]))
        kind = (x or {}).get("kind") or r["kind"]
        tone = kinds[kind]["tone"] if kind in kinds else ("gray" if r["busy"] and not x else "blue")
        title = (x or {}).get("title") or r["title"]
        open_ = (x or {}).get("open") if r["busy"] and x else {"query": "schedule.event", "input": {"event_id": str(r["id"])}, "title": title}
        move = cancel = None
        if x and x.get("move"):
            move = x["move"]
        elif not r["busy"] and not r["source"] and (_owns(ctx, r, r["held"])
                                                    or (can_move and (wide or (r["team_id"] is not None and r["team_id"] in ctx.p.managed_teams)))):
            # lịch của chính mình (người tổ chức) hoặc người được đặt lịch ở đội: kéo = schedule.move, huỷ = schedule.cancel
            move = {"command": "schedule.move", "preset": {"event_id": str(r["id"])}, "fields": {"day": "day", "start": "start", "end": "end"}}
            cancel = {"command": "schedule.cancel", "preset": {"event_id": str(r["id"])}}
        rsvp = {str(a["u"]): a["r"] for a in r["att"]}
        people_ = [{"name": a["n"], "rsvp": a["r"], "label": RSVP[a["r"]], "organizer": a["u"] == str(r["created_by"])} for a in r["att"]]
        respond = ({"command": "schedule.respond", "preset": {"event_id": str(r["id"])}, "param": "answer", "current": r["my_rsvp"],
                    "options": [{"key": k, "title": t} for k, t in (("accepted", "Đồng ý"), ("tentative", "Có thể"), ("declined", "Từ chối"))]}
                   if r["my_rsvp"] and r["created_by"] != me and r["status"] == "booked" else None)
        all_day = (r["e"] - r["s"]) >= timedelta(hours=20)
        start, end = (_iso(r["s"]), _iso(r["e"])) if not all_day else (
            r["s"].astimezone(zone()).date().isoformat(), (r["e"] - timedelta(seconds=1)).astimezone(zone()).date().isoformat())
        subtitle = " · ".join(v for v in ((x or {}).get("subtitle", ""), r["subtitle"]) if v)
        lanes = r["whose"] if inp.view == "day" else [None]
        for u in lanes:
            items.append(ui.cal_item(str(r["id"]), title, start, end, lane=str(u) if u else "", subtitle=subtitle, all_day=all_day, kind=kind,
                                     tone="gray" if (r["busy"] and not x) else tone,
                                     # trên làn của từng người: câu trả lời CỦA NGƯỜI ĐÓ (chưa nhận ⇒ "chờ xác nhận"); tuần / lịch biểu: của tôi
                                     status="booked" if (r["busy"] and x) else (
                                         "tentative" if rsvp.get(str(u if u else me), "") in ("invited", "tentative") and r["status"] == "booked" else r["status"]),
                                     busy=r["busy"] and not x, repeats=r["repeats"], open=open_, links=(x or {}).get("links"),
                                     move=None if all_day else move, cancel=cancel, organizer=r["organizer"] if r["att"] else "", people=people_, respond=respond))

    # mốc hạn của CHÍNH TÔI (gọi lại khách, hạn việc, đợt đóng tiền) — hàng cả ngày, không chiếm giờ
    if me and me in wanted:
        for part in await ctx.plugs("schedule.markers", since=d0, until=d1 + timedelta(days=1), mine=True):
            for mk in part:
                on: date = mk["on"] if isinstance(mk["on"], date) else date.fromisoformat(str(mk["on"]))
                tone = kinds[mk["kind"]]["tone"] if mk["kind"] in kinds else "gray"
                items.append(ui.cal_item(f"m:{mk['kind']}:{mk.get('ref', '')}:{on.isoformat()}", mk["title"], on.isoformat(), on.isoformat(),
                                         lane=str(me) if inp.view == "day" else "", subtitle=mk["label"] + (f" · {mk['detail']}" if mk.get("detail") else ""),
                                         all_day=True, kind=mk["kind"], tone=tone, status="marker", open=mk.get("open")))

    # người cạnh nhau (chế độ Ngày): người có lịch hôm đó + chính tôi; nhiều quá ⇒ cắt, nói rõ còn bao nhiêu người
    lanes_out: list[dict[str, Any]] = []
    note = ""
    if inp.view == "day":
        busy_people = {UUID(i["lane"]) for i in items if i["lane"]}
        order = [u for u in people if u == me] + [u for u in people if u != me and u in busy_people]
        if inp.member_email.strip():
            order = people
        shown = order[:MAX_LANES]
        info = {r["id"]: r for r in await ctx.all("SELECT id, name, email FROM core.users WHERE id = ANY(%s)", (shown,))}
        lanes_out = [{"key": str(u), "title": "Tôi" if u == me else info[u]["name"], "subtitle": info[u]["name"] if u == me else "",
                      "me": u == me, "email": info[u]["email"],
                      "open": {"view": "week", "member_email": info[u]["email"]}} for u in shown if u in info]
        keep = {ln["key"] for ln in lanes_out}
        items = [i for i in items if i["lane"] in keep]
        quiet = len(people) - len(order)
        if who == "team" and team_name:
            note = f"{team_name}: {max(0, len(order) - 1)} người khác có lịch hôm nay" + (f", {quiet} người còn trống" if quiet > 0 else "") + "."
        if len(order) > MAX_LANES:
            note += f" Hiện {MAX_LANES} người đầu — chọn “Một người” ở Lọc thêm để xem người khác."

    # khung giờ lưới: 7–20 giờ, nới cho phủ mọi mục có giờ
    lo_h, hi_h = 7, 20
    for i in items:
        if not i["all_day"]:
            s, e = datetime.fromisoformat(i["start"]), datetime.fromisoformat(i["end"])
            lo_h = min(lo_h, s.hour) if s.date() >= d0 else 0
            hi_h = max(hi_h, min(24, e.hour + (1 if e.minute else 0)) if e.date() == s.date() else 24)
    # ngày nghỉ theo lịch làm việc CÔNG TY / đội đã đặt ở "Ngày làm việc & nghỉ lễ" (cùng nguồn với dời hạn việc); không có ⇒ lịch hành chính
    work = await ctx.call_system_optional("flow.calendar_of", team_id=await _team(ctx, inp.team_code) if inp.team_code else await ctx.home_team(me))
    off = {}
    try:
        off = dict(business_calendar.non_working((work or {}).get("calendar") or "vn-public", d0, d1))
    except ValueError:
        pass
    days = [{"on": x.isoformat(), "label": day_label(x), "off": off.get(x, "")} for x in (d0 + timedelta(days=k) for k in range((d1 - d0).days + 1))]

    creators = await _creators(ctx, kinds)
    t = today()
    nav: dict[str, Any] = {"param_view": "view", "param_on": "on", "prev": _step(inp.view, d, -1).isoformat(), "next": _step(inp.view, d, 1).isoformat(),
                           "views": [{"key": k, "title": v} for k, v in VIEWS.items()]}
    if not narrow and not inp.member_email.strip():
        nav["who"] = {"param": "who", "current": who, "options": [{"key": k, "title": v} for k, v in (WHO_ALL if wide else WHO).items()]}
        units = await ctx.all("SELECT code, name FROM core.teams ORDER BY name LIMIT 300") if wide else await ctx.all(
            "SELECT code, name FROM core.teams WHERE id = ANY(%s) ORDER BY name", (list(ctx.p.managed_teams),))
        if who == "team" and (wide or len(units) > 1):   # chọn chi nhánh / phòng (đơn vị) — đặt bộ lọc team_code của màn
            nav["unit"] = {"param": "team_code", "current": inp.team_code,
                           "options": [{"key": "", "title": "Các trưởng nhóm" if wide else "Mọi đội của tôi"}] + [{"key": u["code"], "title": u["name"]} for u in units]}
    if inp.view == "week":
        title = f"Tuần {d0:%d/%m} – {d1:%d/%m/%Y}"
    elif inp.view == "agenda":
        title = f"{d0:%d/%m} – {d1:%d/%m/%Y}"
    else:
        title = f"{'Hôm nay, ' if d == t else ''}{WEEKDAY[d.isoweekday()].lower() if d == t else WEEKDAY[d.isoweekday()]} {d.day} tháng {d.month}, {d.year}"
    empty = ("Nhấn đúp (hoặc kéo) vào giờ trống để thêm lịch, hoặc gõ một câu ở ô “Thêm nhanh”." if creators else
             "Chưa có lịch nào. Lịch của bạn, họp và mốc hạn sẽ hiện ở đây.")
    if any(i["busy"] for i in items):
        note = (note + " “Bận” là lịch bạn không được xem chi tiết.").strip()
    block = ui.b_calendar(inp.view, start=d0, end=d1, today=t, now=_iso(datetime.now(zone())), tz=zone_label(head=True), days=days, items=items,
                          lanes=lanes_out, kinds=list(kinds.values()), hours=(lo_h, hi_h), nav=nav, title=title, empty=empty, note=note,
                          create={"creators": creators, "understand": "schedule.understand", "me": ctx.p.email, "invite": await _invite(ctx)}
                          if creators else None)
    return ui.card("Lịch", [block], icon="calendar")


async def _invite(ctx: Ctx) -> dict[str, Any]:
    """Ô "Mời" của hộp tạo nhanh: chọn người từ danh mục thành viên (``admin.member_options`` — không gõ email) + gợi ý cả đội: đội tôi quản lý / đội chính
    của tôi; phạm vi cả công ty thêm "Các trưởng nhóm"."""
    me = ctx.p.user_id
    teams = list(ctx.p.managed_teams) or ([t] if (t := await ctx.home_team(me)) else [])
    groups = []
    for t in await ctx.all("SELECT id, name FROM core.teams WHERE id = ANY(%s) ORDER BY name", (teams,)):
        emails = [r["email"] for r in await ctx.all(
            """SELECT DISTINCT u.email FROM core.team_members tm JOIN core.users u ON u.id = tm.user_id JOIN core.memberships mb ON mb.user_id = u.id AND mb.active
               WHERE tm.team_id = %s AND u.id IS DISTINCT FROM core.me() LIMIT 300""", (t["id"],))]
        if emails:
            groups.append({"title": f"Cả đội {t['name']}", "emails": emails})
    if ctx.p.has_scope("schedule", "all"):
        leads = [r["email"] for r in await ctx.all("""SELECT DISTINCT u.email FROM core.team_members tm JOIN core.users u ON u.id = tm.user_id
                                                      JOIN core.memberships mb ON mb.user_id = u.id AND mb.active
                                                      WHERE tm.is_lead AND u.id IS DISTINCT FROM core.me() LIMIT 300""")]
        if leads:
            groups.append({"title": "Các trưởng nhóm", "emails": leads})
    return {"options": "admin.member_options", "groups": groups}


async def _creators(ctx: Ctx, kinds: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    """Cách tạo mục từ ô giờ trống: module khác góp (lệnh của module đó, vd đặt hẹn với đối tượng của nó), lõi Lịch góp ``schedule.plan`` (lịch của chính
    người tạo, mời người khác) cho các loại còn lại. Màu lấy theo loại lịch."""
    out: list[dict[str, Any]] = []
    for part in await ctx.plugs("schedule.quick_add", text=""):
        out += part.get("creators", [])
    taken = {c["kind"] for c in out}
    if ctx.p.user_id is not None and can_run(ctx.p, "schedule.plan"):   # 0240: ai xem được Lịch cũng tạo được lịch CỦA MÌNH và mời người khác
        out += [{"key": k["key"], "label": k["label"], "kind": k["key"], "command": "schedule.plan", "preset": {"kind": k["key"]},
                 "fields": {"day": "day", "start": "start", "end": "end", "title": "title", "attendee": "attendee_emails"},
                 "words": [fold(k["label"]).split()[0]] if k["label"].strip() else []}
                for k in kinds.values() if k["key"] not in taken]
    for c in out:
        c["tone"] = kinds.get(c["kind"], {}).get("tone", "blue")
        c["label"] = kinds.get(c["kind"], {}).get("label", c["label"])
    return out


# ── Thêm bằng câu tự nhiên: máy đoán ngày giờ / loại / khách / địa điểm, người sửa chip rồi bấm xác nhận (0225) ──

class UnderstandIn(Input):
    text: str = field("Câu", max_length=300, example="Thứ 5 2h chiều dẫn khách Lan xem Sun Feliza")
    on: Day | None = field("Đang xem ngày", None, help="Không nêu ngày trong câu ⇒ ngày này")


_DOW = {"hai": 1, "2": 1, "ba": 2, "3": 2, "tu": 3, "4": 3, "nam": 4, "5": 4, "sau": 5, "6": 5, "bay": 6, "7": 6}
_DAY_RE = re.compile(r"\b(?:(hom nay|nay)|(ngay mai|mai)|(ngay kia|ngay mot|mot)|(?:(tuan sau|tuan toi)\s+)?(?:thu\s*(hai|ba|tu|nam|sau|bay|[2-7])|t([2-7])|(chu nhat|cn))"
                     r"(?:\s+(tuan sau|tuan toi))?|(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?)\b")
_TIME_RE = re.compile(r"\b(\d{1,2})\s*(?:(?:gio|:|h|g)\s*(\d{2})?)(?:\s*(ruoi))?(?:\s*(sang|trua|chieu|toi|pm|am))?")
_DUR_RE = re.compile(r"\b(\d{1,3})\s*(phut|p\b|tieng|gio dong ho)")
_UNTIL_RE = re.compile(r"(?:den|toi|-|–)\s*(\d{1,2})\s*(?:gio|:|h|g)\s*(\d{2})?")


def _fold_same_length(s: str) -> str:
    """Như ``fold`` nhưng giữ đúng độ dài từng ký tự — để cắt ngày giờ ra khỏi câu gốc theo cùng vị trí."""
    return "".join((fold(ch) or " ")[0] for ch in s)


def _hour(h: int, mod: str | None) -> int:
    if mod in ("chieu", "toi", "pm") and h < 12:
        return h + 12
    if mod == "trua" and h <= 2:
        return h + 12
    if mod is None and 1 <= h <= 6:   # "2h" trong giờ làm việc là 14h — gõ "6h sáng" nếu là sáng sớm
        return h + 12
    return h


def understand_text(text: str, base: date, now: datetime) -> dict[str, Any]:
    """Phần thuần (không DB) — ngày, giờ, độ dài, chữ còn lại làm tên việc. Không nêu ngày ⇒ ``base``; thứ trong tuần ⇒ lần tới tính từ ``base``."""
    f = _fold_same_length(text).lower()
    cut: list[tuple[int, int]] = []
    day, start, minutes, end = None, None, None, None
    if mt := _DAY_RE.search(f):
        g = mt.groups()
        if g[0]:
            day = now.date()
        elif g[1]:
            day = now.date() + timedelta(days=1)
        elif g[2]:
            day = now.date() + timedelta(days=2)
        elif g[4] or g[5] or g[6]:
            dow = 7 if g[6] else _DOW[g[4] or g[5]]
            ref = max(base, now.date())
            if g[3] or g[7]:   # "thứ 5 tuần sau": thứ đó của tuần kế tiếp (tuần bắt đầu thứ Hai)
                day = ref - timedelta(days=ref.isoweekday() - 1) + timedelta(days=7 + dow - 1)
            else:
                day = ref + timedelta(days=(dow - ref.isoweekday()) % 7)
        elif g[8]:
            y = int(g[10]) if g[10] else base.year
            y = y + 2000 if y < 100 else y
            try:
                day = date(y, int(g[9]), int(g[8]))
            except ValueError:
                day = None
        if day:
            cut.append(mt.span())
    if mt := _TIME_RE.search(f):
        h, mi = int(mt.group(1)), int(mt.group(2) or 0) + (30 if mt.group(3) else 0)
        mod = mt.group(4)
        if mod is None and (mw := re.search(r"\b(sang|trua|chieu|toi)\b", f)):   # "chiều mai 3h": buổi đứng trước ngày / giờ
            mod = mw.group(1)
            cut.append(mw.span())
        if h <= 23 and mi < 60:
            start = time(_hour(h, mod), mi)
            cut.append(mt.span())
            if mu := _UNTIL_RE.search(f, mt.end()):
                eh = _hour(int(mu.group(1)), mod if int(mu.group(1)) < 12 else None)
                if eh <= 23 and (em := int(mu.group(2) or 0)) < 60 and time(eh, em) > start:
                    end = time(eh, em)
                    cut.append(mu.span())
    if md := _DUR_RE.search(f):
        n = int(md.group(1))
        minutes = n * 60 if md.group(2).startswith("tieng") or md.group(2).startswith("gio") else n
        cut.append(md.span())
    if start and end:
        minutes = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
    title = text
    for a, b in sorted(cut, reverse=True):
        title = title[:a] + " " + title[b:]
    title = re.sub(r"\s+", " ", title).strip(" ,.-–")
    title = re.sub(r"^(lúc|vào|từ)\s+|\s+(lúc|vào|từ)$", "", title, flags=re.I).strip()
    return {"day": (day or base).isoformat(), "day_said": day is not None, "start": f"{start:%H:%M}" if start else "", "minutes": minutes or 0,
            "title": title[:1].upper() + title[1:] if title else ""}


@m.query("understand", resource="schedule", title="Hiểu câu thêm lịch")
async def understand(ctx: Ctx, inp: UnderstandIn) -> dict[str, Any]:
    """Câu người gõ ⇒ bản ĐOÁN để người sửa rồi bấm xác nhận (không ghi gì): ngày, giờ, độ dài, loại lịch (từ gợi ý của từng cách tạo), khách và địa điểm
    (module của cách tạo đoán, bằng quyền người xem). Không chắc ⇒ để trống, không bịa."""
    base = inp.on or today()
    got = understand_text(inp.text, base, datetime.now(zone()))
    kinds = await _kinds(ctx)
    creators = await _creators(ctx, kinds)
    f = f" {fold(inp.text)} "
    pick = next((c["key"] for c in creators for w in c.get("words", []) if w and (f" {w}" in f)), "")
    guesses: dict[str, Any] = {}
    for part in await ctx.plugs("schedule.quick_add", text=inp.text):
        for k in ("subject", "place"):
            if part.get(k) and k not in guesses:
                guesses[k] = part[k]
    said = bool(pick)
    if not pick and guesses.get("subject"):   # có khách mà không rõ loại ⇒ cách tạo đầu tiên có ô khách
        pick = next((c["key"] for c in creators if c.get("subject")), "")
    return {**got, "creator": pick or (creators[0]["key"] if creators else ""), "creator_said": said, **guesses}


# ── Lịch CỦA MÌNH (0240, kiểu Teams): ai xem được Lịch cũng tạo được — người tạo là người tổ chức, mời người trong công ty (mềm), không giữ người / phòng ──

PLAN_MORE = "Lặp lại & riêng tư"


class PlanEvent(Input):
    title: str = field("Việc", max_length=200, example="Họp đội đầu tuần")
    day: Day = field("Ngày", example="19/11/2026")
    start: str = field("Từ", "09:00", kind="time", max_length=5)
    end: str = field("Đến", "10:00", kind="time", max_length=5)
    end_day: Day | None = field("Ngày kết thúc", None, help="Trống = cùng ngày")
    kind: str = field("Loại lịch", "", options_query="schedule.event_kind_options", help="Trống = không phân loại")
    attendee_emails: list[str] = field("Mời", [], kind="multiselect", options_query="admin.member_options",
                                       help="Người được mời thấy lịch trên lịch của họ và trả lời Đồng ý / Từ chối; trùng giờ chỉ cảnh báo")
    note: str = field("Ghi chú", "", max_length=1000)
    visibility: Literal["normal", "private"] = field("Chế độ xem", "normal", options=VISIBILITY, group=PLAN_MORE)
    repeat: Literal["", "daily", "weekly"] = field("Lặp lại", "", options=REPEAT, group=PLAN_MORE)
    repeat_until: Day | None = field("Lặp đến ngày", None, group=PLAN_MORE)
    repeat_count: int | None = field("Hoặc số lần", None, ge=1, le=200, group=PLAN_MORE)


@m.command("plan", resource="schedule", action="read", title="Thêm lịch của tôi", agent="propose")
async def plan(ctx: Ctx, inp: PlanEvent) -> dict[str, Any]:
    """Lịch do CHÍNH người đặt tổ chức: người đặt tự nhận, người được mời (trong công ty) thấy lịch trên lịch của họ và trả lời. Không giữ người / phòng
    (giữ cứng cần quyền đặt lịch — ``schedule.create``). Người tổ chức dời / huỷ / đổi người mời được lịch của mình."""
    me = ctx.p.user_id
    if me is None:
        raise Denied("Cần đăng nhập bằng tài khoản người dùng")
    s, e = span(inp.day, inp.start, inp.end, inp.end_day)
    if not inp.title.strip():
        raise Invalid("Nhập tên việc")
    kind = await _kind_ok(ctx, inp.kind)
    people = await _members_by_email(ctx, inp.attendee_emails)
    others = [u for u in people if u != me]
    _agent_guard(ctx, set(others))
    team = await ctx.home_team(me)
    who = await ctx.val("SELECT name FROM core.users WHERE id = %s", (me,)) or ""
    args: dict[str, Any] = {"title": inp.title.strip(), "team": team, "ids": [], "allow_outside": True, "note": inp.note, "kind": kind,
                            "visibility": inp.visibility, "attendees": [me, *others], "accepted": {me}}
    warnings = await _soft_clash(ctx, others, s, e)
    rule = rule_of(inp.repeat, 1, [], inp.repeat_until, inp.repeat_count)
    if rule is not None:
        _, made = await _new_series(ctx, s=s, e=e, rule=rule, parties=[], **args)
        await _tell(ctx, others, f"{who} mời: {inp.title.strip()} — {when(s, e)} từ {s.astimezone(zone()):%d/%m} (lặp)", s)
        return {"warnings": warnings, "invited": len(others), "summary": f"{inp.title.strip()}: {when(s, e)} — lặp {made} lần"
                + (f", mời {len(others)} người" if others else "")}
    eid = await _create(ctx, s=s, e=e, **args)
    await _tell(ctx, others, f"{who} mời: {inp.title.strip()} — {when(s, e)} {s.astimezone(zone()):%d/%m}", s)
    return {"event_id": eid, "warnings": warnings, "invited": len(others),
            "summary": f"{inp.title.strip()}: {when(s, e)} {s.astimezone(zone()):%d/%m}" + (f" — đã mời {len(others)} người" if others else "")}


class EditEvent(Input):
    event_id: UUID = field("Lịch", hidden=True)
    title: str = field("Việc", max_length=200)
    kind: str = field("Loại lịch", "", options_query="schedule.event_kind_options", help="Trống = không phân loại")
    note: str = field("Ghi chú", "", max_length=1000)
    visibility: Literal["normal", "private"] = field("Chế độ xem", "normal", options=VISIBILITY)


@m.command("edit", resource="schedule", action="read", title="Sửa lịch", agent="propose")
async def edit(ctx: Ctx, inp: EditEvent) -> dict[str, Any]:
    """Sửa tên việc, loại, ghi chú, chế độ xem của MỘT lịch (lịch lặp: chỉ lần này). Giờ ⇒ ``schedule.move``; người tham dự ⇒ ``schedule.set_attendees``.
    Lịch của chính mình, hoặc người có quyền đặt lịch ở đội của lịch."""
    ev = await _event(ctx, inp.event_id)
    await _may_change(ctx, ev)
    if ev["status"] != "booked":
        raise Invalid("Chỉ sửa lịch đang đặt")
    if not inp.title.strip():
        raise Invalid("Nhập tên việc")
    kind = await _kind_ok(ctx, inp.kind)
    await ctx.exec("""UPDATE schedule.events SET title = %s, kind = %s, note = %s, visibility = %s, detached = series_id IS NOT NULL OR detached
                      WHERE id = %s""", (inp.title.strip(), kind, inp.note, inp.visibility, ev["id"]))
    return {"event_id": ev["id"], "summary": f"Đã sửa: {inp.title.strip()}"}
