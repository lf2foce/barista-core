"""GỌI VIDEO (quyết định 0114) — cuộc gọi GẮN VÀO một đối tượng công việc hoặc phòng trao đổi, kết quả thành dữ liệu của đối tượng đó.

* Không dựng lại thứ công ty đã có: link là Google Meet / Zoom của công ty (dán tay hoặc cấu hình sẵn), không cần tài khoản hay hạ tầng nào.
* **Ai gọi / tham gia được** = ai thấy đối tượng, kiểm ở DB lúc truy cập (``core.subject_visible``), không chép quyền sang đây.
* **Nhật ký người tham dự** chỉ thêm và ghi rõ nguồn. Nguồn duy nhất hiện nay là BẤM NÚT: biết ai đã bấm, không biết ai thật sự ở trong cuộc gọi,
  không thấy người vào thẳng bằng link. Không đoán số phút khi thiếu lần bấm rời: ``None`` nghĩa là không đo được, không phải 0.
* Kết thúc ⇒ sự kiện ``meet.call_ended`` mang số liệu tham dự cho gói khác phản ứng. Xác nhận ai đã dự là việc của người; trợ lý chỉ soạn thẻ.
"""

import base64
import hashlib
import hmac
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, LiteralString, Protocol
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from psycopg.types.json import Jsonb

from barista.api import (
    Conflict,
    Ctx,
    Denied,
    Input,
    Invalid,
    Module,
    NoInput,
    NotFound,
    Open,
    Pack,
    Screen,
    field,
    links,
    ratelimit,
    ui,
    web,
    zone,
)
from barista.config import settings
from barista.kernel import registry

Pack("meet", "Gọi video",
     "Cuộc gọi gắn vào hồ sơ / khách / phòng trao đổi, dùng Google Meet hoặc Zoom sẵn có của công ty; ghi lại ai đã bấm tham gia",
     grants=(("admin", "meet", "all", "read write"), ("admin", "meet.settings", "all", "read manage"),
             ("director", "meet", "all", "read write"), ("director", "meet.settings", "all", "read manage"),
             ("office", "meet", "team", "read write"), ("staff", "meet", "team", "read write"),
             ("team_lead", "meet", "team", "read write"), ("sale", "meet", "own", "read write"),
             ("sales_admin", "meet", "team", "read write"), ("coordinator", "meet", "team", "read write"),),
     kind="capability",
     rules="""- CUỘC GỌI (meet): danh sách người tham dự chỉ là những người đã BẤM nút trong app, không chứng minh họ ở trong cuộc gọi và không có người vào thẳng bằng link. Khi nói về
  ai đã dự, nói rõ "theo lần bấm" và đừng suy ra số phút. Xác nhận người tham dự (meet.confirm_attendance) chỉ soạn thành thẻ để người bấm, không tự kết luận ai đã dự.
""")

m = Module("meet", "Gọi video", pack="meet", resources={"meet": "Cuộc gọi gắn vào đối tượng", "meet.settings": "Cấu hình gọi video của công ty"},
           setup=("meet.settings",))


# ── Nhà cung cấp: MỘT giao diện. Thêm nhà cung cấp = thêm một lớp vào PROVIDERS (tạo phòng bằng API, ghi tham dự thật — xem 0114 "Khi nào làm tiếp") ──

class Provider(Protocol):
    key: str

    def join_url(self, *, configured: str, pasted: str) -> str:
        """Link tham gia cho một cuộc gọi mới. ``configured``: link công ty cấu hình sẵn, ``pasted``: link người gọi dán lúc này."""
        ...


def clean_url(raw: str) -> str:
    """Link người dán rồi cả công ty sẽ bấm: chỉ nhận https, phải có tên máy, không nhúng tài khoản / mật khẩu, không có khoảng trắng."""
    url = raw.strip()
    try:
        parts = urlsplit(url)
        host, user, secret = parts.hostname, parts.username, parts.password
    except ValueError as e:
        raise Invalid("Link cuộc gọi không hợp lệ") from e
    if parts.scheme != "https" or not host or user or secret or any(c.isspace() for c in url):
        raise Invalid("Link cuộc gọi phải là địa chỉ https đầy đủ, vd https://meet.google.com/abc-defg-hij")
    return url


class External:
    """Google Meet / Zoom của chính công ty: không tạo gì ở phía nhà cung cấp, không cần khoá, chỉ giữ link. Link dán lúc gọi thắng link cấu hình sẵn."""
    key = "external"

    def join_url(self, *, configured: str, pasted: str) -> str:
        if pasted.strip():
            return clean_url(pasted)
        if configured:
            return configured
        raise Invalid("Chưa có link cuộc gọi: dán link Google Meet / Zoom, hoặc nhờ quản trị đặt link phòng của công ty")


class Direct:
    """Gọi trực tiếp 1–1 trong trình duyệt (WebRTC ngang hàng, 0114 bổ sung 07/11): không link ngoài, trang gọi là của chính bản cài."""
    key = "direct"

    def join_url(self, *, configured: str, pasted: str) -> str:
        return ""


PROVIDERS: dict[str, Provider] = {External.key: External(), Direct.key: Direct()}
DEFAULT_PROVIDER = External.key
DIRECT = Direct.key


# ── Số liệu tham dự: tính từ nhật ký, chưa bao giờ đoán ──

@dataclass
class Person:
    user_id: UUID
    first_join_at: datetime | None = None
    last_leave_at: datetime | None = None
    seconds: int | None = None   # None = không đo được (có lần bấm tham gia mà không có lần bấm rời)


def tally(rows: list[dict[str, Any]]) -> list[Person]:
    """Gộp nhật ký (đã xếp theo thứ tự ghi) theo từng người. Chỉ cộng các cặp tham gia → rời; thiếu lần rời ở bất kỳ đâu thì cả người đó là "không đo được":
    cộng phần còn lại sẽ ra một con số trông như thật mà thiếu."""
    people: dict[UUID, Person] = {}
    opened: dict[UUID, datetime] = {}
    total: dict[UUID, float] = {}
    unknown: set[UUID] = set()
    for r in rows:
        uid, at = r["user_id"], r["at"]
        p = people.setdefault(uid, Person(uid))
        if r["event"] == "join":
            if uid in opened:
                unknown.add(uid)
            opened[uid] = at
            p.first_join_at = p.first_join_at or at
        else:
            p.last_leave_at = at
            if uid in opened:
                total[uid] = total.get(uid, 0.0) + (at - opened.pop(uid)).total_seconds()
    unknown |= set(opened)
    for uid, p in people.items():
        p.seconds = None if uid in unknown or uid not in total else int(total[uid])
    return list(people.values())


@dataclass
class End:
    """Một đầu của cuộc gọi trực tiếp (một ghế) với thời gian có mặt THEO MÁY BÁO VỀ."""
    seat: str
    user_id: UUID | None = None
    invite_id: UUID | None = None
    party_id: UUID | None = None
    name: str = ""
    first_at: datetime | None = None
    last_at: datetime | None = None
    seconds: int | None = None   # None = không đo được (chưa có báo nào, hoặc còn một lần "nối được" chưa có lần mất nối / kết thúc đi kèm)
    open: bool = False           # đang nối theo lần báo gần nhất
    reported: bool = False


def presence_seconds(rows: list[dict[str, Any]]) -> dict[Any, tuple[datetime | None, datetime | None, int | None, bool]]:
    """Gộp báo có mặt (theo thứ tự ghi) theo người (``who``: thành viên hoặc lời mời) ⇒ (lần nối đầu, lần rời cuối, số giây, đang nối). Chỉ cộng các khoảng "nối được → mất nối / kết thúc".
    Giờ tính là giờ MÁY CHỦ nhận; giờ máy báo (``client_at``) chỉ RÚT NGẮN một khoảng khi máy báo mất nối muộn (vd mở lại sau khoá màn hình), và
    không bao giờ trước lúc khoảng đó mở. Báo "nối được" lặp lại khi đang nối là cùng một khoảng. Còn khoảng mở ⇒ None, không đoán."""
    state: dict[str, dict[str, Any]] = {}
    for r in rows:
        s = state.setdefault(r["who"], {"first": None, "last": None, "total": 0.0, "open": None})
        if r["event"] == "connected":
            if s["open"] is None:
                s["open"] = r["at"]
            s["first"] = s["first"] or r["at"]
        elif s["open"] is not None:
            close = r["at"]
            if (ca := r.get("client_at")) is not None and s["open"] <= ca < close:
                close = ca
            s["total"] += (close - s["open"]).total_seconds()
            s["open"], s["last"] = None, close
    return {who: (s["first"], s["last"], None if s["open"] is not None or s["first"] is None else int(s["total"]), s["open"] is not None)
            for who, s in state.items()}


async def _ends(ctx: Ctx, call_id: UUID) -> list[End]:
    """Hai đầu của cuộc gọi trực tiếp (ghế đã có người) + thời gian có mặt từ ``meet.presence``. Đọc bằng quyền người hỏi (RLS)."""
    seats = await ctx.all("""SELECT s.seat, s.user_id, s.invite_id, i.party_id, coalesce(u.name, i.name, '') AS name FROM meet.seats s
                             LEFT JOIN core.users u ON u.id = s.user_id LEFT JOIN meet.invites i ON i.id = s.invite_id
                             WHERE s.call_id = %s ORDER BY s.seat""", (call_id,))
    got = presence_seconds(await ctx.all("""SELECT coalesce(user_id, invite_id) AS who, event, at, client_at FROM meet.presence
                                             WHERE call_id = %s ORDER BY id""", (call_id,)))
    out = []
    for s in seats:
        who = s["user_id"] or s["invite_id"]
        first, last, secs, is_open = got.get(who, (None, None, None, False))
        out.append(End(s["seat"], s["user_id"], s["invite_id"], s["party_id"], s["name"], first, last, secs, is_open, who in got))
    return out


@dataclass
class Side:
    """Một đầu của cuộc gọi 1–1 như SỰ THẬT trung tính (không theo người xem): ai, đã vào chưa, đang ở trong không."""
    user_id: UUID | None
    name: str
    joined: bool                  # đã bấm Vào cuộc gọi, hoặc máy đã báo "nối được" ít nhất một lần
    here: bool                    # đang ở trong: máy báo đang nối, hoặc (chưa có báo nào từ máy) lần bấm gần nhất là vào
    first: datetime | None        # lần vào đầu tiên
    last_click: str = ""          # "join" / "leave" / "" — lần bấm gần nhất của thành viên (người ngoài không bấm)


@dataclass
class Facts:
    caller: Side                  # ghế a: người bắt đầu
    callee: Side | None           # ghế b: người được gọi (thành viên hoặc người ngoài được mời); chưa có ⇒ None


async def _facts(ctx: Ctx, call_id: UUID) -> Facts | None:
    """Sự thật trung tính của cuộc gọi trực tiếp, đọc bằng quyền người hỏi (RLS). Không có ghế a ⇒ None."""
    clicks = {r["user_id"]: r for r in await ctx.all(
        """SELECT user_id, (array_agg(event ORDER BY id DESC))[1] AS last, min(at) FILTER (WHERE event = 'join') AS first
           FROM meet.participants WHERE call_id = %s GROUP BY user_id""", (call_id,))}
    sides: dict[str, Side] = {}
    for e in await _ends(ctx, call_id):
        k = clicks.get(e.user_id) if e.user_id else None
        last = k["last"] if k else ""
        firsts = [t for t in (k["first"] if k else None, e.first_at) if t is not None]
        sides[e.seat] = Side(e.user_id, e.name, bool(firsts), e.open if e.reported else last == "join", min(firsts) if firsts else None, last)
    return Facts(sides["a"], sides.get("b")) if "a" in sides else None


