"""ĐIỀU KHIỂN QUẢNG CÁO — phần QUYẾT + LÀM của vòng quảng cáo, KHÔNG phụ thuộc kênh (ADR 0209; dời từ gói kết nối Meta của 0208).

Lõi không biết nhà cung cấp nào. Gói KÊNH (Meta, Google, TikTok…) chỉ làm hai việc: đọc số vào ``mkt`` qua lệnh có sẵn, và cắm ba ĐIỂM của module này:

* ``mkt.ad_objects`` (chỉ DB, không mạng) — vào ``{refs: [{level, ref}]}``; ra ``{channel, label, objects: [{level, ref, name, status: active|paused|ended,
  live, daily_budget (đơn vị nhỏ nhất của tiền tài khoản, None = không đặt ở cấp này), unit (số đơn vị nhỏ / một đồng tiền — VND 1, USD 100), currency,
  account}]}`` cho các mã kênh đó nhận; không nhận mã nào ⇒ None.
* ``mkt.ad_apply`` (gọi kênh) — vào ``{channel, level, ref, status: active|paused|None, daily_budget: int|None}``; kênh ĐỌC trạng thái thật trước, đã đúng
  ⇒ không ghi; ra ``{before: {status, daily_budget}, already, response}``. Không phải kênh của mình ⇒ None. Lỗi tạm (hạn mức, mạng) ⇒ ``Conflict`` (thử lại);
  lỗi hẳn (thiếu quyền, giá trị bị từ chối) ⇒ ``Invalid``.
* ``mkt.ad_convert`` (gọi kênh) — vào ``{channel, event_id, event: "won", occurred_at, value_vnd, campaign_ref, ad_ref, match: {email_sha256, phone_sha256,
  phone_e164_sha256}}``; ra ``{response}``. Cùng luật lỗi. Chỉ dấu băm đi qua — SĐT / email gốc không ra khỏi crm.

Kênh tự đặt khoá ``channel`` (khoá gói của nó — chữ thường); đối tượng nêu bằng (channel, level, ref). Không gói kênh nào ⇒ tác nhân theo dõi vẫn chạy y hệt
(chiến dịch "đang chạy" theo trạng thái ở Marketing), phát hiện không kèm lệnh, người xử lý ngoài app rồi bấm "Đã làm tay" (sổ ghi ``manual``).

Phần ở đây: chính sách công ty (bất biến 5), tác nhân theo dõi (mỗi ngày — chi phí so với lead chất lượng và KẾT QUẢ BÁN trong cửa sổ chính sách, theo chiến
dịch / kế hoạch / [[offering|l]] / quảng cáo — lead theo quảng cáo khi đủ lead biết mẫu, không thì lead nền tảng báo) ⇒ phát hiện + thẻ buổi sáng; lệnh có kiểu
tạm dừng / bật lại / đổi ngân sách ngày (``agent="propose"``, vượt ngưỡng ⇒ chuỗi duyệt) — người bấm ⇒ bước chuẩn bị gọi kênh NGOÀI giao dịch
(``Prep.plugs``), sau duyệt ⇒ hàng đợi + vòng gửi thử lại (vòng gửi cũng gọi kênh ở bước chuẩn bị, handler chỉ ghi — api 1.37); sổ thay đổi; chuyển đổi về kênh khi lead ký (sự kiện ``crm.lead_outcome_recorded``, MẶC ĐỊNH TẮT).
"""

import re
from collections.abc import Awaitable
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Literal, LiteralString
from uuid import NAMESPACE_URL, UUID, uuid5

from psycopg.types.json import Jsonb

from barista.api import Action as A
from barista.api import Conflict, Ctx, Event, Input, Invalid, Money, NoInput, NotFound, Prep, Screen, field, today, ui
from barista.modules.mkt import USE_ADS, m

CHANNEL = re.compile(r"[a-z0-9_]{1,40}")
LEVELS = {"campaign": "Chiến dịch", "adset": "Nhóm quảng cáo", "ad": "Quảng cáo"}
ACTIONS = {"pause": "Tạm dừng", "resume": "Bật lại", "set_budget": "Đổi ngân sách ngày"}
STATES = {"queued": "Đã duyệt — chờ gửi", "sent": "Đã gửi kênh", "failed": "Lỗi — chưa đổi", "manual": "Làm tay ngoài app"}
STATE_TONE = {"queued": "blue", "sent": "green", "failed": "red", "manual": "orange"}
OBJ_STATUS = {"active": "Đang chạy", "paused": "Tạm dừng", "ended": "Đã kết thúc"}
MKT_STATUS = {"running": "active", "paused": "paused", "draft": "paused", "ended": "ended"}   # trạng thái chiến dịch ở Marketing khi chưa nối kênh
RULES = {"no_result": "Chi mà chưa ra lead chất lượng / kết quả bán", "high_cpql": "Chi phí / lead chất lượng cao",
         "no_platform_lead": "Quảng cáo chi mà nền tảng báo 0 lead"}
SCOPES = {"campaign": "Chiến dịch", "plan": "Kế hoạch", "offering": "[[offering]]", "ad": "Quảng cáo"}
CONV_STATES = {"queued": "Chờ gửi", "sent": "Đã gửi kênh", "failed": "Lỗi — chưa gửi", "cancelled": "Không gửi"}
MAX_ATTEMPTS = 5
DISPATCH_LIMIT = 10
CONVERT_LIMIT = 20
RETRY_MIN = (5, 15, 60, 180, 600)   # phút chờ giữa các lần gửi chuyển đổi lỗi tạm
CONFIRM_DAYS = 14                   # thay đổi đã gửi / làm tay: đối chiếu với lần đọc kênh trong ngần này ngày
FX_SOURCE = "Tỷ giá trong chính sách quảng cáo"


def _int(v: Any, default: int, lo: int = 0, hi: int = 10**15) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class AdPolicy:
    """CHÍNH SÁCH quảng cáo của công ty (``mkt.ad_policy.settings``, bất biến 5) — ngưỡng của tác nhân theo dõi, ngưỡng phải duyệt, tỷ giá, công tắc gửi
    chuyển đổi về kênh. Code chỉ kiểm hợp lệ; thiếu khoá ⇒ mặc định ở đây."""
    watch: bool = True
    window_days: int = 14             # cửa sổ tính chi phí / lead / kết quả bán
    min_spend_vnd: int = 5_000_000    # chiến dịch chi ≥ ngần này trong cửa sổ…
    max_qualified: int = 0            # …mà lead chất lượng ≤ ngần này và chưa có kết quả bán ⇒ đề xuất tạm dừng
    max_open_pct: int = 80            # lead của cửa sổ còn đang chăm ≥ % này ⇒ chưa chín, không đề xuất
    max_cpql_vnd: int = 0             # > 0: chi phí / lead chất lượng vượt mức này (chưa có kết quả bán) ⇒ đề xuất giảm ngân sách
    cut_pct: int = 30                 # giảm ngân sách ngày bao nhiêu %
    ad_min_spend_vnd: int = 2_000_000  # quảng cáo chi ≥ ngần này mà chưa ra kết quả ⇒ đề xuất tạm dừng quảng cáo
    ad_attr_pct: int = 80             # lead của chiến dịch biết mẫu quảng cáo ≥ % này ⇒ xét quảng cáo bằng lead CRM; không thì bằng lead nền tảng báo
    budget_pct_max: int = 20          # đổi ngân sách ngày quá % này…
    budget_vnd_max: int = 2_000_000   # …hoặc quá ngần này đồng / ngày ⇒ phải qua chuỗi duyệt "mkt_ad_change"
    resume_needs_approval: bool = True
    conversions: bool = False         # gửi "lead đã có kết quả bán" (dấu băm SĐT / email) về kênh — MẶC ĐỊNH TẮT, chủ công ty bật
    fx: tuple[tuple[str, str], ...] = ()   # (tiền, tỷ giá VND) cho tài khoản quảng cáo tính ngoại tệ

    @classmethod
    def of(cls, raw: dict[str, Any] | None) -> AdPolicy:
        r, d = raw or {}, cls()
        vals: dict[str, Any] = {"fx": tuple((str(a).strip().upper(), str(b).strip()) for a, b in (r.get("fx") or []) if str(a).strip())}
        for f in fields(cls):
            if f.name == "fx":
                continue
            v = r.get(f.name, getattr(d, f.name))
            vals[f.name] = bool(v) if isinstance(getattr(d, f.name), bool) else _int(v, getattr(d, f.name))
        vals["window_days"] = max(1, min(92, vals["window_days"]))
        vals["cut_pct"] = max(1, min(90, vals["cut_pct"]))
        vals["max_open_pct"] = max(1, min(100, vals["max_open_pct"]))
        vals["ad_attr_pct"] = max(1, min(100, vals["ad_attr_pct"]))
        return cls(**vals)

    def as_json(self) -> dict[str, Any]:
        return {**asdict(self), "fx": [list(p) for p in self.fx]}

    def rate(self, currency: str) -> Decimal | None:
        """Tỷ giá VND của một loại tiền (VND ⇒ 1); chưa khai / sai ⇒ None."""
        if currency.upper() in ("", "VND"):
            return Decimal(1)
        for ccy, v in self.fx:
            if ccy == currency.upper():
                try:
                    d = Decimal(v)
                except InvalidOperation:
                    return None
                return d if d > 0 else None
        return None


async def policy(ctx: Ctx) -> AdPolicy:
    return AdPolicy.of(await ctx.val("SELECT settings FROM mkt.ad_policy"))


def money_text(major: Decimal | int | None, currency: str) -> str:
    if major is None:
        return "—"
    if currency.upper() in ("", "VND"):
        return f"{int(major):,} đ".replace(",", ".")
    return f"{Decimal(major):,.2f} {currency.upper()}"


