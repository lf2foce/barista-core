"""LÕI LEAD — nhận lead từ nhiều nguồn, chia theo luật, hạn xử lý, thu hồi, hàng chờ (quyết định 0034, 0038, 0152).

* Cửa vào duy nhất là ``leadflow.intake`` (Excel, webhook Meta / web / tổng đài đều qua đây): chống lặp theo (nhà cung cấp, mã ngoài), tạo khách
  + lead KHÔNG chủ, rồi chia ngay. Người nạp có thể chỉ định "giao cho đội X / người Y", ghi người đã lọc lead. Khách đã có lead quay lại qua
  kênh khác = SỰ KIỆN (báo người đang chăm, mở lại lead đã mất theo luật) — không nuốt im, vẫn một người một khách.
* Luật chia là DỮ LIỆU (``leadflow.rules``): nguồn lead, [[offering|l]], cách nhận (tự chia / hàng chờ điều phối), người nhận (đội + vai, hoặc
  THÀNH VIÊN của [[offering|l]] có hạng + trọng số), cách chia, trần, hạn gọi đầu, THẾ NÀO LÀ ĐÃ LIÊN HỆ, nhắc trước hạn, báo trưởng nhóm, ân hạn
  trước thu hồi, bước bấm nhận, lịch nghỉ lễ, lead ngoài giờ (hoãn hạn tới giờ mở / chia đội trực). Chỉ thêm, hiệu lực theo ngày; luật CỤ THỂ
  NHẤT thắng: nguồn + [[offering|l]] → nguồn → [[offering|l]] → chung. Hạn của mỗi lead CHỤP lúc chia.
* Ai nhận được KHÔNG viết cứng theo tên vai: vai trong luật + vai có quyền xem / ghi Khách hàng (ma trận quyền), không tạm ngừng, không nghỉ phép
  (gói chấm công cắm vào điểm ``leadflow.away`` — gói tắt ⇒ như chưa có). Công tắc công ty ``leadflow.members_only`` (mặc định tắt): người nhận
  chỉ nhận / thấy lead và căn của [[offering|l]] mình là thành viên — chặn ở RLS (crm.leads và bảng [[offering|l]] của gói ngành), không chỉ ở code.
* Đồng hồ hạn chỉ chạy trong giờ làm việc trừ ngày lễ (``workhours`` + ``business_calendar``). Mọi chuyện ghi vào ``leadflow.sla_events`` (chỉ thêm).
* Thu hồi và chấm hạn do MỘT vòng quét (``enforce_sla``). "Đã liên hệ chưa" lấy từ chăm sóc của module Khách + cuộc gọi tổng đài thật (điểm cắm
  ``leadflow.contact_proof`` — gói tổng đài tắt ⇒ chỉ còn liên hệ ghi tay).

Mã (0088): ``__init__`` (module, hằng, luật, thành viên, tạm ngừng) · ``routing`` (ai nhận được, chọn người, chia, nạp lead, khách quay lại, hàng
chờ, nhận lead, chuyển tay) · ``sweep`` (vòng quét) · ``reads`` (hạn của lead, tổng quan, bảng phản hồi, vì sao lead tới tay ai, lead của tôi,
gợi ý buổi sáng, sức khoẻ) · ``screens`` (màn + câu tín hiệu bàn việc).
"""

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from barista.api import (
    Conflict,
    Ctx,
    Day,
    Denied,
    Hours,
    Input,
    Invalid,
    Module,
    NoInput,
    NotFound,
    add_working_minutes,
    business_calendar,
    company_settings,
    field,
    today,
    ui,
    zone,
)
from barista.api.lead_labels import SOURCES

m = Module("leadflow", "Chia lead & hạn xử lý", pack="crm", resources={"leadflow": "Chia lead & hạn xử lý"})
m.consumes("plug")   # điểm cắm của chính module: leadflow.contact_proof (tổng đài), leadflow.away (nghỉ phép — chấm công)
MEMBERS_ONLY = m.setting(
    "members_only", "Người nhận lead chỉ nhận và thấy lead, căn của [[offering|l]] mình là thành viên", default=False,
    help="Bật: sale (phạm vi chia lead của mình) chỉ được chia, giao, thấy lead và bảng hàng của [[offering|l]] có tên mình trong danh sách thành viên "
         "(Kinh doanh › Thành viên [[offering|l]]) — chặn ở cơ sở dữ liệu. Trưởng nhóm, quản lý không bị chặn. Tắt: như cũ, ai cũng thấy mọi [[offering|l]].")

STRATEGIES = {"rotate": "Xoay vòng (người nhận lâu nhất được chia trước)", "least_loaded": "Theo tải (ít lead chưa gọi nhất)"}
STRATEGY_SHORT = {"rotate": "xoay vòng", "least_loaded": "theo tải (ít lead chưa gọi nhất)"}
MODES = {"auto": "Hệ thống tự chia", "coordinator": "Về hàng chờ để một đội điều phối chia"}
RECEIVERS = {"team": "Theo đội + vai", "members": "Thành viên của [[offering|l]] (hạng, trọng số)"}
PROOFS = {"any": "Mọi liên hệ trừ ghi chú", "call": "Có ghi cuộc gọi (kể cả không nghe máy)", "connected": "Cuộc gọi nói chuyện được",
          "meeting": "Gặp / tham quan", "message": "Nhắn Zalo / email / gửi tài liệu", "pbx": "Cuộc gọi tổng đài khách nghe máy"}
PROOF_SHORT = {"any": "mọi liên hệ (trừ ghi chú)", "call": "một cuộc gọi đã ghi", "connected": "cuộc gọi nói chuyện được", "meeting": "buổi gặp / tham quan",
               "message": "tin nhắn / email", "pbx": "cuộc gọi tổng đài khách nghe máy"}