def _declined(c: dict[str, Any], f: Facts, *, by: UUID | None = None, reason: str = "person") -> bool:
    """Người được gọi đã TỪ CHỐI: chính họ kết thúc (nút "Từ chối" dùng đường kết thúc có sẵn) khi chưa từng vào."""
    who = by if by is not None else c["ended_by"]
    return bool(f.callee and f.callee.user_id and not f.callee.joined and reason == "person" and who == f.callee.user_id)


def ring_view(c: dict[str, Any], f: Facts, role: str) -> tuple[str, str, str]:
    """Chữ trên thẻ cuộc gọi 1–1 theo NGƯỜI XEM (``role``: caller / callee / other) và trạng thái THẬT của cuộc gọi ⇒ (tên thẻ, trạng thái, mã trạng thái).
    Mã: ringing (người được gọi chưa vào) · live · declined · missed (kết thúc mà người được gọi chưa từng vào) · ended."""
    assert f.callee is not None
    a, b = f.caller.name, f.callee.name
    if c["status"] == "active":
        if not b or not f.callee.joined:
            head = {"callee": f"{a} gọi cho bạn", "caller": f"Bạn đang gọi {b}"}.get(role, f"{a} đang gọi {b}")
            status = "Đang chờ bạn vào" if role == "callee" else f"Chờ {b} vào"
            return head, status, "ringing"
        here = sum(s.here for s in (f.caller, f.callee))
        began = max(s.first for s in (f.caller, f.callee) if s.first)
        head = {"callee": f"Cuộc gọi với {a}", "caller": f"Cuộc gọi với {b}"}.get(role, f"{a} đang gọi {b}")
        return head, f"Đang diễn ra · {f'{here} người' if here else 'chưa có ai trong cuộc gọi'} · từ {began.astimezone(zone()):%H:%M}", "live"
    if not f.callee.joined:
        head = {"callee": f"{a} đã gọi cho bạn", "caller": f"Bạn đã gọi {b}"}.get(role, f"{a} đã gọi {b}")
        if _declined(c, f):
            return head, "Bạn đã từ chối" if role == "callee" else f"{b} đã từ chối", "declined"
        if c["end_reason"] == "stale":
            return head, "Cuộc gọi nhỡ" if role == "callee" else f"Hết hạn, {b} chưa vào", "missed"
        return head, {"callee": "Cuộc gọi nhỡ", "caller": "Bạn đã huỷ cuộc gọi"}.get(role, f"{a} đã huỷ cuộc gọi"), "missed"
    head = {"callee": f"Cuộc gọi với {a}", "caller": f"Cuộc gọi với {b}"}.get(role, f"{a} đã gọi {b}")
    return head, f"Đã kết thúc · {_when(c)}", "ended"


async def _log(ctx: Ctx, call_id: UUID) -> list[dict[str, Any]]:
    return await ctx.all("SELECT user_id, event, at FROM meet.participants WHERE call_id = %s ORDER BY id", (call_id,))


async def _subject(ctx: Ctx, kind: str, sid: UUID) -> dict[str, Any] | None:
    """Đối tượng như người gọi thấy — None nếu không thấy / loại lạ / gói của đối tượng đã tắt."""
    if kind not in registry.SUBJECTS:
        return None
    try:
        return await ctx.call_as(registry.SUBJECTS[kind][1], ctx.p, subject_id=sid)
    except (NotFound, Denied):
        return None


async def _call(ctx: Ctx, call_id: UUID, *, lock: bool = False) -> dict[str, Any]:
    c = await ctx.one("SELECT * FROM meet.calls WHERE id = %s FOR UPDATE" if lock else "SELECT * FROM meet.calls WHERE id = %s", (call_id,))
    if c is None:
        raise NotFound("Không thấy cuộc gọi (hoặc bạn không xem được đối tượng của cuộc gọi này)")
    return c


async def _click(ctx: Ctx, call_id: UUID, event: str) -> None:
    await ctx.exec("INSERT INTO meet.participants (call_id, user_id, event, source) VALUES (%s, core.me(), %s, 'click')", (call_id, event))


def _host(url: str) -> str:
    return urlsplit(url).hostname or ""


# ── Lệnh ──

class SubjectIn(Input):
    subject_kind: str = field("Loại đối tượng", hidden=True)
    subject_id: UUID = field("Đối tượng", hidden=True)


ROOM = "talk.room"   # loại đối tượng của gói Trao đổi (chỉ là chuỗi — 0114: gói này không nhập module kia)
PROVIDER_LABEL = {"direct": "Gọi trực tiếp trong trình duyệt (2 người)", "external": "Link Google Meet / Zoom"}


class StartIn(SubjectIn):
    title: str = field("Tên cuộc gọi", "", max_length=200, help="Trống = tên hồ sơ / phòng")
    provider: Literal["", "direct", "external"] = field("Cách gọi", "", options=PROVIDER_LABEL,
                                                         help="Trống = có link (dán hoặc phòng công ty) thì dùng link, chưa có thì gọi trực tiếp")
    # không chép vào nhật ký thao tác: link Zoom có thể kèm mật khẩu phòng
    join_url: str = field("Link cuộc gọi", "", max_length=500, audit=False, example="https://meet.google.com/…",
                          help="Dán link Google Meet / Zoom. Trống = link phòng công ty đã cấu hình")
    with_member: str = field("Gọi trực tiếp với", "", options_query="admin.member_options",
                             help="Gọi trực tiếp: người trong công ty sẽ vào chỗ thứ hai. Trống = người phụ trách hồ sơ (nếu không phải mình), hoặc mời người ngoài sau")


async def _on_calendar(ctx: Ctx, call_id: UUID, title: str, starts: datetime, ends: datetime, people: list[UUID]) -> None:
    """0158: cuộc gọi chiếm giờ của người gọi + người được gọi / người đã vào ⇒ MỘT lịch loại họp trên Lịch (người tham dự = những người đó; riêng tư:
    tên cuộc gọi có thể là tên khách của hồ sơ). Lúc gọi: tạm một giờ; kết thúc ⇒ đúng giờ kết thúc. Gói Lịch tắt ⇒ bỏ qua — không làm hỏng cuộc gọi."""
    if not people:
        return
    try:
        await ctx.call_system_optional("schedule.book", source="meet.call", source_ref=str(call_id), title=title or "Cuộc gọi", starts_at=starts, ends_at=ends,
                                       team_id=await ctx.home_team(people[0]), kind="meeting", kind_label="Họp nội bộ", attendee_user_ids=people,
                                       accepted_user_ids=people[:1], visibility="private", notify=False)
    except (Conflict, NotFound):
        pass


def call_page(call_id: UUID | str, *, enter: bool = False, video: bool = False) -> str:
    """Trang gọi trực tiếp trên web (đường tương đối — web mở trong chính nó). ``enter``: trang vào cuộc gọi ngay, không chờ bấm thêm lần nữa (người
    vừa bấm Gọi / Tham gia đã nói rõ ý); ``video``: trang hiện một nút "Bật camera" rõ ràng (camera không bao giờ tự bật; mặc định chỉ tiếng — mỗi bên bật / tắt được trong cuộc gọi)."""
    return f"/goi/{call_id}" + (("?auto=1" + ("&video=1" if video else "")) if enter else "")


def page_url(call_id: UUID | str, *, enter: bool = False, video: bool = False) -> str:
    """Địa chỉ đầy đủ của trang gọi — app Mac mở bằng trình duyệt mặc định (không chạy WebRTC trong app)."""
    return settings.web_url.rstrip("/") + call_page(call_id, enter=enter, video=video)


@m.command("start", resource="meet", title="Bắt đầu cuộc gọi", agent="propose", shows=("meet.call", "call_id"))
async def start(ctx: Ctx, inp: StartIn) -> dict[str, Any]:
    """Mở cuộc gọi trên một đối tượng người gọi thấy được; đang có cuộc gọi thì vào chung cuộc đó. Trợ lý chỉ soạn thẻ, người bấm mới gọi.
    Biểu mẫu đầy đủ (tên, cách gọi, link, người gọi cùng) dành cho quản trị / API; nút Gọi hằng ngày dùng ``meet.ring`` (một chạm — 0114 ghi chú 07/11)."""
    return await _start(ctx, inp.subject_kind, inp.subject_id, provider=inp.provider, join_url=inp.join_url, title=inp.title, with_member=inp.with_member)


async def _start(ctx: Ctx, kind: str, sid: UUID, *, provider: str = "", join_url: str = "", title: str = "", with_member: str = "",
                 enter: bool = False, video: bool = False) -> dict[str, Any]:
    subj = await _subject(ctx, kind, sid)
    if subj is None:
        raise NotFound("Không thấy đối tượng này (hoặc bạn không có quyền xem)")
    room_url = await ctx.val("SELECT room_url FROM meet.settings") or ""
    provider = provider or (External.key if join_url.strip() or room_url else DIRECT)
    url = PROVIDERS[provider].join_url(configured=room_url, pasted=join_url)
    # chỗ thứ hai thuộc về người ĐÃ ĐỊNH: người được chọn, không thì người phụ trách hồ sơ (vd người kia của tin riêng) nếu không phải mình
    callee: UUID | None = None
    callee_name = ""
    if provider == DIRECT:
        callee = await _member(ctx, with_member) if with_member.strip() else subj.get("owner_id")
        if callee == ctx.p.user_id:
            callee = None
        if callee:
            callee_name = await ctx.val("SELECT name FROM core.users WHERE id = %s", (callee,)) or ""
    # Tên lưu là sự thật TRUNG TÍNH, không theo người xem: tin riêng thì tên luồng là tên người kia (người nhận sẽ đọc thấy tên mình) nên đặt "A gọi B";
    # thẻ hiện chữ theo người xem được tính lúc đọc (``call``)
    title = title.strip() or (f"{ctx.p.name} gọi {callee_name}" if callee_name and kind == "talk.direct" else subj["title"])
    cid = await ctx.val("""INSERT INTO meet.calls (subject_kind, subject_id, title, provider, join_url, started_by)
                           VALUES (%s, %s, %s, %s, %s, core.me())
                           ON CONFLICT (tenant_id, subject_kind, subject_id) WHERE status = 'active' DO NOTHING RETURNING id""",
                        (kind, sid, title, provider, url))
    if cid is None:   # có người gọi trước: vào chung cuộc đó, cách gọi / link của họ thắng
        c = await ctx.one("SELECT * FROM meet.calls WHERE subject_kind = %s AND subject_id = %s AND status = 'active'", (kind, sid))
        if c is None:
            raise Conflict("Cuộc gọi vừa kết thúc, bấm Gọi lại")
        out = await _join(ctx, c, enter=enter, video=video)
        return {**out, "already": True, "summary": f"Đang có cuộc gọi “{c['title']}”, anh/chị vào chung"}
    mention: list[str] = []
    if provider == DIRECT:
        await ctx.exec("INSERT INTO meet.seats (call_id, seat, user_id) VALUES (%s, 'a', core.me())", (cid,))
        if callee:
            await _seat_member(ctx, cid, kind, sid, callee)
            if kind != "talk.direct":   # tin riêng đã báo người kia; hồ sơ / phòng: nhắc tên để người được gọi nhận thông báo
                mention = [await ctx.val("SELECT email FROM core.users WHERE id = %s", (callee,))]
    await _click(ctx, cid, "join")
    now = datetime.now(zone())
    await _on_calendar(ctx, cid, title, now, now + timedelta(hours=1), [x for x in (ctx.p.user_id, callee) if x])
    # chữ trong tin có thể nêu tên cả hai người (tên không theo người xem); chữ "gọi cho bạn" / "bạn đang gọi" chỉ tính lúc đọc thẻ
    body = (f"{ctx.p.name} gọi{' video' if video else ''} {callee_name}" + ("" if kind == "talk.direct" else f" · {title}") if callee_name
            else f"{ctx.p.name} bắt đầu cuộc gọi{' video' if video else ''}: {title}")
    try:   # gói Trao đổi là tuỳ chọn; đăng thẻ hỏng không được làm hỏng cuộc gọi
        await ctx.call_optional("talk.post", subject_kind=kind, subject_id=sid, mentions=mention, body=body,
                                card={"query": "meet.call", "input": {"call_id": str(cid)}, "title": f"Cuộc gọi: {title}"})
    except (Invalid, NotFound):
        pass
    if provider == DIRECT:
        return {"call_id": cid, "already": False, "seat": "a", "call_page": call_page(cid, enter=enter, video=video),
                "once_open_url": page_url(cid, enter=enter, video=video), "summary": f"Bắt đầu cuộc gọi trực tiếp “{title}”"}
    return {"call_id": cid, "already": False, "once_open_url": url, "summary": f"Bắt đầu cuộc gọi “{title}”"}


