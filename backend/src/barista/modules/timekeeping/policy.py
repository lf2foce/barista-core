"""CHÍNH SÁCH CHẤM CÔNG là dữ liệu của công ty (0150 §4.3): địa điểm + luật chấm (dải IP văn phòng), giờ làm cố định, ai chấm theo cách nào, loại nghỉ.
Sửa trong app; code chỉ kiểm hợp lệ. Mỗi thứ ở đây quyết ngày công có lương / cờ của lần chấm ⇒ rủi ro "quyền", trợ lý không làm (review 10/11)."""

import ipaddress
import re
from typing import Any, Literal

from barista.api import Ctx, Day, Denied, Input, Invalid, NoInput, NotFound, field, today, ui
from barista.modules.timekeeping import MODES, PAID_BY, m
from barista.modules.timekeeping.common import enrolments, member, names

_CODE = re.compile(r"[A-Z0-9][A-Z0-9_-]{0,29}")
# Dải nội bộ (mạng LAN, CGNAT): máy chủ không bao giờ thấy làm IP của văn phòng. Dải "tài liệu" (TEST-NET) vẫn nhận — dữ liệu mẫu dùng nó.
INTERNAL = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7"))
WEEKDAY = {1: "T2", 2: "T3", 3: "T4", 4: "T5", 5: "T6", 6: "T7", 7: "CN"}


def _need_policy(ctx: Ctx) -> None:
    if not ctx.p.has_scope("timekeeping.policy", "all"):
        raise Denied("Cần quyền chính sách chấm công cả công ty")


def parse_ranges(text: str) -> list[str]:
    """Dải IP công khai (CIDR hoặc một địa chỉ), cách nhau bởi dấu phẩy / xuống dòng. Dải nội bộ (192.168.x…) vô nghĩa với máy chủ ⇒ từ chối."""
    out = []
    for part in re.split(r"[\s,;]+", text.strip()):
        if not part:
            continue
        try:
            net = ipaddress.ip_network(part, strict=False)
        except ValueError as e:
            raise Invalid(f"“{part}” không phải địa chỉ / dải IP") from e
        if net.is_loopback or net.is_link_local or any(net.version == n.version and net.subnet_of(n) for n in INTERNAL):  # type: ignore[arg-type]
            raise Invalid(f"{part} là địa chỉ nội bộ — máy chủ chỉ thấy IP CÔNG KHAI của văn phòng (hỏi nhà mạng / quản trị mạng)")
        if net.prefixlen < (24 if net.version == 4 else 56):   # dải rộng hơn /24 (IPv6 /56) gần như chắc chắn gồm cả mạng ngoài văn phòng
            raise Invalid(f"{part} quá rộng — dải IP văn phòng là một địa chỉ hoặc tối đa /24 (IPv6 /56); nhiều đường mạng thì ghi từng dải")
        out.append(str(net))
    return out


class UpsertSite(Input):
    code: str = field("Mã địa điểm", max_length=30, example="VP-HN", help="Chữ HOA không dấu, số, gạch")
    name: str = field("Tên", max_length=120, example="Văn phòng Hà Nội")
    ip_ranges: str = field("IP công khai của văn phòng", "", kind="textarea", max_length=1000, example="203.0.113.10, 198.51.100.0/28",
                           help="Một hoặc nhiều địa chỉ / dải. Chấm từ mạng khác vẫn được ghi, gắn cờ cho quản lý xét")
    active: bool = field("Đang dùng", True)


@m.command("upsert_site", resource="timekeeping.policy", action="manage", title="Thêm / sửa địa điểm chấm công", agent="deny", risk="authority")
async def upsert_site(ctx: Ctx, inp: UpsertSite) -> dict[str, Any]:
    """Luật chấm của địa điểm (dữ liệu). Đổi luật chỉ áp cho lần chấm SAU; lần chấm cũ giữ kết quả kiểm đã ghi."""
    _need_policy(ctx)
    code = inp.code.strip().upper()
    if not _CODE.fullmatch(code):
        raise Invalid("Mã: chữ HOA không dấu, số, gạch")
    ranges = parse_ranges(inp.ip_ranges)
    sid = await ctx.val("""INSERT INTO timekeeping.sites(code,name,ip_ranges,active,created_by) VALUES(%s,%s,%s::cidr[],%s,core.me())
                           ON CONFLICT (tenant_id,code) DO UPDATE SET name=EXCLUDED.name,ip_ranges=EXCLUDED.ip_ranges,active=EXCLUDED.active,updated_at=now()
                           RETURNING id""", (code, inp.name.strip(), ranges, inp.active))
    return {"site_id": sid, "summary": f"Địa điểm {inp.name.strip()} ({code}): {', '.join(ranges) or 'chưa có IP — mọi lần chấm gắn cờ'}"}