OFF_HOURS = {"defer": "Hạn bắt đầu từ giờ mở cửa kế tiếp", "duty": "Chia cho đội trực trong giờ trực"}
SOURCE_LABEL = {"": "Mọi nguồn", **SOURCES}
DAY_PRESETS = {"mon_fri": ((1, 2, 3, 4, 5), "T2–T6"), "mon_sat": ((1, 2, 3, 4, 5, 6), "T2–T7"), "all": ((1, 2, 3, 4, 5, 6, 7), "Cả tuần")}
EVENT_LABEL = {"assigned": "Được chia", "queued": "Vào hàng chờ", "first_call_met": "Liên hệ lần đầu", "first_call_breached": "Quá hạn gọi",
               "recalled": "Thu hồi", "followup_breached": "Quá hạn chăm sóc lại", "reminded": "Nhắc trước hạn", "escalated": "Báo trưởng nhóm",
               "accepted": "Bấm nhận lead", "accept_missed": "Không bấm nhận kịp", "returned": "Khách quay lại", "reopened": "Mở lại lead đã mất",
               "handed_over": "Chuyển người giữ"}
# vì sao lead đổi người giữ mà KHÔNG phải thu hồi (0153): không tính vào số lần thu hồi, ghi vào sổ sự kiện của lead
HANDOVER = {"away": "người giữ đang nghỉ phép", "deactivated": "người giữ đã nghỉ việc / bị khoá tài khoản",
            "lost_access": "vai của người giữ không còn quyền xem + ghi Khách hàng", "left_team": "người giữ đã rời đội nhận lead của luật",
            "left_offering": "người giữ đã rời danh sách thành viên [[offering|l]]", "manager": "quản lý chuyển tay"}
QUEUE_ESCALATE_MIN = 240                           # mặc định (luật cũ / chưa có luật): lead chờ quá 4 giờ làm việc ⇒ báo người sở hữu hàng chờ
QUEUE_REASON = {"no_rule": "chưa có luật chia", "no_eligible": "không có ai đủ điều kiện nhận", "cap": "mọi người đã chạm trần lead chưa gọi",
                "recall_limit": "đã thu hồi hết số lần cho phép", "coordinator": "chờ đội điều phối chia", "team": "giao cho đội chỉ định chia",
                "paused": "đang tạm ngừng chia lead"}
# lead ĐỖ (0185): trạng thái chia nạp từ hệ khác (``lead_state.imported_at``) là tham chiếu — không chấm hạn / nhắc / thu hồi / chia lại cho tới khi Barista tự chia
PARKED_LABEL = "nạp từ hệ cũ — chưa kích hoạt"
DRAINABLE = ("no_rule", "no_eligible", "cap", "paused")   # chỉ những lý do này vòng quét tự thử chia lại (tạm ngừng: khi bật lại); chờ điều phối thì để người chia
PLANNED = ("coordinator", "team", "paused")                # nằm hàng chờ CÓ CHỦ Ý (không phải kẹt)
OPEN = ("offered", "waiting")                      # chưa có liên hệ đầu: đang chạy hạn gọi (và hạn bấm nhận nếu luật có)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)   # mốc xếp hạng "chưa nhận lần nào" — chỉ so, không hiện
SWEEP_LIMIT = 500


def hm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_hm(s: str) -> int:
    try:
        h, mi = s.strip().split(":")
        v = int(h) * 60 + int(mi)
    except ValueError as e:
        raise Invalid(f"Giờ phải dạng 08:30 (nhận “{s}”)") from e
    if not 0 <= v <= 1440:
        raise Invalid(f"Giờ ngoài khoảng 00:00–24:00: {s}")
    return v


def holidays_of(calendar: str) -> frozenset[date]:
    """Ngày lễ / Tết của lịch luật chọn (lịch nghiệp vụ có phiên bản — 0068). Lịch đã gỡ khỏi bản cài ⇒ không trừ (luật đọc thành câu có cảnh báo)."""
    if not calendar:
        return frozenset()
    try:
        return business_calendar.holidays(calendar)
    except ValueError:
        return frozenset()


def hours_of(rule: dict[str, Any]) -> Hours:
    """Giờ làm việc của luật (hoặc bản chụp luật trên lead): thứ + giờ mở / đóng, trừ ngày lễ của lịch đã chọn."""
    return Hours(tuple(rule["work_days"]), rule["work_open_min"], rule["work_close_min"], holidays_of(rule.get("calendar") or ""))


def duty_hours_of(rule: dict[str, Any]) -> Hours:
    """Giờ TRỰC (lead về ngoài giờ làm việc, luật chọn đội trực) — cũng trừ ngày lễ. Đồng hồ của lead chia đội trực chạy trong HỢP giờ trực và
    giờ làm việc (``clock_of``): hạn không tràn sang ca trực tối hôm sau khi sáng mai đội thường đã đi làm (review 10/11)."""
    return Hours(tuple(rule.get("duty_days") or (1, 2, 3, 4, 5, 6, 7)), rule.get("duty_open_min", 1080), rule.get("duty_close_min", 1320),
                 holidays_of(rule.get("calendar") or ""))


def clock_of(rule: dict[str, Any], duty: bool) -> tuple[Hours, ...]:
    """Các khung giờ mà đồng hồ hạn của MỘT lead chạy: giờ làm việc; lead chia đội trực ⇒ giờ trực ∪ giờ làm việc (cái nào tới trước tính trước)."""
    return (duty_hours_of(rule), hours_of(rule)) if duty else (hours_of(rule),)


def clock() -> datetime:
    """Giờ hiện tại của chia lead — MỘT chỗ để test đặt đồng hồ (đêm, sát giờ đóng cửa) mà không đổi giờ máy."""
    return datetime.now(zone())


def _open_until(at: datetime, h: Hours) -> datetime | None:
    """``at`` đang trong giờ của ``h`` ⇒ lúc khung giờ hôm đó đóng; không ⇒ None."""
    if add_working_minutes(at, 0, h) != at:
        return None
    local = at.astimezone(zone())
    return local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=h.close_min)


def due_in(at: datetime, minutes: int, hs: tuple[Hours, ...]) -> datetime:
    """``at`` + ``minutes`` phút trong HỢP các khung giờ ``hs`` (một khung ⇒ đúng ``add_working_minutes``). Khung kề nhau (18:00 hết giờ làm,
    18:00 vào ca trực) chạy liền; hết mọi khung ⇒ dừng tới khung mở sớm nhất kế tiếp."""
    if len(hs) == 1:
        return add_working_minutes(at, minutes, hs[0])
    cur = min(add_working_minutes(at, 0, h) for h in hs)
    left = timedelta(minutes=minutes)
    for _ in range(400):
        end = max(e for h in hs if (e := _open_until(cur, h)) is not None)
        if left <= end - cur:
            return cur + left
        left -= end - cur
        cur = min(add_working_minutes(end, 0, h) for h in hs)
    raise ValueError("đồng hồ hạn không tới được hạn (lịch không mở)")