class RingIn(SubjectIn):
    """Nút Gọi một chạm (0114 ghi chú 07/11). Các ô dưới đây chỉ hiện khi người phải CHỌN (phòng nhiều người); nút một chạm điền sẵn hết ⇒ chạy luôn."""
    callee: str = field("Gọi ai?", "", options_query="meet.callee_options", options_with=("subject_kind", "subject_id"),
                        help="Chọn người cần gọi. Muốn gọi bằng link Meet / Zoom thì mở “Tuỳ chọn khác”. Trống = người kia của tin riêng / người phụ trách hồ sơ")
    video: bool = field("Gọi video", False, help="Trang gọi hiện nút “Bật camera” ngay khi vào (camera chỉ bật khi bấm). Trong cuộc gọi mỗi bên vẫn bật / tắt camera và micro được")
    via_link: bool = field("Bằng link của công ty", False, hidden=True, help="Gọi cả phòng bằng link Google Meet / Zoom công ty đã đặt (nút điền sẵn)")
    # không chép vào nhật ký thao tác: link Zoom có thể kèm mật khẩu phòng
    join_url: str = field("Link Google Meet / Zoom", "", max_length=500, audit=False, group="Tuỳ chọn khác", example="https://meet.google.com/…",
                          help="Chỉ khi muốn gọi bằng link ngoài thay vì gọi trực tiếp trong app")


@m.command("ring", resource="meet", title="Gọi", agent="propose", shows=("meet.call", "call_id"))
async def ring(ctx: Ctx, inp: RingIn) -> dict[str, Any]:
    """GỌI VIDEO / gọi thoại cho một người cần ĐỐI TƯỢNG cuộc gọi (subject_kind + subject_id): tin riêng với người đó, phòng, hoặc hồ sơ. Chưa có tin riêng ⇒ soạn
    talk.start_direct trước (người bấm xong mới có tin riêng để gọi) rồi gọi theo subject_id của nó — đừng đoán subject_id. Gọi MỘT chạm: người được gọi do SERVER quyết (người kia của tin riêng, người phụ trách hồ sơ, người chọn ở phòng); trang gọi vào ngay, tên cuộc
    gọi = tên phòng / hồ sơ. Có link (dán ở Tuỳ chọn khác hoặc ``via_link``) ⇒ gọi bằng link ngoài như ``meet.start``. Trợ lý chỉ soạn thẻ (như ``meet.start``):
    bấm gọi là việc của người, tiếng / hình đi thẳng giữa hai máy và thông báo tới người được gọi."""
    if inp.via_link or inp.join_url.strip():
        return await _start(ctx, inp.subject_kind, inp.subject_id, provider=External.key, join_url=inp.join_url)
    subj = await _subject(ctx, inp.subject_kind, inp.subject_id)
    if subj is None:
        raise NotFound("Không thấy đối tượng này (hoặc bạn không có quyền xem)")
    if inp.callee.strip().lower() == ctx.p.email.lower():   # tự gọi mình: không ai vào được ghế thứ hai, cuộc gọi treo chặn người khác gọi tới 12 giờ
        raise Invalid("Không gọi cho chính mình được — chọn người khác")
    if not inp.callee.strip() and subj.get("owner_id") in (None, ctx.p.user_id):
        raise Invalid("Chọn người cần gọi (hoặc dán link ở Tuỳ chọn khác)")
    return await _start(ctx, inp.subject_kind, inp.subject_id, provider=DIRECT, with_member=inp.callee, enter=True, video=inp.video)


async def _callable(ctx: Ctx, kind: str, sid: UUID) -> list[dict[str, Any]]:
    """Người gọi được cùng một đối tượng (không kể mình): người trong phòng / người kia của tin riêng / người xem được hồ sơ. Chỉ để chọn."""
    if kind == ROOM:
        try:
            return await ctx.call_optional("talk.room_people", room_id=sid) or []
        except (NotFound, Denied):
            return []
    if kind == "talk.direct":
        return await ctx.all("""SELECT u.email, u.name FROM core.users u WHERE u.id = %s""", ((await _subject(ctx, kind, sid) or {}).get("owner_id"),))
    # hồ sơ: đúng danh sách thành viên công ty mà ai cũng thấy (như ``admin.member_options``) — KHÔNG lọc theo ai xem được hồ sơ, vì danh sách lọc sẽ lộ
    # đồng nghiệp nào xem được hồ sơ này; chọn người không xem được ⇒ ``meet.ring`` báo lỗi lúc chạy (như @nhắc tên của Trao đổi). Không có trần: không bỏ sót ai.
    return await ctx.all("""SELECT u.email, u.name FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant()
                            WHERE mb.active AND u.id <> core.me() ORDER BY u.name""")


class CalleeIn(SubjectIn):
    pass


@m.query("callee_options", resource="meet", title="Người gọi cùng được")
async def callee_options(ctx: Ctx, inp: CalleeIn) -> list[dict[str, str]]:
    """Ô "Gọi ai?": người trong phòng, hoặc thành viên công ty (hồ sơ) — danh sách ai cũng thấy, không lộ ai xem được hồ sơ. Lệnh kiểm lại người được chọn."""
    if await _subject(ctx, inp.subject_kind, inp.subject_id) is None:
        return []
    return ui.options([{"value": r["email"], "label": r["name"]} for r in await _callable(ctx, inp.subject_kind, inp.subject_id)])


class CallRef(Input):
    call_id: UUID = field("Cuộc gọi", hidden=True)


async def _take_seat(ctx: Ctx, c: dict[str, Any]) -> str:
    """Ghế của người bấm trong cuộc gọi trực tiếp. Chỉ hai người ĐÃ ĐỊNH ngồi được (người bắt đầu + người thứ hai được chọn / được mời): người khác
    thấy cuộc gọi không tự chiếm chỗ. Gọi nhóm cần máy chủ media nên không có ghế thứ ba."""
    mine = await ctx.val("SELECT meet.my_seat(%s)", (c["id"],))
    if mine:
        return mine
    if await ctx.val("SELECT 1 FROM meet.seats WHERE call_id = %s AND seat = 'b'", (c["id"],)):
        raise Conflict("Cuộc gọi 1–1 đã đủ người")
    raise Conflict("Cuộc gọi 1–1 này chưa mời anh/chị — người gọi chọn người thứ hai (Chọn người gọi cùng) hoặc mời người ngoài")


async def _member(ctx: Ctx, email: str) -> UUID:
    uid = await ctx.val("""SELECT u.id FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant()
                           WHERE u.email = %s AND mb.active""", (email.strip().lower(),))
    if uid is None:
        raise NotFound(f"Không có người {email} trong công ty")
    return uid


async def _sees_subject(ctx: Ctx, user_id: UUID, kind: str, sid: UUID) -> bool:
    """Người này (quyền nạp lại từ DB) còn thấy đối tượng không — cùng câu hỏi RLS ``p_subject`` đặt cho chính họ."""
    who = await ctx.principal_of(user_id)
    return who is not None and await _subject_as(ctx, who, kind, sid) is not None


async def _subject_as(ctx: Ctx, who: Any, kind: str, sid: UUID) -> dict[str, Any] | None:
    if kind not in registry.SUBJECTS:
        return None
    try:
        return await ctx.call_as(registry.SUBJECTS[kind][1], who, subject_id=sid)
    except (NotFound, Denied):
        return None


async def _seat_member(ctx: Ctx, call_id: UUID, kind: str, sid: UUID, user_id: UUID) -> None:
    if not await _sees_subject(ctx, user_id, kind, sid):
        raise Invalid("Người này không xem được hồ sơ / phòng của cuộc gọi nên không gọi cùng được")
    got = await ctx.val("INSERT INTO meet.seats (call_id, seat, user_id) VALUES (%s, 'b', %s) ON CONFLICT DO NOTHING RETURNING seat", (call_id, user_id))
    if got is None:
        raise Conflict("Cuộc gọi 1–1 đã đủ người")


async def _join(ctx: Ctx, c: dict[str, Any], *, enter: bool = False, video: bool = False) -> dict[str, Any]:
    if c["status"] != "active":
        raise Conflict("Cuộc gọi đã kết thúc")
    if c["provider"] == DIRECT:
        seat = await _take_seat(ctx, c)
        await _click(ctx, c["id"], "join")
        return {"call_id": c["id"], "seat": seat, "call_page": call_page(c["id"], enter=enter, video=video),
                "once_open_url": page_url(c["id"], enter=enter, video=video),
                "summary": f"Vào cuộc gọi trực tiếp “{c['title']}”"}
    await _click(ctx, c["id"], "join")
    return {"call_id": c["id"], "once_open_url": c["join_url"], "summary": f"Đã ghi anh/chị bấm tham gia “{c['title']}”"}


class JoinIn(CallRef):
    enter: bool = field("Vào ngay", False, hidden=True, help="Gọi trực tiếp: trang gọi vào cuộc gọi ngay, không chờ bấm thêm (nút Tham gia trên thẻ đặt sẵn)")
    video: bool = field("Bật camera", False, hidden=True)


@m.command("join", resource="meet", title="Tham gia cuộc gọi", agent="deny", shows=("meet.call", "call_id"))
async def join(ctx: Ctx, inp: JoinIn) -> dict[str, Any]:
    """Ghi lần bấm rồi đưa link (gọi trực tiếp: giữ ghế rồi đưa trang gọi). Trợ lý không làm thay được: "ai đã bấm tham gia" phải là chính người đó."""
    return await _join(ctx, await _call(ctx, inp.call_id), enter=inp.enter, video=inp.video)