class UpsertPattern(Input):
    code: str = field("Mã giờ làm", max_length=30, example="HC", help="Chữ HOA không dấu, số, gạch")
    name: str = field("Tên", max_length=120, example="Giờ hành chính")
    weekdays: str = field("Ngày làm trong tuần", "1,2,3,4,5", max_length=20, example="1,2,3,4,5", help="1 = thứ Hai … 7 = Chủ nhật")
    starts: str = field("Giờ vào", "08:00", kind="time", max_length=5, example="08:00")
    ends: str = field("Giờ ra", "17:00", kind="time", max_length=5, example="17:30")
    break_minutes: int = field("Nghỉ giữa ca (phút)", 60, ge=0, le=240)
    grace_minutes: int = field("Đi muộn được tha (phút)", 5, ge=0, le=120)
    active: bool = field("Đang dùng", True)


@m.command("upsert_pattern", resource="timekeeping.policy", action="manage", title="Thêm / sửa giờ làm", agent="deny", risk="authority")
async def upsert_pattern(ctx: Ctx, inp: UpsertPattern) -> dict[str, Any]:
    _need_policy(ctx)
    code = inp.code.strip().upper()
    if not _CODE.fullmatch(code):
        raise Invalid("Mã: chữ HOA không dấu, số, gạch")
    try:
        days = sorted({int(x) for x in re.split(r"[\s,;]+", inp.weekdays.strip()) if x})
    except ValueError as e:
        raise Invalid("Ngày làm: các số 1–7 cách nhau bởi dấu phẩy") from e
    if not days or not set(days) <= set(WEEKDAY):
        raise Invalid("Ngày làm: các số 1–7 cách nhau bởi dấu phẩy")
    if not all(re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t) for t in (inp.starts, inp.ends)) or inp.ends <= inp.starts:
        raise Invalid("Giờ dạng HH:MM, giờ ra sau giờ vào (ca qua đêm: chưa có ở lát này)")
    pid = await ctx.val("""INSERT INTO timekeeping.patterns(code,name,weekdays,starts,ends,break_minutes,grace_minutes,active,created_by)
                           VALUES(%s,%s,%s,%s::time,%s::time,%s,%s,%s,core.me())
                           ON CONFLICT (tenant_id,code) DO UPDATE SET name=EXCLUDED.name,weekdays=EXCLUDED.weekdays,starts=EXCLUDED.starts,ends=EXCLUDED.ends,
                             break_minutes=EXCLUDED.break_minutes,grace_minutes=EXCLUDED.grace_minutes,active=EXCLUDED.active,updated_at=now()
                           RETURNING id""", (code, inp.name.strip(), days, inp.starts, inp.ends, inp.break_minutes, inp.grace_minutes, inp.active))
    return {"pattern_id": pid, "summary": f"{inp.name.strip()}: {', '.join(WEEKDAY[d] for d in days)} {inp.starts}–{inp.ends}, nghỉ {inp.break_minutes} phút"}


class Enrol(Input):
    person: str = field("Người", options_query="admin.member_options", help="Email thành viên")
    pattern_code: str = field("Giờ làm", options_query="timekeeping.pattern_options")
    site_code: str = field("Địa điểm", "", options_query="timekeeping.site_options", help="Nơi kiểm IP khi chấm; trống = mọi lần chấm gắn cờ")
    mode: Literal["punch", "exception", "off"] = field("Cách tính công", "punch", options=MODES)
    valid_from: Day = field("Từ ngày", example="01/11/2026", help="Đổi ⇒ dòng mới từ ngày mới; dòng cũ giữ làm lịch sử")