def open_in(at: datetime, hs: tuple[Hours, ...]) -> bool:
    """Đồng hồ hạn đang chạy lúc ``at`` (trong ít nhất một khung) — ngoài giờ thì thông báo không rung máy người nhận."""
    return any(_open_until(at, h) is not None for h in hs)


def _span(a: datetime, b: datetime) -> str:
    mins = int(abs((b - a).total_seconds()) // 60)
    return f"{mins} phút" if mins < 120 else (f"{mins // 60} giờ" if mins < 2880 else f"{mins // 1440} ngày")


async def _team_id(ctx: Ctx, code: str) -> UUID | None:
    if not code:
        return None
    tid = await ctx.val("SELECT id FROM core.teams WHERE code = %s", (code,))
    if tid is None:
        raise NotFound(f"Không có đội {code}")
    return tid


async def _user_by_email(ctx: Ctx, email: str) -> UUID:
    uid = await ctx.val("SELECT u.id FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant()"
                        " WHERE u.email = %s AND mb.active", (email.lower(),))
    if uid is None:
        raise NotFound(f"Không có người {email} trong công ty")
    return uid


async def _lead_roles(ctx: Ctx) -> set[str]:
    """Vai làm được lead = vai có quyền XEM và GHI Khách hàng trong ma trận quyền (không viết cứng "sale")."""
    return {r["role"] for r in await ctx.all(
        "SELECT role FROM core.grants WHERE resource = 'crm' AND 'read' = ANY(actions) AND 'write' = ANY(actions)")}


async def members_only(ctx: Ctx) -> bool:
    return await company_settings.enabled(ctx.conn, ctx.p.tenant_id, MEMBERS_ONLY)


async def _offering_code(ctx: Ctx, code: str) -> str:
    return (await ctx.call_system("crm.offering_by_code", code=code))["code"] if code.strip() else ""


async def _routing_on(ctx: Ctx, code: str) -> bool:
    """[[offering|l]] có đang nhận CHIA lead không. Gói danh mục trả ``lead_routing`` (đã có trong danh mục nhưng chưa mở / tạm dừng chia); gói không
    trả cờ, chưa có danh mục hoặc mã không có trong danh mục ⇒ bật. Tắt ⇒ lead VẪN vào (khách không mất), nằm Lead chờ chia tới khi bật lại."""
    if not code.strip():
        return True
    try:
        hit = await ctx.call_system("crm.offering_by_code", code=code)
    except NotFound:
        return True
    return bool((hit or {}).get("lead_routing", True))


# ── Luật ──

G1 = "Cách chia & hạn xử lý"
G2 = "Giờ làm việc & ngày lễ"
G3 = "Ngoài giờ & trực"
G4 = "Kế hoạch & khác"


class SetRule(Input):
    name: str = field("Tên luật", example="Lead quảng cáo qua team Digital", max_length=120)
    source: Literal["", "ads", "telesales", "self", "referral", "event", "import"] = field(
        "Nguồn lead", "", options=SOURCE_LABEL, help="Trống = mọi nguồn. Luật theo nguồn thắng luật theo [[offering|l]]")
    offering_code: str = field("[[offering]]", "", options_query="crm.offering_options", help="Trống = mọi [[offering|l]]")
    effective_from: Day = field("Hiệu lực từ", help="Luật cũ giữ nguyên để tra cứu; lead đã chia giữ hạn đã chụp")
    mode: Literal["auto", "coordinator"] = field("Cách nhận", "auto", options=MODES)
    coord_team_code: str = field("Đội điều phối", "", options_query="admin.team_options", group="Điều phối",
                                 help="Chỉ cho chế độ điều phối: lead nằm hàng chờ của đội này, trưởng đội chia (có gợi ý người nhận)")
    receivers: Literal["team", "members"] = field("Người nhận", "team", options=RECEIVERS,
                                                  help="Thành viên: chia theo danh sách thành viên của [[offering|l]] của lead — hạng 1 nhận trước, trong hạng "
                                                       "chia theo trọng số (Kinh doanh › Thành viên [[offering|l]])")
    team_code: str = field("Đội nhận lead", "", options_query="admin.team_options", help="Ai được giao lead (gồm đội con). Trống = cả công ty")
    roles: list[str] = field("Vai được nhận", ["sale"], options_query="admin.role_options",
                             help="Chỉ vai có quyền xem + ghi Khách hàng mới chọn được (không thì người nhận không thấy lead)")
    # rà form nhóm B 18/11/2026: 33 ô ⇒ 8 ô chọn người nhận; hạn xử lý, giờ làm việc, trực, kế hoạch đều có mặc định hợp lý ⇒ gấp thành 4 nhóm
    strategy: Literal["rotate", "least_loaded"] = field("Cách chia", "rotate", options=STRATEGIES, group=G1)
    cap_uncalled: int = field("Trần lead chưa gọi / người", 10, ge=0, le=500, help="0 = không trần. Chạm trần ⇒ bỏ qua người đó", group=G1)
    first_call_min: int = field("Hạn gọi lần đầu (phút làm việc)", 15, ge=1, le=10080, group=G1)
    contact_proof: list[str] = field("Thế nào là đã liên hệ", [], options=PROOFS, group=G1,
                                     help="Một trong các loại đã chọn là đạt. Trống = mặc định: công ty có tổng đài ⇒ cuộc gọi tổng đài khách nghe máy "
                                          "đủ số giây dưới đây; chưa có tổng đài ⇒ mọi liên hệ trừ ghi chú")
    min_talk_s: int = field("Cuộc gọi tổng đài tối thiểu (giây)", 20, ge=0, le=3600, help="Chỉ cho loại “Cuộc gọi tổng đài khách nghe máy”", group=G1)
    remind_before_min: int = field("Nhắc người giữ trước hạn (phút làm việc)", 10, ge=0, le=1440, help="0 = không nhắc", group=G1)
    escalate: bool = field("Báo trưởng nhóm khi quá hạn", True, help="Trưởng đội chính của người giữ (không có thì trưởng đội cha)", group=G1)
    grace_min: int = field("Ân hạn trước khi thu hồi (phút làm việc)", 0, ge=0, le=1440, group=G1,
                           help="0 = quá hạn là thu hồi ngay. Trong ân hạn trưởng nhóm đã được báo và vẫn có thể chuyển tay")
    accept_min: int = field("Phải bấm nhận lead trong (phút làm việc)", 0, ge=0, le=240, group=G1,
                            help="0 = không cần bấm nhận. Quá hạn bấm nhận ⇒ chia người khác như thu hồi")
    hide_contact: bool = field("Che SĐT / liên hệ tới khi bấm nhận", False, group=G1,
                               help="Cần bước bấm nhận. Lead đang chờ nhận thì ai cũng chỉ thấy số đã che 4 chữ số cuối — bấm Nhận rồi mới thấy đủ (0214)")
    followup_hours: int = field("Hạn chăm sóc lại (giờ làm việc)", 48, ge=1, le=2160, group=G1)
    max_recalls: int = field("Số lần thu hồi tối đa", 2, ge=0, le=10, help="Quá số này ⇒ về hàng chờ", group=G1)
    queue_escalate_min: int = field("Lead nằm hàng chờ quá (phút làm việc) thì báo", QUEUE_ESCALATE_MIN, ge=1, le=10080, group=G1,
                                    help="Báo trưởng đội sở hữu hàng chờ (không có thì quản lý cả công ty), lặp lại mỗi chừng ấy phút tới khi lead có "
                                         "người nhận — không lead nào nằm hàng chờ im lặng")
    days: Literal["mon_fri", "mon_sat", "all"] = field("Ngày làm việc", "mon_sat", options={k: v[1] for k, v in DAY_PRESETS.items()}, group=G2)
    open_at: str = field("Giờ mở", "08:30", kind="time", example="08:30", group=G2)
    close_at: str = field("Giờ đóng", "18:00", kind="time", example="18:00", group=G2)
    calendar: str = field("Lịch nghỉ lễ", "", options={"": "Không trừ ngày lễ", **{c: c for c in business_calendar.calendars()}}, group=G2,
                          help="Ngày lễ / Tết của lịch này không tính vào hạn gọi, hạn chăm sóc lại")
    off_hours: Literal["defer", "duty"] = field("Lead về ngoài giờ", "defer", options=OFF_HOURS, group=G3)
    duty_team_code: str = field("Đội trực", "", options_query="admin.team_options", help="Chỉ khi chọn chia cho đội trực", group=G3)
    duty_days: Literal["mon_fri", "mon_sat", "all"] = field("Ngày trực", "all", options={k: v[1] for k, v in DAY_PRESETS.items()}, group=G3)
    duty_open_at: str = field("Trực từ", "18:00", kind="time", example="18:00", group=G3)
    duty_close_at: str = field("Trực tới", "22:00", kind="time", example="22:00", group=G3,
                               help="Ngoài giờ trực (vd nửa đêm) ⇒ hạn bắt đầu từ giờ mở cửa kế tiếp")
    plan_code: str = field("Kế hoạch chiến dịch", "", max_length=40, example="CP7K3ABCDEFG", group=G4,
                           help="Trống = mọi lead. Có mã = luật cho lead của các chiến dịch thuộc kế hoạch đó — thắng luật theo nguồn / [[offering|l]] (0204)")
    reopen_lost: bool = field("Khách đã mất quay lại ⇒ mở lại lead", True, help="Tắt: chỉ báo người đang chăm, lead vẫn ở chặng Mất", group=G4)


async def _proof(ctx: Ctx, chosen: list[str]) -> list[str]:
    """Bằng chứng liên hệ của luật. Trống ⇒ mặc định theo việc công ty có tổng đài (gói cắm vào ``leadflow.contact_proof``) hay không."""
    pbx = await ctx.plugged("leadflow.contact_proof")
    if bad := [p for p in chosen if p not in PROOFS]:
        raise Invalid(f"Loại liên hệ lạ: {', '.join(bad)}")
    if not chosen:
        return ["pbx"] if pbx else ["any"]
    if "pbx" in chosen and not pbx:
        raise Invalid("Công ty chưa bật Tổng đài — chưa có cuộc gọi tổng đài để làm bằng chứng. Chọn loại liên hệ khác hoặc bật gói Tổng đài trước")
    return ["any"] if "any" in chosen else sorted(set(chosen))


@m.command("set_rule", resource="leadflow", action="manage", title="Thêm luật chia lead", category="setup", agent="propose", risk="authority")
async def set_rule(ctx: Ctx, inp: SetRule) -> dict[str, Any]:
    """Luật CHIA LEAD: lead mới VÀO TAY ai — người nhận, giờ, hạn gọi lần đầu, thế nào là đã liên hệ, nhắc / báo trưởng nhóm / ân hạn, ngoài giờ.
    KHÔNG dùng cho "nếu … thì báo / nhắc / tạo việc cho ai" (đó là flow__save_automation). Trợ lý được SOẠN luật (thẻ đề xuất kèm câu mô tả +
    cảnh báo); người có quyền bấm mới áp dụng — luật quyết ai nhận khách nên luôn là thẻ."""
    if not ctx.p.has_scope("leadflow", "all"):
        raise Denied("Luật chia lead là chính sách cả công ty — cần quyền quản lý chia lead phạm vi cả công ty")
    open_min, close_min = parse_hm(inp.open_at), parse_hm(inp.close_at)
    if open_min >= close_min:
        raise Invalid("Giờ mở phải trước giờ đóng")
    duty_open, duty_close = parse_hm(inp.duty_open_at), parse_hm(inp.duty_close_at)
    if duty_open >= duty_close:
        raise Invalid("Giờ bắt đầu trực phải trước giờ hết trực (ca qua nửa đêm: chọn tới 24:00, phần sau nửa đêm hạn tính từ giờ mở cửa)")
    roles = sorted(set(inp.roles))
    if not roles:
        raise Invalid("Chọn ít nhất một vai được nhận lead")
    known = {r["key"] for r in await ctx.all("SELECT key FROM core.roles")}
    if bad := [r for r in roles if r not in known]:
        raise Invalid(f"Không có vai: {', '.join(bad)}")
    if blind := [r for r in roles if r not in await _lead_roles(ctx)]:
        raise Invalid(f"Vai {', '.join(blind)} chưa có quyền xem + ghi Khách hàng — người nhận sẽ không thấy / không làm được lead. Cấp quyền ở Vai trò & quyền trước")
    if inp.mode == "coordinator" and not inp.coord_team_code:
        raise Invalid("Chế độ điều phối cần chọn đội điều phối")
    if inp.off_hours == "duty" and not inp.duty_team_code:
        raise Invalid("Chia cho đội trực cần chọn đội trực")
    if inp.calendar and inp.calendar not in business_calendar.calendars():
        raise Invalid(f"Không có lịch {inp.calendar}")
    if inp.hide_contact and not inp.accept_min:
        raise Invalid("Che liên hệ cần bước bấm nhận: đặt “Phải bấm nhận lead trong” lớn hơn 0 (chưa bấm nhận thì không có lúc nào để mở số)")
    proof = await _proof(ctx, inp.contact_proof)
    offering_code = await _offering_code(ctx, inp.offering_code)
    rid = await ctx.val(
        """INSERT INTO leadflow.rules (name, source, offering_code, plan_code, effective_from, mode, coord_team_id, team_id, roles, strategy, cap_uncalled,
                                       first_call_min, followup_hours, max_recalls, work_days, work_open_min, work_close_min, created_by,
                                       calendar, contact_proof, min_talk_s, remind_before_min, escalate, grace_min, off_hours, duty_team_id, duty_days,
                                       duty_open_min, duty_close_min, accept_min, receivers, reopen_lost, queue_escalate_min, hide_contact)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (tenant_id, offering_code, source, plan_code, effective_from) DO NOTHING RETURNING id""",
        (inp.name, inp.source, offering_code, plan_code_of(inp.plan_code), inp.effective_from, inp.mode,
         await _team_id(ctx, inp.coord_team_code) if inp.mode == "coordinator" else None, await _team_id(ctx, inp.team_code), roles,
         inp.strategy, inp.cap_uncalled, inp.first_call_min, inp.followup_hours, inp.max_recalls, list(DAY_PRESETS[inp.days][0]),
         open_min, close_min, ctx.p.user_id, inp.calendar, proof, inp.min_talk_s, inp.remind_before_min, inp.escalate, inp.grace_min, inp.off_hours,
         await _team_id(ctx, inp.duty_team_code) if inp.off_hours == "duty" else None, list(DAY_PRESETS[inp.duty_days][0]), duty_open, duty_close,
         inp.accept_min, inp.receivers, inp.reopen_lost, inp.queue_escalate_min, inp.hide_contact))
    if rid is None:
        raise Conflict("Đã có luật cho phạm vi này hiệu lực đúng ngày đó — chọn ngày khác (luật cũ giữ nguyên để tra cứu)")
    rule = await ctx.one("SELECT * FROM leadflow.rules WHERE id = %s", (rid,))
    assert rule is not None
    return {"rule_id": rid, **await _describe(ctx, rule)}


def proof_text(rule: dict[str, Any]) -> str:
    kinds = list(rule.get("contact_proof") or ["any"])
    text = " hoặc ".join(PROOF_SHORT[k] for k in kinds if k in PROOF_SHORT)
    return text + (f" ≥ {rule.get('min_talk_s', 0)} giây" if "pbx" in kinds and rule.get("min_talk_s") else "")


async def _describe(ctx: Ctx, rule: dict[str, Any]) -> dict[str, Any]:
    """Luật đọc như một câu + kiểm tra NGAY lúc này: bao nhiêu người đủ điều kiện, đội điều phối có ai thấy hàng chờ không."""
    teams = {r["id"]: r["name"] for r in await ctx.all("SELECT id, name FROM core.teams")}
    labels = {r["key"]: r["label"] for r in await ctx.all("SELECT key, label FROM core.roles")}
    n = len(await _eligible(ctx, rule, rule["offering_code"]))
    who = " / ".join(labels.get(r, r) for r in rule["roles"]) + (f" đội {teams.get(rule['team_id'], '?')}" if rule["team_id"] else " cả công ty")
    if rule["receivers"] == "members":
        who = f"thành viên {'[[offering|l]] ' + rule['offering_code'] if rule['offering_code'] else 'của [[offering|l]] khách quan tâm'} ({who}; hạng 1 trước, theo trọng số)"
    scope = ("Lead " + (SOURCES[rule["source"]].lower() if rule["source"] else "mọi nguồn")
             + (f" · [[offering|l]] {rule['offering_code']}" if rule["offering_code"] else ""))
    if rule["mode"] == "coordinator":
        summary = (f"{scope} → vào hàng chờ của đội {teams.get(rule['coord_team_id'], '?')}; người điều phối chia cho {who} "
                   f"({n} người) — hệ thống gợi ý người nhận {STRATEGY_SHORT[rule['strategy']]}.")
    else:
        summary = f"{scope} → hệ thống tự chia {STRATEGY_SHORT[rule['strategy']]} cho {who} ({n} người đủ điều kiện)."
    days = next((v[1] for v in DAY_PRESETS.values() if v[0] == tuple(rule["work_days"])), "tuỳ chọn")
    steps = [f"Liên hệ lần đầu trong {rule['first_call_min']} phút làm việc ({days} {hm(rule['work_open_min'])}–{hm(rule['work_close_min'])}"
             + (f", trừ ngày lễ lịch {rule['calendar']}" if rule["calendar"] else "") + f"); tính là đã liên hệ khi có {proof_text(rule)}"]
    if rule["accept_min"]:
        steps.append(f"người nhận phải bấm nhận trong {rule['accept_min']} phút làm việc"
                     + (" — chưa nhận thì SĐT / liên hệ của khách bị che" if rule.get("hide_contact") else ""))
    if rule["remind_before_min"] and rule["remind_before_min"] < rule["first_call_min"]:
        steps.append(f"nhắc người giữ trước hạn {rule['remind_before_min']} phút")
    if rule["escalate"]:
        steps.append("quá hạn báo trưởng nhóm")
    steps.append((f"sau {rule['grace_min']} phút ân hạn " if rule["grace_min"] else "") + f"thu hồi tối đa {rule['max_recalls']} lần")
    steps.append(f"lead nằm hàng chờ quá {rule['queue_escalate_min']} phút làm việc thì báo người sở hữu hàng chờ, lặp lại tới khi có người nhận")
    if rule["cap_uncalled"]:
        steps.append(f"mỗi người tối đa {rule['cap_uncalled']} lead chưa gọi")
    if rule["off_hours"] == "duty":
        ddays = next((v[1] for v in DAY_PRESETS.values() if v[0] == tuple(rule["duty_days"])), "tuỳ chọn")
        steps.append(f"lead về ngoài giờ chia đội trực {teams.get(rule['duty_team_id'], '?')} ({ddays} {hm(rule['duty_open_min'])}–"
                     f"{hm(rule['duty_close_min'])})")
    else:
        steps.append("lead về ngoài giờ: hạn bắt đầu từ giờ mở cửa kế tiếp")
    sla = "; ".join(steps) + "."
    warnings: list[str] = []
    if n == 0:
        warnings.append("Hiện KHÔNG ai đủ điều kiện nhận — lead sẽ nằm hàng chờ")
    if rule["mode"] == "coordinator" and not await _coordinators(ctx, rule["coord_team_id"]):
        warnings.append(f"Đội {teams.get(rule['coord_team_id'], '?')} chưa có trưởng đội có quyền Khách hàng phạm vi đội — "
                        "chỉ quản lý cả công ty thấy hàng chờ này")
    notes = ["Chưa chọn lịch nghỉ lễ — lead về dịp lễ / Tết bị tính hạn như ngày thường"] if not rule["calendar"] else []
    if rule["calendar"] and rule["calendar"] not in business_calendar.calendars():
        warnings.append(f"Lịch {rule['calendar']} không còn trong bản cài — đang không trừ ngày lễ")
    if "pbx" in rule["contact_proof"] and not await ctx.plugged("leadflow.contact_proof"):
        warnings.append("Gói Tổng đài đang tắt — tạm tính cuộc gọi nói chuyện được (ghi tay) thay cho cuộc gọi tổng đài")
    return {"summary": summary, "sla": sla, "receivers_now": n, "warnings": warnings, "notes": notes}


async def _coordinators(ctx: Ctx, team_id: UUID | None) -> list[UUID]:
    """Ai thấy hàng chờ của đội điều phối VÌ là trưởng đội đó (hoặc đội cha) với quyền Khách hàng phạm vi đội. Quản lý cả công ty không
    tính — họ thấy mọi hàng chờ, nhưng không phải người điều phối hằng ngày."""
    rows = await ctx.all(
        """WITH RECURSIVE up AS (SELECT id, parent_id FROM core.teams WHERE id = %s
                                 UNION ALL SELECT t.id, t.parent_id FROM core.teams t JOIN up ON t.id = up.parent_id)
           SELECT DISTINCT tm.user_id FROM core.team_members tm JOIN core.memberships mb ON mb.user_id = tm.user_id AND mb.active
           JOIN core.grants g ON g.role = mb.role AND g.resource = 'crm' AND g.scope = 'team'
           WHERE tm.is_lead AND tm.team_id IN (SELECT id FROM up)""", (team_id,))
    return [r["user_id"] for r in rows]


def _rule_state(rows: list[dict[str, Any]], t: date) -> None:
    """Mỗi phạm vi (thứ khách quan tâm × nguồn): luật mới nhất đã có hiệu lực = đang áp; cũ hơn = đã thay; ngày tương lai = sắp áp."""
    seen: set[tuple[str, str]] = set()
    for r in sorted(rows, key=lambda r: r["effective_from"], reverse=True):
        key = (r["offering_code"], r["source"])
        if r["effective_from"] > t:
            r["state"] = "pending"
        elif key not in seen:
            r["state"] = "active"
            seen.add(key)
        else:
            r["state"] = "replaced"


@m.query("rules", resource="leadflow", title="Lịch sử luật chia lead")
async def rules(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await ctx.all(
        """SELECT r.*, t.code AS team_code, c.code AS coord_code FROM leadflow.rules r LEFT JOIN core.teams t ON t.id = r.team_id
           LEFT JOIN core.teams c ON c.id = r.coord_team_id ORDER BY r.source, r.offering_code, r.effective_from DESC""")
    _rule_state(rows, today())
    for r in rows:
        r["scope"] = " · ".join([SOURCE_LABEL[r["source"]], r["offering_code"] or "Mọi [[offering|l]]"])
        r["how"] = f"Điều phối: {r['coord_code']}" if r["mode"] == "coordinator" else "Tự chia"
        r["who"] = ("Thành viên · " if r["receivers"] == "members" else "") + ", ".join(r["roles"]) + (f" · đội {r['team_code']}" if r["team_code"] else "")
        r["hours"] = f"{hm(r['work_open_min'])}–{hm(r['work_close_min'])}" + (f" · {r['calendar']}" if r["calendar"] else "")
        r["cap_text"] = str(r["cap_uncalled"]) if r["cap_uncalled"] else "Không trần"
        r["proof"] = proof_text(r)
    return ui.table(rows, [
        ui.col("state", "", "badge", labels={"active": "Đang áp", "pending": "Sắp áp", "replaced": "Đã thay"},
               tones={"active": "green", "pending": "blue", "replaced": "gray"}),
        ui.col("scope", "Phạm vi"), ui.col("name", "Luật"), ui.col("effective_from", "Hiệu lực từ", "date"), ui.col("how", "Cách nhận"),
        ui.col("who", "Người nhận"), ui.col("strategy", "Cách chia", "badge", labels={"rotate": "Xoay vòng", "least_loaded": "Theo tải"}),
        ui.col("cap_text", "Trần chưa gọi"), ui.col("first_call_min", "Hạn gọi (phút)", "number"), ui.col("proof", "Đã liên hệ khi"),
        ui.col("hours", "Giờ"), ui.col("max_recalls", "Thu hồi tối đa", "number"),
    ], title="Lịch sử luật chia lead", empty="Chưa có luật — lead mới sẽ chờ quản lý chia tay")


@m.query("policy", resource="leadflow", title="Lead đi đâu")
async def policy(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Luật chia đọc như câu văn, kèm kiểm tra ngay (bao nhiêu người đủ điều kiện, đội điều phối có ai thấy hàng chờ, lịch lễ, tổng đài)."""
    rows = await ctx.all("SELECT * FROM leadflow.rules")
    t = today()
    _rule_state(rows, t)
    active = sorted([r for r in rows if r["state"] == "active"], key=lambda r: (r["source"] == "", r["offering_code"] == ""))
    blocks: list[dict[str, Any]] = [ui.b_text(
        "Mỗi lead mới đi theo luật **cụ thể nhất**: nguồn + [[offering|l]] → nguồn → [[offering|l]] → luật chung. Khách cũ đã có người chăm thì giữ người đó; "
        "khách đã có lead quay lại qua kênh khác thì người đang chăm được báo ngay (lead đã mất được mở lại nếu luật cho phép); "
        "người nạp lead có thể chỉ định thẳng đội / người nhận.")]
    if await members_only(ctx):
        blocks.append(ui.b_callout("accent", "Đang bật: chỉ thành viên",
                                   "Sale chỉ được chia và chỉ thấy lead, bảng hàng của [[offering|l]] mình là thành viên (chặn ở cơ sở dữ liệu)."))
    if not active:
        blocks.append(ui.b_callout("warn", "Chưa có luật nào", "Lead mới sẽ nằm hàng chờ để quản lý chia tay."))
    for r in active:
        d = await _describe(ctx, r)
        tone = "bad" if d["receivers_now"] == 0 else ("warn" if d["warnings"] else "ok")
        title = f"{SOURCE_LABEL[r['source']]} · {r['offering_code'] or 'mọi [[offering|l]]'} — {r['name']}"
        blocks.append(ui.b_callout(tone, title, d["summary"], facts=[d["sla"], f"Hiệu lực từ {r['effective_from']:%d/%m/%Y}", *d["warnings"], *d["notes"]]))
    pending = [r for r in rows if r["state"] == "pending"]
    if pending:
        blocks.append(ui.b_list([{"title": f"{r['name']} — từ {r['effective_from']:%d/%m/%Y}", "subtitle": (await _describe(ctx, r))["summary"],
                                  "tone": "accent"} for r in pending], "Sắp áp dụng"))
    return ui.card("Lead đi đâu", blocks, subtitle=f"{len(active)} luật đang áp", icon="signpost.right",
                   actions=[ui.act("leadflow.set_rule", "Thêm luật chia", style="primary"), ui.act("leadflow.set_member", "Thêm thành viên [[offering|l]]")],
                   open=[{"title": "Lịch sử luật", "query": "leadflow.rules", "input": {}},
                         {"title": "Thành viên [[offering|l]]", "query": "leadflow.members", "input": {}}],
                   ask={"title": "Nhờ trợ lý đổi luật", "prompt": "Đổi luật chia lead: "})


async def _rule_for(ctx: Ctx, offering_code: str, source: str, day: date, plan_code: str = "") -> dict[str, Any] | None:
    """Luật đang hiệu lực cho lead. Thứ tự thắng: kế hoạch chiến dịch (0204) → nguồn → [[offering|l]] → mới nhất. Lead không thuộc kế hoạch nào chỉ khớp
    luật không có kế hoạch (luật của một kế hoạch không bao giờ nhận lead của kế hoạch khác / không kế hoạch)."""
    return await ctx.one(
        """SELECT * FROM leadflow.rules WHERE offering_code IN (%(p)s, '') AND source IN (%(s)s, '') AND plan_code IN (%(pl)s, '') AND effective_from <= %(d)s
           ORDER BY (plan_code = %(pl)s AND plan_code <> '') DESC, (source = %(s)s AND source <> '') DESC, (offering_code = %(p)s AND offering_code <> '') DESC,
                    effective_from DESC LIMIT 1""",
        {"p": offering_code, "s": source, "pl": plan_code or "", "d": day})


PLAN_CODE = re.compile(r"[A-Z0-9_-]{0,40}")


def plan_code_of(raw: str) -> str:
    code = (raw or "").strip().upper()
    if not PLAN_CODE.fullmatch(code):
        raise Invalid(f"Mã kế hoạch “{raw}” không đúng dạng (chữ hoa, số, - _; tối đa 40)")
    return code


# ── Thành viên [[offering|l]] (ai bán gì): hạng + trọng số là dữ liệu quản lý sửa ──

class SetMember(Input):
    offering_code: str = field("[[offering]]", options_query="crm.offering_options", example="HSR")
    user_email: str = field("Người", options_query="leadflow.receiver_options")
    tier: int = field("Hạng", 1, ge=1, le=9, help="Hạng 1 nhận lead trước; hạng sau chỉ nhận khi cả hạng trên đã chạm trần / nghỉ / tạm ngừng")
    weight: int = field("Trọng số", 1, ge=1, le=100, help="Trong cùng hạng: trọng số 2 nhận gấp đôi người trọng số 1 (tính trên 30 ngày gần nhất)")
    active: bool = field("Đang tham gia", True, help="Tắt = rời [[offering|l]] (giữ dòng để tra lịch sử)")
    note: str = field("Ghi chú", "", max_length=300, group="Thêm chi tiết")
    plan_code: str = field("Kế hoạch chiến dịch", "", max_length=40, example="CP7K3ABCDEFG", group="Thêm chi tiết",
                           help="Trống = thành viên [[offering|l]]. Có mã = người THAM GIA kế hoạch đó (vd gói đồng chi trả): chỉ họ nhận lead của kế hoạch "
                                "khi luật của kế hoạch chọn người nhận \"Thành viên\" (0204)")


@m.command("set_member", resource="leadflow", action="manage", title="Thêm / sửa thành viên [[offering|l]]", category="setup", importable=True,
           agent="propose", risk="authority")
async def set_member(ctx: Ctx, inp: SetMember) -> dict[str, Any]:
    """Ai tham gia bán [[offering|l]] nào, hạng + trọng số khi chia lead (luật chọn người nhận "Thành viên"). Khi công tắc "chỉ thành viên" bật thì danh
    sách này cũng quyết ai THẤY lead / bảng hàng của [[offering|l]] ⇒ là việc của quản lý (trợ lý chỉ soạn thẻ). Nhập được từ Excel."""
    if not ctx.p.has_scope("leadflow", "all"):
        raise Denied("Thành viên [[offering|l]] là cấu hình cả công ty — cần quyền quản lý chia lead phạm vi cả công ty")
    code = await _offering_code(ctx, inp.offering_code)
    if not code:
        raise Invalid("Chọn [[offering|l]]")
    uid = await _user_by_email(ctx, inp.user_email)
    plan = plan_code_of(inp.plan_code)
    await ctx.exec(
        """INSERT INTO leadflow.members (offering_code, plan_code, user_id, tier, weight, active, note, updated_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (tenant_id, offering_code, plan_code, user_id) DO UPDATE SET tier = EXCLUDED.tier, weight = EXCLUDED.weight, active = EXCLUDED.active,
               note = EXCLUDED.note, updated_by = EXCLUDED.updated_by, updated_at = now()""",
        (code, plan, uid, inp.tier, inp.weight, inp.active, inp.note, ctx.p.user_id))
    if not inp.active:   # rời [[offering|l]] ⇒ lead của [[offering|l]] này đang giữ theo tư cách thành viên chuyển NGAY qua luật chia (0153)
        await routing.recheck(ctx, user_ids=[uid])
    name = await ctx.val("SELECT name FROM core.users WHERE id = %s", (uid,))
    state = f"hạng {inp.tier}, trọng số {inp.weight}" if inp.active else "đã rời"
    return {"offering_code": code, "plan_code": plan, "user_id": uid, "summary": f"{name} · [[offering|l]] {code}{f' · kế hoạch {plan}' if plan else ''}: {state}"}


class MembersIn(Input):
    offering_code: str = field("[[offering]]", "", options_query="crm.offering_options", help="Trống = mọi [[offering|l]]")


@m.query("members", resource="leadflow", title="Thành viên [[offering|l]]")
async def members(ctx: Ctx, inp: MembersIn) -> dict[str, Any]:
    """Ai bán [[offering|l]] nào, hạng + trọng số + đang nhận được lead lúc này không (khoá, tạm ngừng, nghỉ phép, vai không thấy lead)."""
    code = await _offering_code(ctx, inp.offering_code) if inp.offering_code else ""
    rows = await ctx.all(
        """SELECT m.offering_code, m.user_id, u.name, u.email, m.tier, m.weight, m.active, m.note, m.updated_at FROM leadflow.members m
           JOIN core.users u ON u.id = m.user_id WHERE (%(c)s = '' OR m.offering_code = %(c)s)
           ORDER BY m.offering_code, m.active DESC, m.tier, m.weight DESC, u.name""", {"c": code})
    capable = await _lead_capable(ctx)
    for r in rows:
        r["id"] = f"{r['offering_code']}:{r['user_id']}"
        r["state"] = "off" if not r["active"] else ("ok" if r["user_id"] in capable else "away")
    on = sum(1 for r in rows if r["state"] == "ok")
    return ui.table(rows, [
        ui.col("offering_code", "[[offering]]"), ui.col("name", "Người"), ui.col("tier", "Hạng", "number"), ui.col("weight", "Trọng số", "number"),
        ui.col("state", "Nhận lead", "badge", labels={"ok": "Đang nhận", "away": "Tạm không nhận", "off": "Đã rời"},
               tones={"ok": "green", "away": "orange", "off": "gray"}),
        ui.col("note", "Ghi chú"), ui.col("updated_at", "Cập nhật", "datetime"),
    ], title="Thành viên [[offering|l]]", kpis=[ui.kpi("Thành viên đang nhận lead", on), ui.kpi("Dòng", len(rows))],
        empty="Chưa có thành viên — luật chia theo đội vẫn chạy; thêm thành viên để chia theo hạng / trọng số từng [[offering|l]]",
        empty_actions=[ui.act("leadflow.set_member", "Thêm thành viên", style="primary")])


# ── Tạm ngừng nhận lead ──

class PauseIn(Input):
    until: Day | None = field("Tạm ngừng nhận lead tới hết ngày", None, help="Trống = nhận lại ngay")
    user_email: str = field("Người", "", options_query="admin.member_options", help="Trống = chính bạn. Đặt cho người khác cần quyền quản lý chia lead")


@m.command("pause", resource="leadflow", title="Tạm ngừng / nhận lại lead", agent="deny")
async def pause(ctx: Ctx, inp: PauseIn) -> dict[str, Any]:
    uid = ctx.p.user_id
    if inp.user_email and inp.user_email.lower() != ctx.p.email:
        ctx.require("leadflow", "manage")
        uid = await ctx.val("SELECT u.id FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant()"
                            " WHERE u.email = %s AND mb.active", (inp.user_email.lower(),))
        if uid is None:
            raise NotFound(f"Không có người {inp.user_email}")
    until = datetime.combine(inp.until + timedelta(days=1), datetime.min.time(), tzinfo=zone()) if inp.until else None
    await ctx.exec(
        """INSERT INTO leadflow.receivers (user_id, paused_until) VALUES (%s, %s)
           ON CONFLICT (tenant_id, user_id) DO UPDATE SET paused_until = EXCLUDED.paused_until, updated_at = now()""", (uid, until))
    return {"user_id": uid, "paused_until": until}


# Thứ tự nạp = thứ tự thao tác trong sổ đăng ký (danh mục công cụ của trợ lý / app) — không sắp lại
# isort: off
from barista.modules.leadflow import routing, sweep, reads, screens, history, findings  # noqa: E402, F401
# isort: on
from barista.modules.leadflow.routing import _eligible, _lead_capable, _pick  # noqa: E402, F401 — dùng ở trên lúc chạy (luật, thành viên) + test cũ