@m.command("leave", resource="meet", title="Rời cuộc gọi", agent="deny")
async def leave(ctx: Ctx, inp: CallRef) -> dict[str, Any]:
    """Không bắt buộc: không bấm thì số phút của người đó là "không đo được", không phải 0."""
    c = await _call(ctx, inp.call_id)
    if c["status"] != "active":
        raise Conflict("Cuộc gọi đã kết thúc")
    last = await ctx.val("SELECT event FROM meet.participants WHERE call_id = %s AND user_id = core.me() ORDER BY id DESC LIMIT 1", (c["id"],))
    if last != "join":
        raise Conflict("Anh/chị chưa bấm tham gia cuộc gọi này")
    await _click(ctx, c["id"], "leave")
    return {"call_id": c["id"], "summary": f"Đã ghi anh/chị rời “{c['title']}”"}


async def _finish(ctx: Ctx, c: dict[str, Any], reason: str) -> None:
    """Kết thúc + phát số liệu tham dự. Sự kiện chỉ mang mã và số (không tên): bên nghe tra tên bằng quyền của mình."""
    ended_at = await ctx.val("""UPDATE meet.calls SET status = 'ended', ended_at = clock_timestamp(), ended_by = %s, end_reason = %s
                                WHERE id = %s AND status = 'active' RETURNING ended_at""", (ctx.p.user_id, reason, c["id"]))
    if ended_at is None:
        raise Conflict("Cuộc gọi đã kết thúc")
    if c["provider"] == DIRECT:
        ends = await _ends(ctx, c["id"])
        attendance: dict[str, Any] = {
            "source": "client", "covers_outsiders": True,
            "people": [{"user_id": e.user_id, "first_join_at": e.first_at, "last_leave_at": e.last_at, "seconds": e.seconds} for e in ends if e.user_id],
            "outsiders": [{"invite_id": e.invite_id, "party_id": e.party_id, "first_join_at": e.first_at, "last_leave_at": e.last_at, "seconds": e.seconds}
                          for e in ends if e.invite_id]}
        said = f"{sum(e.seconds is not None and e.seconds > 0 for e in ends)} / {len(ends)} đầu có báo thời gian có mặt"
    else:
        people = tally(await _log(ctx, c["id"]))
        attendance = {"source": "click", "covers_outsiders": False,
                      "people": [{"user_id": p.user_id, "first_join_at": p.first_join_at, "last_leave_at": p.last_leave_at, "seconds": p.seconds}
                                 for p in people]}
        said = f"{len(people)} người đã bấm tham gia"
    people = [c["started_by"], *(r["user_id"] for r in await ctx.all("SELECT user_id FROM meet.seats WHERE call_id = %s AND user_id IS NOT NULL", (c["id"],))),
              *(p_["user_id"] for p_ in attendance["people"])]
    await _on_calendar(ctx, c["id"], c["title"], c["started_at"], max(ended_at, c["started_at"] + timedelta(minutes=1)), [x for x in dict.fromkeys(people) if x])
    ctx.emit("meet.call_ended", call_id=c["id"], subject_kind=c["subject_kind"], subject_id=c["subject_id"], provider=c["provider"],
             started_by=c["started_by"], started_at=c["started_at"], ended_at=ended_at, end_reason=reason, attendance=attendance)
    facts = await _facts(ctx, c["id"]) if c["provider"] == DIRECT else None
    if facts and facts.callee and _declined(c, facts, by=ctx.p.user_id, reason=reason):   # người được gọi bấm "Từ chối": nói rõ cho người gọi
        await ctx.notify([c["started_by"]], kind="meet", title=f"{facts.callee.name} đã từ chối cuộc gọi", body="",
                         push="Cuộc gọi của anh/chị bị từ chối", open={"query": "meet.call", "input": {"call_id": str(c["id"])}})
        return
    await ctx.notify([c["started_by"]], kind="meet", title=f"Cuộc gọi đã kết thúc: {c['title']}", body=said,
                     push="Một cuộc gọi vừa kết thúc", open={"query": "meet.call", "input": {"call_id": str(c["id"])}})


@m.command("end", resource="meet", title="Kết thúc cuộc gọi", agent="propose")
async def end(ctx: Ctx, inp: CallRef) -> dict[str, Any]:
    """Chỉ đóng phía Barista: phòng Google Meet / Zoom vẫn là của công ty, tắt ở đó."""
    c = await _call(ctx, inp.call_id, lock=True)
    await _finish(ctx, c, "person")
    return {"call_id": c["id"], "summary": f"Đã kết thúc “{c['title']}”"}


STALE: LiteralString = "12 hours"


@m.command("end_stale", resource="meet", title="Đóng cuộc gọi bị bỏ quên", internal=True, agent="deny",
           sweep=f"SELECT DISTINCT tenant_id FROM meet.calls WHERE status = 'active' AND started_at < now() - interval '{STALE}'")
async def end_stale(ctx: Ctx, inp: NoInput) -> dict[str, int]:
    """Link ngoài không báo khi nào cuộc gọi tan: không ai bấm kết thúc thì đóng sau một khoảng dài để thẻ không báo "đang gọi" mãi và sự kiện vẫn được phát."""
    rows = await ctx.all("SELECT * FROM meet.calls WHERE status = 'active' AND started_at < now() - %s::interval FOR UPDATE SKIP LOCKED", (STALE,))
    for c in rows:
        await _finish(ctx, c, "stale")
    return {"ended": len(rows)}


class ConfirmIn(CallRef):
    members: list[str] = field("Người trong công ty đã dự", [], options_query="admin.member_options")
    others: str = field("Người ngoài đã dự", "", kind="textarea", max_length=1000, help="Người vào bằng link, không có trong danh sách bấm nút")
    note: str = field("Ghi chú", "", kind="textarea", max_length=1000)


@m.command("confirm_attendance", resource="meet", title="Xác nhận người tham dự", agent="propose")
async def confirm_attendance(ctx: Ctx, inp: ConfirmIn) -> dict[str, Any]:
    """NGƯỜI xác nhận ai đã dự, sau khi cuộc gọi kết thúc, một lần. Trần ``propose``: trợ lý chỉ soạn thẻ, công ty cũng không nới được."""
    c = await _call(ctx, inp.call_id, lock=True)
    if c["status"] != "ended":
        raise Conflict("Cuộc gọi chưa kết thúc")
    if c["confirmed_at"] is not None:
        raise Conflict("Cuộc gọi này đã được xác nhận")
    ids: list[UUID] = []
    for email in dict.fromkeys(e.strip().lower() for e in inp.members if e.strip()):
        uid = await ctx.val("""SELECT u.id FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant()
                               WHERE u.email = %s AND mb.active""", (email,))
        if uid is None:
            raise NotFound(f"Không có người {email} trong công ty")
        ids.append(uid)
    others = inp.others.strip()
    await ctx.exec("""UPDATE meet.calls SET confirmed_by = core.me(), confirmed_at = clock_timestamp(), confirmed_user_ids = %s, confirmed_others = %s,
                      confirmed_note = %s WHERE id = %s""", (ids, others, inp.note.strip(), c["id"]))
    ctx.emit("meet.attendance_confirmed", call_id=c["id"], subject_kind=c["subject_kind"], subject_id=c["subject_id"], user_ids=ids,
             has_others=bool(others), confirmed_by=ctx.p.user_id)
    return {"call_id": c["id"], "summary": f"Đã xác nhận {len(ids)} người trong công ty" + (" và người ngoài" if others else "") + f" dự “{c['title']}”"}


# ── Gọi trực tiếp 1–1 (0114 bổ sung 07/11): máy chủ chỉ chuyển tín hiệu giữa đúng hai ghế, tiếng / hình đi thẳng giữa hai trình duyệt ──

LINK = "meet.invite"                 # loại link người ngoài (0064): chỉ đúng một lời mời của một cuộc gọi
CONSENT_PURPOSE = "data_processing"  # cuộc gọi xử lý tiếng / hình của người được mời; trẻ em ⇒ phải "đủ đồng ý" theo 0112
SIGNAL_MAX = 16_000                  # byte JSON của một tín hiệu (SDP có hình ~6–8 KB) — DB chặn lại lần nữa
SIGNAL_KEYS = {"sid", "gen", "to", "tg", "description", "candidates", "reason"}   # chỉ đúng thứ báo hiệu cần: không thành kênh nhắn tin
SIGNAL_KEEP: LiteralString = "2 hours"
INVITE_HOURS_MAX = 24


def _turn_secret() -> str:
    if settings.turn_secret:
        return settings.turn_secret
    if settings.turn_secret_file:
        return Path(settings.turn_secret_file).read_text().strip()
    return ""


def turn_credential(secret: str, *, ttl_s: int, tag: str, now: float) -> tuple[str, str]:
    """Kiểu "TURN REST API" (coturn ``use-auth-secret`` + ``static-auth-secret``): tên = "<hạn unix>:<nhãn>", mật khẩu = base64(HMAC-SHA1(khoá, tên)).
    coturn tự kiểm hạn trong tên; khoá chung không bao giờ rời máy chủ, trình duyệt chỉ cầm cặp tạm này."""
    user = f"{int(now) + ttl_s}:{tag}"
    return user, base64.b64encode(hmac.new(secret.encode(), user.encode(), hashlib.sha1).digest()).decode()


def seat_tag(call_id: UUID, seat: str, holder: UUID) -> str:
    """Nhãn trong tên TURN: GẮN cuộc gọi + ghế + người ngồi (băm, không tên / không mã người rõ) — hạn mức của coturn (``user-quota``) và nhật ký của
    nó tính theo từng chỗ ngồi, và cặp của người đã bị bỏ không trùng tên với người mới vào ghế đó."""
    return f"{call_id.hex[:12]}{seat}{hashlib.sha256(holder.bytes).hexdigest()[:8]}"


def ice_servers(*, tag: str = "", now: float | None = None) -> dict[str, Any]:
    """Máy STUN / TURN cho một chỗ ngồi. ``tag`` rỗng (không có ghế) ⇒ không phát TURN."""
    split = lambda raw: [u.strip() for u in raw.split(",") if u.strip()]   # noqa: E731
    servers: list[dict[str, Any]] = []
    if stun := split(settings.stun_urls):
        servers.append({"urls": stun})
    turn, secret = split(settings.turn_urls), ""
    if turn and tag:
        secret = _turn_secret()
    if turn and secret:
        user, cred = turn_credential(secret, ttl_s=settings.turn_ttl_s, tag=tag, now=time.time() if now is None else now)
        servers.append({"urls": turn, "username": user, "credential": cred})
    return {"servers": servers, "turn": bool(turn and secret), "ttl_s": settings.turn_ttl_s if turn and secret else None}


log = logging.getLogger(__name__)
CF_TURN_API = "https://rtc.live.cloudflare.com/v1/turn/keys/{key}/credentials/generate-ice-servers"


def _cloudflare_token() -> str:
    if settings.turn_cloudflare_token:
        return settings.turn_cloudflare_token
    if settings.turn_cloudflare_token_file:
        return Path(settings.turn_cloudflare_token_file).read_text().strip()
    return ""


def _browser_urls(urls: Any) -> list[str]:
    """Cloudflare trả cả cổng 53; trình duyệt hay chặn / treo ở cổng 53 (Cloudflare khuyên bỏ) ⇒ lọc đi."""
    out = [urls] if isinstance(urls, str) else list(urls or [])
    return [u for u in out if not re.search(r":53(\?|$)", u)]