@m.command("enrol", resource="timekeeping.policy", action="manage", title="Gán giờ làm / địa điểm cho người", agent="deny", risk="authority")
async def enrol(ctx: Ctx, inp: Enrol) -> dict[str, Any]:
    """Cách tính công quyết công có lương (đủ công theo lịch hay theo giờ chấm) ⇒ người gán không gán cho chính mình."""
    _need_policy(ctx)
    who = await member(ctx, inp.person)
    if who["id"] == ctx.p.user_id:   # DB cũng chặn (trigger enrolments_self)
        raise Denied("Không tự gán cách chấm công cho chính mình — nhờ người phụ trách khác")
    pid = await ctx.val("SELECT id FROM timekeeping.patterns WHERE code=%s AND active", (inp.pattern_code.strip().upper(),))
    if pid is None:
        raise NotFound("Không có giờ làm này (hoặc đã ngừng)")
    sid = None
    if inp.site_code:
        sid = await ctx.val("SELECT id FROM timekeeping.sites WHERE code=%s AND active", (inp.site_code.strip().upper(),))
        if sid is None:
            raise NotFound("Không có địa điểm này (hoặc đã ngừng)")
    if await ctx.val("SELECT 1 FROM timekeeping.enrolments WHERE user_id=%s AND valid_from=%s", (who["id"], inp.valid_from)):
        raise Invalid(f"Đã có dòng từ {inp.valid_from:%d/%m/%Y} — chọn ngày khác")
    eid = await ctx.val("""INSERT INTO timekeeping.enrolments(user_id,team_id,pattern_id,site_id,mode,valid_from,created_by)
                           VALUES(%s,%s,%s,%s,%s,%s,core.me()) RETURNING id""",
                        (who["id"], await ctx.home_team(who["id"]), pid, sid, inp.mode, inp.valid_from))
    return {"enrolment_id": eid, "summary": f"{who['name']}: {MODES[inp.mode].lower()} từ {inp.valid_from:%d/%m/%Y}"}


class UpsertLeaveType(Input):
    code: str = field("Mã loại nghỉ", max_length=30, example="PHEP_NAM")
    name: str = field("Tên", max_length=120, example="Nghỉ phép năm")
    paid_by: Literal["company", "insurance", "none"] = field("Ai trả", "company", options=PAID_BY)
    uses_balance: bool = field("Trừ vào số dư phép", False, help="Có ⇒ đơn nghỉ cần đủ số dư trong sổ phép của năm")
    active: bool = field("Đang dùng", True)


@m.command("upsert_leave_type", resource="timekeeping.policy", action="manage", title="Thêm / sửa loại nghỉ", agent="deny", risk="authority")
async def upsert_leave_type(ctx: Ctx, inp: UpsertLeaveType) -> dict[str, Any]:
    _need_policy(ctx)
    code = inp.code.strip().upper()
    if not _CODE.fullmatch(code):
        raise Invalid("Mã: chữ HOA không dấu, số, gạch")
    await ctx.exec("""INSERT INTO timekeeping.leave_types(code,name,paid_by,uses_balance,active,created_by) VALUES(%s,%s,%s,%s,%s,core.me())
                      ON CONFLICT (tenant_id,code) DO UPDATE SET name=EXCLUDED.name,paid_by=EXCLUDED.paid_by,uses_balance=EXCLUDED.uses_balance,
                        active=EXCLUDED.active,updated_at=now()""", (code, inp.name.strip(), inp.paid_by, inp.uses_balance, inp.active))
    return {"code": code, "summary": f"Loại nghỉ {inp.name.strip()} ({PAID_BY[inp.paid_by].lower()})"}


# Bộ loại nghỉ thường gặp (dữ liệu khởi đầu — công ty sửa / thêm): căn cứ BLLĐ Đ113 (phép năm), Đ115 (nghỉ việc riêng có lương / không lương);
# ốm đau, thai sản do quỹ BHXH trả (Luật BHXH) ⇒ công ty không trả lương ngày đó.
DEFAULT_LEAVE = (("PHEP_NAM", "Nghỉ phép năm", "company", True), ("VIEC_RIENG", "Nghỉ việc riêng có lương (cưới, tang)", "company", False),
                 ("OM_BHXH", "Nghỉ ốm (hưởng BHXH)", "insurance", False), ("THAI_SAN", "Nghỉ thai sản (hưởng BHXH)", "insurance", False),
                 ("KHONG_LUONG", "Nghỉ không lương", "none", False))