def major(minor: int | None, unit: int) -> Decimal | None:
    return None if minor is None else Decimal(minor) / max(1, unit)


def _vnd(x: int) -> str:
    return money_text(x, "VND")


# ── Hỏi các kênh đã cắm (mọi kênh trả phần mình nhận; không kênh nào ⇒ rỗng) ──

async def channel_objects(ctx: Ctx, refs: list[tuple[str, str]]) -> dict[tuple[str, str], dict[str, Any]]:
    """(cấp, mã) ⇒ trạng thái theo lần đọc gần nhất của kênh nhận mã đó (chỉ DB của gói kênh, không mạng). Hai kênh cùng nhận một mã ⇒ kênh nạp trước."""
    if not refs:
        return {}
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for part in await ctx.plugs("mkt.ad_objects", system=True, refs=[{"level": lv, "ref": r} for lv, r in sorted(set(refs))]):
        ch = str(part.get("channel") or "")
        if not CHANNEL.fullmatch(ch):
            continue
        for o in part.get("objects") or []:
            out.setdefault((o["level"], o["ref"]), {**o, "channel": ch, "channel_label": str(part.get("label") or ch), "unit": int(o.get("unit") or 1)})
    return out


# ── Lệnh có kiểu: tạm dừng / bật lại / đổi ngân sách ngày ──

class Target(Input):
    channel: str = field("Kênh", "", hidden=True, max_length=40, help="Trống = chưa nối kênh (chỉ đường làm tay)")
    level: Literal["campaign", "adset", "ad"] = field("Cấp", "campaign", hidden=True, options=LEVELS)
    ref: str = field("Mã trên kênh", hidden=True, max_length=40)


class StatusIn(Target):
    status: Literal["paused", "active"] = field("Trạng thái mới", "paused", options={"paused": "Tạm dừng", "active": "Bật lại"})
    finding_id: UUID | None = field("Phát hiện", None, hidden=True)
    note: str = field("Ghi chú", "", max_length=500)


class BudgetIn(Target):
    daily_budget: Decimal = field("Ngân sách ngày mới", gt=0, example="1500000", help="Theo tiền của tài khoản quảng cáo (VND: đồng)")
    finding_id: UUID | None = field("Phát hiện", None, hidden=True)
    note: str = field("Ghi chú", "", max_length=500)


class ManualIn(Target):
    action: Literal["pause", "resume", "set_budget"] = field("Đã làm gì ngoài app", "pause", options=ACTIONS)
    daily_budget: Decimal | None = field("Ngân sách ngày đã đặt", None, gt=0, help="Chỉ khi đổi ngân sách — theo tiền của tài khoản quảng cáo")
    finding_id: UUID | None = field("Phát hiện", None, hidden=True)
    note: str = field("Ghi chú", "", max_length=500)


CAMPAIGN_COLS: LiteralString = "c.owner_id, c.team_id, c.status, core.can_see('mkt.ad_control', c.owner_id, c.team_id) AS may"


async def _campaign_of(ctx: Ctx, level: str, ref: str) -> dict[str, Any] | None:
    """Chiến dịch ở Marketing của một đối tượng (cấp, mã trên kênh = mã ở Marketing) — tên, trạng thái, người phụ trách, đội và ``may`` (phạm vi
    ``mkt.ad_control`` của người gọi phủ chiến dịch này) — đọc bằng QUYỀN NGƯỜI GỌI (RLS của ``mkt``). Không thấy ⇒ None."""
    if level == "campaign":
        return await ctx.one("SELECT c.name, " + CAMPAIGN_COLS + " FROM mkt.campaigns c WHERE c.code = %s", (ref,))
    if level == "ad":
        return await ctx.one("SELECT coalesce(nullif(a.name, ''), a.code) AS name, " + CAMPAIGN_COLS
                             + " FROM mkt.ads a JOIN mkt.campaigns c ON c.id = a.campaign_id WHERE a.code = %s", (ref,))
    return await ctx.one("SELECT coalesce(nullif(a.adset_name, ''), a.adset_code) AS name, " + CAMPAIGN_COLS
                         + " FROM mkt.ads a JOIN mkt.campaigns c ON c.id = a.campaign_id WHERE a.adset_code = %s ORDER BY a.created_at LIMIT 1", (ref,))


async def _target(ctx: Ctx, channel: str, level: str, ref: str) -> dict[str, Any]:
    """Đối tượng theo kênh đã nối (lần đọc gần nhất), hoặc — chưa nối kênh — chiến dịch / quảng cáo đã có ở Marketing (RLS người gọi). Kèm người phụ trách +
    đội của CHIẾN DỊCH (sổ ghi theo đó). Phạm vi theo đội: chỉ đối tượng thuộc chiến dịch mình thấy VÀ phạm vi điều khiển quảng cáo của mình phủ; đối tượng
    chưa có ở Marketing (không biết đội) chỉ phạm vi "all" đổi được. Kiểm ở đây ⇒ bước gọi kênh (``mkt.ad_target``) cũng bị chặn trước khi gọi."""
    if not channel and level == "adset":
        raise Invalid("Nhóm quảng cáo chỉ nêu được khi kênh quảng cáo đã nối")
    camp = await _campaign_of(ctx, level, ref)
    if (camp is None and ctx.p.scope("mkt.ad_control") != "all") or (camp is not None and not camp["may"]):
        raise NotFound(f"Không thấy {LEVELS.get(level, level).lower()} {ref} trong các chiến dịch bạn phụ trách / đội bạn quản lý ở Marketing")
    who = {"owner_id": camp["owner_id"] if camp else None, "team_id": camp["team_id"] if camp else None}
    if channel:
        got = (await channel_objects(ctx, [(level, ref)])).get((level, ref))
        if got is None or got["channel"] != channel:
            raise NotFound(f"Không thấy {LEVELS.get(level, level).lower()} {ref} trên kênh quảng cáo đã nối (kênh chưa bật hoặc chưa đọc tới)")
        return {**got, **who}
    if camp is None:
        raise NotFound(f"Không thấy {LEVELS[level].lower()} {ref} ở Marketing")
    status = MKT_STATUS.get(camp["status"], "paused")
    return {"level": level, "ref": ref, "name": camp["name"], "status": status, "live": status == "active", "daily_budget": None, "unit": 1,
            "currency": "VND", "channel": "", "channel_label": "", **who}


def _label(t: dict[str, Any]) -> str:
    return f"{LEVELS[t['level']].lower()} “{t['name'] or t['ref']}”"


def _minor(t: dict[str, Any], value: Decimal) -> int:
    return int((value * t["unit"]).quantize(Decimal(1), ROUND_HALF_UP))


def _wanted(t: dict[str, Any], inp: StatusIn | BudgetIn) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """(hành động, trước — theo lần đọc gần nhất, sau) của một lần đổi; kiểm hợp lệ, không đụng kênh."""
    if t["status"] == "ended":
        raise Invalid(f"{_label(t).capitalize()} đã kết thúc / lưu trữ — không đổi được")
    if isinstance(inp, StatusIn):
        return ("pause" if inp.status == "paused" else "resume"), {"status": t["status"]}, {"status": inp.status}
    if t["level"] == "ad":
        raise Invalid("Ngân sách đặt ở chiến dịch hoặc nhóm quảng cáo, không ở từng quảng cáo")
    if t["daily_budget"] is None:
        raise Invalid(f"{_label(t).capitalize()} không có ngân sách NGÀY ở cấp này (ngân sách nằm ở cấp khác hoặc là ngân sách trọn đời)")
    new = _minor(t, inp.daily_budget)
    if new <= 0:
        raise Invalid("Ngân sách ngày phải > 0")
    return "set_budget", {"daily_budget": t["daily_budget"]}, {"daily_budget": new}


def describe(t: dict[str, Any], action: str, before: dict[str, Any], after: dict[str, Any]) -> str:
    """Câu "Đã …" (thẻ đề xuất tự đổi thành "Sẽ …") — số lấy từ dữ liệu, không do model viết."""
    where = f" trên {t['channel_label']}" if t.get("channel_label") else ""
    if action == "set_budget":
        ccy, unit = t["currency"], t["unit"]
        return (f"Đã đổi ngân sách ngày {_label(t)}{where}: {money_text(major(before.get('daily_budget'), unit), ccy)} → "
                f"{money_text(major(after['daily_budget'], unit), ccy)}")
    return f"Đã {'tạm dừng' if action == 'pause' else 'bật lại'} {_label(t)}{where}"