async def ice_for(*, tag: str = "") -> dict[str, Any]:
    """Máy STUN / TURN cho một chỗ ngồi: có khoá Cloudflare ⇒ xin cặp tạm từ Cloudflare; không ⇒ ``ice_servers`` (coturn / chỉ STUN).
    ``tag`` rỗng (không có ghế / vượt hạn mức) ⇒ không phát TURN. Cloudflare lỗi / chậm ⇒ chỉ STUN, ghi log (không in token)."""
    token = _cloudflare_token() if settings.turn_cloudflare_key_id and tag else ""
    if not token:
        return ice_servers(tag=tag)
    try:
        async with httpx.AsyncClient(timeout=2) as client:   # nằm trên đường mở trang gọi: chậm thì thà chỉ STUN
            r = await client.post(CF_TURN_API.format(key=settings.turn_cloudflare_key_id),
                                  headers={"Authorization": f"Bearer {token}"}, json={"ttl": settings.turn_ttl_s})
            r.raise_for_status()
            raw = r.json()["iceServers"]
    except Exception as e:   # Cloudflare TURN lỗi ⇒ dùng danh sách ICE mặc định (đã log)
        log.warning("không xin được TURN Cloudflare: %s", type(e).__name__)
        return ice_servers()
    servers = []
    for s in raw if isinstance(raw, list) else [raw]:
        if urls := _browser_urls(s.get("urls")):
            servers.append({"urls": urls, **({"username": s["username"], "credential": s["credential"]} if s.get("username") else {})})
    turn = any("username" in s for s in servers)
    return {"servers": servers, "turn": turn, "ttl_s": settings.turn_ttl_s if turn else None}


def invite_url(token: str) -> str:
    return f"{settings.web_url.rstrip('/')}/c/goi/{token}"


async def _direct(ctx: Ctx, call_id: UUID, *, lock: bool = False) -> dict[str, Any]:
    c = await _call(ctx, call_id, lock=lock)
    if c["provider"] != DIRECT:
        raise Invalid("Cuộc gọi này dùng link ngoài, không phải gọi trực tiếp")
    return c


async def _my_seat(ctx: Ctx, call_id: UUID) -> str:
    seat = await ctx.val("SELECT meet.my_seat(%s)", (call_id,))
    if not seat:
        raise Denied("Anh/chị không ở trong cuộc gọi này")
    return seat


def _me(ctx: Ctx) -> UUID:
    """Người đang ngồi: thành viên (mã người) hoặc người ngoài (mã lời mời của link)."""
    who = ctx.p.user_id or ctx.p.link_ref
    assert who is not None
    return who


async def _consent(ctx: Ctx, party_id: UUID | None) -> dict[str, Any] | None:
    """Trạng thái đồng ý của người được mời (0112, đọc qua hợp đồng hẹp của parties). None = không gắn định danh / gói định danh tắt."""
    if party_id is None:
        return None
    return await ctx.call_system_optional("parties.call_consent", party_id=party_id, purpose=CONSENT_PURPOSE)


def _consent_block(got: dict[str, Any] | None, *, party_id: UUID | None, adult_declared: bool) -> str:
    """Lý do CHẶN người ngoài (chuỗi rỗng = được gọi). Không gắn định danh ⇒ người mời phải xác nhận người đó đã đủ tuổi thành niên. Có định danh:
    trẻ em (cần người giám hộ đồng ý) phải "đủ đồng ý"; chưa biết tuổi mà không có người giám hộ cũng chặn (0112 "Còn mở": đường gửi đầu tiên chặn khi
    chưa xét được tuổi) — ghi ngày sinh trước. Gói định danh tắt mà lời mời có định danh ⇒ không kiểm được ⇒ chặn."""
    if party_id is None:
        return "" if adult_declared else ("Người được mời có thể là trẻ em: chọn định danh của người đó để kiểm sự đồng ý, hoặc xác nhận người đó đã đủ "
                                          "tuổi thành niên")
    if got is None:
        return "Không kiểm được sự đồng ý của người được mời (gói định danh đang tắt)"
    if got["child"] and got["state"] != "granted":
        return f"Người được mời là trẻ em và chưa đủ sự đồng ý xử lý dữ liệu ({got['state_label'].lower()}). Ghi sự đồng ý trước rồi mời lại."
    if not got["age_known"] and not got["child"]:
        return "Chưa có ngày sinh của người được mời nên chưa xét được tuổi: ghi ngày sinh (và sự đồng ý nếu là trẻ em) rồi mời lại."
    return ""


class SignalIn(Input):
    kind: Literal["hello", "description", "candidate", "bye"] = field("Loại tín hiệu")
    # SDP / ICE có địa chỉ IP: không chép vào nhật ký thao tác (lệnh chạy ``quiet`` nên bình thường không ghi gì)
    body: dict[str, Any] = field("Nội dung", audit=False)


async def _signal(ctx: Ctx, c: dict[str, Any], inp: SignalIn) -> dict[str, Any]:
    if c["status"] != "active":
        raise Conflict("Cuộc gọi đã kết thúc")
    if extra := set(inp.body) - SIGNAL_KEYS:
        raise Invalid(f"Tín hiệu có trường lạ: {', '.join(sorted(extra))}")
    if len(json.dumps(inp.body, ensure_ascii=False).encode()) > SIGNAL_MAX:
        raise Invalid(f"Tín hiệu quá lớn (tối đa {SIGNAL_MAX // 1000} KB)")
    seat = await _my_seat(ctx, c["id"])
    if not ratelimit.allow(f"{c['id']}:{seat}:{_me(ctx)}", "seat_signal"):
        raise Invalid("Gửi tín hiệu quá nhiều trong thời gian ngắn — đợi một lúc rồi thử lại")
    # đúng NGƯỜI đang ngồi ghế kia lúc này; chưa có ai thì không có ai để gửi (người vào sau tự chào)
    to = await ctx.one("SELECT user_id, invite_id FROM meet.seats WHERE call_id = %s AND seat <> %s", (c["id"], seat))
    if to is None:
        return {}
    await ctx.exec("INSERT INTO meet.signals (call_id, from_seat, to_user, to_invite, kind, body) VALUES (%s, %s, %s, %s, %s, %s)",
                   (c["id"], seat, to["user_id"], to["invite_id"], inp.kind, Jsonb(inp.body)))
    return {}


class PeerIn(CallRef):
    seat: Literal["a", "b"] = field("Ghế của người kia")


@m.query("peer_state", resource="meet", title="Người kia còn được ở trong cuộc gọi không", internal=True, agent="deny", callers=("meet",),
         why="Chính meet, mỗi lần kênh tín hiệu của một ghế đọc thư (người hỏi đã qua RLS của cuộc gọi, đang ngồi ghế còn lại): hỏi người ngồi ghế kia "
             "còn được ở trong cuộc gọi không — thành viên còn thấy đối tượng (nạp lại quyền của họ), người ngoài còn lời mời chưa thu hồi / chưa hết hạn "
             "và còn đủ sự đồng ý. Cần quyền hệ thống vì phiên link không đọc được quyền của thành viên. Trả một nhãn băm của người kia, rỗng nếu không còn.")
async def peer_state(ctx: Ctx, inp: PeerIn) -> str:
    s = await ctx.one("""SELECT s.user_id, s.invite_id, c.subject_kind, c.subject_id, i.party_id, i.adult_declared,
                                (i.revoked_at IS NULL AND i.expires_at > now()) AS invite_live
                         FROM meet.seats s JOIN meet.calls c ON c.id = s.call_id LEFT JOIN meet.invites i ON i.id = s.invite_id
                         WHERE s.call_id = %s AND s.seat = %s""", (inp.call_id, inp.seat))
    if s is None:
        return ""
    if s["user_id"]:
        ok = await _sees_subject(ctx, s["user_id"], s["subject_kind"], s["subject_id"])
    else:
        ok = bool(s["invite_live"]) and not _consent_block(await _consent(ctx, s["party_id"]), party_id=s["party_id"], adult_declared=s["adult_declared"])
    return hashlib.sha256((s["user_id"] or s["invite_id"]).bytes).hexdigest()[:12] if ok else ""


async def _inbox(ctx: Ctx, c: dict[str, Any], after: int) -> dict[str, Any]:
    """Thư GỬI CHO MÌNH sau mốc ``after`` (RLS: chỉ thư tới chính mình, chỉ khi còn thấy cuộc gọi) + nhãn người kia nếu họ CÒN được ở trong cuộc gọi
    (``peer`` rỗng ⇒ trang bỏ kết nối với họ ngay: bị thu hồi, rút đồng ý, mất quyền xem, bị trả chỗ)."""
    seat = await _my_seat(ctx, c["id"])
    rows = await ctx.all("SELECT id, kind, body FROM meet.signals WHERE call_id = %s AND id > %s ORDER BY id LIMIT 200", (c["id"], after))
    peer = await ctx.call_system("meet.peer_state", call_id=c["id"], seat="b" if seat == "a" else "a") if c["status"] == "active" else ""
    return {"call_id": c["id"], "active": c["status"] == "active", "signals": rows, "peer": peer}


class PresenceIn(Input):
    event: Literal["connected", "disconnected", "ended"] = field("Sự kiện")
    detail: str = field("Chi tiết", "", max_length=40)
    client_at: datetime | None = field("Giờ máy báo", None)


async def _presence(ctx: Ctx, c: dict[str, Any], inp: PresenceIn, *, invite_id: UUID | None = None) -> dict[str, Any]:
    """Máy báo về chính nó: nối được / mất nối / kết thúc. "Nối được" chỉ nhận khi cuộc gọi còn diễn ra; mất nối / kết thúc nhận cả ngay sau khi
    cuộc gọi vừa đóng (máy bên kia báo khi nhận tin kết thúc) — nhưng số phút trong sự kiện kết thúc đã chốt lúc đóng."""
    if inp.event == "connected" and c["status"] != "active":
        raise Conflict("Cuộc gọi đã kết thúc")
    seat = await _my_seat(ctx, c["id"])
    await ctx.exec("""INSERT INTO meet.presence (call_id, seat, user_id, invite_id, event, detail, client_at)
                      VALUES (%s, %s, CASE WHEN %s::uuid IS NULL THEN core.me() END, %s, %s, %s, %s)""",
                   (c["id"], seat, invite_id, invite_id, inp.event, inp.detail, inp.client_at))
    return {"call_id": c["id"], "event": inp.event}


async def _state(ctx: Ctx, c: dict[str, Any], seat: str | None) -> dict[str, Any]:
    """Thứ trang gọi cần lúc mở: ghế, người kia, máy STUN / TURN (``once_`` — không lưu, không tới model), mốc tín hiệu để chỉ nghe thư MỚI.
    Mật khẩu TURN gắn cuộc gọi + ghế + người, có hạn mức số lần lấy theo chỗ ngồi; vượt ⇒ chỉ STUN."""
    ends = {e.seat: e for e in await _ends(ctx, c["id"])}
    peer = ends.get("b" if seat == "a" else "a") if seat else None
    cursor = await ctx.val("SELECT coalesce(max(id), 0) FROM meet.signals WHERE call_id = %s", (c["id"],)) if seat else 0
    ice = None
    if seat and c["status"] == "active":
        tag = seat_tag(c["id"], seat, _me(ctx)) if ratelimit.allow(f"{c['id']}:{seat}:{_me(ctx)}", "turn") else ""
        ice = await ice_for(tag=tag)
    return {"call_id": c["id"], "title": c["title"], "active": c["status"] == "active", "seat": seat, "polite": seat == "b",
            "peer": {"name": peer.name, "outsider": peer.invite_id is not None} if peer else None, "cursor": cursor, "once_ice": ice}