@m.command("install_leave_types", resource="timekeeping.policy", action="manage", title="Cài loại nghỉ thường gặp", agent="deny", risk="authority")
async def install_leave_types(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    _need_policy(ctx)
    n = 0
    for code, name, paid, bal in DEFAULT_LEAVE:
        n += await ctx.exec("""INSERT INTO timekeeping.leave_types(code,name,paid_by,uses_balance,created_by) VALUES(%s,%s,%s,%s,core.me())
                               ON CONFLICT (tenant_id,code) DO NOTHING""", (code, name, paid, bal))
    return {"added": n, "summary": f"Đã thêm {n} loại nghỉ (loại đã có giữ nguyên)"}


@m.query("site_options", resource="timekeeping.policy", title="Địa điểm chấm công")
async def site_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return ui.options(await ctx.all("SELECT code AS value, name || ' (' || code || ')' AS label FROM timekeeping.sites WHERE active ORDER BY name"))


@m.query("pattern_options", resource="timekeeping.policy", title="Giờ làm")
async def pattern_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return ui.options(await ctx.all("SELECT code AS value, name || ' (' || code || ')' AS label FROM timekeeping.patterns WHERE active ORDER BY name"))


@m.query("leave_type_options", resource="timekeeping", title="Loại nghỉ")
async def leave_type_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return ui.options(await ctx.all("SELECT code AS value, name AS label FROM timekeeping.leave_types WHERE active ORDER BY name"))


@m.query("policy", resource="timekeeping.policy", title="Địa điểm, giờ làm, loại nghỉ")
async def policy(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    sites = await ctx.all("SELECT code,name,array_to_string(ip_ranges,', ') AS ips,active FROM timekeeping.sites ORDER BY code")
    pats = await ctx.all("""SELECT code,name,weekdays,to_char(starts,'HH24:MI') || '–' || to_char(ends,'HH24:MI') AS hours,break_minutes,grace_minutes
                            FROM timekeeping.patterns WHERE active ORDER BY code""")
    for p in pats:
        p["days"] = ", ".join(WEEKDAY[d] for d in p.pop("weekdays"))
    types = await ctx.all("SELECT code,name,paid_by,uses_balance FROM timekeeping.leave_types WHERE active ORDER BY code")
    for t in types:
        t["balance"] = "Có" if t.pop("uses_balance") else "—"
    for s in sites:
        s["state"] = "on" if s.pop("active") else "off"
    return ui.card("Chính sách chấm công", [
        ui.b_table(ui.table(sites, [ui.col("code", "Mã"), ui.col("name", "Địa điểm"), ui.col("ips", "IP văn phòng", width=260),
                                    ui.col("state", "Đang dùng", "badge", labels={"on": "Có", "off": "Ngừng"}, tones={"on": "green", "off": "gray"})],
                            title="Địa điểm", empty="Chưa có địa điểm — thêm IP văn phòng để kiểm lần chấm")),
        ui.b_table(ui.table(pats, [ui.col("code", "Mã"), ui.col("name", "Giờ làm"), ui.col("days", "Ngày"), ui.col("hours", "Giờ"),
                                   ui.col("break_minutes", "Nghỉ (phút)", "number"), ui.col("grace_minutes", "Tha muộn (phút)", "number")],
                            title="Giờ làm", empty="Chưa có giờ làm")),
        ui.b_table(ui.table(types, [ui.col("code", "Mã"), ui.col("name", "Loại nghỉ"), ui.col("paid_by", "Ai trả", "badge", labels=PAID_BY),
                                    ui.col("balance", "Trừ số dư")], title="Loại nghỉ", empty="Chưa có loại nghỉ — cài bộ thường gặp")),
    ], icon="gearshape", actions=[ui.act("timekeeping.upsert_site", "Thêm địa điểm"), ui.act("timekeeping.upsert_pattern", "Thêm giờ làm"),
                                   ui.act("timekeeping.install_leave_types", "Cài loại nghỉ thường gặp")])


@m.query("enrolments", resource="timekeeping", min_scope="all", title="Ai chấm công thế nào")
async def enrolments_q(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = list((await enrolments(ctx, today())).values())
    who = await names(ctx, [r["user_id"] for r in rows])
    for r in rows:
        r["id"] = str(r["user_id"])
        r["name"] = who.get(str(r["user_id"]), "")
    rows.sort(key=lambda r: r["name"])
    return ui.table(rows, [ui.col("name", "Người"), ui.col("mode", "Cách tính công", "badge", labels=MODES,
                                                           tones={"punch": "blue", "exception": "green", "off": "gray"}),
                           ui.col("pattern_name", "Giờ làm"), ui.col("site_name", "Địa điểm"), ui.col("valid_from", "Từ ngày", "date")],
                    title="Ai chấm công thế nào", limit=2000, empty="Chưa gán ai — gán giờ làm và địa điểm cho từng người")
