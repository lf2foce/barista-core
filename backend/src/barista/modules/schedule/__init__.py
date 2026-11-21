"""Lịch & nguồn lực (0113) — năng lực dùng chung mọi ngành: người, phòng, thiết bị; giờ rảnh theo tuần; sự kiện giữ một hay nhiều nguồn lực cùng lúc
(vd một người + một phòng); KHÔNG TRÙNG LỊCH do ràng buộc loại trừ của Postgres chặn (``schedule.bookings.no_double_booking``), kể cả hai người đặt
cùng giây. Ví dụ ngành chỉ ở chú thích này: giáo viên + phòng học, ca làm của nhân viên, bàn ở quán — module không biết ngành nào.

Module khác đặt / nhả lịch cho ĐỐI TƯỢNG CỦA CHÍNH NÓ qua hợp đồng nội bộ ``schedule.book`` / ``schedule.release`` (``source = "<module>.<loại>"``,
``source_ref`` = mã đối tượng), nghe MỘT sự kiện ``schedule.booking_changed`` (``change`` = moved / cancelled / resource_replaced) khi người dời /
huỷ / đổi người trên lịch đó. Giờ lưu ``timestamptz``; nhập và hiện theo múi giờ công ty (0169).

MỘT ĐỐI TƯỢNG LỊCH (0158) cho mọi thứ chiếm thời gian của người / phòng — họp, hẹn khách, buổi của gói ngành, nghỉ phép: ``schedule.events`` có LOẠI
lịch (danh mục của công ty, gói góp bộ mặc định), chế độ riêng tư, nguồn (``source`` / ``source_ref``). Ba kiểu người tham gia: NGUỒN LỰC (giữ cứng,
DB chặn trùng / đủ lượt), NGƯỜI THAM DỰ trong công ty (mời / trả lời, hiện bận, không chặn cứng — trùng giờ chỉ CẢNH BÁO), BÊN NGOÀI (mã định danh ở
``parties``). Lặp lại theo tập con RFC 5545 (``rrule.py``): lần lặp sinh thành dòng lịch trong tầm 90 ngày (vòng quét kéo dài) nên DB vẫn chặn từng lần;
sửa / huỷ "chỉ lần này" / "lần này và các lần sau" / "cả chuỗi". Ai đọc chi tiết: người tạo, người tham dự, người được xếp, người có phạm vi đội của đội
lịch / đội của nguồn lực — người khác có quyền xem Lịch chỉ thấy khối "Bận". Mốc hạn (hạn gọi lại, hạn việc, hạn đóng tiền) KHÔNG là lịch: đọc gộp qua
``schedule.markers`` (điểm cắm của module sở hữu). Mã thêm: ``calendar.py`` (loại lịch, trả lời mời, người tham dự, tìm giờ, mốc hạn, kéo dài chuỗi).
"""

import re
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from typing import Any, Literal, LiteralString
from uuid import UUID

from psycopg import errors as pg

from barista.api import Action as A
from barista.api import (
    Conflict,
    Ctx,
    Day,
    Denied,
    Desk,
    Input,
    Invalid,
    Line,
    Module,
    NeedsConfirmation,
    NoInput,
    NotFound,
    Open,
    Pack,
    Screen,
    Workspace,
    can_run,
    field,
    kpi,
    line,
    today,
    ui,
    zone,
    zone_label,
)
from barista.modules.schedule import rrule

Pack(
    "schedule",
    "Lịch & nguồn lực",
    "Một lịch cho mọi thứ chiếm thời gian: người, phòng, thiết bị; người tham dự và bên ngoài; lặp lại; giờ rảnh theo tuần; đặt lịch không trùng; "
    "tìm ai / phòng nào rảnh, tìm giờ cho nhiều người",
    kind="capability",
    always_on=True,   # 0157: mọi công ty đều có người / phòng / lịch — gói khác cần nó khỏi "cần bật trước"
    grants=(
        ("admin", "schedule", "all", "read write manage"),
        ("office", "schedule", "all", "read write manage"),
        ("director", "schedule", "all", "read"),
        ("staff", "schedule", "own", "read"),
        # 0158, kiểu Teams: quyền Lịch ở phạm vi nào cũng cho xem BẬN / RẢNH của mọi người trong công ty (không tên việc). Vai bán hàng có sẵn quyền đó.
        ("sale", "schedule", "own", "read"), ("sales_admin", "schedule", "own", "read"), ("team_lead", "schedule", "team", "read"),
        # 0240: kế toán / marketing cũng có lịch của mình (chốt bảng kê, mở chi, họp) — trước đó hai vai này không mở được màn Lịch
        ("accountant", "schedule", "own", "read"), ("marketer", "schedule", "own", "read"),
        # CHI TIẾT lịch người khác (tên việc, người tham dự, bên ngoài, ghi chú) là quyền riêng — chính sách công ty, quản trị sửa ở Phân quyền:
        # người ĐIỀU PHỐI lịch (quản trị, hành chính — có quyền dời / huỷ / đổi người cả công ty) cả công ty, trưởng nhóm lịch của đội mình. Người tạo /
        # người tham dự / người được xếp luôn thấy. Giám đốc, nhân viên, bán hàng: chỉ bận / rảnh.
        ("admin", "schedule.details", "all", "read"), ("office", "schedule.details", "all", "read"), ("team_lead", "schedule.details", "team", "read"),
    ),
    # loại lịch mặc định của lõi Lịch — gói khác góp loại của mình (vd hẹn khách, buổi của gói ngành, nghỉ) qua đúng lệnh này
    records=(("schedule.save_kind", "meeting", {"key": "meeting", "label": "Họp nội bộ", "sort": 10, "tone": "purple", "only_new": True}),),
    rules="""- LỊCH & NGUỒN LỰC: "ai / phòng nào rảnh", "ai thay được lúc … " ⇒ schedule__find_free (ngày, giờ, loại, thẻ, đội); chỉ nêu người / phòng có
  trong kết quả. "Tìm giờ họp cho A, B (và phòng X)" ⇒ schedule__find_time; đặt ⇒ schedule__create (người tham dự, phòng) — chỉ là đề xuất, người bấm.
  Thay người trên một lịch ⇒ schedule__replace_resource; dời ⇒ schedule__move; huỷ ⇒ schedule__cancel (lịch lặp: nói rõ chỉ lần này / các lần sau /
  cả chuỗi) — đều chỉ là đề xuất. Trợ lý KHÔNG tự đặt lịch lên lịch của người khác. Khối "Bận" là lịch người hỏi không được xem chi tiết: không đoán
  nội dung. Lịch do module khác đặt (có nguồn) thì nói rõ nguồn khi đề xuất dời / huỷ. Giờ luôn là giờ của công ty.
""",
)

m = Module("schedule", "Lịch & nguồn lực", pack="schedule",
           resources={"schedule": "Lịch, người / phòng / thiết bị và giờ rảnh (xem bận / rảnh của mọi người)",
                      "schedule.details": "Chi tiết lịch của người khác (tên việc, người tham dự, bên ngoài, ghi chú)"})
m.consumes("plug")   # điểm cắm của chính module: schedule.markers (mốc hạn của module sở hữu)

# NGHIỆP VỤ CÔNG TY CÓ DÙNG (api 1.63): đặt lịch theo NGƯỜI / PHÒNG / THIẾT BỊ (spa, phòng học, xưởng) chỉ có nghĩa khi công ty có nhiều nguồn lực để xếp.
# Tắt ⇒ ẩn Tìm người / phòng rảnh, Tìm giờ cho nhiều người, Người & phòng, Loại lịch (Lịch và Lịch của tôi vẫn còn). Mặc định BẬT — công ty đang chạy không đổi.
USE_RESOURCES = m.setting("use_resources", "Đặt lịch theo người / phòng / thiết bị (spa, phòng học, xưởng)", default=True,
                          help="Tắt nếu công ty chỉ cần lịch cá nhân — các màn tìm giờ trống, người & phòng, loại lịch sẽ ẩn")

NATURE = {"person": "Người", "thing": "Phòng / thiết bị"}
STATUS = {"booked": "Đã đặt", "cancelled": "Đã huỷ", "done": "Đã xong", "busy": "Bận", "tentative": "Chờ xác nhận",
          "maybe": "Bận (chưa chắc)"}
WEEKDAY = {1: "Thứ Hai", 2: "Thứ Ba", 3: "Thứ Tư", 4: "Thứ Năm", 5: "Thứ Sáu", 6: "Thứ Bảy", 7: "Chủ nhật"}
SOURCE = re.compile(r"^[a-z_]+\.[a-z_]+$")
VISIBILITY = {"normal": "Bình thường", "private": "Riêng tư — người khác chỉ thấy “Bận”"}
RSVP = {"invited": "Chờ xác nhận", "accepted": "Tham dự", "declined": "Không tham dự", "tentative": "Có thể"}
SCOPE = {"this": "Chỉ lần này", "following": "Lần này và các lần sau", "all": "Cả chuỗi"}
REPEAT = {"": "Không lặp", "daily": "Hằng ngày", "weekly": "Hằng tuần"}
BUSY = "Bận"
# được mời mà chưa nhận (hoặc trả lời "có thể"): trên lịch CỦA NGƯỜI ĐÓ là "Chờ xác nhận" (nút trả lời); trong bận / rảnh người khác thấy "chưa chắc",
# không phải bận — tìm giờ xếp sau giờ rảnh hẳn nhưng trước giờ bận, tìm người rảnh không loại họ
MAYBE = "Bận (chưa chắc)"
HORIZON = timedelta(days=90)   # lần lặp sinh trước tối đa chừng này; vòng quét schedule.extend_series kéo dài
KIND_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


# ── Giờ: nhập "HH:MM" theo múi giờ công ty (0169), lưu timestamptz ──

def minutes(hhmm: str, *, end: bool = False) -> int:
    """'08:30' ⇒ 510. ``end``: cho phép '24:00' (hết ngày)."""
    m_ = re.fullmatch(r"\s*(\d{1,2})[:h.](\d{2})\s*", hhmm or "")
    if not m_:
        raise Invalid(f"Giờ dạng 08:30, không đọc được “{hhmm}”")
    h, mi = int(m_.group(1)), int(m_.group(2))
    total = h * 60 + mi
    if mi >= 60 or total > (1440 if end else 1439):
        raise Invalid(f"Giờ không hợp lệ: {hhmm}")
    return total


def at(day: date, mins: int) -> datetime:
    return datetime.combine(day, time(0), zone()) + timedelta(minutes=mins)


def span(day: date, start: str, end: str, end_day: date | None) -> tuple[datetime, datetime]:
    s, e = at(day, minutes(start)), at(end_day or day, minutes(end, end=True))
    if e <= s:
        raise Invalid("Giờ kết thúc phải sau giờ bắt đầu (qua đêm thì chọn ngày kết thúc)")
    return s, e


def shown(t: datetime) -> str:
    return f"{t.astimezone(zone()):%H:%M}"


def when(s: datetime, e: datetime) -> str:
    s, e = s.astimezone(zone()), e.astimezone(zone())
    return f"{s:%H:%M}–{e:%H:%M}" + ("" if e.date() == s.date() or (e - s <= timedelta(days=1) and e.time() == time(0)) else f" (đến {e:%d/%m})")


