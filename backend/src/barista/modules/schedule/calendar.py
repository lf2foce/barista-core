"""Phần lịch của gói Lịch (0158): loại lịch (danh mục công ty), trả lời lời mời, người tham dự & bên ngoài của một lịch, tìm giờ cho nhiều người (người
tham dự mềm, phòng / thiết bị cứng, giờ làm việc), mốc hạn (đọc gộp từ module sở hữu — không lưu thành lịch), vòng quét kéo dài lịch lặp."""

import re
from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from uuid import UUID

from pydantic import field_validator

from barista.api import Ctx, Day, Input, Invalid, NoInput, NotFound, field, fold, today, ui, zone, zone_label
from barista.modules.schedule import (
    BUSY,
    HORIZON,
    KIND_KEY,
    RSVP,
    _agent_guard,
    _event,
    _may_change,
    _members_by_email,
    _parties_seen,
    _put_attendees,
    _put_parties,
    _soft_clash,
    _tell,
    _windows,
    fits,
    m,
    materialise,
    minutes,
    when,
)

# ── Loại lịch: dữ liệu của công ty ──

# màu của loại trên màn Lịch (0240) — tên tông của hợp đồng thẻ, app đổi ra màu theo giao diện sáng / tối
TONES = {"blue": "Xanh dương", "green": "Xanh lá", "orange": "Cam", "red": "Đỏ", "purple": "Tím", "teal": "Xanh ngọc", "pink": "Hồng", "indigo": "Chàm",
         "gray": "Xám"}

def code_from_name(name: str, taken: set[str] | frozenset[str] = frozenset(), limit: int = 30) -> str:
    """Mã do MÁY đặt từ tên (ADR 0225): chữ HOA không dấu, số, gạch nối; trùng ``taken`` ⇒ -2, -3… (cùng luật ``kernel/text.code_from_name`` — module không
    import ``barista.kernel``; bỏ bản này khi ``barista.api`` xuất hàm đó)."""
    words = re.findall(r"[a-z0-9]+", fold(name))
    base = ""
    for w in words:
        nxt = f"{base}-{w}" if base else w
        if len(nxt) > limit - 3:   # chừa chỗ cho hậu tố -NN
            break
        base = nxt
    base = (base or (words[0][: limit - 3] if words else "ma")).upper()
    code, i = base, 2
    while code in taken:
        code, i = f"{base}-{i}", i + 1
    return code


def _kind_key(label: str, taken: set[str]) -> str:
    base = code_from_name(label, limit=40).lower().replace("-", "_")
    base = base if base[:1].isalpha() else f"loai_{base}"
    key, i = base, 2
    while key in taken:
        key, i = f"{base}_{i}", i + 1
    return key


class KindIn(Input):
    label: str = field("Tên hiện ra", max_length=80, example="Họp nội bộ")
    key: str = field("Mã", "", max_length=40, example="meeting", placeholder="Máy đặt từ tên", group="Thêm chi tiết",
                     help="Trống = máy đặt từ tên (loại trùng tên đã có ⇒ sửa loại đó). Chữ thường không dấu, gạch dưới — không đổi sau khi đã dùng")
    active: bool = field("Đang dùng", True, help="Bỏ chọn: không chọn được cho lịch mới; lịch cũ giữ nguyên")
    sort: int = field("Thứ tự", 100, ge=0, le=10_000, group="Thêm chi tiết")
    tone: Literal["", "blue", "green", "orange", "red", "purple", "teal", "pink", "indigo", "gray"] = field(
        "Màu trên lịch", "", options={"": "Tự chọn theo thứ tự", **TONES}, help="Màu của mục loại này trên màn Lịch (0240)")
    only_new: bool = field("Chỉ thêm khi chưa có", False, hidden=True, agent=False, help="Bộ mặc định của gói: công ty đã đặt thì giữ của công ty")