@m.query("direct", resource="meet", title="Trang gọi trực tiếp", agent="deny")
async def direct(ctx: Ctx, inp: CallRef) -> dict[str, Any]:
    """Trạng thái cho trang gọi của thành viên. Không có ghế ⇒ ``seat`` rỗng (trang báo lý do). Trợ lý không dùng: có mật khẩu TURN tạm."""
    c = await _direct(ctx, inp.call_id)
    seat = await ctx.val("SELECT meet.my_seat(%s)", (c["id"],))
    out = await _state(ctx, c, seat)
    inv = await ctx.one("SELECT id, name, expires_at FROM meet.invites WHERE call_id = %s AND revoked_at IS NULL", (c["id"],))
    out["invite"] = {"name": inv["name"], "expires_at": inv["expires_at"]} if inv else None
    out["subject"] = {"kind": c["subject_kind"], "id": c["subject_id"]}
    return out


class SignalCmd(CallRef, SignalIn):
    pass


@m.command("signal", resource="meet", title="Chuyển tín hiệu cuộc gọi", agent="deny")
async def signal(ctx: Ctx, inp: SignalCmd) -> dict[str, Any]:
    """Offer / answer / ICE / chào / tạm biệt tới ĐÚNG người kia của cuộc gọi. Không chép vào nhật ký (route chạy ``quiet``): là vận chuyển, mỗi cuộc
    vài chục dòng; vào / rời / có mặt / kết thúc mới là việc có vết."""
    return await _signal(ctx, await _direct(ctx, inp.call_id), inp)


class InboxIn(CallRef):
    after: int = field("Sau tín hiệu", 0, ge=0)


@m.query("signals", resource="meet", title="Tín hiệu gửi cho tôi", agent="deny")
async def signals(ctx: Ctx, inp: InboxIn) -> dict[str, Any]:
    return await _inbox(ctx, await _direct(ctx, inp.call_id), inp.after)


class PresenceCmd(CallRef, PresenceIn):
    pass


@m.command("presence", resource="meet", title="Báo có mặt trong cuộc gọi", agent="deny")
async def presence(ctx: Ctx, inp: PresenceCmd) -> dict[str, Any]:
    """Chỉ máy của chính người đó báo (RLS ``p_self``); trợ lý không báo thay."""
    return await _presence(ctx, await _direct(ctx, inp.call_id), inp)


class SeatMemberIn(CallRef):
    member: str = field("Người gọi cùng", options_query="admin.member_options")


async def _as_host(ctx: Ctx, call_id: UUID) -> dict[str, Any]:
    c = await _direct(ctx, call_id, lock=True)
    if c["status"] != "active":
        raise Conflict("Cuộc gọi đã kết thúc")
    if await ctx.val("SELECT meet.my_seat(%s)", (c["id"],)) != "a":
        raise Denied("Chỉ người bắt đầu cuộc gọi chọn / trả người thứ hai")
    return c


@m.command("seat_member", resource="meet", title="Chọn người gọi cùng", agent="propose")
async def seat_member(ctx: Ctx, inp: SeatMemberIn) -> dict[str, Any]:
    """Người bắt đầu chọn người trong công ty vào chỗ thứ hai (người đó phải thấy hồ sơ / phòng của cuộc gọi)."""
    c = await _as_host(ctx, inp.call_id)
    uid = await _member(ctx, inp.member)
    if uid == ctx.p.user_id:
        raise Invalid("Anh/chị đã ở trong cuộc gọi")
    await _seat_member(ctx, c["id"], c["subject_kind"], c["subject_id"], uid)
    return {"call_id": c["id"], "summary": f"Đã chọn {inp.member} gọi cùng"}


@m.command("release_seat", resource="meet", title="Trả chỗ người gọi cùng", agent="propose")
async def release_seat(ctx: Ctx, inp: CallRef) -> dict[str, Any]:
    """Người bắt đầu trả chỗ thứ hai của một thành viên: người đó mất kênh tín hiệu ngay, trang bên này bỏ kết nối với họ. Người ngoài ⇒ Thu hồi lời mời."""
    c = await _as_host(ctx, inp.call_id)
    if not await ctx.exec("DELETE FROM meet.seats WHERE call_id = %s AND seat = 'b' AND user_id IS NOT NULL", (c["id"],)):
        raise Conflict("Không có người trong công ty nào ở chỗ thứ hai (người ngoài: thu hồi lời mời)")
    return {"call_id": c["id"], "summary": "Đã trả chỗ thứ hai — chọn người khác hoặc mời người ngoài"}


class InviteIn(CallRef):
    name: str = field("Tên người được mời", max_length=120, example="Chị Lan (khách)")
    party_id: UUID | None = field("Định danh", None, options_query="parties.options",
                                  help="Người trong danh bạ — bắt buộc nếu người được mời có thể là trẻ em (kiểm sự đồng ý của người giám hộ)")
    adult_declared: bool = field("Tôi xác nhận người được mời đã đủ tuổi thành niên", False,
                                 help="Chỉ khi không chọn định danh. Ghi vào lời mời cùng tên người xác nhận")
    hours: int = field("Link dùng được trong (giờ)", 2, ge=1, le=INVITE_HOURS_MAX)


@m.command("invite", resource="meet", title="Mời người ngoài vào cuộc gọi", agent="propose", shows=("meet.invitation", "once_invite"))
async def invite(ctx: Ctx, inp: InviteIn) -> dict[str, Any]:
    """Link hẹp có hạn cho MỘT người ngoài, chỉ vào đúng cuộc gọi trực tiếp này (kiểu 0064). Người đó giữ chỗ thứ hai. Trẻ em: phải có định danh và đủ
    sự đồng ý (0112); không có định danh thì người mời xác nhận người đó đã thành niên."""
    c = await _as_host(ctx, inp.call_id)
    if inp.party_id is not None:   # định danh do người mời chọn: phải thấy được bằng QUYỀN CỦA HỌ trước khi hỏi trạng thái đồng ý bằng quyền hệ thống (0125 §B)
        seen = await ctx.call_as_optional("parties.names", ctx.p, party_ids=[inp.party_id])
        if seen is None:
            raise Invalid("Không kiểm được định danh: công ty chưa dùng danh mục, hoặc bạn không có quyền xem danh mục — bỏ ô định danh và xác nhận người đó đã thành niên")
        if str(inp.party_id) not in seen:
            raise NotFound("Không thấy định danh này")
    if reason := _consent_block(await _consent(ctx, inp.party_id), party_id=inp.party_id, adult_declared=inp.adult_declared):
        raise Invalid(reason)
    if await ctx.val("SELECT 1 FROM meet.seats WHERE call_id = %s AND seat = 'b'", (c["id"],)):
        raise Conflict("Cuộc gọi 1–1 đã đủ người — thu hồi lời mời / trả chỗ cũ trước")
    iid = await ctx.val("""INSERT INTO meet.invites (call_id, name, party_id, adult_declared, created_by, expires_at)
                           VALUES (%s, %s, %s, %s, core.me(), clock_timestamp() + %s) RETURNING id""",
                        (c["id"], inp.name, inp.party_id, inp.party_id is None and inp.adult_declared, timedelta(hours=inp.hours)))
    await ctx.exec("INSERT INTO meet.seats (call_id, seat, invite_id) VALUES (%s, 'b', %s)", (c["id"], iid))
    token = await links.issue(ctx, LINK, iid, ttl=timedelta(hours=inp.hours))
    return {"call_id": c["id"], "invite_id": iid, "once_invite": f"{iid}:{token}", "once_url": invite_url(token),
            "summary": f"Đã tạo link mời {inp.name} (dùng được {inp.hours} giờ)"}


@m.command("revoke_invite", resource="meet", title="Thu hồi lời mời", agent="propose")
async def revoke_invite(ctx: Ctx, inp: CallRef) -> dict[str, Any]:
    """Link chết ngay, kênh tín hiệu của người ngoài đóng, trang bên này bỏ kết nối với họ; chỗ trống lại. Nhật ký có mặt của người đó giữ nguyên.
    Thư đã gửi người đó không ai khác đọc được (thư gửi theo người, không theo ghế)."""
    c = await _direct(ctx, inp.call_id, lock=True)
    iid = await ctx.val("UPDATE meet.invites SET revoked_at = clock_timestamp() WHERE call_id = %s AND revoked_at IS NULL RETURNING id", (c["id"],))
    if iid is None:
        raise Conflict("Không có lời mời nào đang hiệu lực")
    await links.revoke(ctx, LINK, iid)
    await ctx.exec("DELETE FROM meet.seats WHERE call_id = %s AND invite_id = %s", (c["id"], iid))
    return {"call_id": c["id"], "summary": "Đã thu hồi lời mời — link cũ không dùng được nữa"}


class InvitationIn(Input):
    once_invite: str = field("Lời mời", hidden=True, max_length=200)   # "<mã lời mời>" hoặc "<mã>:<mã link>" ngay sau khi tạo (kiểm lại, không tin app)


@m.query("invitation", resource="meet", title="Lời mời vào cuộc gọi")
async def invitation(ctx: Ctx, inp: InvitationIn) -> dict[str, Any]:
    """Thẻ lời mời: link hiện ĐÚNG lần ngay sau khi tạo (mã kiểm lại với mã băm), sau đó chỉ còn tên + hạn. Trợ lý không bao giờ thấy link."""
    iid_s, _, token = inp.once_invite.partition(":")
    try:
        iid = UUID(iid_s)
    except ValueError as e:
        raise NotFound("Không thấy lời mời") from e
    i = await ctx.one("SELECT * FROM meet.invites WHERE id = %s", (iid,))
    if i is None:
        raise NotFound("Không thấy lời mời (hoặc bạn không xem được cuộc gọi)")
    c = await _call(ctx, i["call_id"])
    live = i["revoked_at"] is None and c["status"] == "active"
    blocks: list[dict[str, Any]] = []
    if live and token and ctx.p.via != "agent" and await links.matches(ctx, LINK, iid, token):
        blocks.append(ui.b_callout("accent", "Link gửi người được mời — copy gửi qua Zalo / tin nhắn",
                                   "Link chỉ hiện lần này (hệ thống không lưu), chỉ vào được đúng cuộc gọi này và hết hạn như ghi dưới.",
                                   draft=f"{await ctx.company()} mời anh/chị vào cuộc gọi “{c['title']}”. Bấm link để vào bằng trình duyệt "
                                         f"(không cần cài, không cần đăng nhập): {invite_url(token)}"))
    state = "Đã thu hồi" if i["revoked_at"] else "Cuộc gọi đã kết thúc" if c["status"] != "active" else f"Dùng được tới {i['expires_at'].astimezone(zone()):%H:%M %d/%m}"
    return ui.card(f"Mời {i['name']}", blocks, subtitle=f"{c['title']} · {state}", icon="video",
                   actions=[ui.act("meet.revoke_invite", "Thu hồi lời mời", {"call_id": str(c["id"])}, "destructive")] if live else [],
                   open=[{"title": "Mở cuộc gọi", "query": "meet.call", "input": {"call_id": str(c["id"])}}])