def _brief(t: dict[str, Any], action: str, before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Phần người duyệt đọc ở thẻ (ADR 0253): đối tượng nào trên kênh nào + cũ → mới — số lấy từ dữ liệu như ``describe``."""
    subject = {"label": f"{_label(t).capitalize()}" + (f" · {t['channel_label']}" if t.get("channel_label") else ""), "open": None}
    if action == "set_budget":
        ccy, unit = t["currency"], t["unit"]
        return {"subject": subject, "changes": [{"label": "Ngân sách ngày", "before": money_text(major(before.get("daily_budget"), unit), ccy),
                                                 "after": money_text(major(after["daily_budget"], unit), ccy)}]}
    names = {"active": "Đang chạy", "paused": "Tạm dừng"}
    return {"subject": subject, "changes": [{"label": "Trạng thái", "before": names.get(str(before.get("status") or ""), str(before.get("status") or "")),
                                             "after": names.get(str(after.get("status") or ""), str(after.get("status") or ""))}]}


async def _finding(ctx: Ctx, finding_id: UUID | None) -> dict[str, Any] | None:
    if finding_id is None:
        return None
    f = await ctx.one("SELECT id, state, figures, title FROM mkt.ad_findings WHERE id = %s", (finding_id,))
    if f is None:
        raise NotFound("Không thấy phát hiện của tác nhân theo dõi")
    if f["state"] != "open":
        raise Conflict("Phát hiện này đã được xử lý / bỏ qua / hết hạn")
    return f


async def sensitive(ctx: Ctx, inp: StatusIn | BudgetIn) -> str | None:
    """Lần đổi nào phải qua chuỗi duyệt công ty đặt cho lệnh (mặc định "mkt_ad_change") — theo CHÍNH SÁCH công ty, không viết cứng."""
    try:
        t = await _target(ctx, inp.channel, inp.level, inp.ref)
        action, before, after = _wanted(t, inp)
    except (NotFound, Invalid):
        return None   # handler báo lỗi rõ
    pol = await policy(ctx)
    if action == "resume":
        return f"Bật lại {_label(t)} — công ty đặt: bật lại quảng cáo phải duyệt" if pol.resume_needs_approval else None
    if action != "set_budget":
        return None
    ccy = t["currency"]
    old, new = major(before["daily_budget"], t["unit"]), major(after["daily_budget"], t["unit"])
    assert old is not None and new is not None
    pct = abs(new - old) * 100 / old if old else Decimal(100)
    rate = pol.rate(ccy)
    vnd = abs(new - old) * rate if rate is not None else None
    over = [f"{pct:.0f}% > {pol.budget_pct_max}%"] if pct > pol.budget_pct_max else []
    if vnd is None:
        over.append(f"chưa khai tỷ giá {ccy} — không quy được ra đồng")
    elif vnd > pol.budget_vnd_max:
        over.append(f"{money_text(vnd, 'VND')} / ngày > {money_text(pol.budget_vnd_max, 'VND')}")
    if not over:
        return None
    return f"Ngân sách ngày {_label(t)}: {money_text(old, ccy)} → {money_text(new, ccy)} (vượt ngưỡng: {'; '.join(over)})"


async def basis(ctx: Ctx, inp: StatusIn | BudgetIn) -> dict[str, str]:
    """Căn cứ của thẻ (0071): trạng thái / ngân sách đối tượng theo lần đọc gần nhất + trạng thái phát hiện — đổi giữa lúc soạn và lúc bấm ⇒ không chạy."""
    try:
        t = await _target(ctx, inp.channel, inp.level, inp.ref)
        seen = f"{t['status']}/{t['daily_budget']}"
    except (NotFound, Invalid):
        seen = "không thấy"
    out = {"Đối tượng trên kênh (lần đọc gần nhất)": seen}
    if inp.finding_id is not None:
        out["Phát hiện"] = str(await ctx.val("SELECT state FROM mkt.ad_findings WHERE id = %s", (inp.finding_id,)) or "không thấy")
    return out


@m.query("ad_target", resource="mkt.ad_control", title="Đối tượng trên kênh quảng cáo (cho bước gọi kênh)", agent="deny")
async def ad_target(ctx: Ctx, inp: Target) -> dict[str, Any]:
    """Trạng thái một chiến dịch / nhóm / quảng cáo theo lần đọc gần nhất của kênh — bước chuẩn bị của lệnh đọc bằng quyền người bấm."""
    return await _target(ctx, inp.channel, inp.level, inp.ref)


async def _apply_live(prep: Prep, inp: StatusIn | BudgetIn) -> dict[str, Any]:
    """NGOÀI giao dịch: kiểm lại như handler, rồi nhờ kênh đọc trạng thái THẬT và ghi (nếu chưa đúng). Lỗi kênh ⇒ ném (handler không chạy, sổ không có dòng)."""
    t = await prep.query("mkt.ad_target", channel=inp.channel, level=inp.level, ref=inp.ref)
    if not t["channel"]:
        raise Invalid("Chưa nối kênh quảng cáo cho đối tượng này — đổi trong trình quản lý quảng cáo rồi bấm “Đã làm tay”")
    _, _, after = _wanted(t, inp)
    got = await prep.plugs("mkt.ad_apply", channel=t["channel"], level=t["level"], ref=t["ref"], status=after.get("status"),
                           daily_budget=after.get("daily_budget"))
    if not got:
        raise Invalid(f"Kênh {t['channel_label']} không nhận thay đổi này (gói kênh đã tắt?) — chưa đổi gì")
    return {**got[0], "at": datetime.now(UTC).isoformat()}


async def _record(ctx: Ctx, t: dict[str, Any], action: str, before: dict[str, Any], after: dict[str, Any], inp: Any, *, state: str,
                  mode: str = "api", response: dict[str, Any] | None = None) -> UUID:
    cid = await ctx.val(
        """INSERT INTO mkt.ad_changes (owner_id, team_id, channel, level, ref, name, currency, action, mode, state, before, after, origin, finding_id,
                                       requested_by, approved_by, note, response, executed_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CASE WHEN %s IN ('sent', 'manual') THEN now() END) RETURNING id""",
        (t["owner_id"], t["team_id"], t["channel"], t["level"], t["ref"], (t["name"] or "")[:300], (t["currency"] or "").upper() if t["channel"] else "",
         action, mode, state,
         Jsonb({**before, "unit": t["unit"]}), Jsonb({**after, "unit": t["unit"]}), "watcher" if inp.finding_id else "person", inp.finding_id,
         ctx.p.user_id, list(ctx.p.approved_by), inp.note.strip(), Jsonb(response) if response is not None else None, state))
    if inp.finding_id is not None:
        await ctx.exec("UPDATE mkt.ad_findings SET state = 'done', decided_by = %s, decided_at = now() WHERE id = %s AND state = 'open'",
                       (ctx.p.user_id, inp.finding_id))
    return cid


async def _change(ctx: Ctx, inp: StatusIn | BudgetIn) -> dict[str, Any]:
    t = await _target(ctx, inp.channel, inp.level, inp.ref)
    action, before, after = _wanted(t, inp)
    finding = await _finding(ctx, inp.finding_id)
    text = describe(t, action, before, after)
    brief = _brief(t, action, before, after)
    if ctx.preview:   # thẻ đề xuất: chưa gọi kênh; số của phát hiện (nếu có) đi kèm thẻ
        return {"summary": text, "before": before, "after": after, "figures": finding["figures"] if finding else None,
                "finding": finding and finding["title"], **brief}
    if not t["channel"]:
        raise Invalid("Chưa nối kênh quảng cáo cho đối tượng này — đổi trong trình quản lý quảng cáo rồi bấm “Đã làm tay”")
    got = ctx.prepared
    if got is None:   # sau phiếu duyệt (giao dịch của người duyệt) / gọi lồng: không gọi mạng ở đây ⇒ hàng đợi của vòng gửi
        cid = await _record(ctx, t, action, before, after, inp, state="queued")
        return {"change_id": cid, "state": "queued", "summary": f"{text.replace('Đã ', 'Đã duyệt — sẽ ', 1)} (vòng gửi chạy trong vài phút)", **brief}
    real = {k: got["before"].get(k) for k in before}
    cid = await _record(ctx, t, action, real, after, inp, state="sent", response={**(got.get("response") or {}), "at": got["at"]})
    ctx.emit("mkt.ad_changed", channel=t["channel"], level=t["level"], ref=t["ref"], after=after)
    already = bool(got.get("already"))
    return {"change_id": cid, "state": "sent", "already": already, "summary": text + (" (kênh đã ở đúng giá trị này — không ghi lại)" if already else ""),
            **brief}


@m.command("set_ad_status",
           purpose="Tạm dừng hoặc bật lại quảng cáo trên kênh để cắt chi tiêu kém hiệu quả hoặc chạy tiếp khi đã ổn.", reversible="Chạy lại lệnh với trạng thái ngược lại (tạm dừng ↔ bật lại)",
           resource="mkt.ad_control", title="Tạm dừng / bật lại quảng cáo trên kênh", agent="propose", risk="authority",
           prepare=_apply_live, sensitive=sensitive, basis=basis)
async def set_ad_status(ctx: Ctx, inp: StatusIn) -> dict[str, Any]:
    """Tạm dừng hoặc bật lại MỘT chiến dịch / nhóm quảng cáo / quảng cáo trên kênh đã nối. Trợ lý chỉ soạn thẻ; người bấm mới gọi kênh. Bật lại có thể phải
    duyệt (chính sách công ty)."""
    return await _change(ctx, inp)


@m.command("set_ad_budget",
           purpose="Đổi ngân sách ngày của chiến dịch hoặc nhóm quảng cáo trên kênh để chi tiêu đúng mức công ty cho phép.", reversible="Đặt lại ngân sách cũ bằng chính lệnh này (đổi vượt ngưỡng của công ty thì cần duyệt)",
           resource="mkt.ad_control", title="Đổi ngân sách ngày trên kênh", agent="propose", risk="authority",
           prepare=_apply_live, sensitive=sensitive, basis=basis)
async def set_ad_budget(ctx: Ctx, inp: BudgetIn) -> dict[str, Any]:
    """Đổi ngân sách NGÀY của một chiến dịch hoặc nhóm quảng cáo trên kênh đã nối. Quá ngưỡng của công ty (% hoặc số đồng / ngày) ⇒ phiếu duyệt."""
    return await _change(ctx, inp)


@m.command("record_ad_manual", resource="mkt.ad_control", title="Đã làm tay ngoài app", agent="deny")
async def record_ad_manual(ctx: Ctx, inp: ManualIn) -> dict[str, Any]:
    """Người đã tự đổi trong trình quản lý quảng cáo (chưa nối kênh / chưa có quyền ghi): ghi CÙNG sổ thay đổi, đánh dấu làm tay; lần đọc kênh sau thấy đúng
    ⇒ xác nhận. Trợ lý không khai thay người."""
    t = await _target(ctx, inp.channel, inp.level, inp.ref)
    if inp.action == "set_budget":
        if inp.daily_budget is None:
            raise Invalid("Nhập ngân sách ngày đã đặt")
        unknown = t["daily_budget"] is None   # chưa nối kênh / kênh chưa đọc ngân sách: ghi giá trị người khai, "trước" không biết
        action, before, after = _wanted({**t, "daily_budget": 0} if unknown else t,
                                        BudgetIn(channel=inp.channel, level=inp.level, ref=inp.ref, daily_budget=inp.daily_budget))
        before = {"daily_budget": None} if unknown else before
    else:
        action, before, after = _wanted(t, StatusIn(channel=inp.channel, level=inp.level, ref=inp.ref,
                                                    status="paused" if inp.action == "pause" else "active"))
    await _finding(ctx, inp.finding_id)
    cid = await _record(ctx, t, action, before, after, inp, state="manual", mode="manual")
    where = f" trên {t['channel_label']}" if t.get("channel_label") else ""
    return {"change_id": cid, "state": "manual", "summary": describe(t, action, before, after).replace(where, "", 1) + " — làm tay ngoài app"}


# ── Vòng gửi: thay đổi đã duyệt ⇒ kênh (quyền hệ thống); đối chiếu thay đổi đã gửi / làm tay với lần đọc kênh ──

DISPATCH_DUE = ("SELECT DISTINCT tenant_id FROM mkt.ad_changes WHERE state = 'queued'"
                " OR (state IN ('sent', 'manual') AND confirmed_at IS NULL AND channel <> '' AND created_at > now() - interval '14 days'"
                " AND coalesce(checked_at, created_at) < now() - interval '30 minutes')")


def _outcome(e: Exception) -> dict[str, Any]:
    """Lỗi kênh ở bước chuẩn bị ⇒ kết quả để handler ghi: ``Conflict`` = lỗi tạm (thử lại), còn lại = lỗi hẳn."""
    return {"ok": False, "transient": isinstance(e, Conflict), "error": str(e)[:500]}


async def _call_channel(call: Awaitable[list[Any]]) -> dict[str, Any]:
    """MỘT lời gọi kênh (``prep.plugs(…)``) — ngoài mọi giao dịch (phần cắm khai ``prepare=`` gọi nhà cung cấp không giữ kết nối). Trả kết quả phần cắm
    hoặc lỗi đã phân loại; không ném (một dòng lỗi không bỏ cả lượt)."""
    try:
        got = await call
        if not got:
            raise Invalid("Kênh quảng cáo không còn bật / không nhận yêu cầu này")
    except (Conflict, Invalid, NotFound) as e:
        return _outcome(e)
    return {"ok": True, "got": got[0], "at": datetime.now(UTC).isoformat()}


@m.query("ad_dispatch_plan", resource="mkt.ad_control", title="Thay đổi quảng cáo đã duyệt chờ gửi (cho bước gọi kênh)", internal=True, agent="deny")
async def ad_dispatch_plan(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Bước chuẩn bị của vòng gửi đọc (giao dịch đọc ngắn): lô thay đổi ``queued`` + số lần đã thử — handler chỉ ghi khi dòng còn đúng như lúc đọc."""
    rows = await ctx.all("""SELECT id, channel, level, ref, after, attempts FROM mkt.ad_changes WHERE state = 'queued' ORDER BY created_at LIMIT %s""",
                         (DISPATCH_LIMIT,))
    return {"changes": [{**r, "id": str(r["id"])} for r in rows]}


async def _prepare_dispatch(prep: Prep, inp: NoInput) -> list[dict[str, Any]]:
    """NGOÀI giao dịch: đọc lô chờ gửi, gọi kênh từng dòng (kênh đọc trạng thái thật trước — đã đúng ⇒ không ghi lại, nên hai lượt chồng nhau gửi trùng
    cũng vô hại). Trả kết quả theo dòng cho handler ghi."""
    plan = await prep.query("mkt.ad_dispatch_plan")
    out = []
    for ch in plan["changes"]:
        after = {k: v for k, v in ch["after"].items() if k in ("status", "daily_budget")}
        got = await _call_channel(prep.plugs("mkt.ad_apply", channel=ch["channel"], level=ch["level"], ref=ch["ref"], status=after.get("status"),
                                             daily_budget=after.get("daily_budget")))
        out.append({"id": ch["id"], "attempts": ch["attempts"], **got})
    return out


async def _record_send(ctx: Ctx, res: dict[str, Any]) -> str | None:
    """Ghi kết quả gửi MỘT thay đổi — chỉ khi dòng còn ``queued`` với đúng số lần thử lúc đọc (lượt khác đã ghi / đang ghi ⇒ bỏ, không ghi hai lần)."""
    ch = await ctx.one("SELECT * FROM mkt.ad_changes WHERE id = %s AND state = 'queued' AND attempts = %s FOR UPDATE SKIP LOCKED",
                       (UUID(res["id"]), res["attempts"]))
    if ch is None:
        return None
    if not res["ok"]:
        attempts = ch["attempts"] + 1
        final = not res["transient"] or attempts >= MAX_ATTEMPTS
        await ctx.exec("UPDATE mkt.ad_changes SET attempts = %s, error = %s, state = %s WHERE id = %s",
                       (attempts, res["error"], "failed" if final else "queued", ch["id"]))
        if final:
            await ctx.notify([ch["requested_by"]], kind="mkt_ad_change", title="Thay đổi quảng cáo KHÔNG thực hiện được",
                             body=f"{ACTIONS[ch['action']]} {LEVELS[ch['level']].lower()} “{ch['name'] or ch['ref']}”: {res['error']}"[:1000],
                             open={"query": "mkt.ad_changes", "input": {}})
        return "failed" if final else "retry"
    got = res["got"]
    after = {k: v for k, v in ch["after"].items() if k in ("status", "daily_budget")}
    await ctx.exec("UPDATE mkt.ad_changes SET state = 'sent', executed_at = now(), attempts = attempts + 1, error = '', response = %s WHERE id = %s",
                   (Jsonb({**(got.get("response") or {}), "already": bool(got.get("already")), "at": res["at"]}), ch["id"]))
    ctx.emit("mkt.ad_changed", channel=ch["channel"], level=ch["level"], ref=ch["ref"], after=after)
    return "sent"


async def _confirm(ctx: Ctx) -> int:
    """Thay đổi đã gửi / làm tay mà lần đọc kênh gần nhất đã đúng giá trị sau ⇒ xác nhận."""
    rows = await ctx.all("""SELECT id, channel, level, ref, after FROM mkt.ad_changes WHERE state IN ('sent', 'manual') AND confirmed_at IS NULL
                            AND channel <> '' AND created_at > now() - make_interval(days => %s) ORDER BY created_at LIMIT 200""", (CONFIRM_DAYS,))
    if not rows:
        return 0
    objs = await channel_objects(ctx, [(r["level"], r["ref"]) for r in rows])
    done = []
    for r in rows:
        o = objs.get((r["level"], r["ref"]))
        if o and o["channel"] == r["channel"] and all(o.get(k) == v for k, v in r["after"].items() if k in ("status", "daily_budget")):
            done.append(r["id"])
    await ctx.exec("UPDATE mkt.ad_changes SET checked_at = now(), confirmed_at = CASE WHEN id = ANY(%s) THEN now() END WHERE id = ANY(%s)",
                   (done, [r["id"] for r in rows]))
    return len(done)


@m.command("dispatch_ad_changes", resource="mkt.ad_control", title="Gửi thay đổi quảng cáo đã duyệt lên kênh", internal=True, agent="deny",
           sweep=DISPATCH_DUE, prepare=_prepare_dispatch)
async def dispatch_ad_changes(ctx: Ctx, inp: NoInput) -> dict[str, int]:
    """Vòng quét của MỘT công ty: thay đổi ``queued`` (đã duyệt) ⇒ kênh — lỗi tạm thử lại lượt sau (tối đa ``MAX_ATTEMPTS``), lỗi hẳn ⇒ ``failed`` + báo người
    xin; rồi đối chiếu thay đổi đã gửi / làm tay với lần đọc kênh gần nhất. Kênh được gọi ở bước chuẩn bị, NGOÀI mọi giao dịch; handler chỉ ghi.
    ``ctx.prepared`` None (gọi lồng / chạy thử) ⇒ không gửi gì, dòng chờ nằm nguyên cho lượt sau."""
    out = {"sent": 0, "failed": 0, "retry": 0}
    for res in ctx.prepared or []:
        if (got := await _record_send(ctx, res)) is not None:
            out[got] += 1
    out["confirmed"] = await _confirm(ctx)
    return out


# ── Tác nhân theo dõi: mỗi ngày, chỉ phát hiện + đề xuất (không bao giờ gọi kênh) ──

# Công ty CHƯA có dòng chính sách (chưa ai mở "Chính sách quảng cáo") vẫn được theo dõi theo chính sách MẶC ĐỊNH khi đã có chi phí quảng cáo — trước đây
# vòng quét chỉ chọn công ty đã có dòng ``mkt.ad_policy``, mà dòng đó lại chỉ sinh ở lượt theo dõi đầu ⇒ không bao giờ chạy (rà 23/11/2026: FB-HSR chi 61 tr,
# 0 lead chất lượng, màn vẫn báo ổn). Lượt đầu tự ghi dòng (``run_watch``) ⇒ từ đó đi nhánh trên.
WATCH_DUE = ("SELECT p.tenant_id FROM mkt.ad_policy p WHERE coalesce((p.settings->>'watch')::boolean, true)"
             " AND (p.last_watch_on IS NULL OR p.last_watch_on < core.today_of(p.tenant_id))"
             " UNION SELECT DISTINCT s.tenant_id FROM mkt.spend s"
             " WHERE NOT EXISTS (SELECT 1 FROM mkt.ad_policy p WHERE p.tenant_id = s.tenant_id)")


def _figures(r: dict[str, Any], d_from: Any, d_to: Any, outcome: dict[str, Any] | None, **extra: Any) -> dict[str, Any]:
    q = r.get("qualified") or 0
    return {"spend": r["spend"], "leads": r.get("leads", 0), "qualified": q, "open_leads": r.get("open_leads", 0),
            "outcome_count": r.get("outcome_count", 0) if outcome else None, "outcome_value_vnd": r.get("outcome_value_vnd", 0) if outcome else None,
            "outcome_label": outcome["count"] if outcome else "", "cpql": round(r["spend"] / q) if q else None,
            "window_from": d_from.isoformat(), "window_to": d_to.isoformat(), **extra}


def _ripe(pol: AdPolicy, r: dict[str, Any]) -> bool:
    """Nhóm lead đủ chín để kết luận: không quá ``max_open_pct`` % lead còn đang chăm."""
    return not (r["leads"] and r["open_leads"] * 100 / r["leads"] >= pol.max_open_pct)


def _no_result(pol: AdPolicy, r: dict[str, Any], measured: bool, min_spend: int) -> bool:
    if r["spend"] < min_spend or r["qualified"] > pol.max_qualified or (measured and r["outcome_count"] > 0):
        return False
    return _ripe(pol, r) or not r["qualified"]


def _high_cpql(pol: AdPolicy, r: dict[str, Any], measured: bool) -> bool:
    return (pol.max_cpql_vnd > 0 and r["qualified"] > 0 and r["spend"] / r["qualified"] > pol.max_cpql_vnd and not (measured and r["outcome_count"] > 0)
            and _ripe(pol, r))


def _title(scope: str, name: str, rule: str, f: dict[str, Any], days: int) -> str:
    """Câu của phát hiện — CODE ráp từ số (không model)."""
    what = f"{SCOPES[scope]} “{name}”"
    if rule == "no_platform_lead":
        return f"{what} chi {_vnd(f['spend'])} trong {days} ngày, nền tảng báo 0 lead form"
    outcome = (f"{f['outcome_count']} {f['outcome_label'].lower()}" if f["outcome_count"] is not None else "kết quả bán: không đo được")
    if rule == "high_cpql":
        return f"{what}: chi phí / lead chất lượng {_vnd(f['cpql'] or 0)} ({_vnd(f['spend'])} ÷ {f['qualified']} lead chất lượng) trong {days} ngày, {outcome}"
    return f"{what} chi {_vnd(f['spend'])} trong {days} ngày: {f['leads']} lead, {f['qualified']} lead chất lượng, {outcome}"


async def _file(ctx: Ctx, day: Any, scope: str, key: str, rule: str, title: str, d_from: Any, d_to: Any, figures: dict[str, Any],
                obj: dict[str, Any] | None, level: str | None, proposal: dict[str, Any] | None, who: Who) -> bool:
    """Một phát hiện / (ngày, phạm vi, mã, luật) — chạy lại trong ngày không nhân đôi; phát hiện cũ còn mở của cùng (phạm vi, mã, luật) hết hạn. ``who`` =
    (người phụ trách, đội) của chiến dịch — người phạm vi "team" chỉ thấy phát hiện của chiến dịch mình / đội mình."""
    fid = await ctx.val(
        """INSERT INTO mkt.ad_findings (owner_id, team_id, day, scope, scope_key, rule, title, window_from, window_to, figures, channel, level, ref, proposal)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (tenant_id, day, scope, scope_key, rule) DO NOTHING RETURNING id""",
        (*who, day, scope, key, rule, title[:500], d_from, d_to, Jsonb(figures), obj["channel"] if obj else "", level, key if level else "",
         Jsonb(proposal) if proposal else None))
    if fid is None:
        return False
    await ctx.exec("""UPDATE mkt.ad_findings SET state = 'expired', decided_at = now() WHERE scope = %s AND scope_key = %s AND rule = %s
                      AND state = 'open' AND id <> %s""", (scope, key, rule, fid))
    return True


type Who = tuple[UUID | None, UUID | None]
NOBODY: Who = (None, None)


def _common(whos: list[Who]) -> Who:
    """(người phụ trách, đội) chung của nhiều chiến dịch (kế hoạch / [[offering]]) — khác nhau ⇒ trống (chỉ phạm vi "all" thấy)."""
    owners, teams = {w[0] for w in whos}, {w[1] for w in whos}
    return (next(iter(owners)) if len(owners) == 1 else None), (next(iter(teams)) if len(teams) == 1 else None)


def _pause(o: dict[str, Any] | None, level: str, ref: str) -> dict[str, Any] | None:
    if o is None:
        return None   # chưa nối kênh: phát hiện không kèm lệnh — người làm tay rồi bấm "Đã làm tay"
    return {"command": "mkt.set_ad_status", "action": "pause", "input": {"channel": o["channel"], "level": level, "ref": ref, "status": "paused"}}


async def run_watch(ctx: Ctx) -> dict[str, int]:
    """Một lượt theo dõi (quyền hệ thống trong vòng quét; test gọi thẳng). Trả số phát hiện mới. Kết quả như nhau dù công ty có 0, 1 hay nhiều kênh nối:
    kênh chỉ góp TRẠNG THÁI (đang chạy?, ngân sách) và cho phép đề xuất kèm lệnh."""
    pol, day = await policy(ctx), today()
    d_to = day - timedelta(days=1)
    d_from = d_to - timedelta(days=pol.window_days - 1)
    await ctx.exec("""INSERT INTO mkt.ad_policy (last_watch_on) VALUES (%s)
                      ON CONFLICT (tenant_id) DO UPDATE SET last_watch_on = EXCLUDED.last_watch_on""", (day,))
    res = await ctx.call("mkt.ad_results", date_from=d_from, date_to=d_to)
    out = {"findings": 0, "unreliable": 0}
    if not res["reliable"]:
        out["unreliable"] = 1
        return out
    outcome, measured, days = res["outcome"], res["outcome"] is not None, pol.window_days
    objs = await channel_objects(ctx, [("campaign", c["code"]) for c in res["campaigns"]] + [("ad", a["code"]) for a in res["ads"]])
    camps = {c["code"]: c for c in res["campaigns"]}
    whos: dict[str, Who] = {r["code"]: (r["owner_id"], r["team_id"]) for r in await ctx.all(
        "SELECT code, owner_id, team_id FROM mkt.campaigns WHERE code = ANY(%s)", (sorted(camps.keys() | {a["campaign_code"] for a in res["ads"]}),))}

    def live_c(c: dict[str, Any]) -> bool:
        o = objs.get(("campaign", c["code"]))
        return bool(o["live"]) if o else c["status"] == "running"

    live = [c for c in res["campaigns"] if live_c(c)]
    for c in live:
        o = objs.get(("campaign", c["code"]))
        f = _figures(c, d_from, d_to, outcome, channel=o["channel"] if o else "")
        name = str(o["name"] if o and o["name"] else c["name"] or c["code"])
        if _no_result(pol, c, measured, pol.min_spend_vnd):
            out["findings"] += await _file(ctx, day, "campaign", c["code"], "no_result", _title("campaign", name, "no_result", f, days), d_from, d_to, f, o,
                                           "campaign", _pause(o, "campaign", c["code"]), whos.get(c["code"], NOBODY))
        elif _high_cpql(pol, c, measured):
            prop = None
            if o and o["daily_budget"]:
                ccy, unit = o["currency"], o["unit"]
                cur = major(o["daily_budget"], unit)
                assert cur is not None
                new = (cur * (100 - pol.cut_pct) / 100).quantize(Decimal(1) if unit == 1 else Decimal(1) / unit, ROUND_HALF_UP)
                prop = {"command": "mkt.set_ad_budget", "action": "cut",
                        "input": {"channel": o["channel"], "level": "campaign", "ref": c["code"], "daily_budget": str(new)}}
                f["proposed_budget"] = str(new)
                f["currency"] = ccy
            out["findings"] += await _file(ctx, day, "campaign", c["code"], "high_cpql", _title("campaign", name, "high_cpql", f, days), d_from, d_to, f, o,
                                           "campaign", prop, whos.get(c["code"], NOBODY))
    for scope, k in (("plan", "plan_code"), ("offering", "offering_code")):
        groups: dict[str, list[dict[str, Any]]] = {}
        for c in live:
            if c[k]:
                groups.setdefault(c[k], []).append(c)
        for key, cs in sorted(groups.items()):
            if len(cs) < 2:
                continue   # một chiến dịch: đã có phát hiện ở cấp chiến dịch
            g = {"spend": sum(c["spend"] for c in cs), **{x: sum(c[x] for c in cs) for x in ("leads", "qualified", "open_leads", "outcome_count",
                                                                                             "outcome_value_vnd")}}
            if _no_result(pol, g, measured, pol.min_spend_vnd):
                f = _figures(g, d_from, d_to, outcome, campaigns=sorted(c["code"] for c in cs))
                out["findings"] += await _file(ctx, day, scope, key, "no_result", _title(scope, key, "no_result", f, days), d_from, d_to, f, None, None, None,
                                               _common([whos.get(c["code"], NOBODY) for c in cs]))
    for a in res["ads"]:
        c = camps.get(a["campaign_code"])
        o = objs.get(("ad", a["code"]))
        if not (bool(o["live"]) if o else (c is not None and live_c(c))) or a["spend"] < pol.ad_min_spend_vnd:
            continue
        name = str(o["name"] if o and o["name"] else a["name"] or a["code"])
        # đủ lead của chiến dịch biết mẫu ⇒ xét quảng cáo bằng lead CRM + kết quả bán; không thì chỉ còn số nền tảng báo
        known = bool(c and c["leads"] and c["with_ad"] * 100 / c["leads"] >= pol.ad_attr_pct)
        if known:
            if _no_result(pol, a, measured, pol.ad_min_spend_vnd):
                f = _figures(a, d_from, d_to, outcome, channel=o["channel"] if o else "", leads_platform=a["leads_platform"])
                out["findings"] += await _file(ctx, day, "ad", a["code"], "no_result", _title("ad", name, "no_result", f, days), d_from, d_to, f, o, "ad",
                                               _pause(o, "ad", a["code"]), whos.get(a["campaign_code"], NOBODY))
        elif a["leads_platform"] == 0:
            f = _figures({"spend": a["spend"]}, d_from, d_to, None, channel=o["channel"] if o else "", leads_platform=0)
            out["findings"] += await _file(ctx, day, "ad", a["code"], "no_platform_lead", _title("ad", name, "no_platform_lead", f, days), d_from, d_to, f,
                                           o, "ad", _pause(o, "ad", a["code"]), whos.get(a["campaign_code"], NOBODY))
    return out


@m.command("watch_ads", resource="mkt.ad_control", title="Theo dõi hiệu quả quảng cáo", internal=True, agent="deny", sweep=WATCH_DUE)
async def watch_ads(ctx: Ctx, inp: NoInput) -> dict[str, int]:
    """Vòng quét mỗi ngày của MỘT công ty đã lưu chính sách quảng cáo (theo dõi bật) — chỉ tạo phát hiện / đề xuất, không bao giờ gọi kênh."""
    return await run_watch(ctx)


@m.query("ad_suggestions", resource="mkt.ad_control", title="Gợi ý buổi sáng (quảng cáo)", agent="deny")
async def ad_suggestions(ctx: Ctx, inp: NoInput) -> list[dict[str, Any]]:
    """Nguồn gợi ý buổi sáng (0074 A): phát hiện đang mở có đề xuất ⇒ lệnh thật (chạy thử thành thẻ, số của phát hiện đi kèm); lý do là câu code ráp từ số.
    Theo quyền người nhận (RLS): phạm vi "team" chỉ nhận phát hiện của chiến dịch mình / đội mình."""
    rows = await ctx.all("""SELECT id, title, proposal FROM mkt.ad_findings WHERE state = 'open' AND proposal IS NOT NULL
                            ORDER BY (figures->>'spend')::bigint DESC, created_at LIMIT 3""")
    return [{"command": r["proposal"]["command"], "input": {**r["proposal"]["input"], "finding_id": str(r["id"])}, "reason": r["title"],
             "key": f"mkt_ad:{r['id']}", "rank": 40 + i} for i, r in enumerate(rows)]


m.suggests("mkt.ad_suggestions", rank=30)


class FindingRef(Input):
    finding_id: UUID = field("Phát hiện", hidden=True)
    note: str = field("Lý do bỏ qua", "", max_length=500)


@m.command("dismiss_ad_finding", resource="mkt.ad_control", title="Bỏ qua phát hiện quảng cáo", agent="propose", risk="low")
async def dismiss_ad_finding(ctx: Ctx, inp: FindingRef) -> dict[str, Any]:
    n = await ctx.exec("UPDATE mkt.ad_findings SET state = 'dismissed', decided_by = %s, decided_at = now(), note = %s WHERE id = %s AND state = 'open'",
                       (ctx.p.user_id, inp.note.strip(), inp.finding_id))
    if not n:
        raise NotFound("Phát hiện không còn mở")
    return {"summary": "Đã bỏ qua phát hiện"}


# ── Chuyển đổi về kênh: lead có kết quả bán ⇒ kênh tối ưu theo người mua (MẶC ĐỊNH TẮT — dấu băm SĐT / email của khách rời công ty) ──

@m.on("crm.lead_outcome_recorded")
async def queue_ad_conversion(ctx: Ctx, event: Event) -> None:
    """Công ty BẬT gửi chuyển đổi + lead đến từ chiến dịch / quảng cáo một kênh đang nối ⇒ một dòng chờ gửi (event_id cố định theo lead + mã kết quả ⇒ không
    nhân đôi). Kết quả bị huỷ trước khi gửi ⇒ không gửi. Tắt ⇒ không ghi gì."""
    pl = event.payload
    lead, ref = UUID(str(pl["lead_id"])), str(pl.get("ref") or "")[:80]
    if pl.get("outcome") == "undone":
        await ctx.exec("""UPDATE mkt.ad_conversions SET state = 'cancelled', error = 'Kết quả bán đã huỷ trước khi gửi' WHERE lead_id = %s AND outcome_ref = %s
                          AND state = 'queued'""", (lead, ref))
        return
    if not (await policy(ctx)).conversions:
        return
    src = await ctx.call_system("crm.lead_ad_of", lead_id=lead)
    if not src or not src["campaign_id"]:
        return
    codes = await ctx.one("""SELECT c.code AS campaign_code, coalesce((SELECT a.code FROM mkt.ads a WHERE a.id = %s AND a.campaign_id = c.id), '') AS ad_code,
                                    c.owner_id, c.team_id FROM mkt.campaigns c WHERE c.id = %s""", (src["ad_id"], src["campaign_id"]))
    if codes is None:
        return
    objs = await channel_objects(ctx, [("campaign", codes["campaign_code"])] + ([("ad", codes["ad_code"])] if codes["ad_code"] else []))
    owner = objs.get(("ad", codes["ad_code"])) or objs.get(("campaign", codes["campaign_code"]))
    if owner is None:
        return   # lead không đến từ kênh đang nối — không có ai để gửi
    await ctx.exec(
        """INSERT INTO mkt.ad_conversions (owner_id, team_id, channel, lead_id, outcome_ref, event_id, campaign_code, ad_code, value_vnd, occurred_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (tenant_id, channel, event_id) DO NOTHING""",
        (codes["owner_id"], codes["team_id"], owner["channel"], lead, ref, uuid5(NAMESPACE_URL, f"barista:won:{lead}:{ref}").hex, codes["campaign_code"], codes["ad_code"],
         max(0, int(pl.get("value_vnd") or 0)), pl.get("occurred_at") or datetime.now(UTC)))


CONVERT_DUE = "SELECT DISTINCT tenant_id FROM mkt.ad_conversions WHERE state = 'queued' AND next_at <= now()"


@m.query("ad_conversion_plan", resource="mkt.ad_control", title="Chuyển đổi chờ gửi về kênh (cho bước gọi kênh)", internal=True, agent="deny")
async def ad_conversion_plan(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Bước chuẩn bị của vòng gửi chuyển đổi đọc (giao dịch đọc ngắn): công tắc của công ty + lô chờ gửi; dấu băm SĐT / email lấy từ crm NGAY lúc này
    (không lưu — chỉ ở bộ nhớ tới lúc gửi kênh). Công ty đã tắt ⇒ không lấy dấu băm, không dòng nào."""
    if not (await policy(ctx)).conversions:
        return {"on": False, "rows": []}
    rows = await ctx.all("""SELECT id, channel, event_id, occurred_at, value_vnd, campaign_code, ad_code, lead_id, attempts FROM mkt.ad_conversions
                            WHERE state = 'queued' AND next_at <= now() ORDER BY created_at LIMIT %s""", (CONVERT_LIMIT,))
    out = []
    for r in rows:
        try:
            async with ctx.conn.transaction():   # lead không đọc được ⇒ chỉ dòng đó lỗi
                keys = await ctx.call_system("crm.lead_match_keys", lead_id=r["lead_id"])
        except (Invalid, NotFound):
            keys = None
        out.append({**r, "id": str(r["id"]), "lead_id": str(r["lead_id"]), "match": keys})
    return {"on": True, "rows": out}


async def _prepare_conversions(prep: Prep, inp: NoInput) -> list[dict[str, Any]]:
    """NGOÀI giao dịch: gửi từng chuyển đổi về kênh (``event_id`` cố định ⇒ kênh khử trùng khi hai lượt chồng nhau cùng gửi)."""
    plan = await prep.query("mkt.ad_conversion_plan")
    out = []
    for r in plan["rows"]:
        keys = r["match"]
        if not keys or not (keys["email_sha256"] or keys["phone_sha256"]):
            got = _outcome(Invalid("Lead không có SĐT / email để kênh khớp người mua"))
        else:
            got = await _call_channel(prep.plugs("mkt.ad_convert", channel=r["channel"], event_id=r["event_id"], event="won",
                                                 occurred_at=r["occurred_at"], value_vnd=r["value_vnd"], campaign_ref=r["campaign_code"],
                                                 ad_ref=r["ad_code"], match=keys))
        out.append({"id": r["id"], "attempts": r["attempts"], **got})
    return out


async def _record_conversion(ctx: Ctx, res: dict[str, Any]) -> str | None:
    """Ghi kết quả gửi MỘT chuyển đổi — chỉ khi dòng còn đúng số lần thử lúc đọc (lượt khác đã ghi / đang ghi ⇒ bỏ). Dòng vừa bị huỷ (kết quả bán huỷ,
    công ty tắt) trong lúc gửi mà kênh ĐÃ nhận ⇒ ghi ``sent`` (sổ nói đúng điều đã xảy ra); gửi lỗi ⇒ để nguyên ``cancelled``."""
    r = await ctx.one("""SELECT id, state, attempts FROM mkt.ad_conversions WHERE id = %s AND attempts = %s AND state IN ('queued', 'cancelled')
                         FOR UPDATE SKIP LOCKED""", (UUID(res["id"]), res["attempts"]))
    if r is None or (r["state"] == "cancelled" and not res["ok"]):
        return None
    if not res["ok"]:
        attempts = r["attempts"] + 1
        final = not res["transient"] or attempts >= MAX_ATTEMPTS
        await ctx.exec("""UPDATE mkt.ad_conversions SET attempts = %s, error = %s, state = %s,
                          next_at = now() + make_interval(mins => %s) WHERE id = %s""",
                       (attempts, res["error"], "failed" if final else "queued", RETRY_MIN[min(attempts, len(RETRY_MIN)) - 1], r["id"]))
        return "failed" if final else "retry"
    await ctx.exec("UPDATE mkt.ad_conversions SET state = 'sent', sent_at = now(), attempts = attempts + 1, error = '', response = %s WHERE id = %s",
                   (Jsonb(res["got"].get("response") or {}), r["id"]))
    return "sent"


@m.command("send_ad_conversions", resource="mkt.ad_control", title="Gửi chuyển đổi về kênh quảng cáo", internal=True, agent="deny", sweep=CONVERT_DUE,
           prepare=_prepare_conversions)
async def send_ad_conversions(ctx: Ctx, inp: NoInput) -> dict[str, int]:
    """Vòng quét của MỘT công ty: chuyển đổi chờ gửi ⇒ kênh. Kênh được gọi ở bước chuẩn bị, NGOÀI mọi giao dịch (dấu băm lấy từ crm ngay trước khi gửi,
    không lưu); handler chỉ ghi. Lỗi tạm ⇒ thử lại giãn dần (tối đa ``MAX_ATTEMPTS``), lỗi hẳn ⇒ ``failed``. Công ty đã tắt (đọc lại trong giao dịch) ⇒
    dòng còn chờ thành ``cancelled``. ``ctx.prepared`` None (gọi lồng / chạy thử) ⇒ không gửi gì."""
    out = {"sent": 0, "failed": 0, "retry": 0, "cancelled": 0}
    for res in ctx.prepared or []:
        if (got := await _record_conversion(ctx, res)) is not None:
            out[got] += 1
    if not (await policy(ctx)).conversions:
        out["cancelled"] = len(await ctx.all(
            """UPDATE mkt.ad_conversions SET state = 'cancelled', error = 'Công ty đã tắt gửi chuyển đổi về kênh'
               WHERE id IN (SELECT id FROM mkt.ad_conversions WHERE state = 'queued' AND next_at <= now() ORDER BY created_at LIMIT %s FOR UPDATE SKIP LOCKED)
               RETURNING id""", (CONVERT_LIMIT,)))
    return out


# ── Chính sách (bất biến 5): ngưỡng theo dõi + ngưỡng duyệt + tỷ giá + công tắc chuyển đổi ──

def fx_pairs(text: str) -> list[list[str]]:
    """"USD = 26.280" mỗi dòng ⇒ [["USD", "26280"]] (tỷ giá dạng người Việt gõ: chấm nghìn, phẩy thập phân)."""
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        k, _, v = line.partition("=")
        ccy = k.strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", ccy) or ccy == "VND":
            raise Invalid(f"Tỷ giá: “{line.strip()}” — dạng “USD = 26.280”")
        raw = v.strip().replace(" ", "")
        raw = raw.replace(".", "").replace(",", ".") if ("," in raw or raw.count(".") > 1 or re.fullmatch(r"\d{1,3}(\.\d{3})+", raw)) else raw
        try:
            rate = Decimal(raw)
        except InvalidOperation:
            raise Invalid(f"Tỷ giá {ccy}: “{v.strip()}” không phải số") from None
        if rate <= 0:
            raise Invalid(f"Tỷ giá {ccy} phải > 0")
        out.append([ccy, str(rate.normalize())])
    return out


class PolicyIn(Input):
    watch: bool = field("Bật tác nhân theo dõi (mỗi ngày)", True)
    window_days: int = field("Cửa sổ tính (ngày)", 14, ge=1, le=92)
    min_spend_vnd: Money = field("Chiến dịch chi từ", 5_000_000, ge=0, help="…trong cửa sổ mà chưa ra kết quả ⇒ đề xuất tạm dừng")
    max_qualified: int = field("…khi lead chất lượng không quá", 0, ge=0, le=1000)
    max_open_pct: int = field("Bỏ qua khi lead còn đang chăm từ (%)", 80, ge=1, le=100, help="Nhóm lead chưa chín ⇒ chưa kết luận")
    max_cpql_vnd: Money = field("Chi phí / lead chất lượng tối đa", 0, ge=0, help="0 = không xét; vượt ⇒ đề xuất giảm ngân sách")
    cut_pct: int = field("Giảm ngân sách (%)", 30, ge=1, le=90)
    ad_min_spend_vnd: Money = field("Quảng cáo chi từ (chưa ra kết quả)", 2_000_000, ge=0)
    ad_attr_pct: int = field("Xét quảng cáo bằng lead CRM khi lead biết mẫu từ (%)", 80, ge=1, le=100,
                             help="Dưới mức này: chỉ xét quảng cáo bằng số lead nền tảng báo")
    budget_pct_max: int = field("Đổi ngân sách ngày quá (%) phải duyệt", 20, ge=0, le=1000)
    budget_vnd_max: Money = field("…hoặc quá (đồng / ngày) phải duyệt", 2_000_000, ge=0)
    resume_needs_approval: bool = field("Bật lại quảng cáo phải duyệt", True)
    conversions: bool = field("Gửi chuyển đổi về kênh quảng cáo", False,
                              help="Lead có kết quả bán (vd ký hợp đồng) ⇒ gửi DẤU BĂM SĐT / email của khách + giá trị về kênh (vd Meta) để kênh tối ưu theo "
                                   "người mua. Dữ liệu khách rời công ty — chỉ bật khi công ty đã có căn cứ (đồng ý / điều khoản) cho việc này")
    fx: str = field("Tỷ giá (tài khoản tính ngoại tệ)", "", kind="textarea", max_length=500, example="USD = 26.280",
                    help="Mỗi dòng “USD = 26.280” — quy chi phí ra đồng và tính ngưỡng duyệt")


@m.command("set_ad_policy",
           purpose="Đặt ngưỡng theo dõi, ngưỡng phải duyệt và tỷ giá cho quảng cáo, để tác nhân theo dõi và người duyệt cùng một luật.", reversible="Chạy lại lệnh với ngưỡng cũ",
           resource="mkt.ad_policy", action="manage", title="Chính sách quảng cáo", agent="propose", risk="authority")
async def set_ad_policy(ctx: Ctx, inp: PolicyIn) -> dict[str, Any]:
    """Ngưỡng của tác nhân theo dõi + ngưỡng phải duyệt + tỷ giá + công tắc gửi chuyển đổi — DỮ LIỆU của công ty (bất biến 5); người duyệt là chuỗi
    "mkt_ad_change". Lưu lần đầu ⇒ tác nhân theo dõi bắt đầu chạy mỗi ngày (nếu bật)."""
    pol = AdPolicy.of({**inp.model_dump(), "fx": fx_pairs(inp.fx)})
    await ctx.exec("""INSERT INTO mkt.ad_policy (settings, updated_by) VALUES (%s, core.me())
                      ON CONFLICT (tenant_id) DO UPDATE SET settings = EXCLUDED.settings, updated_by = core.me(), updated_at = now()""", (Jsonb(pol.as_json()),))
    return {"summary": "Đã lưu chính sách quảng cáo", "policy": pol.as_json()}


@m.query("fx_rates", resource=None, title="Tỷ giá trong chính sách quảng cáo", internal=True, agent="deny", family="company_figures",
         why="Gói khai consumes(\"company_figures\") (vd gói kết nối kênh quảng cáo) quy chi phí tài khoản ngoại tệ ra đồng khi ghi vào Marketing — cùng tỷ giá "
             "ngưỡng duyệt dùng. Trả {tiền: tỷ giá VND dạng chuỗi} (VND = 1) — chỉ dữ liệu cấu hình của công ty, không số liệu.")
async def fx_rates(ctx: Ctx, inp: NoInput) -> dict[str, str]:
    pol = await policy(ctx)
    return {"VND": "1", **{ccy: str(r) for ccy, _ in pol.fx if (r := pol.rate(ccy)) is not None}}


# ── Màn ──

@m.query("ad_policy_page", resource="mkt.ad_control", title="Chính sách quảng cáo")
async def ad_policy_page(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    saved = await ctx.val("SELECT 1 FROM mkt.ad_policy WHERE settings <> '{}'::jsonb")
    pol = await policy(ctx)
    facts = [f"Theo dõi mỗi ngày: {'bật' if pol.watch and saved else 'chưa chạy — lưu chính sách để bật' if not saved else 'tắt'} · cửa sổ {pol.window_days} ngày",
             f"Đề xuất tạm dừng: chi ≥ {_vnd(pol.min_spend_vnd)} mà lead chất lượng ≤ {pol.max_qualified}, chưa có kết quả bán",
             f"Quảng cáo: chi ≥ {_vnd(pol.ad_min_spend_vnd)} — xét bằng lead CRM khi ≥ {pol.ad_attr_pct}% lead của chiến dịch biết mẫu",
             f"Phải duyệt: đổi ngân sách ngày > {pol.budget_pct_max}% hoặc > {_vnd(pol.budget_vnd_max)} / ngày"
             + (" · bật lại quảng cáo" if pol.resume_needs_approval else ""),
             f"Gửi chuyển đổi về kênh: {'BẬT — dấu băm SĐT / email của khách ký đi ra kênh' if pol.conversions else 'tắt'}",
             "Tỷ giá: " + (", ".join(f"{a} = {b}" for a, b in pol.fx) or "chưa khai (tài khoản ngoại tệ chưa ghi được chi phí)")]
    act = ui.act("mkt.set_ad_policy", "Sửa chính sách", values={**pol.as_json(), "fx": "\n".join(f"{a} = {b}" for a, b in pol.fx)}, style="primary")
    return ui.card("Chính sách quảng cáo", [ui.b_callout("muted", "Dữ liệu của công ty", "Ngưỡng của tác nhân theo dõi và ngưỡng phải duyệt — không viết cứng",
                                                         facts=facts)],
                   subtitle="Áp cho mọi kênh quảng cáo", icon="slider.horizontal.3", actions=[act] if ctx.p.can("mkt.ad_policy", "manage") else [],
                   open=[{"title": "Đề xuất quảng cáo", "query": "mkt.ad_findings", "input": {}},
                         {"title": "Sổ thay đổi quảng cáo", "query": "mkt.ad_changes", "input": {}},
                         {"title": "Chuyển đổi gửi về kênh", "query": "mkt.ad_conversions", "input": {}}])


class FindingsIn(Input):
    state: Literal["open", "all"] = field("Hiện", "open", options={"open": "Đang mở", "all": "Tất cả"})


@m.query("ad_findings", resource="mkt.ad_control", title="Đề xuất quảng cáo")
async def ad_findings(ctx: Ctx, inp: FindingsIn) -> dict[str, Any]:
    """Phát hiện của tác nhân theo dõi — SỐ là bản chụp lúc tính (kết quả ``mkt.ad_results``), không tính lại. Theo phạm vi người xem (RLS theo người phụ
    trách / đội của chiến dịch): phạm vi "team" thấy phát hiện của chiến dịch mình / đội mình, "all" thấy cả công ty."""
    rows = await ctx.all("""SELECT * FROM mkt.ad_findings WHERE (%s = 'all' OR state = 'open') ORDER BY day DESC, (figures->>'spend')::bigint DESC
                            LIMIT 300""", (inp.state,))
    # câu báo trống phải nói THẬT: chưa quét lần nào ≠ quét rồi không thấy gì (rà 23/11/2026)
    last = await ctx.val("SELECT last_watch_on FROM mkt.ad_policy")
    empty = (f"Lượt theo dõi gần nhất {last:%d/%m/%Y}: không có chiến dịch nào vượt ngưỡng của công ty" if last else
             "Tác nhân chưa quét lần nào — lượt đầu chạy trong ngày khi công ty đã có chi phí quảng cáo")
    out = []
    for r in rows:
        f = r["figures"]
        prop = (r["proposal"] or {}).get("action", "")
        out.append({"id": str(r["id"]), "day": r["day"], "scope": r["scope"], "rule": r["rule"], "title": r["title"], "spend": f.get("spend"),
                    "leads": f.get("leads"), "qualified": f.get("qualified"), "outcome": f.get("outcome_count"), "cpql": f.get("cpql"),
                    "leads_platform": f.get("leads_platform"), "window": f"{f['window_from']} … {f['window_to']}", "state": r["state"],
                    "channel": r["channel"], "level": r["level"] or "", "ref": r["ref"], "proposal": prop, "has_object": bool(r["level"]),
                    "proposed_budget": f.get("proposed_budget", ""), "manual_action": "set_budget" if prop == "cut" else "pause"})
    return ui.table(out, [
        ui.col("day", "Ngày", "date"), ui.col("scope", "Phạm vi", "badge", labels=SCOPES), ui.col("rule", "Luật", "badge", labels=RULES),
        ui.col("title", "Phát hiện", width=360), ui.col("spend", "Đã chi", "money"), ui.col("leads", "Lead", "number"),
        ui.col("qualified", "Lead chất lượng", "number"), ui.col("outcome", "Kết quả bán", "number"), ui.col("cpql", "Chi phí / lead CL", "money"),
        ui.col("leads_platform", "Lead nền tảng báo", "number"), ui.col("channel", "Kênh"), ui.col("window", "Cửa sổ"),
        ui.col("state", "Trạng thái", "badge", labels={"open": "Đang mở", "done": "Đã xử lý", "dismissed": "Bỏ qua", "expired": "Hết hạn"}),
    ], title="Đề xuất quảng cáo", empty=empty,
       note="Số là bản chụp lúc tác nhân tính (cùng định nghĩa với Hiệu quả quảng cáo). Kết quả bán trống = công ty chưa có gói ghi kết quả bán — không phải 0. "
            "Kênh trống = chưa nối kênh quảng cáo: làm tay rồi bấm “Đã làm tay”. Tác nhân không tự đổi gì trên kênh.")


@m.query("ad_changes", resource="mkt.ad_control", title="Sổ thay đổi quảng cáo")
async def ad_changes(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Mỗi lần gọi kênh / mỗi lần khai đã làm tay: ai bấm, ai duyệt, trước / sau, phản hồi kênh, đã xác nhận bằng lần đọc sau chưa."""
    rows = await ctx.all("""SELECT ch.*, u.name AS by_name,
                                   (SELECT string_agg(coalesce(x.name, x.email), ', ') FROM core.users x WHERE x.id = ANY(ch.approved_by)) AS approvers
                            FROM mkt.ad_changes ch LEFT JOIN core.users u ON u.id = ch.requested_by ORDER BY ch.created_at DESC LIMIT 300""")

    def val(d: dict[str, Any], ccy: str) -> str:
        if "daily_budget" in d:
            return money_text(major(d["daily_budget"], int(d.get("unit") or 1)), ccy)
        return OBJ_STATUS.get(str(d.get("status") or ""), str(d.get("status") or ""))

    out = [{"id": str(r["id"]), "at": r["created_at"], "what": f"{LEVELS[r['level']]} “{r['name'] or r['ref']}”", "channel": r["channel"] or "—",
            "action": r["action"], "before": val(r["before"], r["currency"]), "after": val(r["after"], r["currency"]), "state": r["state"],
            "by": r["by_name"] or "", "approvers": r["approvers"] or "", "origin": "Tác nhân theo dõi" if r["origin"] == "watcher" else "Người",
            "error": r["error"], "confirmed": "Đã khớp kênh" if r["confirmed_at"] else ""} for r in rows]
    return ui.table(out, [ui.col("at", "Lúc", "datetime"), ui.col("what", "Đối tượng", width=240), ui.col("channel", "Kênh"),
                          ui.col("action", "Việc", "badge", labels=ACTIONS), ui.col("before", "Trước"), ui.col("after", "Sau"),
                          ui.col("state", "Trạng thái", "badge", labels=STATES, tones=STATE_TONE), ui.col("origin", "Đề xuất bởi"), ui.col("by", "Người bấm"),
                          ui.col("approvers", "Người duyệt"), ui.col("confirmed", "Xác nhận"), ui.col("error", "Lỗi kênh", width=260)],
                    title="Sổ thay đổi quảng cáo", empty="Chưa có thay đổi nào", limit=300)


@m.query("ad_conversions", resource="mkt.ad_control", title="Chuyển đổi gửi về kênh quảng cáo")
async def ad_conversions(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Mỗi kết quả bán gửi (hoặc định gửi) về kênh: kênh, chiến dịch / quảng cáo, giá trị, trạng thái, lỗi. Không SĐT / email / dấu băm."""
    rows = await ctx.all("SELECT * FROM mkt.ad_conversions ORDER BY created_at DESC LIMIT 300")
    out = [{"id": str(r["id"]), "at": r["occurred_at"], "channel": r["channel"], "campaign": r["campaign_code"], "ad": r["ad_code"], "ref": r["outcome_ref"],
            "value": r["value_vnd"], "state": r["state"], "attempts": r["attempts"], "error": r["error"]} for r in rows]
    on = (await policy(ctx)).conversions
    return ui.table(out, [ui.col("at", "Lúc có kết quả", "datetime"), ui.col("channel", "Kênh"), ui.col("campaign", "Chiến dịch"), ui.col("ad", "Quảng cáo"),
                          ui.col("ref", "Mã kết quả"), ui.col("value", "Giá trị", "money"),
                          ui.col("state", "Trạng thái", "badge", labels=CONV_STATES, tones={"sent": "green", "failed": "red", "queued": "blue"}),
                          ui.col("attempts", "Lần gửi", "number"), ui.col("error", "Lỗi", width=260)],
                    title="Chuyển đổi gửi về kênh quảng cáo", limit=300,
                    empty="Chưa có chuyển đổi nào" + ("" if on else " — công ty đang TẮT gửi chuyển đổi (Chính sách quảng cáo)"),
                    note="Chỉ dấu băm SHA-256 của SĐT / email đi ra kênh, lấy ngay lúc gửi, không lưu ở đây.")


_bind = {"channel": "channel", "level": "level", "ref": "ref", "finding_id": "id"}
m.screens("marketing", (
    Screen("ad_findings", "Đề xuất quảng cáo", "wand.and.stars", "mkt.ad_findings", tint="pink", row_actions=(
        A("mkt.set_ad_status", "Tạm dừng trên kênh", bind=_bind, preset={"status": "paused"}, when={"proposal": ["pause"]}, style="primary"),
        A("mkt.set_ad_budget", "Giảm ngân sách ngày", bind=_bind, prefill={"daily_budget": "proposed_budget"}, when={"proposal": ["cut"]}, style="primary"),
        A("mkt.record_ad_manual", "Đã làm tay", bind=_bind, prefill={"action": "manual_action", "daily_budget": "proposed_budget"},
          when={"has_object": [True], "state": ["open"]}),
        A("mkt.dismiss_ad_finding", "Bỏ qua", bind={"finding_id": "id"}, when={"state": ["open"]}))),
    Screen("ad_changes", "Sổ thay đổi quảng cáo", "list.bullet.rectangle", "mkt.ad_changes", tint="gray", feature=USE_ADS),
    Screen("ad_conversions", "Chuyển đổi gửi về kênh", "arrow.up.forward.square", "mkt.ad_conversions", tint="gray", feature=USE_ADS),
    Screen("ad_policy", "Chính sách quảng cáo", "slider.horizontal.3", "mkt.ad_policy_page", tint="gray"),
), rank=40)