@m.command("save_kind", resource="schedule", action="manage", title="Thêm / sửa loại lịch", agent="propose", category="setup")
async def save_kind(ctx: Ctx, inp: KindIn) -> dict[str, Any]:
    """Loại lịch là danh mục của công ty (0158): gói góp bộ mặc định (họp, lịch của gói ngành…) qua đúng lệnh này lúc cài; quản trị đổi
    tên / tắt / thêm ở Lịch › Loại lịch."""
    key = inp.key.strip().lower()
    if not key and inp.label.strip():   # máy đặt mã từ tên (0225): loại trùng tên đã có ⇒ sửa loại đó
        key = await ctx.val("SELECT key FROM schedule.kinds WHERE core.vn_fold(btrim(label)) = core.vn_fold(btrim(%s)) LIMIT 1", (inp.label,)) or \
            _kind_key(inp.label, {r["key"] for r in await ctx.all("SELECT key FROM schedule.kinds")})
    if not KIND_KEY.match(key) or not inp.label.strip():
        raise Invalid("Mã loại lịch: chữ thường không dấu, số, gạch dưới (bắt đầu bằng chữ); cần tên hiện ra")
    if inp.only_new:
        n = await ctx.exec("""INSERT INTO schedule.kinds (key, label, active, sort, tone) VALUES (%s, %s, %s, %s, %s)
                              ON CONFLICT (tenant_id, key) DO NOTHING""", (key, inp.label.strip(), inp.active, inp.sort, inp.tone))
        return {"key": key, "added": bool(n)}
    await ctx.exec("""INSERT INTO schedule.kinds (key, label, active, sort, tone) VALUES (%s, %s, %s, %s, %s)
                      ON CONFLICT (tenant_id, key) DO UPDATE SET label = excluded.label, active = excluded.active, sort = excluded.sort, tone = excluded.tone""",
                   (key, inp.label.strip(), inp.active, inp.sort, inp.tone))
    return {"key": key, "added": True, "summary": f"Loại lịch {inp.label.strip()}"}