# ── Người ngoài qua link (public="meet.invite"): RLS chỉ mở đúng cuộc gọi của lời mời (p_link), không thấy thành viên / cấu hình nào ──

async def _link_call(ctx: Ctx) -> tuple[dict[str, Any], dict[str, Any]]:
    i = await ctx.one("SELECT * FROM meet.invites WHERE id = core.link_ref()")
    c = await ctx.one("SELECT * FROM meet.calls WHERE id = meet.link_call()")
    if i is None or c is None:
        raise NotFound("Link không còn hiệu lực — liên hệ bên mời để nhận link mới")
    return i, c


async def _link_guard(ctx: Ctx) -> tuple[dict[str, Any], dict[str, Any]]:
    """MỌI lần người ngoài đọc / gửi / báo: lời mời còn, và nếu là trẻ em thì sự đồng ý VẪN đủ (rút lại giữa chừng ⇒ dừng ngay)."""
    i, c = await _link_call(ctx)
    if reason := _consent_block(await _consent(ctx, i["party_id"]), party_id=i["party_id"], adult_declared=i["adult_declared"]):
        raise Denied(reason)
    return i, c


@m.query("link_view", resource="meet", title="Cuộc gọi (người ngoài qua link)", public=LINK, agent="deny")
async def link_view(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    i, c = await _link_guard(ctx)
    out = await _state(ctx, c, "b")
    host = await ctx.val("SELECT u.name FROM meet.seats s JOIN core.users u ON u.id = s.user_id WHERE s.call_id = %s AND s.seat = 'a'", (c["id"],))
    # core.users đóng với phiên link (p_no_link) ⇒ tên người mời không đọc được ở đây: ghi chung "người mời"
    out["peer"] = {"name": host or "Người mời", "outsider": False}
    return {**out, "company": await ctx.company(), "invitee": i["name"], "expires_at": i["expires_at"]}


@m.command("link_signal", resource="meet", title="Người ngoài chuyển tín hiệu cuộc gọi", public=LINK, agent="deny")
async def link_signal(ctx: Ctx, inp: SignalIn) -> dict[str, Any]:
    _, c = await _link_guard(ctx)
    return await _signal(ctx, c, inp)


class AfterIn(Input):
    after: int = field("Sau tín hiệu", 0, ge=0)


@m.query("link_signals", resource="meet", title="Tín hiệu gửi người ngoài", public=LINK, agent="deny")
async def link_signals(ctx: Ctx, inp: AfterIn) -> dict[str, Any]:
    _, c = await _link_guard(ctx)
    return await _inbox(ctx, c, inp.after)


@m.command("link_presence", resource="meet", title="Người ngoài báo có mặt", public=LINK, agent="deny")
async def link_presence(ctx: Ctx, inp: PresenceIn) -> dict[str, Any]:
    i, c = await _link_guard(ctx)
    return await _presence(ctx, c, inp, invite_id=i["id"])


@m.command("purge_signals", resource="meet", title="Xoá tín hiệu cuộc gọi đã xong", internal=True, agent="deny",
           sweep=f"""SELECT DISTINCT s.tenant_id FROM meet.signals s JOIN meet.calls c ON c.id = s.call_id
                     WHERE c.status = 'ended' OR s.at < now() - interval '{SIGNAL_KEEP}'""")
async def purge_signals(ctx: Ctx, inp: NoInput) -> dict[str, int]:
    """Tín hiệu (SDP / ICE, có địa chỉ IP) chỉ cần lúc nối: xoá khi cuộc gọi kết thúc hoặc sau 2 giờ."""
    n = await ctx.exec("""DELETE FROM meet.signals s USING meet.calls c WHERE c.id = s.call_id
                          AND (c.status = 'ended' OR s.at < now() - %s::interval)""", (SIGNAL_KEEP,))
    return {"deleted": n}


# ── Xem ──

STATUS = {"active": "Đang diễn ra", "ended": "Đã kết thúc"}


def _when(c: dict[str, Any]) -> str:
    begin = c["started_at"].astimezone(zone())
    if c["ended_at"] is None:
        return f"từ {begin:%H:%M %d/%m}"
    return f"{begin:%H:%M}–{c['ended_at'].astimezone(zone()):%H:%M %d/%m}"


def _spent(seconds: int | None) -> str:
    return "không đo được" if seconds is None else f"{round(seconds / 60)} phút theo lần bấm"


def _present(e: End) -> str:
    if not e.reported:
        return "chưa có báo nào từ máy"
    if e.open:
        return "đang nối" + (f" · từ {e.first_at.astimezone(zone()):%H:%M}" if e.first_at else "")
    if e.seconds is None:
        return "không đo được"
    return f"có mặt {e.seconds} giây theo máy báo về" if e.seconds < 60 else f"có mặt {round(e.seconds / 60)} phút theo máy báo về"


@m.query("call", resource="meet", title="Một cuộc gọi")
async def call(ctx: Ctx, inp: CallRef) -> dict[str, Any]:
    """Thẻ cuộc gọi (cũng là thẻ sống trong phòng trao đổi — 0089): chữ + nút DO SERVER QUYẾT theo người xem (người gọi / người được gọi / người khác) và trạng thái
    thật của cuộc gọi (chưa ai trả lời, đang diễn ra, bị từ chối…); nút chạy lại bằng quyền người xem. Trợ lý không nhận link: link Zoom có thể kèm mật khẩu phòng."""
    c = await _call(ctx, inp.call_id)
    direct = c["provider"] == DIRECT
    log = [] if direct else await _log(ctx, c["id"])
    people = tally(log)
    names = {u["id"]: u for u in await ctx.all("SELECT id, name, email FROM core.users WHERE id = ANY(%s)", ([p.user_id for p in people] + c["confirmed_user_ids"],))}
    ref = {"call_id": str(c["id"])}
    blocks: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    ends = await _ends(ctx, c["id"]) if direct else []
    mine = await ctx.val("SELECT meet.my_seat(%s)", (c["id"],)) if direct else None
    facts = await _facts(ctx, c["id"]) if direct else None
    ring = facts is not None and facts.callee is not None   # 1–1 có người nhận đã định: chữ và nút theo người xem
    headline = ""
    status = f"{STATUS[c['status']]} · {_when(c)} · {'gọi trực tiếp' if direct else _host(c['join_url'])}"
    title = c["title"]
    state = ""
    if ring:
        assert facts is not None
        role = {"a": "caller", "b": "callee"}.get(mine or "", "other")
        title, status, state = ring_view(c, facts, role)
        headline = title
        me = facts.caller if role == "caller" else facts.callee if role == "callee" else None
        if c["status"] == "active":
            if state == "ringing":   # người nhận chưa vào: chưa phải "đang diễn ra"; người nhận từ chối được, người gọi huỷ được; người khác không có nút kết thúc
                if role == "callee":
                    actions += [ui.act("meet.join", "Vào cuộc gọi", {**ref, "enter": True, "video": False}, "instant"), ui.act("meet.end", "Từ chối", ref, "destructive")]
                elif role == "caller":
                    actions += [ui.act("meet.join", "Vào lại", {**ref, "enter": True, "video": False}, "instant"), ui.act("meet.end", "Huỷ cuộc gọi", ref, "destructive")]
            elif me is not None:   # đang diễn ra: hai người trong cuộc vào / rời và kết thúc; người khác chỉ xem
                actions.append(ui.act("meet.leave", "Rời", ref, "instant") if me.here and me.last_click == "join"
                               else ui.act("meet.join", "Vào cuộc gọi", {**ref, "enter": True, "video": False}, "instant"))
                actions.append(ui.act("meet.end", "Kết thúc", ref, "destructive"))
            blocks.append(ui.b_callout("muted", "Gọi trực tiếp, hai người",
                                       "Tiếng / hình đi thẳng giữa hai trình duyệt, không qua máy chủ Barista. Người ngoài vào bằng link mời có hạn."))
    elif c["status"] == "active":
        if direct:
            if mine or len(ends) < 2:
                actions.append(ui.act("meet.join", "Vào cuộc gọi", {**ref, "enter": True, "video": False}, "instant"))
            if len(ends) < 2:
                actions.append(ui.act("meet.invite", "Mời người ngoài", ref))
            if await ctx.val("SELECT 1 FROM meet.invites WHERE call_id = %s AND revoked_at IS NULL", (c["id"],)):
                actions.append(ui.act("meet.revoke_invite", "Thu hồi lời mời", ref))
            blocks.append(ui.b_callout("muted", "Gọi trực tiếp, hai người",
                                       "Tiếng / hình đi thẳng giữa hai trình duyệt, không qua máy chủ Barista. Người ngoài vào bằng link mời có hạn."))
        else:
            actions.append(ui.act("meet.join", "Tham gia", ref, "instant"))
            if ctx.p.via != "agent":
                blocks.append(ui.b_callout("accent", "Link cuộc gọi", f"Người ngoài công ty vào bằng link này ({_host(c['join_url'])}). Lượt của họ không được ghi tự động.",
                                           draft=c["join_url"]))
            last = await ctx.val("SELECT event FROM meet.participants WHERE call_id = %s AND user_id = core.me() ORDER BY id DESC LIMIT 1", (c["id"],))
            if last == "join":
                actions.append(ui.act("meet.leave", "Rời", ref, "instant"))
            in_now = len({r["user_id"] for r in log if r["event"] == "join"} - {r["user_id"] for r in log if r["event"] == "leave"})
            status = f"Đang diễn ra · {in_now} người đã bấm tham gia · {_when(c)} · {_host(c['join_url'])}"
        actions.append(ui.act("meet.end", "Kết thúc", ref, "destructive"))
    if c["status"] != "active" and c["confirmed_at"] is None and state not in ("declined", "missed"):
        # chỉ gắn mã cuộc gọi: trường điền sẵn trên nút là trường CỐ ĐỊNH (app ẩn đi) — danh sách người do người xác nhận tự chọn
        actions.append(ui.act("meet.confirm_attendance", "Xác nhận người tham dự", ref, "primary"))
    if c["confirmed_at"] is not None:
        who = [names[u]["name"] for u in c["confirmed_user_ids"] if u in names]
        by = await ctx.val("SELECT name FROM core.users WHERE id = %s", (c["confirmed_by"],))
        blocks.append(ui.b_callout("ok", "Người đã xác nhận danh sách tham dự", f"{by}, {c['confirmed_at'].astimezone(zone()):%H:%M %d/%m}",
                                   facts=[*who, *([c["confirmed_others"]] if c["confirmed_others"] else []), *([c["confirmed_note"]] if c["confirmed_note"] else [])]))
    if direct:
        side = {facts.caller.user_id: facts.caller, **({facts.callee.user_id: facts.callee} if facts.callee else {})} if facts else {}
        blocks.append(ui.b_list([{"title": (f"Người ngoài: {e.name}" if e.invite_id else e.name) or "Không rõ",
                                  "subtitle": "chưa vào" if ring and not e.reported and e.user_id in side and not side[e.user_id].joined else _present(e),
                                  "tone": "ok" if e.open else "muted"} for e in ends],
                                "Hai đầu cuộc gọi (thời gian do máy hai bên báo về, người vẫn phải xác nhận)"))
    else:
        blocks.append(ui.b_list([{"title": names[p.user_id]["name"] if p.user_id in names else "Không rõ", "subtitle": _spent(p.seconds),
                                  "tone": "muted"} for p in people], "Đã bấm tham gia (không phải danh sách có mặt)"))
    subject = registry.SUBJECTS.get(c["subject_kind"], ("Đối tượng",))[0]
    target = await _subject(ctx, c["subject_kind"], c["subject_id"])
    return ui.card(title, blocks, subtitle=status, icon="video", actions=actions, headline=headline,
                   open=[{"title": f"Mở {subject[:1].lower()}{subject[1:]}", **target["open"]}] if target and target.get("open") else [])


class ListIn(Input):
    subject_kind: str = field("Loại đối tượng", "", hidden=True)
    subject_id: UUID | None = field("Đối tượng", None, hidden=True)


@m.query("calls", resource="meet", title="Cuộc gọi")
async def calls(ctx: Ctx, inp: ListIn) -> dict[str, Any]:
    """Cuộc gọi trên những đối tượng người xem thấy (RLS), mới nhất trước; truyền đối tượng ⇒ chỉ của đối tượng đó."""
    rows = await ctx.all(
        """SELECT c.id, c.title, c.status, c.started_at, c.ended_at, u.name AS started_by, CASE WHEN c.confirmed_at IS NOT NULL THEN 'yes' WHEN c.status = 'ended' THEN 'no' ELSE '' END AS confirmed,
                  (SELECT count(DISTINCT p.user_id) FROM meet.participants p WHERE p.call_id = c.id AND p.event = 'join') AS clicked
           FROM meet.calls c JOIN core.users u ON u.id = c.started_by
           WHERE (%(k)s = '' OR c.subject_kind = %(k)s) AND (%(s)s::uuid IS NULL OR c.subject_id = %(s)s::uuid)
           ORDER BY c.started_at DESC LIMIT 300""", {"k": inp.subject_kind, "s": inp.subject_id})
    return ui.table(rows, [
        ui.col("status", "", "badge", labels=STATUS, tones={"active": "green", "ended": "gray"}), ui.col("title", "Cuộc gọi"),
        ui.col("started_at", "Bắt đầu", "datetime"), ui.col("ended_at", "Kết thúc", "datetime"), ui.col("started_by", "Người gọi"),
        ui.col("clicked", "Đã bấm tham gia", "number"), ui.col("confirmed", "Người tham dự", "badge", labels={"yes": "Đã xác nhận", "no": "Chờ xác nhận"}, tones={"yes": "green", "no": "orange"}),
    ], kpis=[ui.kpi("Đang diễn ra", sum(r["status"] == "active" for r in rows)),
             ui.kpi("Chờ xác nhận", sum(r["confirmed"] == "no" for r in rows))],
        title="Cuộc gọi", empty="Chưa có cuộc gọi nào. Bấm Gọi trong một phòng hoặc trên khối Trao đổi của hồ sơ.", limit=300)


@m.plug("talk.thread_actions", "Nút Gọi trên khối trao đổi", resource="meet", action="write")
async def plug_thread_actions(ctx: Ctx, inp: SubjectIn) -> list[dict[str, Any]]:
    """Nút Gọi MỘT CHẠM như Messenger / Zalo / Lark (0114 ghi chú 07/11) — SERVER quyết gọi ai, app chỉ vẽ nút:
    đang có cuộc gọi ⇒ "Tham gia"; tin riêng / hồ sơ có người phụ trách khác mình ⇒ "Gọi" + "Gọi video" gọi thẳng người đó (nút điền sẵn hết ⇒ chạy ngay);
    phòng một người kia ⇒ như tin riêng; phòng nhiều người ⇒ "Gọi" mở ô chọn "Gọi ai?" (link ngoài nằm ở "Tuỳ chọn khác"), thêm "Gọi bằng link Meet/Zoom
    của công ty" khi công ty đã đặt link phòng. Gói tắt / thiếu quyền ⇒ khối trao đổi không có nút, phần còn lại không đổi."""
    ref = {"subject_kind": inp.subject_kind, "subject_id": str(inp.subject_id)}
    live = await ctx.one("SELECT id, provider, meet.my_seat(id) AS seat FROM meet.calls WHERE subject_kind = %s AND subject_id = %s AND status = 'active'",
                         (inp.subject_kind, inp.subject_id))
    if live:   # cuộc gọi 1–1 đã đủ hai người mà mình không ngồi ghế nào ⇒ không có gì để bấm (bấm "Tham gia" chỉ ra lỗi "đã đủ người")
        if live["provider"] == DIRECT and not live["seat"] and await ctx.val("SELECT 1 FROM meet.seats WHERE call_id = %s AND seat = 'b'", (live["id"],)):
            return []
        return [ui.act("meet.join", "Tham gia cuộc gọi", {"call_id": str(live["id"]), "enter": True, "video": False}, "instant")]
    subj = await _subject(ctx, inp.subject_kind, inp.subject_id)
    if subj is None:
        return []

    def direct(callee: str) -> list[dict[str, Any]]:   # điền sẵn MỌI ô ⇒ không còn gì để hỏi
        base = {**ref, "callee": callee, "join_url": ""}
        return [ui.act("meet.ring", "Gọi", {**base, "video": False}, "instant"), ui.act("meet.ring", "Gọi video", {**base, "video": True}, "instant")]

    if inp.subject_kind == ROOM:
        others = await _callable(ctx, inp.subject_kind, inp.subject_id)
        out = direct(others[0]["email"]) if len(others) == 1 else [ui.act("meet.ring", "Gọi", ref, "primary")] if others else []
        if await ctx.val("SELECT room_url FROM meet.settings"):
            out.append(ui.act("meet.ring", "Gọi bằng link Meet/Zoom của công ty", {**ref, "via_link": True, "callee": "", "video": False, "join_url": ""}, "instant"))
        return out
    owner = subj.get("owner_id")
    if owner and owner != ctx.p.user_id:
        return direct("")
    return [ui.act("meet.ring", "Gọi", ref, "primary")]   # hồ sơ của chính mình / chưa có người phụ trách: chọn gọi ai


# ── Cấu hình của công ty: dữ liệu, không có khoá bí mật nào ──

class SettingsIn(Input):
    room_url: str = field("Link phòng của công ty", "", max_length=500, audit=False, example="https://meet.google.com/…",
                          help="Phòng Google Meet / Zoom cố định: bấm Gọi không cần dán link. Trống = mỗi lần gọi tự dán")


@m.command("configure", resource="meet.settings", action="manage", title="Đặt link phòng của công ty", agent="propose")
async def configure(ctx: Ctx, inp: SettingsIn) -> dict[str, Any]:
    url = clean_url(inp.room_url) if inp.room_url.strip() else ""
    await ctx.exec("""INSERT INTO meet.settings (room_url, updated_by) VALUES (%s, core.me())
                      ON CONFLICT (tenant_id) DO UPDATE SET room_url = EXCLUDED.room_url, updated_by = EXCLUDED.updated_by, updated_at = now()""", (url,))
    return {"configured": bool(url), "summary": f"Đặt link phòng {_host(url)}" if url else "Bỏ link phòng: mỗi lần gọi tự dán link"}


@m.query("settings", resource="meet.settings", title="Cấu hình gọi video")
async def settings_page(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    url = await ctx.val("SELECT room_url FROM meet.settings") or ""
    return ui.card("Gọi video", [ui.b_callout("accent" if url else "muted", "Link phòng của công ty" if url else "Chưa đặt link phòng",
                                              "Barista không tạo phòng và không giữ khoá nào của Google / Zoom: cuộc gọi diễn ra ở dịch vụ công ty đang dùng.",
                                              draft=url if ctx.p.via != "agent" else "")],
                   subtitle="Dịch vụ gọi của chính công ty", icon="video", actions=[ui.act("meet.configure", "Đặt link phòng", {"room_url": url} if ctx.p.via != "agent" else {})])


m.screens("work", (Screen("meet_calls", "Cuộc gọi", "video", "meet.calls", tint="teal", row_click="open", open=Open("meet.call", {"call_id": "id"}, "Cuộc gọi")),), rank=15)
m.screens("admin", (Screen("meet_settings", "Gọi video", "video", "meet.settings", tint="gray", group="Hệ thống"),), rank=60)



# ── HTTP của cuộc gọi trực tiếp 1–1 (``Module.route``; 0114 bổ sung 07/11): kênh TÍN HIỆU (offer / answer / ICE) giữa đúng hai ghế. Tiếng / hình
# không qua đây. Mỗi lần có tín hiệu mới (NOTIFY chỉ mang mã cuộc gọi), kênh đọc lại thư gửi cho MÌNH qua executor bằng quyền người nghe (RLS: còn
# thấy đối tượng của cuộc gọi, đúng ghế của mình) rồi mới gửi xuống. Sự kiện như kênh trao đổi (0115): gone / auth / retry, thêm signal + ended. ──

@m.route("GET", "/v1/meet/calls/{call_id}/stream")
async def http_stream(call_id: UUID, request: web.Request, after: int = 0) -> web.StreamingResponse:
    """SSE tín hiệu của một cuộc gọi trực tiếp cho thành viên đang ngồi một ghế. ``after``: mã thư cuối đã nhận (nối lại sau khi rớt mạng không mất thư)."""
    who = web.Listener(request)

    async def fetch(since: int) -> tuple[str, Any]:
        return await who.fetch("meet.signals", {"call_id": str(call_id), "after": since})

    first, _ = await fetch(after)
    if first != "ok":
        raise web.HTTPException({"gone": 404, "auth": 401}.get(first, 503), "Không nghe được cuộc gọi này")
    p = await who.principal()
    return web.sse(web.relay(request, f"meet:{p.tenant_id}:{call_id}", after, fetch))


@m.route("POST", "/v1/meet/calls/{call_id}/signal")
async def http_signal(call_id: UUID, request: web.Request, body: Annotated[dict[str, Any], web.Body()]) -> web.JSONResponse:
    """Gửi một tín hiệu tới người kia — qua executor (quyền, RLS ghế), ``quiet``: không thành dòng nhật ký thao tác cho mỗi ICE."""
    p = await web.caller(request)
    return web.result(await web.run(p, "meet.signal", {**body, "call_id": str(call_id)}, quiet=True))


@m.route("GET", "/v1/public/links/{token}/stream", public="người ngoài cầm link mời — quyền = mã link (web.link_caller: hạn, thu hồi, tần suất)")
async def http_link_stream(token: str, request: web.Request, after: int = 0) -> web.StreamingResponse:
    """SSE tín hiệu cho người ngoài cầm link mời (``meet.link_signals``). Gửi tín hiệu / báo có mặt qua link: route chung của lõi
    ``/v1/public/links/{token}/signal`` · ``/presence`` (thao tác ``link_signal`` / ``link_presence`` của module này)."""
    p0 = await web.link_caller(token, "view")
    who = web.LinkListener(token, p0)

    async def fetch(since: int) -> tuple[str, Any]:
        return await who.fetch("meet.link_signals", {"after": since})

    first, out = await fetch(after)
    if first != "ok":
        raise web.HTTPException(404 if first == "gone" else 503, "Link không còn hiệu lực — liên hệ bên mời để nhận link mới")
    return web.sse(web.relay(request, f"meet:{p0.tenant_id}:{out['call_id']}", after, fetch))