def merged(windows: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for s, e in sorted(windows):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def fits(windows: dict[int, list[tuple[int, int]]], start: datetime, end: datetime) -> bool:
    """Khoảng [start, end) nằm trọn trong giờ rảnh theo tuần (giờ công ty). Không khai giờ rảnh = không giới hạn. Qua nửa đêm: mỗi ngày xét phần của
    ngày đó; khung 22:00–24:00 hôm trước nối liền 00:00–02:00 hôm sau."""
    if not windows:
        return True
    s, e = start.astimezone(zone()), end.astimezone(zone())
    d = s.date()
    while (day0 := datetime.combine(d, time(0), zone())) < e:
        a, b = max(s, day0), min(e, day0 + timedelta(days=1))
        need_from = int((a - day0).total_seconds() // 60)
        need_to = -int(-(b - day0).total_seconds() // 60)   # làm tròn lên: 10:00:30 cần tới phút 601
        if not any(ws <= need_from and need_to <= we for ws, we in merged(windows.get(d.isoweekday(), []))):
            return False
        d += timedelta(days=1)
    return True


def tags_of(text: str) -> list[str]:
    return sorted({t.strip().lower() for t in re.split(r"[,;\n]", text or "") if t.strip()})


# ── Đọc / kiểm chung ──

async def _team(ctx: Ctx, code: str) -> UUID | None:
    if not code:
        return None
    tid = await ctx.val("SELECT id FROM core.teams WHERE code = %s", (code,))
    if tid is None:
        raise NotFound(f"Không có đội {code}")
    return tid


def _may_write_at(ctx: Ctx, team: UUID | None) -> None:
    """Ghi lịch cần phạm vi cả công ty, hoặc phạm vi đội mà sự kiện ở đội mình quản lý (RLS chặn lần nữa ở DB). Phạm vi "của mình" chỉ để xem."""
    if ctx.p.has_scope("schedule", "all"):
        return
    if ctx.p.scope("schedule") == "team" and team is not None and team in ctx.p.managed_teams:
        return
    raise Denied("Chỉ đặt / sửa lịch ở đội mình quản lý")


def _owns(ctx: Ctx, ev: dict[str, Any], held: int) -> bool:
    """Lịch CỦA CHÍNH người xem (0240, kiểu Teams): người tạo, không do module khác đặt, không giữ người / phòng nào (``held`` = số dòng giữ chỗ đang có).
    Người tổ chức luôn dời / huỷ / đổi người mời được lịch của mình — giữ cứng người / phòng vẫn cần quyền đặt lịch ở đội."""
    return ev["created_by"] is not None and ev["created_by"] == ctx.p.user_id and not ev["source"] and held == 0


async def _may_change(ctx: Ctx, ev: dict[str, Any]) -> None:
    """Sửa một lịch: quyền đặt lịch ở đội của lịch (``_may_write_at``), HOẶC đó là lịch của chính mình (``_owns``). RLS chặn lần nữa ở DB."""
    if ctx.p.user_id is not None and ev["created_by"] == ctx.p.user_id and not ev["source"]:
        held = await ctx.val("SELECT count(*) FROM schedule.bookings WHERE event_id = %s AND released_at IS NULL", (ev["id"],)) or 0
        if _owns(ctx, ev, held):
            return
    if not can_run(ctx.p, "schedule.create"):
        raise Denied("Chỉ sửa được lịch do chính bạn tạo (không giữ người / phòng)")
    _may_write_at(ctx, ev["team_id"])


async def _windows(ctx: Ctx, ids: list[UUID]) -> dict[UUID, dict[int, list[tuple[int, int]]]]:
    out: dict[UUID, dict[int, list[tuple[int, int]]]] = {}
    for r in await ctx.all("SELECT resource_id, weekday, start_min, end_min FROM schedule.availability WHERE resource_id = ANY(%s)", (ids,)):
        out.setdefault(r["resource_id"], {}).setdefault(r["weekday"], []).append((r["start_min"], r["end_min"]))
    return out


async def _busy(ctx: Ctx, ids: list[UUID], s: datetime, e: datetime, except_event: UUID | None = None) -> set[UUID]:
    rows = await ctx.all("SELECT b AS id FROM schedule.busy(%s, tstzrange(%s, %s), %s) b", (ids, s, e, except_event))
    return {r["id"] for r in rows}


async def _lock(ctx: Ctx, ids: list[UUID] | set[UUID]) -> None:
    """Giữ lượt theo từng nguồn lực, theo THỨ TỰ mã cố định, tới hết lần ghi: hai lần đặt / dời cùng người / phòng chạy lần lượt — lần sau kiểm thấy
    lịch lần trước đã ghi, không khoá chéo nhau ở ràng buộc loại trừ (ràng buộc vẫn là lưới cuối)."""
    for rid in sorted(set(ids)):
        await ctx.exec("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"schedule.resource:{rid}",))


async def _check(ctx: Ctx, ids: list[UUID], s: datetime, e: datetime, *, allow_outside: bool, except_event: UUID | None = None) -> dict[UUID, str]:
    """Nguồn lực thấy được (RLS), đang dùng, trong giờ rảnh (trừ khi ``allow_outside``), chưa bận. Trả {id: tên}. DB vẫn chặn trùng lần cuối."""
    if not ids:
        raise Invalid("Cần ít nhất một người / phòng")
    await _lock(ctx, ids)
    rows = {r["id"]: r for r in await ctx.all("SELECT id, name, active FROM schedule.resources WHERE id = ANY(%s)", (ids,))}
    if missing := [i for i in ids if i not in rows]:
        raise NotFound(f"Không thấy {len(missing)} người / phòng trong phạm vi của bạn")
    if off := [rows[i]["name"] for i in ids if not rows[i]["active"]]:
        raise Invalid(f"Đã ngừng dùng: {', '.join(off)}")
    if not allow_outside:
        win = await _windows(ctx, ids)
        if outside := [rows[i]["name"] for i in ids if not fits(win.get(i, {}), s, e)]:
            raise Invalid(f"Ngoài giờ rảnh: {', '.join(outside)} — chọn giờ khác, hoặc đánh dấu “vẫn đặt ngoài giờ rảnh”")
    if taken := await _busy(ctx, ids, s, e, except_event):
        raise Conflict(f"Trùng lịch: {', '.join(rows[i]['name'] for i in ids if i in taken)} đã có lịch (hoặc đã đủ lượt) trong khoảng {when(s, e)} "
                       f"{s.astimezone(zone()):%d/%m}")
    return {i: rows[i]["name"] for i in ids}


async def _guarded(ctx: Ctx, sql: Any, params: Any) -> Any:
    """Câu ghi có thể chạm ràng buộc không trùng lịch: lỗi DB ⇒ Conflict dễ hiểu, trong điểm lưu để bên gọi (module khác) xử lý tiếp được."""
    try:
        async with ctx.conn.transaction():
            cur = await ctx.conn.execute(sql, params)
            return await cur.fetchone() if cur.description else None
    except (pg.ExclusionViolation, pg.DeadlockDetected) as err:   # hai lần chèn chồng giờ cùng lúc: Postgres báo một trong hai, cùng một nghĩa
        raise Conflict("Vừa có người đặt trùng giờ cho người / phòng này — chọn giờ hoặc người khác") from err


async def _event(ctx: Ctx, event_id: UUID, *, lock: bool = True) -> dict[str, Any]:
    row = await ctx.one("SELECT * FROM schedule.events WHERE id = %s" + (" FOR UPDATE" if lock else ""), (event_id,))
    if row is None:
        raise NotFound("Không thấy lịch (hoặc ngoài phạm vi sửa của bạn)")
    return row


async def _booked_users(ctx: Ctx, event_id: UUID) -> list[UUID]:
    """Người cần biết lịch đổi: người được xếp (nguồn lực gắn tài khoản) + người tham dự chưa từ chối."""
    return [r["user_id"] for r in await ctx.all(
        """SELECT user_id FROM schedule.bookings WHERE event_id = %(e)s AND released_at IS NULL AND user_id IS NOT NULL
           UNION SELECT user_id FROM schedule.attendees WHERE event_id = %(e)s AND rsvp <> 'declined'""", {"e": event_id})]


async def _tell(ctx: Ctx, users: list[UUID], title: str, s: datetime) -> None:
    await ctx.notify(users, kind="schedule", title=title, push=f"Lịch của anh/chị thay đổi — {s.astimezone(zone()):%H:%M %d/%m}",
                     open={"query": "schedule.my_agenda", "input": {}})


# ── Người tham dự, bên ngoài, loại lịch ──

async def _members_by_email(ctx: Ctx, emails: list[str]) -> dict[UUID, str]:
    """Email thành viên đang hoạt động ⇒ {mã tài khoản: tên}. Danh mục thành viên là thứ mọi người trong công ty thấy (ô chọn người)."""
    want = sorted({e.strip().lower() for e in emails if e.strip()})
    if not want:
        return {}
    rows = await ctx.all("""SELECT u.id, u.name, lower(u.email) AS email FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.active
                            WHERE lower(u.email) = ANY(%s)""", (want,))
    if missing := sorted(set(want) - {r["email"] for r in rows}):
        raise NotFound(f"Không có thành viên {', '.join(missing)}")
    return {r["id"]: r["name"] for r in rows}


async def _members_by_id(ctx: Ctx, ids: list[UUID]) -> dict[UUID, str]:
    if not ids:
        return {}
    rows = await ctx.all("""SELECT u.id, u.name FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.active WHERE u.id = ANY(%s)""",
                         (list(set(ids)),))
    if len(rows) < len(set(ids)):
        raise NotFound("Có người tham dự không còn là thành viên đang hoạt động")
    return {r["id"]: r["name"] for r in rows}


async def _parties_seen(ctx: Ctx, party_ids: list[UUID]) -> None:
    """Bên ngoài do NGƯỜI GỌI nêu ⇒ kiểm bằng quyền của họ (0125 §B): thấy được trong danh mục mới gắn được vào lịch."""
    if not party_ids:
        return
    got = await ctx.call_as_optional("parties.names", ctx.p, party_ids=list(set(party_ids)))
    if got is None:
        raise Invalid("Công ty chưa dùng danh mục cá nhân & tổ chức, hoặc bạn không có quyền xem danh mục: bỏ trống ô bên ngoài")
    if missing := [p for p in set(party_ids) if str(p) not in got]:
        raise NotFound(f"Không thấy {len(missing)} bên ngoài trong danh mục")


async def _put_attendees(ctx: Ctx, event_id: UUID, want: list[UUID], accepted: set[UUID] | frozenset[UUID] = frozenset()) -> list[UUID]:
    """Khai báo: người tham dự của lịch đúng bằng ``want`` (bỏ người không còn, thêm người mới — người cũ giữ câu trả lời). ``accepted``: nhận sẵn
    (người tự đặt cho mình, nghỉ của chính người đó). Trả người MỚI được mời (để báo)."""
    want = list(dict.fromkeys(want))
    have = {r["user_id"]: r["rsvp"] for r in await ctx.all("SELECT user_id, rsvp FROM schedule.attendees WHERE event_id = %s", (event_id,))}
    if gone := [u for u in have if u not in want]:
        await ctx.exec("DELETE FROM schedule.attendees WHERE event_id = %s AND user_id = ANY(%s)", (event_id, gone))
    for u in want:
        if u not in have:
            yes = u in accepted
            await ctx.exec("INSERT INTO schedule.attendees (event_id, user_id, rsvp, responded_at) VALUES (%s, %s, %s, %s)",
                           (event_id, u, "accepted" if yes else "invited", datetime.now(zone()) if yes else None))
        elif u in accepted and have[u] == "invited":
            await ctx.exec("UPDATE schedule.attendees SET rsvp = 'accepted', responded_at = now() WHERE event_id = %s AND user_id = %s", (event_id, u))
    return [u for u in want if u not in have and u not in accepted]


async def _put_parties(ctx: Ctx, event_id: UUID, want: list[UUID]) -> None:
    want = list(dict.fromkeys(want))
    await ctx.exec("DELETE FROM schedule.event_parties WHERE event_id = %s AND NOT (party_id = ANY(%s))", (event_id, want))
    for pid in want:
        await ctx.exec("INSERT INTO schedule.event_parties (event_id, party_id) VALUES (%s, %s) ON CONFLICT (tenant_id, event_id, party_id) DO NOTHING",
                       (event_id, pid))


async def _ensure_kind(ctx: Ctx, key: str, label: str) -> None:
    """Loại lịch module nguồn dùng: công ty chưa có (gói bật trước khi có Lịch, công ty có từ trước) ⇒ thêm với nhãn mặc định của module đó."""
    if not key:
        return
    if not KIND_KEY.match(key):
        raise Invalid(f"Mã loại lịch không hợp lệ: {key}")
    await ctx.exec("INSERT INTO schedule.kinds (key, label) VALUES (%s, %s) ON CONFLICT (tenant_id, key) DO NOTHING", (key, (label or key)[:80]))


async def _kind_ok(ctx: Ctx, key: str) -> str:
    key = key.strip().lower()
    if key and not await ctx.val("SELECT 1 FROM schedule.kinds WHERE key = %s AND active", (key,)):
        raise NotFound(f"Không có loại lịch {key} (hoặc đã tắt) — xem Lịch › Loại lịch")
    return key


async def _soft_clash(ctx: Ctx, users: list[UUID], s: datetime, e: datetime, except_event: UUID | None = None) -> list[str]:
    """Người tham dự đã có lịch khác chồng giờ ⇒ CẢNH BÁO (không chặn). Chỉ nêu tên người + giờ, không nêu lịch kia là gì (có thể là lịch riêng tư)."""
    if not users:
        return []
    rows = await ctx.all("""SELECT user_id, min(starts_at) AS s, max(ends_at) AS e FROM schedule.blocks(%s::uuid[], %s::uuid[], tstzrange(%s, %s), %s)
                            WHERE user_id IS NOT NULL GROUP BY user_id""", ([], users, s, e, except_event))
    if not rows:
        return []
    names = await _members_by_id(ctx, [r["user_id"] for r in rows])
    return [f"{names.get(r['user_id'], 'Một người')} đã bận {when(r['s'], r['e'])} {r['s'].astimezone(zone()):%d/%m}" for r in rows]


async def _people_of(ctx: Ctx, resource_ids: list[UUID]) -> set[UUID]:
    return {r["user_id"] for r in await ctx.all("SELECT user_id FROM schedule.resources WHERE id = ANY(%s) AND user_id IS NOT NULL", (resource_ids,))}


def _agent_guard(ctx: Ctx, people: set[UUID]) -> None:
    """Bất biến 8 (0158): trợ lý KHÔNG tự đặt lịch lên lịch của người khác — kể cả khi công ty nới (cho làm thẳng) lệnh của module khác gọi tới đây. Trợ lý
    đang làm thật (không phải chạy thử để soạn thẻ) và lịch có người khác ⇒ cả lệnh thành thẻ đề xuất, người bấm mới chạy."""
    if ctx.p.via == "agent" and not ctx.preview and any(u != ctx.p.user_id for u in people):
        raise NeedsConfirmation("Trợ lý không tự đặt lịch lên lịch của người khác — cần người xác nhận")


async def _create(ctx: Ctx, *, title: str, s: datetime, e: datetime, team: UUID | None, ids: list[UUID], allow_outside: bool, note: str = "",
                  source: str = "", source_ref: str = "", kind: str = "", visibility: str = "normal", attendees: list[UUID] | tuple[()] = (),
                  accepted: set[UUID] | frozenset[UUID] = frozenset(), parties: list[UUID] | tuple[()] = (), series_id: UUID | None = None,
                  occurrence_at: datetime | None = None, created_by: UUID | None = None) -> UUID:
    ids = sorted(set(ids))   # thứ tự cố định: hai lần đặt chéo nhau không khoá chéo
    if not ids and not attendees:
        raise Invalid("Cần ít nhất một người / phòng, hoặc một người tham dự")
    if ids:
        await _check(ctx, ids, s, e, allow_outside=allow_outside)
    row = await ctx.one("""INSERT INTO schedule.events (title, starts_at, ends_at, team_id, note, source, source_ref, created_by, kind, visibility, series_id,
                                                        occurrence_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                        (title, s, e, team, note, source, source_ref, created_by or ctx.p.user_id, kind, visibility, series_id, occurrence_at))
    assert row
    for rid in ids:
        await _guarded(ctx, "INSERT INTO schedule.bookings (event_id, resource_id, during) VALUES (%s, %s, tstzrange(%s, %s))", (row["id"], rid, s, e))
    await _put_attendees(ctx, row["id"], list(attendees), accepted)
    await _put_parties(ctx, row["id"], list(parties))
    return row["id"]


# ── Nguồn lực ──

class ResourceIn(Input):
    name: str = field("Tên", max_length=120, example="Phòng 201")
    nature: Literal["person", "thing"] = field("Người hay phòng", "thing", options=NATURE)
    kind: str = field("Loại", max_length=60, example="phòng họp", options_query="schedule.kind_options", help="Chữ tự đặt; cùng loại thì tìm rảnh cùng nhau")
    team_code: str = field("Đội / cơ sở chính", "", options_query="admin.team_options")
    user_email: str = field("Tài khoản đăng nhập", "", options_query="admin.member_options", help="Người có đăng nhập: thấy lịch của mình ở bàn việc")
    # rà form nhóm B 18/11/2026: tên, loại, đội, tài khoản là đủ để khai một người / phòng; thẻ, sức chứa, lượt đặt, hồ sơ người ngoài, ghi chú gấp lại
    tags: str = field("Thẻ / kỹ năng", "", max_length=500, example="máy chiếu, 20 chỗ", help="Cách nhau dấu phẩy", group="Thêm chi tiết")
    capacity: int = field("Sức chứa", 1, ge=1, le=10_000, help="Số người phòng / thiết bị chứa được — để lọc khi tìm phòng", group="Thêm chi tiết")
    slots: int = field("Số lượt đặt cùng lúc", 1, ge=1, le=10_000, group="Thêm chi tiết",
                       help="1 = mỗi lúc một lịch (phòng họp, một người). Lớn hơn: nhiều lịch cùng giờ tới khi đủ lượt (chỗ ngồi chung, suất trong lớp)")
    party_id: UUID | None = field("Hồ sơ người / tổ chức ngoài công ty", None, options_query="parties.options", group="Thêm chi tiết",
                                  aliases=("Định danh",), help="Người ngoài công ty (không đăng nhập) — nếu dùng danh mục")
    note: str = field("Ghi chú", "", max_length=500, group="Thêm chi tiết")


class UpdateResource(ResourceIn):
    resource_id: UUID = field("Người / phòng", hidden=True)
    active: bool = field("Đang dùng", True, help="Bỏ chọn: không đặt lịch mới được; lịch đã đặt giữ nguyên")


async def _resource_fields(ctx: Ctx, inp: ResourceIn) -> dict[str, Any]:
    team = await _team(ctx, inp.team_code)
    if not ctx.p.has_scope("schedule", "all") and (team is None or team not in ctx.p.managed_teams):
        raise Denied("Chỉ khai người / phòng thuộc đội mình quản lý")
    user = None
    if inp.user_email:
        user = await ctx.val("SELECT u.id FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id WHERE lower(u.email) = lower(%s) AND mb.active",
                             (inp.user_email,))
        if user is None:
            raise NotFound(f"Không có thành viên {inp.user_email}")
    if inp.nature == "thing" and (user or inp.party_id):
        raise Invalid("Phòng / thiết bị không gắn người hay tài khoản")
    if inp.nature == "person" and inp.slots > 1:
        raise Invalid("Một người chỉ ở một lịch mỗi lúc — số lượt đặt cùng lúc của người là 1")
    if inp.party_id:
        got = await ctx.call_as_optional("parties.names", ctx.p, party_ids=[inp.party_id])   # định danh do người gọi chọn ⇒ quyền của họ (0125 §B)
        if got is None:
            raise Invalid("Công ty chưa dùng danh mục cá nhân & tổ chức, hoặc bạn không có quyền xem danh mục: bỏ trống ô định danh")
        if str(inp.party_id) not in got:
            raise NotFound("Không thấy định danh này")
    kind = inp.kind.strip().lower()
    if not inp.name.strip() or not kind:
        raise Invalid("Cần tên và loại")
    return {"name": inp.name.strip(), "nature": inp.nature, "kind": kind, "tags": tags_of(inp.tags), "capacity": inp.capacity, "slots": inp.slots,
            "team_id": team, "user_id": user, "party_id": inp.party_id, "note": inp.note}


@m.command("add_resource", resource="schedule", action="manage", title="Thêm người / phòng / thiết bị", agent="propose", category="setup")
async def add_resource(ctx: Ctx, inp: ResourceIn) -> dict[str, Any]:
    f = await _resource_fields(ctx, inp)
    try:
        async with ctx.conn.transaction():
            rid = await ctx.val("""INSERT INTO schedule.resources (name, nature, kind, tags, capacity, slots, team_id, user_id, party_id, note)
                                   VALUES (%(name)s, %(nature)s, %(kind)s, %(tags)s, %(capacity)s, %(slots)s, %(team_id)s, %(user_id)s, %(party_id)s,
                                           %(note)s)
                                   RETURNING id""", f)
    except pg.UniqueViolation as err:
        raise Conflict("Tài khoản / định danh này đã là một nguồn lực khác") from err
    return {"resource_id": rid, "summary": f"Thêm {f['name']} ({f['kind']})"}


@m.command("update_resource", resource="schedule", action="manage", title="Sửa người / phòng / thiết bị", agent="propose", category="setup")
async def update_resource(ctx: Ctx, inp: UpdateResource) -> dict[str, Any]:
    f = await _resource_fields(ctx, inp)
    await _lock(ctx, [inp.resource_id])   # cùng lượt với các lần đặt: số lượt mới kiểm trên đúng những gì đang giữ
    over = await ctx.all("""SELECT lower(b.during) AS s, upper(b.during) AS e, schedule.load(b.resource_id, b.during, NULL, NULL) AS n FROM schedule.bookings b
                            WHERE b.resource_id = %s AND b.active AND upper(b.during) > now() ORDER BY lower(b.during) LIMIT 2000""", (inp.resource_id,))
    if busy := [r for r in over if r["n"] > f["slots"]]:   # số lượt mới thấp hơn số lịch đang chồng nhau sắp tới ⇒ từ chối, nêu đúng các giờ đó
        at_ = ", ".join(dict.fromkeys(f"{when(r['s'], r['e'])} {r['s'].astimezone(zone()):%d/%m}" for r in busy[:5]))
        raise Conflict(f"Không giảm số lượt của {f['name']} xuống {f['slots']} được: sắp tới có lúc tới {max(r['n'] for r in busy)} lịch cùng giờ "
                       f"({at_}{' …' if len(busy) > 5 else ''}) — dời / huỷ bớt trước")
    try:
        async with ctx.conn.transaction():
            n = await ctx.exec("""UPDATE schedule.resources SET name = %(name)s, nature = %(nature)s, kind = %(kind)s, tags = %(tags)s, capacity = %(capacity)s,
                                  slots = %(slots)s, team_id = %(team_id)s, user_id = %(user_id)s, party_id = %(party_id)s, note = %(note)s,
                                  active = %(active)s WHERE id = %(id)s""",
                               {**f, "active": inp.active, "id": inp.resource_id})
    except pg.UniqueViolation as err:
        raise Conflict("Tài khoản / định danh này đã là một nguồn lực khác") from err
    except (pg.ExclusionViolation, pg.DeadlockDetected) as err:
        raise Conflict("Sắp tới có lịch chồng giờ trên nguồn lực này — không về một lượt được; dời / huỷ bớt trước") from err
    if not n:
        raise NotFound("Không thấy người / phòng này")
    out: dict[str, Any] = {"resource_id": inp.resource_id}
    if not inp.active and (left := await ctx.val("SELECT count(*) FROM schedule.bookings WHERE resource_id = %s AND active AND upper(during) > now()",
                                                 (inp.resource_id,))):
        out["warning"] = f"Đã ngừng dùng nhưng còn {left} lịch sắp tới giữ nguyên — đổi người / phòng trên từng lịch nếu cần"
    return out


class WindowIn(Input):
    weekday: Literal[1, 2, 3, 4, 5, 6, 7] = field("Thứ", 1, options={str(k): v for k, v in WEEKDAY.items()})
    start: str = field("Từ", "08:00", max_length=5)
    end: str = field("Đến", "17:00", max_length=5, help="24:00 = hết ngày")


class SetAvailability(Input):
    resource_id: UUID = field("Người / phòng", hidden=True)
    windows: list[WindowIn] = field("Giờ rảnh trong tuần", [], help="Để trống = không giới hạn giờ. Qua đêm: tách thành hai dòng 22:00–24:00 và 00:00–02:00")


@m.command("set_availability", resource="schedule", action="manage", title="Đặt giờ rảnh theo tuần", agent="propose", category="setup")
async def set_availability(ctx: Ctx, inp: SetAvailability) -> dict[str, Any]:
    """Thay toàn bộ giờ rảnh của một người / phòng. Lịch đã đặt ngoài giờ mới không tự huỷ."""
    if not await ctx.val("SELECT 1 FROM schedule.resources WHERE id = %s", (inp.resource_id,)):
        raise NotFound("Không thấy người / phòng này")
    rows = [(w.weekday, minutes(w.start), minutes(w.end, end=True)) for w in inp.windows]
    if bad := [w for w in rows if w[2] <= w[1]]:
        raise Invalid(f"{WEEKDAY[bad[0][0]]}: giờ kết thúc phải sau giờ bắt đầu")
    await ctx.exec("DELETE FROM schedule.availability WHERE resource_id = %s", (inp.resource_id,))
    for day in sorted({w[0] for w in rows}):
        for s, e in merged([(a, b) for d, a, b in rows if d == day]):   # khung chồng / nối nhau gộp làm một
            await ctx.exec("INSERT INTO schedule.availability (resource_id, weekday, start_min, end_min) VALUES (%s, %s, %s, %s)",
                           (inp.resource_id, day, s, e))
    return {"resource_id": inp.resource_id, "summary": f"{len(rows)} khung giờ rảnh" if rows else "Không giới hạn giờ"}


# ── Lịch ──

REPEAT_GROUP = "Lặp lại"
WHO_GROUP = "Người tham dự & riêng tư"


class CreateEvent(Input):
    title: str = field("Việc", max_length=200, example="Họp đầu tuần")
    day: Day = field("Ngày", example="10/11/2026")
    start: str = field("Từ", "08:00", max_length=5, kind="time")
    end: str = field("Đến", "09:00", max_length=5, kind="time")
    resource_ids: list[UUID] = field("Người / phòng / thiết bị", [], kind="multiselect", options_query="schedule.resource_options", group=WHO_GROUP,
                                     help="Đã bận giờ này (hoặc hết chỗ) thì không đặt được")   # rà UI 21/11: gấp vào nhóm người tham dự — lịch cá nhân không cần
    kind: str = field("Loại lịch", "", options_query="schedule.event_kind_options", help="Trống = không phân loại")
    # rà form nhóm B 18/11/2026: ô giờ là ô chọn giờ (trước đây gõ chữ "08:00"); ngày kết thúc / đội / ghi chú / ngoài giờ rảnh gấp lại
    end_day: Day | None = field("Ngày kết thúc", None, help="Trống = cùng ngày. Qua đêm thì chọn ngày hôm sau", placeholder="Cùng ngày", group="Thêm chi tiết")
    team_code: str = field("Ở đội / cơ sở", "", options_query="admin.team_options", help="Trống = đội chính của bạn", placeholder="Đội chính của tôi",
                           group="Thêm chi tiết")
    note: str = field("Ghi chú", "", max_length=1000, group="Thêm chi tiết")
    allow_outside: bool = field("Vẫn đặt ngoài giờ rảnh", False, group="Thêm chi tiết")
    attendee_emails: list[str] = field("Người tham dự", [], kind="multiselect", options_query="admin.member_options", group=WHO_GROUP,
                                       help="Được mời và trả lời; lịch hiện bận cho họ nhưng không chặn cứng — trùng giờ chỉ cảnh báo")
    party_ids: list[UUID] = field("Bên ngoài (người / tổ chức ngoài công ty)", [], kind="multiselect", options_query="parties.options", group=WHO_GROUP)
    visibility: Literal["normal", "private"] = field("Chế độ xem", "normal", options=VISIBILITY, group=WHO_GROUP)
    repeat: Literal["", "daily", "weekly"] = field("Lặp lại", "", options=REPEAT, group=REPEAT_GROUP)
    every: int = field("Mỗi … ngày / tuần", 1, ge=1, le=52, group=REPEAT_GROUP)
    weekdays: list[int] = field("Vào các thứ", [], kind="multiselect", options={str(k): v for k, v in WEEKDAY.items()}, group=REPEAT_GROUP,
                                help="Lặp hằng tuần: trống = đúng thứ của ngày bắt đầu")
    repeat_until: Day | None = field("Lặp đến ngày", None, group=REPEAT_GROUP)
    repeat_count: int | None = field("Hoặc số lần", None, ge=1, le=rrule.MAX_COUNT, group=REPEAT_GROUP)


def rule_of(repeat: str, every: int, weekdays: list[int], until: date | None, count: int | None) -> rrule.Rule | None:
    if not repeat:
        return None
    return rrule.Rule("DAILY" if repeat == "daily" else "WEEKLY", every, tuple(sorted(set(weekdays))),
                      datetime.combine(until, time(23, 59, 59), zone()) if until else None, count)


@m.command("create", resource="schedule", title="Đặt lịch", agent="propose")
async def create(ctx: Ctx, inp: CreateEvent) -> dict[str, Any]:
    """Một lịch giữ một hay nhiều người / phòng cùng lúc (giữ cứng — trùng giờ ⇒ từ chối, DB chặn kể cả khi đặt cùng lúc), mời người tham dự (hiện bận,
    trùng giờ chỉ cảnh báo), gắn bên ngoài. Có lặp lại ⇒ một chuỗi: sinh các lần trong 90 ngày tới, lần nào trùng thì không đặt chuỗi nào."""
    team = await _team(ctx, inp.team_code) if inp.team_code else await ctx.home_team(ctx.p.user_id)
    _may_write_at(ctx, team)
    s, e = span(inp.day, inp.start, inp.end, inp.end_day)
    kind = await _kind_ok(ctx, inp.kind)
    people = await _members_by_email(ctx, inp.attendee_emails)
    await _parties_seen(ctx, inp.party_ids)
    _agent_guard(ctx, set(people) | await _people_of(ctx, inp.resource_ids))
    me = {ctx.p.user_id} & set(people) if ctx.p.user_id else set()
    args: dict[str, Any] = {"title": inp.title, "team": team, "ids": inp.resource_ids, "allow_outside": inp.allow_outside, "note": inp.note, "kind": kind,
                            "visibility": inp.visibility, "attendees": list(people), "accepted": me, "parties": inp.party_ids}
    warnings = await _soft_clash(ctx, list(people), s, e)
    rule = rule_of(inp.repeat, inp.every, inp.weekdays, inp.repeat_until, inp.repeat_count)
    if rule is not None:
        sid, made = await _new_series(ctx, s=s, e=e, rule=rule, **args)
        await _tell(ctx, [u for u in people if u not in me], f"Lịch mới (lặp): {inp.title} — {rrule.describe(rule).lower()}", s)
        return {"event_id": await ctx.val("SELECT id FROM schedule.events WHERE series_id = %s ORDER BY starts_at LIMIT 1", (sid,)), "series_id": sid,
                "occurrences": made, "warnings": warnings, "summary": f"{inp.title}: {when(s, e)} — {rrule.describe(rule).lower()} ({made} lần trong 90 ngày tới)"}
    eid = await _create(ctx, s=s, e=e, **args)
    await _tell(ctx, await _booked_users(ctx, eid), f"Lịch mới: {inp.title} {when(s, e)} {s.astimezone(zone()):%d/%m}", s)
    return {"event_id": eid, "warnings": warnings, "summary": f"{inp.title}: {when(s, e)} {s.astimezone(zone()):%d/%m}"}


# ── Chuỗi lặp: sinh lần lặp thành dòng lịch, sửa / huỷ theo phạm vi ──

async def _new_series(ctx: Ctx, *, title: str, s: datetime, e: datetime, team: UUID | None, ids: list[UUID], allow_outside: bool, note: str, kind: str,
                      visibility: str, attendees: list[UUID], accepted: set[UUID], parties: list[UUID], rule: rrule.Rule,
                      created_by: UUID | None = None) -> tuple[UUID, int]:
    if not ids and not attendees:
        raise Invalid("Cần ít nhất một người / phòng, hoặc một người tham dự")
    sid = await ctx.val("""INSERT INTO schedule.series (title, kind, visibility, team_id, note, rrule, dtstart, duration_min, resource_ids, attendee_ids,
                                                       party_ids, allow_outside, horizon_until, created_by)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                        (title, kind, visibility, team, note, rrule.text(rule), s, int((e - s).total_seconds() // 60), sorted(set(ids)), attendees,
                         parties, allow_outside, s - timedelta(seconds=1), created_by or ctx.p.user_id))
    sr = await ctx.one("SELECT * FROM schedule.series WHERE id = %s", (sid,))
    assert sr
    made = await materialise(ctx, sr, datetime.now(zone()) + HORIZON, strict=True, accepted=accepted)
    if not made:
        raise Invalid("Quy tắc lặp không cho lần nào trong 90 ngày tới — xem lại ngày / thứ / số lần")
    return sid, made


async def materialise(ctx: Ctx, sr: dict[str, Any], upto: datetime, *, strict: bool, accepted: set[UUID] | frozenset[UUID] = frozenset()) -> int:
    """Sinh các lần lặp còn thiếu tới ``upto``. ``strict`` (người đặt đang chờ): lần nào trùng ⇒ lỗi nêu đúng lần đó, không sinh lần nào. Vòng quét
    (không ai chờ): lần trùng thành dòng ĐÃ HUỶ có lý do (ngoại lệ — không thử lại mỗi vòng), người tạo chuỗi được báo."""
    rule = rrule.parse(sr["rrule"])
    dur = timedelta(minutes=sr["duration_min"])
    accepted = accepted or ({sr["created_by"]} & set(sr["attendee_ids"]) if sr["created_by"] else set())
    made = 0
    for t in rrule.starts(rule, sr["dtstart"], upto=upto, after=sr["horizon_until"]):
        if await ctx.val("SELECT 1 FROM schedule.events WHERE series_id = %s AND occurrence_at = %s", (sr["id"], t)):
            continue
        args: dict[str, Any] = {"title": sr["title"], "s": t, "e": t + dur, "team": sr["team_id"], "ids": list(sr["resource_ids"]),
                                "allow_outside": sr["allow_outside"], "note": sr["note"], "kind": sr["kind"], "visibility": sr["visibility"],
                                "attendees": list(sr["attendee_ids"]), "accepted": accepted, "parties": list(sr["party_ids"]), "series_id": sr["id"],
                                "occurrence_at": t, "created_by": sr["created_by"]}
        if strict:
            try:
                await _create(ctx, **args)
            except (Conflict, Invalid) as err:
                raise type(err)(f"Lần {t.astimezone(zone()):%H:%M %d/%m/%Y}: {err.message} — chưa đặt lần nào của chuỗi") from err
        else:
            try:
                async with ctx.conn.transaction():
                    await _create(ctx, **args)
            except (Conflict, Invalid) as err:
                await ctx.exec("""INSERT INTO schedule.events (title, starts_at, ends_at, team_id, note, created_by, kind, visibility, series_id, occurrence_at,
                                                              status, closed_at, cancel_reason)
                                  VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'cancelled', now(), %s)""",
                               (sr["title"], t, t + dur, sr["team_id"], sr["note"], sr["created_by"], sr["kind"], sr["visibility"], sr["id"], t,
                                f"Không giữ được chỗ khi kéo dài chuỗi: {err.message}"[:200]))
                await ctx.notify([sr["created_by"]], kind="schedule", title=f"Lịch lặp “{sr['title']}” không đặt được lần {t.astimezone(zone()):%H:%M %d/%m}",
                                 body=err.message, open={"query": "schedule.my_agenda", "input": {}})
        made += 1
    done = (rule.until is not None and rule.until <= upto) or (rule.count is not None and len(rrule.starts(rule, sr["dtstart"], upto=upto)) >= rule.count)
    await ctx.exec("UPDATE schedule.series SET horizon_until = greatest(horizon_until, %s), status = CASE WHEN %s AND status = 'active' THEN 'ended' ELSE status END "
                   "WHERE id = %s", (upto, done, sr["id"]))
    return made


def _shift_days(byday: tuple[int, ...], delta: int) -> tuple[int, ...]:
    return tuple(sorted({(d - 1 + delta) % 7 + 1 for d in byday}))


async def _series(ctx: Ctx, series_id: UUID) -> dict[str, Any]:
    sr = await ctx.one("SELECT * FROM schedule.series WHERE id = %s FOR UPDATE", (series_id,))
    if sr is None:
        raise NotFound("Không thấy chuỗi lặp (hoặc ngoài phạm vi sửa của bạn)")
    return sr


async def _cut(ctx: Ctx, sr: dict[str, Any], at_: datetime, reason: str) -> None:
    """Chuỗi dừng TRƯỚC lần lặp ``at_``: mọi lần từ đó trở đi (kể cả lần đã sửa riêng) huỷ, quy tắc thêm mốc kết thúc."""
    for o in await ctx.all("SELECT * FROM schedule.events WHERE series_id = %s AND occurrence_at >= %s AND status = 'booked' ORDER BY starts_at FOR UPDATE",
                           (sr["id"], at_)):
        await _cancel(ctx, o, reason)
    rule = rrule.parse(sr["rrule"])
    await ctx.exec("UPDATE schedule.series SET rrule = %s, status = 'ended' WHERE id = %s",
                   (rrule.text(replace(rule, until=at_ - timedelta(seconds=1), count=None)), sr["id"]))


async def _move_series(ctx: Ctx, ev: dict[str, Any], s: datetime, e: datetime, *, allow_outside: bool, title: str) -> int:
    """"Cả chuỗi": mọi lần SẮP TỚI chưa sửa riêng dời cùng một kiểu (lệch ngày + giờ mới + độ dài mới); lần đã qua giữ nguyên; quy tắc đổi theo để lần
    sinh sau khớp. Lần nào trùng ⇒ không dời lần nào."""
    sr = await _series(ctx, ev["series_id"])
    old = ev["starts_at"].astimezone(zone())
    delta = (s.astimezone(zone()).date() - old.date()).days
    clock, dur = s.astimezone(zone()).timetz(), e - s
    occ = await ctx.all("""SELECT * FROM schedule.events WHERE series_id = %s AND status = 'booked' AND NOT detached AND ends_at > now()
                           ORDER BY starts_at FOR UPDATE""", (sr["id"],))
    later = delta > 0 or (delta == 0 and (clock.replace(tzinfo=None) > old.time() or dur > ev["ends_at"] - ev["starts_at"]))
    for o in sorted(occ, key=lambda x: x["starts_at"], reverse=later):   # dời về sau: lần cuối đi trước, lần nào cũng rơi vào chỗ lần sau vừa nhả
        ns = datetime.combine(o["starts_at"].astimezone(zone()).date() + timedelta(days=delta), clock)
        await _move(ctx, o, ns, ns + dur, allow_outside=allow_outside)
        await ctx.exec("UPDATE schedule.events SET occurrence_at = %s, title = %s WHERE id = %s", (ns, title or o["title"], o["id"]))
    rule = rrule.parse(sr["rrule"])
    if rule.byday and delta % 7:
        rule = replace(rule, byday=_shift_days(rule.byday, delta))
    start = datetime.combine(sr["dtstart"].astimezone(zone()).date() + timedelta(days=delta), clock)
    await ctx.exec("UPDATE schedule.series SET dtstart = %s, duration_min = %s, rrule = %s, title = %s WHERE id = %s",
                   (start, int(dur.total_seconds() // 60), rrule.text(rule), title or sr["title"], sr["id"]))
    return len(occ)


async def _split(ctx: Ctx, ev: dict[str, Any], s: datetime, e: datetime, *, allow_outside: bool, title: str) -> tuple[UUID, int]:
    """"Lần này và các lần sau": chuỗi cũ dừng trước lần này, chuỗi MỚI bắt đầu từ giờ mới, cùng quy tắc (số lần còn lại nếu chuỗi đếm số lần), cùng
    người / phòng / người tham dự / bên ngoài."""
    sr = await _series(ctx, ev["series_id"])
    rule = rrule.parse(sr["rrule"])
    left = rrule.nth(rule, sr["dtstart"], ev["occurrence_at"])
    await _cut(ctx, sr, ev["occurrence_at"], "Tách chuỗi: các lần sau theo giờ mới")
    delta = (s.astimezone(zone()).date() - ev["occurrence_at"].astimezone(zone()).date()).days
    new = replace(rule, count=rule.count - left if rule.count else None, byday=_shift_days(rule.byday, delta) if rule.byday and delta % 7 else rule.byday)
    return await _new_series(ctx, title=title or sr["title"], s=s, e=e, team=sr["team_id"], ids=list(sr["resource_ids"]), allow_outside=allow_outside,
                             note=sr["note"], kind=sr["kind"], visibility=sr["visibility"], attendees=list(sr["attendee_ids"]), accepted=set(),
                             parties=list(sr["party_ids"]), rule=new, created_by=sr["created_by"])


class MoveEvent(Input):
    event_id: UUID = field("Lịch", hidden=True)
    day: Day = field("Ngày mới")
    start: str = field("Từ", max_length=5, kind="time")
    end: str = field("Đến", max_length=5, kind="time")
    end_day: Day | None = field("Ngày kết thúc", None, help="Trống = cùng ngày")
    allow_outside: bool = field("Vẫn đặt ngoài giờ rảnh", False)
    scope: Literal["this", "following", "all"] = field("Với lịch lặp", "this", options=SCOPE, help="Chỉ có nghĩa với lịch lặp lại")
    title: str = field("Đổi tên việc", "", max_length=200, help="Trống = giữ tên")


async def _move(ctx: Ctx, ev: dict[str, Any], s: datetime, e: datetime, *, allow_outside: bool, tell_source: bool = True) -> None:
    ids = [r["resource_id"] for r in await ctx.all("SELECT resource_id FROM schedule.bookings WHERE event_id = %s AND released_at IS NULL", (ev["id"],))]
    if ids:
        await _check(ctx, sorted(ids), s, e, allow_outside=allow_outside, except_event=ev["id"])
    await _guarded(ctx, "UPDATE schedule.events SET starts_at = %s, ends_at = %s WHERE id = %s", (s, e, ev["id"]))
    if tell_source and ev["source"]:   # module nguồn tự dời qua schedule.book thì không cần nghe lại chính việc mình làm
        ctx.emit("schedule.booking_changed", change="moved", event_id=ev["id"], source=ev["source"], source_ref=ev["source_ref"], starts_at=s, ends_at=e)


@m.command("move", resource="schedule", action="read", title="Dời lịch", agent="propose")
async def move(ctx: Ctx, inp: MoveEvent) -> dict[str, Any]:
    """Dời (và đổi tên) một lịch. Lịch lặp: chỉ lần này (thành lần sửa riêng), lần này và các lần sau (tách chuỗi), cả chuỗi (mọi lần sắp tới chưa sửa
    riêng). Người / phòng trùng giờ mới ⇒ không dời lần nào; người tham dự trùng ⇒ chỉ cảnh báo."""
    ev = await _event(ctx, inp.event_id)
    await _may_change(ctx, ev)
    if ev["status"] != "booked":
        raise Invalid(f"Lịch đang ở trạng thái “{STATUS[ev['status']].lower()}” — không dời được")
    s, e = span(inp.day, inp.start, inp.end, inp.end_day)
    title = inp.title.strip()
    attendees = [r["user_id"] for r in await ctx.all("SELECT user_id FROM schedule.attendees WHERE event_id = %s AND rsvp <> 'declined'", (ev["id"],))]
    warnings = await _soft_clash(ctx, attendees, s, e, ev["id"])
    scope = inp.scope if ev["series_id"] else "this"
    if scope == "all":
        n = await _move_series(ctx, ev, s, e, allow_outside=inp.allow_outside, title=title)
        what = f"cả chuỗi ({n} lần sắp tới)"
    elif scope == "following":
        _, n = await _split(ctx, ev, s, e, allow_outside=inp.allow_outside, title=title)
        what = f"lần này và các lần sau ({n} lần)"
    else:
        await _move(ctx, ev, s, e, allow_outside=inp.allow_outside)
        await ctx.exec("UPDATE schedule.events SET detached = series_id IS NOT NULL, title = %s WHERE id = %s", (title or ev["title"], ev["id"]))
        what = ""
    await _tell(ctx, await _booked_users(ctx, ev["id"]), f"Dời lịch: {title or ev['title']} sang {when(s, e)} {s.astimezone(zone()):%d/%m}", s)
    return {"event_id": ev["id"], "warnings": warnings,
            "summary": f"Dời {title or ev['title']}{' — ' + what if what else ''} sang {when(s, e)} {s.astimezone(zone()):%d/%m}"}


class ReplaceResource(Input):
    event_id: UUID = field("Lịch", hidden=True)
    resource_id: UUID = field("Thay ai / phòng nào", options_query="schedule.resource_options")
    new_resource_id: UUID = field("Bằng", options_query="schedule.resource_options")
    reason: str = field("Lý do", "", max_length=200, example="Nghỉ ốm")
    allow_outside: bool = field("Vẫn đặt ngoài giờ rảnh", False)


@m.command("replace_resource", resource="schedule", title="Đổi người / phòng của lịch", agent="propose")
async def replace_resource(ctx: Ctx, inp: ReplaceResource) -> dict[str, Any]:
    """Người báo nghỉ: tìm người rảnh (``schedule.find_free``) rồi đổi ở đây. Dòng cũ nhả chỗ nhưng giữ lại làm vết (ai nhả, lúc nào, vì sao).
    Lịch lặp: chỉ đổi lần này (lần đó thành lần sửa riêng)."""
    ev = await _event(ctx, inp.event_id)
    _may_write_at(ctx, ev["team_id"])
    if ev["status"] != "booked":
        raise Invalid("Chỉ đổi người / phòng của lịch đang đặt")
    if inp.resource_id == inp.new_resource_id:
        raise Invalid("Người / phòng mới trùng người / phòng cũ")
    old = await ctx.one("SELECT id, resource_name, user_id FROM schedule.bookings WHERE event_id = %s AND resource_id = %s AND released_at IS NULL FOR UPDATE",
                        (ev["id"], inp.resource_id))
    if old is None:
        raise NotFound("Người / phòng này không có trong lịch")
    names = await _check(ctx, [inp.new_resource_id], ev["starts_at"], ev["ends_at"], allow_outside=inp.allow_outside)
    await ctx.exec("UPDATE schedule.bookings SET active = false, released_at = now(), released_by = %s, release_reason = %s WHERE id = %s",
                   (ctx.p.user_id, inp.reason, old["id"]))
    await _guarded(ctx, "INSERT INTO schedule.bookings (event_id, resource_id, during) VALUES (%s, %s, tstzrange(%s, %s))",
                   (ev["id"], inp.new_resource_id, ev["starts_at"], ev["ends_at"]))
    await ctx.exec("UPDATE schedule.events SET detached = true WHERE id = %s AND series_id IS NOT NULL", (ev["id"],))
    new = names[inp.new_resource_id]
    ctx.emit("schedule.booking_changed", change="resource_replaced", event_id=ev["id"], source=ev["source"], source_ref=ev["source_ref"], old_resource_id=inp.resource_id,
             new_resource_id=inp.new_resource_id)
    s = ev["starts_at"]
    await _tell(ctx, [old["user_id"]] if old["user_id"] else [], f"Không còn trong lịch: {ev['title']} {when(s, ev['ends_at'])} {s.astimezone(zone()):%d/%m}", s)
    await _tell(ctx, await _booked_users(ctx, ev["id"]), f"Lịch: {ev['title']} {when(s, ev['ends_at'])} {s.astimezone(zone()):%d/%m} — {new} thay {old['resource_name']}", s)
    return {"event_id": ev["id"], "summary": f"{ev['title']}: {new} thay {old['resource_name']}"}


class CancelEvent(Input):
    event_id: UUID = field("Lịch", hidden=True)
    reason: str = field("Lý do", "", max_length=200)
    scope: Literal["this", "following", "all"] = field("Với lịch lặp", "this", options=SCOPE, help="Chỉ có nghĩa với lịch lặp lại")


async def _cancel(ctx: Ctx, ev: dict[str, Any], reason: str, *, tell_source: bool = True) -> None:
    await ctx.exec("UPDATE schedule.events SET status = 'cancelled', closed_by = %s, closed_at = now(), cancel_reason = %s WHERE id = %s",
                   (ctx.p.user_id, reason, ev["id"]))
    if tell_source and ev["source"]:   # module nguồn tự nhả qua schedule.release thì không cần nghe lại
        ctx.emit("schedule.booking_changed", change="cancelled", event_id=ev["id"], source=ev["source"], source_ref=ev["source_ref"])


@m.command("cancel", resource="schedule", action="read", title="Huỷ lịch", agent="propose")
async def cancel(ctx: Ctx, inp: CancelEvent) -> dict[str, Any]:
    """Huỷ nhả mọi người / phòng của lịch. Lịch do module khác đặt (có nguồn): module đó nhận ``schedule.booking_changed`` (``change="cancelled"``).
    Lịch lặp: chỉ lần này (giữ làm ngoại lệ — lần sinh sau không đặt lại), lần này và các lần sau, cả chuỗi (mọi lần sắp tới)."""
    ev = await _event(ctx, inp.event_id)
    await _may_change(ctx, ev)
    if ev["status"] != "booked":
        raise Invalid(f"Lịch đang ở trạng thái “{STATUS[ev['status']].lower()}”")
    users = await _booked_users(ctx, ev["id"])
    scope = inp.scope if ev["series_id"] else "this"
    if scope == "following":
        await _cut(ctx, await _series(ctx, ev["series_id"]), ev["occurrence_at"], inp.reason or "Huỷ các lần sau")
    elif scope == "all":
        sr = await _series(ctx, ev["series_id"])
        for o in await ctx.all("SELECT * FROM schedule.events WHERE series_id = %s AND status = 'booked' AND ends_at > now() FOR UPDATE", (sr["id"],)):
            await _cancel(ctx, o, inp.reason or "Huỷ cả chuỗi")
        await ctx.exec("UPDATE schedule.series SET status = 'cancelled' WHERE id = %s", (sr["id"],))
    else:
        await _cancel(ctx, ev, inp.reason)
    what = {"this": "", "following": " — lần này và các lần sau", "all": " — cả chuỗi"}[scope]
    await _tell(ctx, users, f"Huỷ lịch: {ev['title']}{what} {when(ev['starts_at'], ev['ends_at'])} {ev['starts_at'].astimezone(zone()):%d/%m}", ev["starts_at"])
    return {"event_id": ev["id"], "summary": f"Huỷ {ev['title']}{what}"}


class EventRef(Input):
    event_id: UUID = field("Lịch", hidden=True)


@m.command("mark_done",
           purpose="Đánh dấu lịch đã diễn ra để giờ đó được giữ như đã dùng thật.", irreversible=True, effect="Lịch chuyển sang đã xong và giữ khoảng giờ đó như đã dùng thật; không trả về chưa xong được.",
           resource="schedule", action="read", title="Đánh dấu đã xong", agent="propose")
async def mark_done(ctx: Ctx, inp: EventRef) -> dict[str, Any]:
    """Lịch đã diễn ra. Vẫn giữ khoảng giờ đó (đã dùng thật) — không ai đặt chồng vào quá khứ được."""
    ev = await _event(ctx, inp.event_id)
    await _may_change(ctx, ev)
    if ev["status"] != "booked":
        raise Invalid(f"Lịch đang ở trạng thái “{STATUS[ev['status']].lower()}”")
    if ev["starts_at"] > datetime.now(zone()):
        raise Invalid("Lịch chưa bắt đầu")
    await ctx.exec("UPDATE schedule.events SET status = 'done', closed_by = %s, closed_at = now() WHERE id = %s", (ctx.p.user_id, ev["id"]))
    return {"event_id": ev["id"], "summary": f"Đã xong: {ev['title']}"}


# ── Hợp đồng cho module khác: đặt / nhả lịch cho đối tượng của CHÍNH module gọi ──

class Book(Input):
    source: str
    source_ref: str
    title: str
    starts_at: datetime
    ends_at: datetime
    team_id: UUID | None = None
    resource_ids: list[UUID] = field("Người / phòng", [])
    allow_outside: bool = False
    note: str = ""
    kind: str = ""                         # loại lịch (khoá); công ty chưa có ⇒ thêm với ``kind_label``
    kind_label: str = ""
    attendee_user_ids: list[UUID] = field("Người tham dự", [])     # người tham dự trong công ty (mềm — trùng giờ chỉ cảnh báo)
    accepted_user_ids: list[UUID] = field("Đã nhận sẵn", [])     # trong số đó: ai nhận sẵn (vd người tự đặt cho mình, nghỉ của chính người đó)
    party_ids: list[UUID] = field("Bên ngoài", [])             # bên ngoài: mã định danh ở parties
    visibility: Literal["normal", "private"] = "normal"
    notify: bool = True                    # báo người được mời mới (module nguồn tự báo thì tắt)


def _own_source(ctx: Ctx, source: str, ref: str) -> None:
    if not SOURCE.match(source) or not ref:
        raise Invalid("Nguồn lịch dạng <module>.<loại> và cần mã đối tượng")
    ctx.owned_by(source.split(".")[0], f"Nguồn lịch {source}")


BOOK_WHY = ("crm.schedule / move_appointment / chuyển người phụ trách (hẹn với khách của lead người gọi thấy qua RLS: sale phụ trách là người tham dự, khách "
            "là bên ngoài, riêng tư) và vòng quét crm.heal_appointments (hẹn bản cũ ghi lúc chuyển, quyền hệ thống); meet.start / kết thúc (cuộc gọi trên đối tượng người gọi thấy: người gọi + người được gọi là người tham dự); "
            "timekeeping.approve_leave (đơn nghỉ đã duyệt: chính người xin là người tham dự, riêng tư); gói khai consumes(\"booking\") xếp đối tượng của nó lên "
            "lịch (người đặt: thấy đối tượng của mình, mọi người / phòng nằm trong danh sách NGƯỜI ĐẶT thấy — vd một buổi: người phụ trách + phòng). Chỉ đặt lịch nguồn '<module gọi>.<loại>' "
            "(owned_by). Nguồn lực trùng / đủ lượt ⇒ Conflict nêu người / phòng và giờ, không ghi dở; người tham dự trùng giờ ⇒ chỉ cảnh báo (tên + giờ, "
            "không nêu lịch kia là gì). Trợ lý làm thật mà lịch có người khác ⇒ thành đề xuất. Trả mã lịch + cảnh báo, không trả lịch của ai khác."
            " [id: module gọi tự có — attendee_user_ids / accepted_user_ids / party_ids lấy từ đối tượng của module gọi mà người gọi đã thấy qua RLS "
            "(người phụ trách lead, người phụ trách buổi của gói, người xin nghỉ, người được gọi đã chọn theo quyền người gọi; định danh của lead)]")


@m.command("book", resource="schedule", title="Đặt / giữ lịch cho đối tượng của module", internal=True, agent="deny",
           description="Khai báo: gọi lại với cùng (source, source_ref) ⇒ lịch đó khớp giờ / người / phòng / người tham dự / bên ngoài mới (dời, thêm, "
                       "nhả), không tạo lịch thứ hai. Bên gọi kiểm quyền trên đối tượng của mình trước. Trả {event_id, created, warnings}.",
           callers=("crm", "meet", "timekeeping"), family="booking", why=BOOK_WHY)
async def book(ctx: Ctx, inp: Book) -> dict[str, Any]:
    _own_source(ctx, inp.source, inp.source_ref)
    if inp.starts_at.tzinfo is None or inp.ends_at.tzinfo is None:
        raise Invalid("Giờ đặt lịch phải kèm múi giờ")
    if inp.ends_at <= inp.starts_at:
        raise Invalid("Giờ kết thúc phải sau giờ bắt đầu")
    _agent_guard(ctx, set(inp.attendee_user_ids) | await _people_of(ctx, inp.resource_ids))
    # Một lượt cho mỗi đối tượng: hai lần gọi cùng lúc lần lượt chạy, lần sau thấy lịch lần trước ⇒ cập nhật, không tạo thứ hai
    await ctx.exec("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"schedule.book:{ctx.p.tenant_id}:{inp.source}:{inp.source_ref}",))
    try:
        async with ctx.conn.transaction():   # điểm lưu: bên gọi bắt Conflict rồi làm tiếp thì không còn gì đổi dở (đã nhả / đã dời một nửa)
            return await _book(ctx, inp)
    except pg.UniqueViolation as err:
        raise Conflict("Lịch của đối tượng này vừa được đặt ở nơi khác — gọi lại để cập nhật") from err


async def _book(ctx: Ctx, inp: Book) -> dict[str, Any]:
    await _members_by_id(ctx, inp.attendee_user_ids)
    await _ensure_kind(ctx, inp.kind, inp.kind_label)
    accepted = set(inp.accepted_user_ids) & set(inp.attendee_user_ids)
    ev = await ctx.one("SELECT * FROM schedule.events WHERE source = %s AND source_ref = %s AND status <> 'cancelled' FOR UPDATE", (inp.source, inp.source_ref))
    warnings = await _soft_clash(ctx, inp.attendee_user_ids, inp.starts_at, inp.ends_at, ev["id"] if ev else None)
    if ev is None:
        eid = await _create(ctx, title=inp.title, s=inp.starts_at, e=inp.ends_at, team=inp.team_id, ids=inp.resource_ids, allow_outside=inp.allow_outside,
                            note=inp.note, source=inp.source, source_ref=inp.source_ref, kind=inp.kind, visibility=inp.visibility,
                            attendees=inp.attendee_user_ids, accepted=accepted, parties=inp.party_ids)
        if inp.notify:
            await _tell(ctx, [u for u in inp.attendee_user_ids if u not in accepted],
                        f"Lời mời: {inp.title} {when(inp.starts_at, inp.ends_at)} {inp.starts_at.astimezone(zone()):%d/%m}", inp.starts_at)
        return {"event_id": eid, "created": True, "warnings": warnings}
    if ev["status"] != "booked":
        raise Invalid("Lịch của đối tượng này đã xong — không đặt lại")
    want = set(inp.resource_ids)
    have = {r["resource_id"]: r["id"] for r in await ctx.all("SELECT id, resource_id FROM schedule.bookings WHERE event_id = %s AND released_at IS NULL", (ev["id"],))}
    if not want and not inp.attendee_user_ids:
        raise Invalid("Cần ít nhất một người / phòng, hoặc một người tham dự")
    await _lock(ctx, want | set(have))   # mọi nguồn lực sẽ đụng, một lần theo thứ tự — không khoá dần từng nhóm
    for rid in sorted(set(have) - want):
        await ctx.exec("UPDATE schedule.bookings SET active = false, released_at = now(), release_reason = %s WHERE id = %s", (f"{inp.source} đổi", have[rid]))
    if (ev["starts_at"], ev["ends_at"]) != (inp.starts_at, inp.ends_at):
        await _move(ctx, ev, inp.starts_at, inp.ends_at, allow_outside=inp.allow_outside, tell_source=False)
    if add := sorted(want - set(have)):
        await _check(ctx, add, inp.starts_at, inp.ends_at, allow_outside=inp.allow_outside)
        for rid in add:
            await _guarded(ctx, "INSERT INTO schedule.bookings (event_id, resource_id, during) VALUES (%s, %s, tstzrange(%s, %s))",
                           (ev["id"], rid, inp.starts_at, inp.ends_at))
    await ctx.exec("""UPDATE schedule.events SET title = %(t)s, note = %(n)s, kind = %(k)s, visibility = %(v)s, team_id = %(team)s
                      WHERE id = %(id)s AND (title, note, kind, visibility, team_id) IS DISTINCT FROM (%(t)s, %(n)s, %(k)s, %(v)s, %(team)s)""",
                   {"t": inp.title, "n": inp.note, "k": inp.kind, "v": inp.visibility, "team": inp.team_id, "id": ev["id"]})
    invited = await _put_attendees(ctx, ev["id"], inp.attendee_user_ids, accepted)
    await _put_parties(ctx, ev["id"], inp.party_ids)
    if inp.notify and invited:
        await _tell(ctx, invited, f"Lời mời: {inp.title} {when(inp.starts_at, inp.ends_at)} {inp.starts_at.astimezone(zone()):%d/%m}", inp.starts_at)
    return {"event_id": ev["id"], "created": False, "warnings": warnings}


class Release(Input):
    source: str
    source_ref: str
    reason: str = ""


@m.command("release", resource="schedule", title="Nhả lịch của đối tượng của module", internal=True, agent="deny",
           description="Huỷ lịch đang đặt của (source, source_ref); không có ⇒ released = 0 (gọi lại an toàn).",
           callers=("crm",), family="booking",
           why="crm (huỷ hẹn của lead người huỷ thấy) / gói khai consumes(\"booking\") (huỷ đối tượng đã xếp lịch của nó), SAU khi chính lệnh đó kiểm người huỷ được sửa đối tượng của mình: nhả "
               "người / phòng của lịch nguồn '<module gọi>.<loại>' (owned_by). Trả số lịch đã nhả.")
async def release(ctx: Ctx, inp: Release) -> dict[str, int]:
    _own_source(ctx, inp.source, inp.source_ref)
    ev = await ctx.one("SELECT * FROM schedule.events WHERE source = %s AND source_ref = %s AND status = 'booked' FOR UPDATE", (inp.source, inp.source_ref))
    if ev is None:
        return {"released": 0}
    await _cancel(ctx, ev, inp.reason, tell_source=False)
    return {"released": 1}


class Finish(Input):
    source: str
    source_ref: str


@m.command("finish", resource="schedule", title="Chốt lịch của đối tượng của module là đã diễn ra", internal=True, agent="deny",
           description="Lịch đang đặt của (source, source_ref) ⇒ đã xong (giữ khoảng giờ đã dùng). Chưa tới giờ bắt đầu ⇒ nhả (không giữ giờ chưa dùng). "
                       "Không có ⇒ finished = 0.",
           callers=("crm",),
           why="crm.close_appointment (người chốt thấy hẹn qua RLS của lead): hẹn đã gặp / khách không tới ⇒ lịch nguồn 'crm.<loại>' (owned_by) thành đã "
               "xong. Trả số lịch đã chốt.")
async def finish(ctx: Ctx, inp: Finish) -> dict[str, int]:
    _own_source(ctx, inp.source, inp.source_ref)
    ev = await ctx.one("SELECT * FROM schedule.events WHERE source = %s AND source_ref = %s AND status = 'booked' FOR UPDATE", (inp.source, inp.source_ref))
    if ev is None:
        return {"finished": 0}
    if ev["starts_at"] > datetime.now(zone()):
        await _cancel(ctx, ev, "Chốt trước giờ hẹn", tell_source=False)
    else:
        await ctx.exec("UPDATE schedule.events SET status = 'done', closed_by = %s, closed_at = now() WHERE id = %s", (ctx.p.user_id, ev["id"]))
    return {"finished": 1}


class TimesOf(Input):
    source: str
    refs: list[str] | None = None          # None = mọi đối tượng của nguồn này trong khoảng (vòng quét nhắc hẹn)
    since: datetime | None = None
    until: datetime | None = None
    include_cancelled: bool = False        # lịch sử (hành trình khách): kể cả lịch đã huỷ — có lịch còn hiệu lực thì lịch đó thắng


@m.query("times_of", resource="schedule", title="Giờ lịch của các đối tượng của module", internal=True, agent="deny", callers=("crm",),
         why="crm (lịch hẹn, thẻ buổi sáng, hành trình khách, nhắc trước giờ hẹn, gợi ý đặt lịch, hoàn tác) đọc GIỜ hẹn từ lịch — một nguồn — cho đúng "
             "các hẹn người xem đã thấy qua RLS của lead (hoặc mọi hẹn của nguồn 'crm.<loại>' trong một khoảng, cho vòng quét quyền hệ thống). Chỉ nguồn "
             "của module gọi (owned_by). Trả mã đối tượng → mã lịch, giờ bắt đầu / kết thúc, trạng thái; không tên việc, không người, không lịch khác.")
async def times_of(ctx: Ctx, inp: TimesOf) -> dict[str, Any]:
    _own_source(ctx, inp.source, "*")
    if inp.refs is None and (inp.since is None or inp.until is None):
        raise Invalid("Không nêu đối tượng thì cần khoảng thời gian")
    rows = await ctx.all("""SELECT id, source_ref, starts_at, ends_at, status FROM schedule.events
                            WHERE source = %(src)s AND (%(cancelled)s OR status <> 'cancelled') AND (%(all)s OR source_ref = ANY(%(refs)s))
                              AND (%(since)s::timestamptz IS NULL OR ends_at > %(since)s) AND (%(until)s::timestamptz IS NULL OR starts_at < %(until)s)
                            ORDER BY status <> 'cancelled', starts_at LIMIT 5000""",
                         {"src": inp.source, "all": inp.refs is None, "refs": inp.refs or [], "since": inp.since, "until": inp.until,
                          "cancelled": inp.include_cancelled})
    return {"times": {r["source_ref"]: {"event_id": r["id"], "starts_at": r["starts_at"], "ends_at": r["ends_at"], "status": r["status"]} for r in rows}}


class ResourcesFor(Input):
    user_ids: list[UUID] = field("Tài khoản", [])
    room_ids: list[UUID] = field("Phòng / thiết bị", [])
    resource_ids: list[UUID] = field("Nguồn lực", [])
    team_ids: list[UUID] = field("Đội", [])


@m.query("resources_for", resource="schedule", title="Nguồn lực theo tài khoản / mã", internal=True, agent="deny", family="booking",
         why="Gói khai consumes(\"booking\") (xếp đối tượng của mình lên lịch, lựa chọn phòng, nghe đổi người trên lịch) cần biết nguồn lực của những tài khoản / mã nó đã "
             "có, mà người xếp không có quyền xem lịch cả công ty. Chỉ trả mã: tài khoản → nguồn lực đang dùng (khớp theo user_id), mã → tài khoản, "
             "và với phòng / thiết bị: loại người hay vật, đội, đang dùng, tên; danh sách phòng / thiết bị đang dùng của các đội được hỏi. "
             "Không trả lịch, giờ rảnh, ghi chú hay sự kiện nào."
             " [id: module gọi tự có — user_ids lấy từ đối tượng của module gọi mà người xếp thấy qua RLS (người phụ trách và người cùng làm)]")
async def resources_for(ctx: Ctx, inp: ResourcesFor) -> dict[str, Any]:
    users = {str(r["user_id"]): r["id"] for r in await ctx.all(
        "SELECT id, user_id FROM schedule.resources WHERE user_id = ANY(%s) AND active", (inp.user_ids,))}
    people = {str(r["id"]): r["user_id"] for r in await ctx.all(
        "SELECT id, user_id FROM schedule.resources WHERE id = ANY(%s) AND user_id IS NOT NULL", (inp.resource_ids,))}
    rooms = {str(r["id"]): {"nature": r["nature"], "team_id": r["team_id"], "active": r["active"], "name": r["name"]} for r in await ctx.all(
        "SELECT id, nature, team_id, active, name FROM schedule.resources WHERE id = ANY(%s)", (inp.room_ids,))}
    in_teams = await ctx.all("""SELECT id, name, team_id FROM schedule.resources WHERE team_id = ANY(%s) AND nature = 'thing' AND active
                                ORDER BY name LIMIT 500""", (inp.team_ids,))
    return {"users": users, "people": people, "rooms": rooms, "team_rooms": in_teams}


# ── Xem ──

MORE = "Lọc thêm"
FREE_MORE = "Thêm điều kiện"


class WeekIn(Input):
    week_of: Day | None = field("Tuần có ngày", None, help="Trống = tuần này", placeholder="Tuần này")
    team_code: str = field("Cơ sở / đội", "", options_query="admin.team_options", group=MORE)
    resource_id: UUID | None = field("Người / phòng", None, options_query="schedule.resource_options", group=MORE)
    member_email: str = field("Lịch của", "", options_query="admin.member_options", group=MORE,
                              help="Một người trong công ty: lịch bạn được xem hiện đủ, lịch khác của họ chỉ hiện “Bận”")
    show_cancelled: bool = field("Hiện lịch đã huỷ", False, group=MORE)


def _monday(d: date) -> date:
    return d - timedelta(days=d.isoweekday() - 1)


def _shape(r: dict[str, Any], lo: datetime) -> dict[str, Any]:
    r["day"] = max(r["starts_at"], lo).astimezone(zone()).date().isoformat()   # lịch bắt đầu từ tuần trước: hiện ở ngày đầu khung
    r["when"] = when(r["starts_at"], r["ends_at"])
    r["line"] = " · ".join(x for x in (r["when"], r["resources"], r["attendees"], r["team"] or "") if x)
    r["starts_at"], r["ends_at"] = r["starts_at"].astimezone(zone()).isoformat(), r["ends_at"].astimezone(zone()).isoformat()
    return r


async def _event_rows(ctx: Ctx, lo: datetime, hi: datetime, extra: LiteralString, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Lịch người xem đọc được CHI TIẾT (RLS: người tạo, người tham dự, người được xếp, người có quyền ``schedule.details``; riêng tư chỉ người trong
    lịch). ``my_rsvp``: câu trả lời của chính người xem nếu được mời — chưa nhận thì lịch hiện "Chờ xác nhận" trên lịch của họ."""
    rows = await ctx.all(
        """SELECT e.id, e.title, e.starts_at, e.ends_at, e.status, e.source, e.kind, coalesce(k.label, '') AS kind_label, e.visibility,
                  e.series_id IS NOT NULL AS repeats, false AS busy, t.name AS team,
                  coalesce((SELECT a.rsvp FROM schedule.attendees a WHERE a.event_id = e.id AND a.user_id = core.me()), '') AS my_rsvp,
                  coalesce((SELECT string_agg(b.resource_name, ', ' ORDER BY b.resource_name) FROM schedule.bookings b
                            WHERE b.event_id = e.id AND b.released_at IS NULL), '') AS resources,
                  coalesce((SELECT string_agg(u.name, ', ' ORDER BY u.name) FROM schedule.attendees a JOIN core.users u ON u.id = a.user_id
                            WHERE a.event_id = e.id AND a.rsvp <> 'declined'), '') AS attendees
           FROM schedule.events e LEFT JOIN core.teams t ON t.id = e.team_id LEFT JOIN schedule.kinds k ON k.tenant_id = e.tenant_id AND k.key = e.kind
           WHERE tstzrange(e.starts_at, e.ends_at) && tstzrange(%(lo)s, %(hi)s) """ + extra + """
           ORDER BY e.starts_at, e.title LIMIT 1000""", {"lo": lo, "hi": hi, **params})
    for r in rows:
        if r["status"] == "booked" and r["my_rsvp"] in ("invited", "tentative"):
            r["status"] = "tentative"   # trên lịch của người được mời: chưa nhận ⇒ "chưa chắc", nút trả lời trên thẻ
    return [_shape(r, lo) for r in rows]


async def _busy_rows(ctx: Ctx, lo: datetime, hi: datetime, rids: list[UUID], uids: list[UUID], seen: set[UUID]) -> list[dict[str, Any]]:
    """Khối "Bận" (0158): lịch chạm người / phòng người xem được nhìn mà người xem KHÔNG được đọc chi tiết — chỉ giờ + người / phòng đang giữ, không
    tên việc, không bên ngoài, không ghi chú. Dữ liệu từ ``schedule.blocks`` (hàm chỉ trả khối giờ)."""
    if not rids and not uids:
        return []
    got: dict[UUID, dict[str, Any]] = {}
    names = {r["id"]: r["name"] for r in await ctx.all("SELECT id, name FROM schedule.resources WHERE id = ANY(%s)", (rids,))}
    people = {r["id"]: r["name"] for r in await ctx.all("SELECT id, name FROM core.users WHERE id = ANY(%s)", (uids,))}
    for b in await ctx.all("SELECT * FROM schedule.blocks(%s::uuid[], %s::uuid[], tstzrange(%s, %s), NULL)", (rids, uids, lo, hi)):
        if b["event_id"] in seen:
            continue
        r = got.setdefault(b["event_id"], {"id": b["event_id"], "title": MAYBE, "starts_at": b["starts_at"], "ends_at": b["ends_at"],
                                           "status": "maybe", "source": "", "kind": "", "kind_label": "", "visibility": "", "repeats": False, "busy": True,
                                           "team": "", "my_rsvp": "", "res": set(), "who": set()})
        if b["hard"] or b["rsvp"] == "accepted":   # giữ cứng / đã nhận ⇒ bận; chỉ được mời / "có thể" ⇒ chưa chắc
            r["title"], r["status"] = BUSY, "busy"
        if b["resource_id"] in names:
            r["res"].add(names[b["resource_id"]])
        if b["user_id"] in people and b["resource_id"] is None:
            r["who"].add(people[b["user_id"]])
    out = []
    for r in got.values():
        r["resources"], r["attendees"] = ", ".join(sorted(r.pop("res"))), ", ".join(sorted(r.pop("who")))
        out.append(_shape(r, lo))
    return out


EVENT_COLS = [
    ui.col("day", "Ngày", "date"), ui.col("when", "Giờ", width=120), ui.col("title", "Việc", width=240), ui.col("kind_label", "Loại"),
    ui.col("resources", "Người / phòng", width=240), ui.col("attendees", "Người tham dự", width=200), ui.col("team", "Ở đâu"),
    ui.col("status", "Trạng thái", "badge", labels=STATUS, tones={"booked": "blue", "cancelled": "gray", "done": "green", "busy": "gray", "tentative": "orange",
                                                                  "maybe": "gray"}),
    ui.col("my_rsvp", "Tôi trả lời", "badge", labels={"": "", **RSVP}, tones={"invited": "orange", "accepted": "green", "tentative": "orange", "declined": "gray"}),
]


@m.query("week", resource="schedule", title="Lịch tuần")
async def week(ctx: Ctx, inp: WeekIn) -> dict[str, Any]:
    """Lịch trong một tuần (thứ Hai – Chủ nhật, giờ công ty) trong phạm vi người xem: đội mình quản lý, cả công ty, hoặc lịch mình có tên. Lịch chạm
    người / phòng mình được nhìn mà mình không được đọc chi tiết (riêng tư, của đội khác) hiện thành khối "Bận"."""
    mon = _monday(inp.week_of or today())
    lo, hi = datetime.combine(mon, time(0), zone()), datetime.combine(mon + timedelta(days=7), time(0), zone())
    team = await _team(ctx, inp.team_code)
    member = next(iter(await _members_by_email(ctx, [inp.member_email])), None) if inp.member_email.strip() else None
    rows = await _event_rows(ctx, lo, hi,
                             """AND (%(cancelled)s OR e.status <> 'cancelled') AND (%(team)s::uuid IS NULL OR e.team_id = %(team)s)
                                AND (%(res)s::uuid IS NULL OR EXISTS (SELECT 1 FROM schedule.bookings x WHERE x.event_id = e.id AND x.resource_id = %(res)s
                                                                      AND x.released_at IS NULL))
                                AND (%(member)s::uuid IS NULL OR EXISTS (SELECT 1 FROM schedule.bookings x WHERE x.event_id = e.id AND x.user_id = %(member)s
                                                                         AND x.released_at IS NULL)
                                     OR EXISTS (SELECT 1 FROM schedule.attendees a WHERE a.event_id = e.id AND a.user_id = %(member)s AND a.rsvp <> 'declined'))""",
                             {"cancelled": inp.show_cancelled, "team": team, "res": inp.resource_id, "member": member})
    rids = [] if member else [r["id"] for r in await ctx.all(
        "SELECT id FROM schedule.resources WHERE (%(res)s::uuid IS NULL OR id = %(res)s) AND (%(team)s::uuid IS NULL OR team_id = %(team)s) LIMIT 1000",
        {"res": inp.resource_id, "team": team})]
    rows += await _busy_rows(ctx, lo, hi, rids, [member] if member else [], {r["id"] for r in rows})
    rows.sort(key=lambda r: (r["starts_at"], r["title"]))
    live = [r for r in rows if r["status"] != "cancelled"]
    empty, hint = "Tuần này chưa có lịch nào.", []
    if not rows:
        if await ctx.val("SELECT count(*) FROM schedule.resources WHERE active") == 0 and can_run(ctx.p, "schedule.add_resource"):
            empty = "Chưa có lịch, và cũng chưa có người hay phòng nào để đặt. Thêm ở Người & phòng trước."
            hint = [ui.act("schedule.add_resource", "Thêm người / phòng", style="primary")]
        else:
            empty += (" Bấm “Đặt lịch” để thêm một lịch." if can_run(ctx.p, "schedule.create") else "") + " Muốn xem tuần khác: chọn ngày ở ô “Tuần có ngày”."
            if ctx.p.scope("schedule") == "own":
                empty += " Ở đây chỉ hiện lịch có tên bạn."
    mine = "Chỉ hiện lịch có tên bạn. " if ctx.p.scope("schedule") == "own" else ""
    busy = " “Bận” là lịch bạn không được xem chi tiết." if any(r["busy"] for r in rows) else ""
    return ui.table(rows, EVENT_COLS, title=f"Lịch tuần {mon:%d/%m} – {mon + timedelta(days=6):%d/%m/%Y}", empty=empty, limit=1000, empty_actions=hint,
                    kpis=[ui.kpi("Lịch trong tuần", len(live)), ui.kpi("Hôm nay", sum(1 for r in live if r["day"] == today().isoformat()))],
                    note=mine + f"{zone_label(head=True)}." + busy, views=ui.views(calendar=ui.calendar_view("day", "title", "line", overdue=False)))


# Chỉ ba ô đầu (ngày, từ giờ, đến giờ) hiện sẵn — để trống thì lấy hôm nay, giờ tròn kế tiếp, một giờ sau. Các ô còn lại là tuỳ chọn, gấp
# trong nhóm "Thêm điều kiện".
class FindFree(Input):
    day: Day | None = field("Ngày", None, help="Trống = hôm nay", placeholder="Hôm nay")
    start: str = field("Từ giờ", "", max_length=5, example="14:00", help="Trống = giờ tròn kế tiếp", placeholder="Giờ tròn kế tiếp")
    end: str = field("Đến giờ", "", max_length=5, example="15:30", help="Trống = một giờ sau giờ bắt đầu", placeholder="1 giờ sau")
    end_day: Day | None = field("Ngày kết thúc", None, help="Trống = cùng ngày; chỉ cần khi qua đêm", placeholder="Cùng ngày", group=FREE_MORE)
    nature: Literal["", "person", "thing"] = field("Người hay phòng", "", options={"": "Cả hai", **NATURE}, group=FREE_MORE)
    kind: str = field("Loại", "", options_query="schedule.kind_options", group=FREE_MORE)
    tags: str = field("Cần kỹ năng / thiết bị", "", max_length=300, help="Nhiều thẻ cách nhau dấu phẩy: phải có đủ", group=FREE_MORE)
    team_code: str = field("Cơ sở / đội", "", options_query="admin.team_options", group=FREE_MORE)
    min_capacity: int = field("Sức chứa tối thiểu", 1, ge=1, group=FREE_MORE)
    except_event_id: UUID | None = field("Bỏ qua lịch", None, hidden=True, help="Tìm người thay cho chính lịch này: lịch đó không tính là bận")


def _default_span(inp: FindFree) -> tuple[datetime, datetime]:
    if inp.start:
        s = at(inp.day or today(), minutes(inp.start))
    else:
        now = datetime.now(zone())
        s = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0) if inp.day in (None, today()) else at(inp.day, 8 * 60)
    e = at(inp.end_day or s.astimezone(zone()).date(), minutes(inp.end, end=True)) if inp.end else s + timedelta(hours=1)
    if e <= s:
        raise Invalid("Giờ kết thúc phải sau giờ bắt đầu (qua đêm thì chọn ngày kết thúc)")
    return s, e


@m.query("find_free", resource="schedule", title="Tìm người / phòng rảnh")
async def find_free(ctx: Ctx, inp: FindFree) -> dict[str, Any]:
    """Người / phòng RẢNH trong khoảng giờ: đang dùng, đúng loại / thẻ / đội / sức chứa, khoảng giờ nằm trong giờ rảnh theo tuần, và không có lịch đang
    giữ trùng giờ (xét MỌI lịch của công ty, kể cả của đội người hỏi không thấy — chỉ biết bận, không lộ lịch). Chỉ trong phạm vi nguồn lực người hỏi
    thấy. Dùng cho "người phụ trách báo nghỉ, ai thay được lúc …"."""
    s, e = _default_span(inp)
    team = await _team(ctx, inp.team_code)
    cands = await ctx.all(
        """SELECT r.id, r.name, r.nature, r.kind, r.tags, r.capacity, r.user_id, t.name AS team FROM schedule.resources r LEFT JOIN core.teams t ON t.id = r.team_id
           WHERE r.active AND (%(nature)s = '' OR r.nature = %(nature)s) AND (%(kind)s = '' OR r.kind = %(kind)s) AND r.tags @> %(tags)s
             AND (%(team)s::uuid IS NULL OR r.team_id = %(team)s) AND r.capacity >= %(cap)s
             AND NOT EXISTS (SELECT 1 FROM schedule.bookings x WHERE x.event_id = %(skip)s AND x.resource_id = r.id AND x.released_at IS NULL)
           ORDER BY r.name LIMIT 500""",   # tìm người thay cho một lịch: người / phòng đang ở chính lịch đó không phải ứng viên
        {"nature": inp.nature, "kind": inp.kind.strip().lower(), "tags": tags_of(inp.tags), "team": team, "cap": inp.min_capacity,
         "skip": inp.except_event_id})
    ids = [c["id"] for c in cands]
    win, busy = (await _windows(ctx, ids), await _busy(ctx, ids, s, e, inp.except_event_id)) if ids else ({}, set())
    # người có tài khoản đang được mời / đã nhận một lịch khác (họp, hẹn khách, nghỉ — 0158) cũng là bận, dù lịch đó không giữ cứng họ
    if users := [c["user_id"] for c in cands if c["user_id"]]:
        soft = {r["user_id"] for r in await ctx.all("""SELECT DISTINCT user_id FROM schedule.blocks(%s::uuid[], %s::uuid[], tstzrange(%s, %s), %s)
                                                         WHERE NOT hard AND rsvp = 'accepted'""",
                                                     ([], users, s, e, inp.except_event_id))}
        busy |= {c["id"] for c in cands if c["user_id"] in soft}
    outside = {c["id"] for c in cands if not fits(win.get(c["id"], {}), s, e)}
    free = [{k: v for k, v in c.items() if k != "user_id"} | {"tags": ", ".join(c["tags"])} for c in cands if c["id"] not in busy and c["id"] not in outside]
    booked = [c["name"] for c in cands if c["id"] in busy and c["id"] not in outside]
    off_hours = [c["name"] for c in cands if c["id"] in outside]
    window = f"{when(s, e)} {s.astimezone(zone()):%d/%m/%Y}"
    why = " ".join(x for x in (f"Đã có lịch: {_names(booked)}." if booked else "", f"Ngoài giờ rảnh đã khai: {_names(off_hours)}." if off_hours else "") if x)
    empty_actions: list[dict[str, Any]] = []
    if free:
        note = f"Có {len(free)} người / phòng rảnh trong khung giờ này ({window})."
        empty = ""
    elif not cands and not await ctx.val("SELECT count(*) FROM schedule.resources WHERE active"):
        note = ""
        if can_run(ctx.p, "schedule.add_resource"):
            empty, empty_actions = "Chưa có người hay phòng nào. Thêm ở Người & phòng.", [ui.act("schedule.add_resource", "Thêm người / phòng", style="primary")]
        else:
            empty = "Chưa có người hay phòng nào. Nhờ quản lý thêm ở Người & phòng."
    elif not cands:
        note = f"Có 0 người / phòng rảnh trong khung giờ này ({window})."
        empty = "Không có người / phòng nào khớp điều kiện đã chọn. Thử bỏ bớt ở “Thêm điều kiện”."
    else:
        note = f"Có 0 người / phòng rảnh trong khung giờ này ({window})."
        empty = f"Không ai rảnh trong khung giờ này. {why}"
    return ui.table(free, [
        ui.col("name", "Tên", width=220), ui.col("nature", "Người hay phòng", "badge", labels=NATURE), ui.col("kind", "Loại"),
        ui.col("tags", "Thẻ / kỹ năng", width=220), ui.col("capacity", "Sức chứa", "number"), ui.col("team", "Cơ sở / đội"),
    ], title=f"Rảnh {window}", empty=empty or "Không ai / phòng nào rảnh khớp điều kiện", limit=500, empty_actions=empty_actions,
        note=(note + (f" Không rảnh: {why}" if free and why else "") + f" {zone_label(head=True)}. Chỉ xét người / phòng trong phạm vi bạn được xem.").strip())


def _names(names: list[str], most: int = 5) -> str:
    return ", ".join(names[:most]) + (f" và {len(names) - most} nơi khác" if len(names) > most else "")


class AgendaIn(Input):
    days: int = field("Số ngày tới", 7, ge=1, le=60)


@m.query("my_agenda", resource="schedule", title="Lịch của tôi")
async def my_agenda(ctx: Ctx, inp: AgendaIn) -> dict[str, Any]:
    """Lịch có tên tôi (nguồn lực gắn tài khoản của tôi, hoặc tôi là người tham dự chưa từ chối) từ bây giờ tới N ngày — kể cả khi tôi chỉ có phạm
    vi "của mình"."""
    now = datetime.now(zone())
    rows = await _event_rows(ctx, now, datetime.combine(today() + timedelta(days=inp.days), time(0), zone()),
                             """AND e.status = 'booked' AND (EXISTS (SELECT 1 FROM schedule.bookings me WHERE me.event_id = e.id AND me.user_id = core.me()
                                                                     AND me.released_at IS NULL)
                                                         OR EXISTS (SELECT 1 FROM schedule.attendees a WHERE a.event_id = e.id AND a.user_id = core.me()
                                                                    AND a.rsvp <> 'declined'))""", {})
    t = today().isoformat()
    todays = [r for r in rows if r["day"] == t]
    return ui.table(rows, [c for c in EVENT_COLS if c["key"] != "status"], title="Lịch của tôi", empty="Chưa có lịch nào sắp tới", limit=1000,
                    kpis=[ui.kpi("Hôm nay", len(todays)), ui.kpi(f"{inp.days} ngày tới", len(rows))],
                    note=(f"Sớm nhất hôm nay: {todays[0]['when']} — {todays[0]['title']}." if todays else "") + f" {zone_label(head=True)}.",
                    views=ui.views(calendar=ui.calendar_view("day", "title", "line", overdue=False)))


def _say_today(o: Any) -> Line | None:
    n = int(kpi(o, "Hôm nay"))
    if not n:
        return None
    first = next((r for r in (o.get("rows") or []) if r.get("day") == today().isoformat()), None)
    return line("accent", f"Hôm nay có {n} lịch", f"Sớm nhất {first['when']} — {first['title']}" if first else "")


@m.query("event", resource="schedule", title="Một lịch")
async def event(ctx: Ctx, inp: EventRef) -> dict[str, Any]:
    """Thẻ một lịch. Người không được đọc chi tiết (riêng tư, đội khác) mà có quyền xem Lịch ⇒ thẻ "Bận": chỉ giờ, không tên việc / người ngoài / ghi chú."""
    ev = await ctx.one("""SELECT e.*, t.name AS team, coalesce(k.label, '') AS kind_label FROM schedule.events e LEFT JOIN core.teams t ON t.id = e.team_id
                          LEFT JOIN schedule.kinds k ON k.tenant_id = e.tenant_id AND k.key = e.kind WHERE e.id = %s""", (inp.event_id,))
    if ev is None:
        block = await ctx.one("SELECT schedule.busy_span(%s) AS span", (inp.event_id,))
        if block is None or block["span"] is None:
            raise NotFound("Không thấy lịch")
        s0, e0 = block["span"].lower, block["span"].upper
        return ui.card(BUSY, [ui.b_text("Bạn không được xem chi tiết lịch này — chỉ biết khoảng giờ đó đã bận.", "")],
                       subtitle=f"{s0.astimezone(zone()):%d/%m/%Y} {when(s0, e0)}", icon="calendar")
    held = await ctx.all("SELECT resource_id, resource_name FROM schedule.bookings WHERE event_id = %s AND released_at IS NULL ORDER BY resource_name", (ev["id"],))
    gone = await ctx.all("SELECT resource_name, release_reason, released_at FROM schedule.bookings WHERE event_id = %s AND released_at IS NOT NULL ORDER BY released_at",
                         (ev["id"],))
    att = await ctx.all("""SELECT a.user_id, a.rsvp, u.name, u.email FROM schedule.attendees a JOIN core.users u ON u.id = a.user_id WHERE a.event_id = %s
                           ORDER BY u.name""", (ev["id"],))
    pids = [r["party_id"] for r in await ctx.all("SELECT party_id FROM schedule.event_parties WHERE event_id = %s", (ev["id"],))]
    s, e = ev["starts_at"].astimezone(zone()), ev["ends_at"]
    blocks: list[dict[str, Any]] = []
    if held:
        blocks.append(ui.b_list([{"title": h["resource_name"]} for h in held], "Người / phòng"))
    if att:
        blocks.append(ui.b_list([{"title": a["name"], "badge": RSVP[a["rsvp"]], "tone": {"accepted": "ok", "declined": "muted", "tentative": "warn"}.get(a["rsvp"], "default")}
                                 for a in att], "Người tham dự"))
    if pids:
        names = await ctx.call_as_optional("parties.names", ctx.p, party_ids=pids) or {}
        shown_ = [{"title": names[str(p)]["name"]} for p in pids if str(p) in names]
        blocks.append(ui.b_list(shown_, "Bên ngoài") if shown_ else ui.b_text(f"{len(pids)} bên ngoài (bạn không xem được danh mục)", "Bên ngoài"))
    if gone:
        blocks.append(ui.b_list([{"title": g["resource_name"], "subtitle": g["release_reason"], "badge": f"{g['released_at'].astimezone(zone()):%H:%M %d/%m}"}
                                 for g in gone], "Đã nhả chỗ"))
    if ev["note"]:
        blocks.append(ui.b_text(ev["note"], "Ghi chú"))
    if ev["series_id"] and (sr := await ctx.one("SELECT rrule FROM schedule.series WHERE id = %s", (ev["series_id"],))):
        blocks.append(ui.b_text(rrule.describe(rrule.parse(sr["rrule"])) + (" · lần này đã sửa riêng" if ev["detached"] else ""), "Lặp lại"))
    if ev["source"]:
        blocks.append(ui.b_text(f"Lịch do **{ev['source']}** đặt — dời / huỷ ở đây thì module đó nhận tin.", ""))
    actions, opens = [], []
    eid = str(ev["id"])
    mine = next((a for a in att if a["user_id"] == ctx.p.user_id), None)
    if ev["status"] == "booked" and mine and ev["created_by"] != ctx.p.user_id:   # người tổ chức không "trả lời" lịch của chính mình
        actions += [ui.act("schedule.respond", label, {"event_id": eid, "answer": k}, "primary" if k == "accepted" else "default")
                    for k, label in (("accepted", "Tham dự"), ("tentative", "Có thể"), ("declined", "Không tham dự")) if k != mine["rsvp"]]
    # 0240: nút sửa chỉ khi người xem SỬA ĐƯỢC lịch này (lịch của chính mình, hoặc quyền đặt lịch ở đội của lịch) — ô điền sẵn giá trị hiện có
    try:
        await _may_change(ctx, ev)
        may = True
    except Denied:
        may = False
    ez = e.astimezone(zone())
    if ev["status"] == "booked" and may:
        pids_s = [str(p) for p in pids]
        actions += [ui.act("schedule.edit", "Sửa", {"event_id": eid}, "primary",
                           values={"title": ev["title"], "kind": ev["kind"], "note": ev["note"], "visibility": ev["visibility"]}),
                    ui.act("schedule.move", "Đổi giờ", {"event_id": eid}, values={"day": s.date().isoformat(), "start": f"{s:%H:%M}", "end": f"{ez:%H:%M}",
                                                                               **({"end_day": ez.date().isoformat()} if ez.date() != s.date() else {})}),
                    ui.act("schedule.set_attendees", "Người tham dự", {"event_id": eid},
                           values={"attendee_emails": [a["email"] for a in att], "party_ids": pids_s}),
                    ui.act("schedule.cancel", "Xoá lịch", {"event_id": eid}, "destructive")]
        actions += [ui.act("schedule.replace_resource", f"Đổi {h['resource_name']}", {"event_id": eid, "resource_id": str(h["resource_id"])}) for h in held]
    if ev["status"] == "booked":
        opens = [{"title": "Tìm người / phòng rảnh cùng giờ", "query": "schedule.find_free",
                  "input": {"day": s.date().isoformat(), "start": f"{s:%H:%M}", "end": f"{e.astimezone(zone()):%H:%M}",
                            "end_day": e.astimezone(zone()).date().isoformat(), "except_event_id": eid}}]
    organizer = await ctx.val("SELECT name FROM core.users WHERE id = %s", (ev["created_by"],)) if ev["created_by"] and att else None
    sub = " · ".join(x for x in (f"{s:%d/%m/%Y} {when(s, e)}", ev["kind_label"], f"tổ chức: {organizer}" if organizer else "", ev["team"] or "chưa gắn đội", STATUS[ev["status"]],
                                 "riêng tư" if ev["visibility"] == "private" else "") if x)
    return ui.card(ev["title"], blocks, subtitle=sub, icon="calendar", actions=actions, open=opens)


@m.query("resources", resource="schedule", title="Người & phòng")
async def resources(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await ctx.all("""SELECT r.id, r.name, r.nature, r.kind, array_to_string(r.tags, ', ') AS tags, r.capacity, r.slots, t.code AS team_code, t.name AS team,
                                   r.active, u.email AS user_email, r.party_id, r.note,
                                   (SELECT count(*) FROM schedule.availability a WHERE a.resource_id = r.id) AS windows
                            FROM schedule.resources r LEFT JOIN core.teams t ON t.id = r.team_id LEFT JOIN core.users u ON u.id = r.user_id
                            ORDER BY r.active DESC, r.kind, r.name LIMIT 1000""")
    for r in rows:
        r["hours"] = f"{r['windows']} khung / tuần" if r["windows"] else "Không giới hạn"
        r["state"] = "active" if r["active"] else "inactive"
    return ui.table(rows, [
        ui.col("name", "Tên", width=220), ui.col("nature", "Người hay phòng", "badge", labels=NATURE), ui.col("kind", "Loại"), ui.col("tags", "Thẻ / kỹ năng", width=200),
        ui.col("capacity", "Sức chứa", "number"), ui.col("slots", "Lượt cùng lúc", "number"), ui.col("team", "Cơ sở / đội"), ui.col("hours", "Giờ rảnh"),
        ui.col("state", "Trạng thái", "badge", labels={"active": "Đang dùng", "inactive": "Ngừng dùng"}, tones={"active": "green", "inactive": "gray"}),
    ], title="Người & phòng", limit=1000, empty="Chưa có người hay phòng nào. Bấm “Thêm người / phòng / thiết bị” để bắt đầu.",
                    note="Giờ rảnh: chọn một dòng rồi “Giờ rảnh” để khai; không khai thì không giới hạn giờ.")


class ResourceRef(Input):
    resource_id: UUID = field("Người / phòng", hidden=True)


@m.query("resource", resource="schedule", title="Một người / phòng")
async def resource(ctx: Ctx, inp: ResourceRef) -> dict[str, Any]:
    r = await ctx.one("SELECT * FROM schedule.resources WHERE id = %s", (inp.resource_id,))
    if r is None:
        raise NotFound("Không thấy người / phòng này")
    win = await _windows(ctx, [r["id"]])
    hours = [{"title": WEEKDAY[d], "subtitle": ", ".join(f"{s // 60:02d}:{s % 60:02d}–{e // 60:02d}:{e % 60:02d}" for s, e in merged(ws))}
             for d, ws in sorted(win.get(r["id"], {}).items())]
    now = datetime.now(zone())
    rows = await _event_rows(ctx, now, now + timedelta(days=14), """AND e.status = 'booked' AND EXISTS (SELECT 1 FROM schedule.bookings x
                             WHERE x.event_id = e.id AND x.resource_id = %(res)s AND x.released_at IS NULL)""", {"res": r["id"]})
    blocks = [ui.b_list(hours, "Giờ rảnh trong tuần") if hours else ui.b_text("Không giới hạn giờ (chưa khai giờ rảnh).", "Giờ rảnh trong tuần"),
              ui.b_list([{"title": x["title"], "subtitle": x["line"], "badge": f"{date.fromisoformat(x['day']):%d/%m}"} for x in rows], "14 ngày tới")]
    return ui.card(r["name"], blocks, subtitle=f"{NATURE[r['nature']]} · {r['kind']}" + (f" · {', '.join(r['tags'])}" if r["tags"] else ""),
                   icon="person.crop.circle" if r["nature"] == "person" else "door.left.hand.closed",
                   actions=[ui.act("schedule.set_availability", "Đặt giờ rảnh", {"resource_id": str(r["id"])})])


@m.query("resource_options", resource="schedule", title="Chọn người / phòng", creates="schedule.add_resource")
async def resource_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    rows = await ctx.all("SELECT id, name, kind FROM schedule.resources WHERE active ORDER BY name LIMIT 1000")
    return [{"value": str(r["id"]), "label": f"{r['name']} ({r['kind']})"} for r in rows]


@m.query("kind_options", resource="schedule", title="Loại người / phòng")
async def kind_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return ui.options(await ctx.all("SELECT DISTINCT kind AS value, kind AS label FROM schedule.resources ORDER BY kind"))


# Menu theo vai (không viết tên vai): màn nào hiện cho ai do QUYỀN quyết, qua nút lệnh (needs_action) — mọi người có quyền Lịch thấy "Lịch tuần" (phạm vi "của
# mình" thì chỉ lịch có tên mình); "Tìm người / phòng rảnh" và "Tìm giờ cho nhiều người" chỉ người đặt được lịch cho người khác; "Người & phòng", "Loại lịch"
# (thiết lập) chỉ người quản lý, xếp ở nhóm "Thiết lập". Các màn đó không tự ghim cho vai nào.
m.workspace(Workspace("schedule", "Lịch làm việc", "calendar", (
    # 0240: màn chính là LỊCH kiểu app lịch (Ngày mặc định — hôm nay theo giờ, người cạnh nhau; Tuần; Lịch biểu). Khoá màn giữ "schedule_week" để màn đã
    # ghim / bộ menu theo vai không đổi; bảng "Lịch tuần" (schedule.week) vẫn là truy vấn cho trợ lý / xuất Excel.
    # MỘT cách tạo lịch (rà UI 21/11): nút "Thêm lịch" của khối lịch (ô nổi → "Thêm chi tiết…" mở form đầy đủ) + ô gõ câu máy điền hộ — bỏ nút "Đặt lịch chi tiết" trùng việc
    Screen("schedule_week", "Lịch", "calendar", "schedule.calendar", tint="orange"),
    Screen("schedule_free", "Tìm người / phòng rảnh", "person.badge.clock", "schedule.find_free", tint="green", needs_action=True, feature=USE_RESOURCES, pinnable=False,
           actions=(A("schedule.create"),),
           open=Open("schedule.resource", {"resource_id": "id"}, "Xem lịch người / phòng")),
    Screen("schedule_time", "Tìm giờ cho nhiều người", "clock.badge.checkmark", "schedule.find_time", tint="green", needs_action=True, feature=USE_RESOURCES, pinnable=False,
           actions=(A("schedule.create"),),
           row_actions=(A("schedule.create", "Đặt giờ này", prefill={"day": "day", "start": "start", "end": "end", "attendee_emails": "attendee_emails",
                                                                      "resource_ids": "resource_ids"}),)),
    Screen("schedule_resources", "Người & phòng", "person.2", "schedule.resources", tint="blue", group="Thiết lập", needs_action=True, feature=USE_RESOURCES, pinnable=False,
           actions=(A("schedule.add_resource", style="primary"),),
           row_actions=(A("schedule.update_resource", "Sửa", bind={"resource_id": "id"},
                          prefill={"name": "name", "nature": "nature", "kind": "kind", "tags": "tags", "capacity": "capacity", "slots": "slots", "team_code": "team_code",
                                   "user_email": "user_email", "party_id": "party_id", "note": "note", "active": "active"}),
                        A("schedule.set_availability", "Giờ rảnh", bind={"resource_id": "id"})),
           open=Open("schedule.resource", {"resource_id": "id"}, "Xem")),
    Screen("schedule_kinds", "Loại lịch", "tag", "schedule.kinds", tint="blue", group="Thiết lập", needs_action=True, feature=USE_RESOURCES, pinnable=False,
           actions=(A("schedule.save_kind", style="primary"),),
           row_actions=(A("schedule.save_kind", "Sửa", prefill={"key": "key", "label": "label", "active": "active", "sort": "sort", "tone": "tone"}),)),
    Screen("schedule_agenda", "Lịch của tôi", "calendar.badge.clock", "schedule.my_agenda", tint="orange",
           row_click="open", open=Open("schedule.event", {"event_id": "id"}, "Xem lịch"), desk=Desk("do", say=_say_today, rank=30)),
), order=45))

from barista.modules.schedule import calendar, imported, view  # noqa: E402,F401 — đăng ký thao tác của phần lịch (loại lịch, trả lời mời, tìm giờ, mốc hạn) + nạp lịch từ hệ khác (0179)