@m.query("kinds", resource="schedule", title="Loại lịch")
async def kinds(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await ctx.all("""SELECT k.key, k.label, k.active, k.sort, k.tone, (SELECT count(*) FROM schedule.events e WHERE e.kind = k.key) AS used
                            FROM schedule.kinds k ORDER BY k.active DESC, k.sort, k.label""")
    for r in rows:
        r["id"] = r["key"]
        r["state"] = "active" if r["active"] else "inactive"
        r["color"] = r["tone"] or "auto"
    return ui.table(rows, [ui.col("label", "Tên", width=220), ui.col("key", "Mã"),
                           ui.col("color", "Màu trên lịch", "badge", labels={"auto": "Tự chọn", **TONES}, tones={k: k for k in TONES}), ui.col("used", "Đã dùng", "number"), ui.col("sort", "Thứ tự", "number"),
                           ui.col("state", "Trạng thái", "badge", labels={"active": "Đang dùng", "inactive": "Đã tắt"}, tones={"active": "green", "inactive": "gray"})],
                    title="Loại lịch", limit=500, empty="Chưa có loại lịch nào — bật gói sẽ có bộ mặc định, hoặc bấm “Thêm / sửa loại lịch”.",
                    note="Gói góp loại của mình khi cài (họp, lịch của gói ngành…); công ty đổi tên / tắt tuỳ ý. “Đã dùng” đếm lịch bạn xem được.")


@m.query("event_kind_options", resource="schedule", title="Chọn loại lịch")
async def event_kind_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return ui.options(await ctx.all("SELECT key AS value, label FROM schedule.kinds WHERE active ORDER BY sort, label"))


# ── Người tham dự: trả lời lời mời, đổi danh sách ──

class Respond(Input):
    event_id: UUID = field("Lịch", hidden=True)
    answer: Literal["accepted", "declined", "tentative"] = field("Trả lời", "accepted", options={k: RSVP[k] for k in ("accepted", "tentative", "declined")})


# action="read": trả lời lời mời là việc của CHÍNH người được mời trên lịch họ đã thấy — ai đọc được Lịch là trả lời được, không cần quyền ghi lịch
@m.command("respond", resource="schedule", action="read", title="Trả lời lời mời", agent="propose")
async def respond(ctx: Ctx, inp: Respond) -> dict[str, Any]:
    """Người được mời trả lời: tham dự / có thể / không tham dự. Không tham dự ⇒ lịch không còn tính là bận của mình. Người tạo lịch được báo."""
    a = await ctx.one("""SELECT a.id, a.rsvp, e.id AS event_id, e.title, e.status, e.created_by, e.starts_at, e.ends_at FROM schedule.attendees a
                         JOIN schedule.events e ON e.id = a.event_id WHERE a.event_id = %s AND a.user_id = core.me() FOR UPDATE OF a""", (inp.event_id,))
    if a is None:
        raise NotFound("Bạn không có trong danh sách mời của lịch này")
    if a["status"] != "booked":
        raise Invalid("Lịch đã huỷ / đã xong — không cần trả lời")
    await ctx.exec("UPDATE schedule.attendees SET rsvp = %s, responded_at = now() WHERE id = %s", (inp.answer, a["id"]))
    s = a["starts_at"]
    await ctx.notify([a["created_by"]], kind="schedule", title=f"{ctx.p.name}: {RSVP[inp.answer].lower()} — {a['title']} {when(s, a['ends_at'])} {s.astimezone(zone()):%d/%m}",
                     open={"query": "schedule.event", "input": {"event_id": str(a["event_id"])}})
    return {"event_id": a["event_id"], "rsvp": inp.answer, "summary": f"{RSVP[inp.answer]}: {a['title']}"}


class SetAttendees(Input):
    event_id: UUID = field("Lịch", hidden=True)
    attendee_emails: list[str] = field("Người tham dự", [], kind="multiselect", options_query="admin.member_options",
                                       help="Danh sách đầy đủ: bỏ tên là bỏ mời; người còn trong danh sách giữ câu trả lời")
    party_ids: list[UUID] = field("Bên ngoài (người / tổ chức ngoài công ty)", [], kind="multiselect", options_query="parties.options")


@m.command("set_attendees", resource="schedule", action="read", title="Đổi người tham dự", agent="propose")
async def set_attendees(ctx: Ctx, inp: SetAttendees) -> dict[str, Any]:
    """Đổi người tham dự / bên ngoài của MỘT lịch (lịch lặp: chỉ lần này — thành lần sửa riêng). Người mới được báo; trùng giờ chỉ cảnh báo."""
    ev = await _event(ctx, inp.event_id)
    await _may_change(ctx, ev)
    if ev["status"] != "booked":
        raise Invalid("Chỉ đổi người tham dự của lịch đang đặt")
    people = await _members_by_email(ctx, inp.attendee_emails)
    await _parties_seen(ctx, inp.party_ids)
    _agent_guard(ctx, set(people))
    me = {ctx.p.user_id} & set(people) if ctx.p.user_id else set()
    invited = await _put_attendees(ctx, ev["id"], list(people), me)
    await _put_parties(ctx, ev["id"], inp.party_ids)
    await ctx.exec("UPDATE schedule.events SET detached = true WHERE id = %s AND series_id IS NOT NULL", (ev["id"],))
    s = ev["starts_at"]
    await _tell(ctx, invited, f"Lời mời: {ev['title']} {when(s, ev['ends_at'])} {s.astimezone(zone()):%d/%m}", s)
    warnings = await _soft_clash(ctx, invited, s, ev["ends_at"], ev["id"])
    return {"event_id": ev["id"], "invited": len(invited), "warnings": warnings, "summary": f"{ev['title']}: {len(people)} người tham dự"}


# ── Tìm giờ cho nhiều người: người tham dự MỀM (bận ⇒ xếp sau, nêu tên), phòng / thiết bị CỨNG (bận / đủ lượt ⇒ loại), trong giờ làm việc ──

MORE = "Thêm điều kiện"
STEP = timedelta(minutes=30)


class FindTime(Input):
    @field_validator("attendee_emails", "resource_ids", mode="before")
    @classmethod
    def _one_or_many(cls, v: Any) -> Any:
        """Bộ lọc chọn MỘT người gửi chuỗi, không phải danh sách ("Input should be a valid list" — rà UI 20/11) ⇒ coi là danh sách một phần tử; chuỗi rỗng = không chọn."""
        return [] if v in ("", None) else [v] if isinstance(v, str) else v

    attendee_emails: list[str] = field("Ai cần có mặt", [], kind="multiselect", options_query="admin.member_options")
    resource_ids: list[UUID] = field("Phòng / thiết bị", [], kind="multiselect", options_query="schedule.resource_options",
                                     help="Giữ cứng: giờ nào phòng bận / đủ lượt thì không gợi ý")
    from_day: Day | None = field("Từ ngày", None, placeholder="Hôm nay")
    to_day: Day | None = field("Đến ngày", None, placeholder="7 ngày tới")
    minutes: int = field("Dài bao lâu (phút)", 60, ge=15, le=600)
    day_start: str = field("Sớm nhất", "08:00", max_length=5, group=MORE)
    day_end: str = field("Muộn nhất", "17:30", max_length=5, group=MORE)
    weekend: bool = field("Xét cả thứ Bảy, Chủ nhật", False, group=MORE)


def _peak(spans: list[tuple[datetime, datetime]], s: datetime, e: datetime) -> int:
    inside = [(a, b) for a, b in spans if a < e and b > s]
    return max((sum(1 for a, b in inside if a <= t < b) for t in [s, *(a for a, _ in inside if a > s)]), default=0)


@m.query("find_time", resource="schedule", title="Tìm giờ cho nhiều người")
async def find_time(ctx: Ctx, inp: FindTime) -> dict[str, Any]:
    """Khung giờ ``minutes`` phút trong khoảng ngày, trong giờ làm việc (khung sớm nhất / muộn nhất, giờ làm của chấm công nếu có, giờ rảnh đã khai của
    người / phòng): phòng / thiết bị phải rảnh (hoặc còn lượt); người tham dự bận thì khung đó xếp sau và nêu TÊN người vướng — không nêu lịch kia là
    gì. Đặt giờ ⇒ ``schedule.create`` (thẻ đề xuất nếu là trợ lý; người bấm mới đặt)."""
    people = await _members_by_email(ctx, inp.attendee_emails)
    cols = [ui.col("day", "Ngày", "date"), ui.col("when", "Giờ", width=120), ui.col("who", "Ai vướng", width=320)]
    if not people and not inp.resource_ids:   # màn mở lần đầu: chưa chọn ai — nói việc cần làm, không báo lỗi
        return ui.table([], cols, title="Tìm giờ cho nhiều người", empty="Chọn những người cần có mặt (và phòng / thiết bị nếu cần) để tìm giờ trống.",
                        note="Người tham dự bận thì khung đó xếp sau và chỉ nêu tên; phòng / thiết bị bận thì không gợi ý. " + f"{zone_label(head=True)}.")
    d0 = inp.from_day or today()
    d1 = inp.to_day or d0 + timedelta(days=6)
    if d1 < d0 or (d1 - d0).days > 13:
        raise Invalid("Khoảng ngày tối đa 14 ngày")
    lo_min, hi_min = minutes(inp.day_start), minutes(inp.day_end, end=True)
    dur = timedelta(minutes=inp.minutes)
    if hi_min - lo_min < inp.minutes:
        raise Invalid("Khung giờ trong ngày ngắn hơn độ dài cuộc hẹn")
    res = {r["id"]: r for r in await ctx.all("SELECT id, name, slots, active FROM schedule.resources WHERE id = ANY(%s)", (inp.resource_ids,))}
    if len(res) < len(set(inp.resource_ids)):
        raise NotFound("Không thấy phòng / thiết bị trong phạm vi của bạn")
    if off := [r["name"] for r in res.values() if not r["active"]]:
        raise Invalid(f"Đã ngừng dùng: {', '.join(off)}")
    uids = list(people)
    lo, hi = datetime.combine(d0, time(0), zone()), datetime.combine(d1 + timedelta(days=1), time(0), zone())
    blocks = await ctx.all("SELECT * FROM schedule.blocks(%s::uuid[], %s::uuid[], tstzrange(%s, %s), NULL)", (list(res), uids, lo, hi))
    hard: dict[UUID, list[tuple[datetime, datetime]]] = {}
    soft: dict[UUID, list[tuple[datetime, datetime]]] = {}    # bận chắc: giữ cứng hoặc đã nhận lời
    maybe: dict[UUID, list[tuple[datetime, datetime]]] = {}   # chưa chắc: được mời chưa nhận / "có thể"
    for b in blocks:
        if b["resource_id"] in res:
            hard.setdefault(b["resource_id"], []).append((b["starts_at"], b["ends_at"]))
        if b["user_id"] in people:
            (soft if b["hard"] or b["rsvp"] == "accepted" else maybe).setdefault(b["user_id"], []).append((b["starts_at"], b["ends_at"]))
    room_win = await _windows(ctx, list(res))
    person_win = {r["user_id"]: r["id"] for r in await ctx.all("SELECT id, user_id FROM schedule.resources WHERE user_id = ANY(%s) AND active", (uids,))}
    pwin = await _windows(ctx, list(person_win.values()))
    # giờ làm theo chấm công (gói tắt ⇒ None): giờ làm là RẢNH để hẹn, không phải lịch (ca làm không thành sự kiện — 0158)
    hours = (await ctx.call_system_optional("timekeeping.work_hours", user_ids=uids, on=d0)) or {} if uids else {}
    now = datetime.now(zone())
    slots: list[dict[str, Any]] = []
    d = d0
    while d <= d1:
        if inp.weekend or d.isoweekday() <= 5:
            t = datetime.combine(d, time(0), zone()) + timedelta(minutes=lo_min)
            end_of_day = datetime.combine(d, time(0), zone()) + timedelta(minutes=hi_min)
            while t + dur <= end_of_day:
                e = t + dur
                if t >= now and all(fits(room_win.get(r, {}), t, e) and _peak(hard.get(r, []), t, e) < res[r]["slots"] for r in res):
                    stuck, unsure = [], []
                    for u, name in people.items():
                        h = hours.get(str(u))
                        outside = (h and (d.isoweekday() not in h["weekdays"] or t < datetime.combine(d, time(0), zone()) + timedelta(minutes=minutes(h["starts"]))
                                          or e > datetime.combine(d, time(0), zone()) + timedelta(minutes=minutes(h["ends"], end=True))))
                        own = pwin.get(person_win[u], {}) if u in person_win else {}
                        if outside or not fits(own, t, e) or _peak(soft.get(u, []), t, e) > 0:
                            stuck.append(name)
                        elif _peak(maybe.get(u, []), t, e) > 0:
                            unsure.append(name)
                    slots.append({"t": t, "e": e, "stuck": sorted(stuck), "unsure": sorted(unsure)})
                t += STEP
        d += timedelta(days=1)
    best = sorted(slots, key=lambda x: (len(x["stuck"]), len(x["unsure"]), x["t"]))[:12]
    rows = [{"id": f"{x['t']:%Y%m%d%H%M}", "day": x["t"].date().isoformat(), "start": f"{x['t']:%H:%M}", "end": f"{x['e']:%H:%M}", "when": when(x["t"], x["e"]),
             "who": " · ".join(y for y in (f"{BUSY}: {', '.join(x['stuck'])}" if x["stuck"] else "",
                                           f"Chưa chắc: {', '.join(x['unsure'])}" if x["unsure"] else "") if y) or "Mọi người rảnh",
             "free": not x["stuck"] and not x["unsure"],
             "attendee_emails": inp.attendee_emails, "resource_ids": [str(r) for r in inp.resource_ids]} for x in best]
    everyone = sum(1 for x in slots if not x["stuck"] and not x["unsure"])
    return ui.table(rows, cols,
                    title=f"Giờ trống {d0:%d/%m} – {d1:%d/%m}", limit=12,
                    empty="Không còn khung nào phòng / thiết bị rảnh trong khoảng này — đổi ngày, giờ hoặc phòng.",
                    note=(f"{everyone} khung mọi người cùng rảnh; hiện tối đa 12 khung tốt nhất. " if slots else "")
                    + "Người vướng chỉ nêu tên (không nêu lịch kia là gì); “chưa chắc” = được mời nhưng chưa nhận lời. " + f"{zone_label(head=True)}.")


# ── Mốc hạn: hạn gọi lại, hạn việc, hạn đóng tiền… KHÔNG là lịch — đọc gộp qua điểm cắm của module sở hữu, bằng quyền người xem ──

class MarkersIn(Input):
    from_day: Day | None = field("Từ ngày", None, placeholder="Hôm nay")
    to_day: Day | None = field("Đến ngày", None, placeholder="14 ngày tới")
    mine: bool = field("Chỉ của tôi", True)


@m.query("markers", resource="schedule", title="Mốc hạn trong khoảng ngày")
async def markers(ctx: Ctx, inp: MarkersIn) -> dict[str, Any]:
    """Mốc hạn để màn lịch VẼ CHỒNG lên (hạn gọi lại khách, hạn việc, đợt đóng tiền…): module sở hữu cắm vào điểm ``schedule.markers`` và trả mốc
    bằng quyền người xem (thiếu quyền phần nào ⇒ bỏ phần đó). Không lưu thành lịch, không chiếm giờ, không làm ai bận."""
    d0 = inp.from_day or today()
    d1 = inp.to_day or d0 + timedelta(days=13)
    if d1 < d0 or (d1 - d0).days > 92:
        raise Invalid("Khoảng ngày tối đa 3 tháng")
    rows: list[dict[str, Any]] = []
    for part in await ctx.plugs("schedule.markers", since=d0, until=d1 + timedelta(days=1), mine=inp.mine):
        for x in part:
            on: date = x["on"] if isinstance(x["on"], date) else date.fromisoformat(str(x["on"]))
            rows.append({"id": f"{x['kind']}:{x.get('ref', '')}:{on.isoformat()}", "day": on.isoformat(), "title": x["title"], "kind": x["kind"],
                         "label": x["label"], "detail": x.get("detail", ""), "open": x.get("open")})
    rows.sort(key=lambda r: (r["day"], r["label"], r["title"]))
    return ui.table(rows, [ui.col("day", "Ngày", "date"), ui.col("label", "Loại mốc"), ui.col("title", "Việc", width=280), ui.col("detail", "Chi tiết", width=220)],
                    title=f"Mốc hạn {d0:%d/%m} – {d1:%d/%m}", limit=2000, empty="Không có mốc hạn nào trong khoảng này.",
                    note="Mốc hạn không chiếm giờ của ai — chỉ để nhìn cùng lịch.", views=ui.views(calendar=ui.calendar_view("day", "title", "label", overdue=True)))


# ── Vòng quét: kéo dài các chuỗi lặp còn chạy để luôn có lần lặp trong 90 ngày tới ──

@m.command("extend_series", resource="schedule", title="Kéo dài lịch lặp", internal=True, agent="deny",
           sweep="SELECT DISTINCT tenant_id FROM schedule.series WHERE status = 'active' AND horizon_until < now() + interval '83 days'")
async def extend_series(ctx: Ctx, inp: NoInput) -> dict[str, int]:
    """Quyền hệ thống: mỗi chuỗi đang chạy sinh thêm lần lặp tới 90 ngày tới. Lần trùng người / phòng thành ngoại lệ đã huỷ có lý do (không thử lại),
    người tạo chuỗi được báo."""
    made = 0
    upto = datetime.now(zone()) + HORIZON
    for sr in await ctx.all("""SELECT * FROM schedule.series WHERE status = 'active' AND horizon_until < now() + interval '83 days'
                               ORDER BY created_at LIMIT 200 FOR UPDATE SKIP LOCKED"""):
        made += await materialise(ctx, sr, upto, strict=False)
    return {"made": made}

