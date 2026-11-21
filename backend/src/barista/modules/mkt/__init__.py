"""MARKETING — chiến dịch, chi phí theo ngày (nhập tay / dán từ file xuất của nền tảng quảng cáo), hiệu quả: lead, lead
chất lượng, giao dịch, chi phí / lead (CPL), chi phí / giao dịch.

Số lead lấy từ CRM qua ``crm.lead_stats`` (nội bộ, chỉ trả số đếm) — marketing không có và không cần quyền xem khách.
``leads_platform`` = số nền tảng báo, để đối chiếu với số lead thật vào CRM (lệch nhiều = form rác / trùng / sale chưa nhập).
"""

import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal
from uuid import UUID

from barista.api import Action as A
from barista.api import (
    Ctx,
    Day,
    Dim,
    Hints,
    ImportKey,
    Input,
    Invalid,
    Measure,
    Module,
    Money,
    NoInput,
    NotFound,
    Pack,
    Percent,
    Question,
    Ratio,
    Role,
    Screen,
    SearchIn,
    SearchTerms,
    Workspace,
    dong,
    field,
    fold,
    today,
    ui,
)

# Gói 'marketing' tự khai (0069) — kernel không biết tên gói; vai / đội / mẫu mặc định xem presets, màn & "cần chú ý" khai ngay dưới.
Pack("marketing", "Marketing",
     "Chiến dịch theo [[offering|l]], chi phí quảng cáo, hiệu quả theo nguồn lead",
     roles={
         "marketer": Role("Marketing", ai_tier="manager",
                          tools=("mkt.overview", "mkt.campaigns", "mkt.spend", "mkt.ds_spend", "mkt.ad_findings", "mkt.ads", "mkt.creatives", "mkt.record_spend",
                                 "mkt.cost_shares", "mkt.cost_share_statement", "mkt.export_campaigns", "kb.documents", "flow.tasks"),   # 0218
                          home=("marketing", ("overview", "campaigns", "spend", "axis", "kb")),
                          picker=("Chức năng", "Chiến dịch, chi phí, số lead — không thấy khách cụ thể.", "Chiến dịch nào đang đốt tiền mà chưa ra lead?"),
                          # rà vai 18/11/2026 (4 / 5 người marketing thấy 0 chiến dịch với "team" — phạm vi team = đội mình LÀM TRƯỞNG): marketing thấy chiến
                          # dịch + điều khiển quảng cáo của các ĐỘI MÌNH THAM GIA ("unit", 0125 §A) — cả phòng marketing; người làm trưởng phòng có vai
                          # "Trưởng phòng marketing" (cả công ty). Công ty đang chạy: đổi ở ma trận quyền (cài mặc định không ghi đè dòng đã có)
                          grants=(("mkt", "unit", "read write"), ("mkt.ad_control", "unit", "read write"),
                                  ("kb.common", "all", "read write"), ("papers", "own", "read write"),   # ấn phẩm + bàn giao thuê ngoài (0074 C)
                                  ("work", "own", "read write"), ("flow", "team", "read write"),
                                  ("talk", "own", "read write"), ("training", "own", "read write"),),
                          suggest=(("mkt.campaigns", "Chiến dịch nào có chi phí trên mỗi lead cao nhất?"),
                                   ("mkt.spend", "Hôm qua và tuần này đã chi bao nhiêu cho quảng cáo, chiến dịch nào chi nhiều nhất?"),
                                   ("mkt.campaigns", "Ngân sách còn lại của các chiến dịch đang chạy?"))),
         # TRƯỞNG PHÒNG MARKETING (chủ sản phẩm 14/11/2026): đọc HẾT chi phí + lead của cả công ty để đánh giá kênh / chiến dịch; lead CHỈ ĐỌC (giao,
         # chuyển, chăm sóc lead là việc của kinh doanh); không sửa chính sách quảng cáo (ngưỡng — quản trị), không quản lý đồng chi trả (chính sách
         # tiền — giám đốc). Công ty có thể có nhiều người vai này; việc của chính phòng mình (tài liệu, quy trình, trao đổi) theo đội mình quản lý.
         "marketing_head": Role("Trưởng phòng marketing", ai_tier="manager",
                                # 0218: bộ của marketing + sổ điều khiển quảng cáo cả công ty + lead (chỉ đọc) để trả lời "lead đến từ đâu"
                                tools=("mkt.overview", "mkt.campaigns", "mkt.spend", "mkt.ds_spend", "mkt.ad_findings", "mkt.ads", "mkt.creatives",
                                       "mkt.record_spend", "mkt.cost_shares", "mkt.cost_share_statement", "mkt.export_campaigns", "kb.documents",
                                       "flow.tasks", "mkt.ad_changes", "mkt.ad_conversions", "mkt.set_ad_status", "mkt.set_ad_budget",
                                       "mkt.dismiss_ad_finding", "crm.leads"),
                                home=("marketing", ("overview", "campaigns", "spend", "axis", "kb")),
                                picker=("Quản lý", "Mọi chiến dịch, chi phí và lead của cả công ty (lead chỉ đọc) — không sửa quyền, không duyệt chi.",
                                        "Kênh nào ra lead chất lượng rẻ nhất tháng này?"),
                                grants=(("mkt", "all", "read write"), ("mkt.ad_control", "all", "read write"), ("mkt.ad_policy", "all", "read"),
                                        ("crm", "all", "read"), ("leadflow", "all", "read"), ("core.ai_usage", "team", "read"),
                                        ("kb.common", "all", "read write"), ("papers", "team", "read write"), ("work", "team", "read write"),
                                        ("flow", "team", "read write"), ("talk", "team", "read write"), ("training", "team", "read write"),),
                                suggest=(("mkt.campaigns", "Chiến dịch nào hiệu quả nhất, chiến dịch nào đang đốt tiền?"),
                                         ("mkt.spend", "Tháng này đã chi bao nhiêu cho quảng cáo, theo kênh?"),
                                         ("crm.leads", "Lead tuần này đến từ nguồn nào nhiều nhất?"))),
     },
     questions=(Question("grant:marketer:mkt", "Nhân viên marketing thấy chiến dịch và chi phí tới đâu?",
                         (("own", "Chỉ chiến dịch mình phụ trách"), ("unit", "Chiến dịch của các đội mình tham gia (cả phòng marketing)"),
                          ("team", "Chiến dịch của đội mình làm trưởng và đội con"), ("all", "Mọi chiến dịch của công ty")),
                         "unit", "Marketing làm theo phòng: thấy chiến dịch của đồng nghiệp cùng phòng để không chạy trùng; trưởng phòng có vai riêng",
                         "giám đốc", "admin.set_grant", topic="Vai & quyền"),),
     tools={"director": ("mkt.overview",), "coordinator": ("mkt.overview", "mkt.campaigns")},   # 0218
     hints={"coordinator": Hints(suggest=(("mkt.overview", "Chiến dịch nào đang ra nhiều lead nhất tuần này?"),),
                                 showcase=(("Chiến dịch nào ra nhiều lead nhất tuần này, lead của chiến dịch đó đã được gọi chưa?", ("mkt.overview", "crm.leads")),)),
            "director": Hints(suggest=(("mkt.campaigns", "Chiến dịch nào hiệu quả nhất, chiến dịch nào đang đốt tiền?"),)),
            "*": Hints(suggest=(("mkt.campaigns", "Chiến dịch nào có chi phí trên mỗi lead cao nhất?"),))},
     # director quản lý đồng chi trả (0196: tỷ lệ góp chi phí của đội / người là chính sách tiền — không phải việc nhập liệu của marketing)
     grants=(("coordinator", "mkt", "all", "read"), ("admin", "mkt", "all", "read"), ("director", "mkt", "all", "read manage"),
             ("accountant", "mkt", "all", "read"),
             # điều khiển quảng cáo trên kênh (0209): đề xuất / thay đổi / sổ — chính sách (ngưỡng, gửi chuyển đổi về kênh) chỉ quản trị sửa
             ("admin", "mkt.ad_control", "all", "read write manage"), ("admin", "mkt.ad_policy", "all", "read write manage"),
             ("director", "mkt.ad_control", "all", "read write"), ("director", "mkt.ad_policy", "all", "read"),),
     # thay đổi quảng cáo VƯỢT NGƯỠNG chính sách (mkt.ad_policy) đi qua chuỗi này; lệnh tự thu hẹp bằng ``sensitive`` — dưới ngưỡng chạy thẳng (0209)
     chains=(("mkt_ad_change", "Thay đổi quảng cáo vượt ngưỡng", ({"kind": "role", "role": "director"},)),),
     approval_rules=(("mkt.set_ad_budget", "mkt_ad_change"), ("mkt.set_ad_status", "mkt_ad_change")),
     rules="""- ĐIỀU KHIỂN QUẢNG CÁO (mkt.ad_*): chi phí / lead / lead chất lượng / kết quả bán nằm trên THẺ (mkt__ad_findings, mkt__overview) — đọc số trên thẻ,
  không tự cộng. Tạm dừng / bật lại / đổi ngân sách ngày (mkt__set_ad_status, mkt__set_ad_budget) chỉ SOẠN thành thẻ; người bấm mới gọi kênh. "Đã làm tay"
  (mkt__record_ad_manual) là việc người tự khai — trợ lý không khai thay. Phát hiện "tiêu tiền chưa ra kết quả" là gợi ý KIỂM TRA, không đổ lỗi cho sale.
""",
     kind="capability",   # năng lực dùng chung (0124): chỉ cần Khách hàng; số kết quả bán / phí chỉ hiện khi có gói cắm vào ``crm.lead_outcome``
     requires=("crm",))

m = Module("mkt", "Marketing", pack="marketing", resources={"mkt": "Chiến dịch & chi phí quảng cáo",
                                                           "mkt.ad_control": "Điều khiển quảng cáo trên kênh (đề xuất, thay đổi, chuyển đổi)",
                                                           "mkt.ad_policy": "Chính sách quảng cáo (ngưỡng theo dõi / duyệt, gửi chuyển đổi về kênh)"})
m.consumes("plug")   # điểm cắm của chính module (0209): mkt.ad_objects / ad_apply / ad_convert — gói kênh quảng cáo cắm, hỏi bằng quyền hệ thống

CHANNELS = {"facebook": "Facebook", "google": "Google", "tiktok": "TikTok", "zalo": "Zalo", "event": "Sự kiện",
            "telesales": "Telesales", "other": "Khác"}
STATUS = {"draft": "Nháp", "running": "Đang chạy", "paused": "Tạm dừng", "ended": "Đã kết thúc"}


DETAIL = "Thêm chi tiết"   # nhóm gấp của form: ô máy đoán được / hiếm dùng nằm sau nút này (ADR 0225)


class UpsertCampaign(Input):
    # rà form nhóm B 18/11/2026: mã do máy đặt từ tên (nhập file / nền tảng vẫn mang mã gốc); 5 ô chính, còn lại gấp lại
    name: str = field("Tên", example="FB lead form tháng 11", max_length=160)
    channel: Literal["facebook", "google", "tiktok", "zalo", "event", "telesales", "other"] = field("Kênh", "facebook", options=CHANNELS)
    offering_code: str = field("[[offering]]", "", options_query="crm.offering_options", example="HSR")
    budget_vnd: Money = field("Ngân sách", 0, ge=0, example="150tr")
    status: Literal["draft", "running", "paused", "ended"] = field("Trạng thái", "running", options=STATUS)
    code: str = field("Mã chiến dịch", "", example="FB-T11", max_length=30, placeholder="Máy đặt từ tên", group=DETAIL,
                      help="Trống = máy đặt từ tên (chiến dịch trùng tên đã có ⇒ sửa chiến dịch đó). Nhập mã để sửa đúng một chiến dịch đã có")
    start_on: Day | None = field("Bắt đầu", None, example="01/11/2026", placeholder="Hôm nay", group=DETAIL)
    end_on: Day | None = field("Kết thúc", None, example="30/11/2026", placeholder="Chưa định", group=DETAIL)
    owner_email: str = field("Người chạy", "", options_query="admin.member_options", placeholder="Chính tôi", group=DETAIL,
                             help="Trống = chính bạn (chiến dịch mới) / giữ người đang chạy (chiến dịch đã có)")
    plan_code: str = field("Mã kế hoạch", "", max_length=40, example="KH-HSR-T11", group=DETAIL,
                           help="Nhóm các chiến dịch cùng một kế hoạch (người chạy, [[offering|l]], đội). Trống = giữ mã đang có")
    original_name: str = field("Tên gốc", "", max_length=160, group=DETAIL,
                               help="Tên lần đầu chiến dịch xuất hiện (nạp từ nền tảng đã đổi tên). Trống = tên hiện tại. Đã có thì không đổi")


class CampaignPickIn(Input):
    offering_code: str = field("[[offering]]", "", help="Có ⇒ chỉ chiến dịch của [[offering|l]] này")
    q: str = field("Tìm chiến dịch", "", max_length=60)


@m.query("campaign_options", resource="mkt", title="Chọn chiến dịch", agent="deny")
async def campaign_options(ctx: Ctx, inp: CampaignPickIn) -> list[dict[str, str]]:
    """Ô "Chiến dịch" của lead / chi phí / quảng cáo (ADR 0225 — chọn, không gõ mã): chiến dịch người xem thấy (RLS), đang chạy trước; lọc theo
    [[offering|l]] nếu có. Danh sách lớn mà chưa có chữ tìm ⇒ rỗng (ô gõ), có chữ tìm ⇒ tối đa 30 dòng khớp."""
    rows = await ctx.all("""SELECT code, name, status FROM mkt.campaigns WHERE (%(o)s = '' OR offering_code = upper(%(o)s))
                            ORDER BY (status = 'running') DESC, start_on DESC, code LIMIT 3000""", {"o": inp.offering_code.strip()})
    items = [{"value": r["code"], "label": f"{r['code']} — {r['name']}"} for r in rows]
    needle = fold(inp.q.strip())
    return [x for x in items if needle in fold(x["label"])][:30] if needle else (items if len(items) <= 300 else [])


class AdPickIn(Input):
    campaign_code: str = field("Chiến dịch", "", help="Có ⇒ chỉ quảng cáo của chiến dịch này")
    q: str = field("Tìm quảng cáo", "", max_length=60)


@m.query("ad_options", resource="mkt", title="Chọn quảng cáo", agent="deny")
async def ad_options(ctx: Ctx, inp: AdPickIn) -> list[dict[str, str]]:
    """Ô "Mẫu quảng cáo" (ADR 0225): quảng cáo của chiến dịch đã chọn, hoặc mọi quảng cáo người xem thấy khi có chữ tìm. Danh sách lớn không chữ tìm ⇒ rỗng."""
    rows = await ctx.all("""SELECT a.code, a.name FROM mkt.ads a JOIN mkt.campaigns c ON c.id = a.campaign_id
                            WHERE (%(c)s = '' OR c.code = upper(%(c)s)) ORDER BY a.updated_at DESC LIMIT 3000""", {"c": inp.campaign_code.strip()})
    items = [{"value": r["code"], "label": f"{r['name'] or r['code']} ({r['code']})"} for r in rows]
    needle = fold(inp.q.strip())
    return [x for x in items if needle in fold(x["label"])][:30] if needle else (items if len(items) <= 300 else [])


PLAN = re.compile(r"[A-Za-z0-9_-]{0,40}")


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


async def _member(ctx: Ctx, email: str) -> UUID:
    """Người do người gọi nêu (0125): tra bằng QUYỀN NGƯỜI GỌI; ghi chiến dịch / đồng chi trả cho người đó vẫn qua RLS của người gọi (ngoài phạm vi ⇒ bị chặn)."""
    uid = await ctx.val("SELECT u.id FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant()"
                        " WHERE u.email = %s AND mb.active", (email.strip().lower(),))
    if uid is None:
        raise NotFound(f"Không có người {email} trong công ty")
    return uid


async def _campaigns_existing(ctx: Ctx, keys: list[tuple[str, ...]]) -> dict[tuple[str, ...], dict[str, Any]]:
    """Nhập Excel nhận ra chiến dịch đã có theo mã (0226): giá trị hiện tại để file chỉ điền ô trống và giữ trạng thái / kênh đang chạy."""
    rows = await ctx.all("""SELECT code, name, channel, offering_code, budget_vnd, start_on, end_on, status, plan_code FROM mkt.campaigns WHERE upper(code) = ANY(%s)""",
                         ([k[0].upper() for k in keys],))
    return {(r["code"].lower(),): r for r in rows}


@m.command("upsert_campaign",
           purpose="Thêm hoặc sửa chiến dịch để chi phí và lead được tính đúng chiến dịch, đúng ngân sách và người chạy.", reversible="Chạy lại lệnh với giá trị cũ (tên, ngân sách, trạng thái)",
           resource="mkt", title="Thêm / sửa chiến dịch", importable=True, import_key=ImportKey(("code",), _campaigns_existing, update="self"))
async def upsert_campaign(ctx: Ctx, inp: UpsertCampaign) -> dict[str, Any]:
    """Tên chiến dịch đổi được (nền tảng cho đổi + tái dùng); TÊN GỐC giữ tên lần đầu hệ biết — sửa tên sau này không ghi đè (0195). Người chạy nêu
    bằng email; không nêu thì chiến dịch mới thuộc người tạo, chiến dịch đã có giữ người đang chạy."""
    if not PLAN.fullmatch(inp.plan_code):
        raise Invalid("Mã kế hoạch: chữ, số, gạch ngang, gạch dưới (tối đa 40 ký tự)")
    offering = await ctx.call_system("crm.offering_by_code", code=inp.offering_code) if inp.offering_code else None
    code = inp.code.strip().upper()
    if not code:   # máy đặt mã từ tên (0225): chiến dịch trùng tên ⇒ sửa chiến dịch đó
        code = await ctx.val("SELECT code FROM mkt.campaigns WHERE core.vn_fold(btrim(name)) = core.vn_fold(btrim(%s)) LIMIT 1", (inp.name,)) or \
            code_from_name(inp.name, {r["code"].upper() for r in await ctx.all("SELECT code FROM mkt.campaigns")}, 30)
    owner = await _member(ctx, inp.owner_email) if inp.owner_email else None
    team = await ctx.home_team(owner or ctx.p.user_id)
    cid = await ctx.val(
        """INSERT INTO mkt.campaigns AS c (code, name, channel, offering_id, offering_code, owner_id, team_id, budget_vnd, start_on, end_on, status,
                                           original_name, plan_code)
           VALUES (%(code)s, %(name)s, %(channel)s, %(oid)s, %(ocode)s, %(owner)s, %(team)s, %(budget)s, %(start)s, %(end)s, %(status)s,
                   coalesce(nullif(%(orig)s, ''), %(name)s), %(plan)s)
           ON CONFLICT (tenant_id, code) DO UPDATE SET name = EXCLUDED.name, channel = EXCLUDED.channel,
               offering_id = EXCLUDED.offering_id, offering_code = EXCLUDED.offering_code, budget_vnd = EXCLUDED.budget_vnd,
               start_on = EXCLUDED.start_on, end_on = EXCLUDED.end_on, status = EXCLUDED.status,
               original_name = coalesce(c.original_name, nullif(%(orig)s, ''), c.name),
               plan_code = CASE WHEN EXCLUDED.plan_code <> '' THEN EXCLUDED.plan_code ELSE c.plan_code END,
               owner_id = CASE WHEN %(set_owner)s THEN EXCLUDED.owner_id ELSE c.owner_id END,
               team_id = CASE WHEN %(set_owner)s THEN EXCLUDED.team_id ELSE c.team_id END
           RETURNING id""",
        {"code": code, "name": inp.name, "channel": inp.channel, "oid": offering and offering["id"],
         "ocode": offering["code"] if offering else "", "owner": owner or ctx.p.user_id, "team": team, "budget": inp.budget_vnd,
         "start": inp.start_on or today(), "end": inp.end_on, "status": inp.status, "orig": inp.original_name.strip(), "plan": inp.plan_code,
         "set_owner": owner is not None})
    return {"campaign_id": cid, "code": code, "subject": {"label": f"{inp.name} ({code})", "open": None}}


class Spend(Input):
    # rà form nhóm B 18/11/2026: 3 ô chính (chiến dịch chọn, ngày, chi phí); số nền tảng báo và tiền gốc / tỷ giá gấp lại; tiền gốc hiện là ô số (không phải ô chữ)
    campaign_code: str = field("Chiến dịch", example="FB-T11", aliases=("Mã chiến dịch",), options_query="mkt.campaign_options")
    day: Day = field("Ngày", example="05/11/2026")
    amount_vnd: Money = field("Chi phí", ge=0, example="4.200.000",
                              help="VND. Tài khoản quảng cáo tính ngoại tệ: VND quy đổi, làm tròn MỘT lần cho dòng chiến dịch × ngày")
    leads_platform: int = field("Lead nền tảng báo", 0, ge=0, example=12, group="Số nền tảng báo")
    impressions: int | None = field("Lượt hiển thị", None, ge=0, example=48210, help="Trống = nền tảng không báo (khác 0)", group="Số nền tảng báo")
    clicks: int | None = field("Lượt nhấp", None, ge=0, example=731, help="Trống = nền tảng không báo (khác 0)", group="Số nền tảng báo")
    amount_native: Decimal | None = field("Chi phí theo tiền gốc", None, ge=0, example="159.81", kind="number", group="Tiền gốc & tỷ giá",
                                          help="Số tiền nền tảng báo theo tiền của tài khoản quảng cáo (vd USD có xu)")
    currency_native: str = field("Tiền gốc", "", max_length=3, example="USD", placeholder="USD", group="Tiền gốc & tỷ giá")
    fx_rate: Decimal | None = field("Tỷ giá", None, gt=0, example="26280", kind="number", group="Tiền gốc & tỷ giá",
                                    help="1 đơn vị tiền gốc = ? VND, đã dùng để quy đổi")
    fx_source: str = field("Nguồn tỷ giá", "", max_length=120, example="Tỷ giá hệ thống quảng cáo ngày 05/11/2026", group="Tiền gốc & tỷ giá")


@m.command("record_spend",
           purpose="Ghi chi phí quảng cáo của một ngày để chi phí mỗi lead và báo cáo marketing đúng số nền tảng đã tính.", reversible="Nhập lại số đúng cho cùng chiến dịch và ngày (ghi đè)",
           resource="mkt", title="Nhập chi phí ngày", importable=True)
async def record_spend(ctx: Ctx, inp: Spend) -> dict[str, Any]:
    """Một dòng / chiến dịch / ngày — nhập lại cùng ngày thì GHI ĐÈ (file xuất của nền tảng hay cập nhật số trễ). Tiền gốc + tỷ giá giữ lại để đối
    soát tuyệt đối với nền tảng (0195); số đo lượt hiển thị / nhấp trống nghĩa là "không đo", không phải 0. Ngày đã có số theo từng quảng cáo (0198) thì
    dòng này là tổng của chúng — nhập theo quảng cáo, không ghi đè tay."""
    ccy = _native(inp.amount_native, inp.currency_native, inp.fx_rate)
    cid = await ctx.val("SELECT id FROM mkt.campaigns WHERE code = %s", (inp.campaign_code.upper(),))
    if cid is None:
        raise NotFound(f"Không có chiến dịch {inp.campaign_code}")
    if await ctx.val("SELECT 1 FROM mkt.ad_days d JOIN mkt.ads a ON a.id = d.ad_id WHERE a.campaign_id = %s AND d.day = %s LIMIT 1", (cid, inp.day)):
        raise Invalid("Ngày này đã có số theo từng quảng cáo — chi phí chiến dịch là tổng của chúng, nhập theo quảng cáo (Nhập số quảng cáo theo ngày)")
    before = await ctx.val("SELECT amount_vnd FROM mkt.spend WHERE campaign_id = %s AND day = %s", (cid, inp.day))   # ghi đè ⇒ người duyệt thấy số cũ
    await ctx.exec(
        """INSERT INTO mkt.spend (campaign_id, day, amount_vnd, leads_platform, impressions, clicks, amount_native, currency_native, fx_rate, fx_source)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (campaign_id, day) DO UPDATE SET amount_vnd = EXCLUDED.amount_vnd, leads_platform = EXCLUDED.leads_platform,
               impressions = EXCLUDED.impressions, clicks = EXCLUDED.clicks, amount_native = EXCLUDED.amount_native,
               currency_native = EXCLUDED.currency_native, fx_rate = EXCLUDED.fx_rate, fx_source = EXCLUDED.fx_source""",
        (cid, inp.day, inp.amount_vnd, inp.leads_platform, inp.impressions, inp.clicks, inp.amount_native, ccy or None, inp.fx_rate,
         inp.fx_source.strip()))
    return {"campaign_code": inp.campaign_code.upper(), "day": inp.day,
            "subject": {"label": f"Chiến dịch {inp.campaign_code.upper()} · {inp.day:%d/%m/%Y}", "open": None},
            "changes": [{"label": "Chi phí ngày", "before": dong(before) if before is not None else "", "after": dong(inp.amount_vnd)}]}


def _native(amount: Decimal | None, currency: str, fx: Decimal | None) -> str:
    ccy = currency.strip().upper()
    if (amount is None) != (not ccy):
        raise Invalid("Chi phí theo tiền gốc và tiền gốc phải đi cùng nhau")
    if ccy and not re.fullmatch(r"[A-Z]{3}", ccy):
        raise Invalid("Tiền gốc: mã 3 chữ (USD, VND…)")
    if fx is not None and amount is None:
        raise Invalid("Có tỷ giá thì cần chi phí theo tiền gốc")
    return ccy


# ── QUẢNG CÁO (0198): tầng dưới chiến dịch — nội dung + số theo ngày ──

AD_CODE = re.compile(r"[A-Za-z0-9_-]{1,40}")
MEDIA = {"": "—", "image": "Ảnh", "video": "Video", "carousel": "Nhiều ảnh", "other": "Khác"}


class UpsertAd(Input):
    # rà form nhóm B 18/11/2026: chiến dịch chọn; nội dung quảng cáo và nhóm quảng cáo (thứ nền tảng trả về) gấp lại
    campaign_code: str = field("Chiến dịch", example="FB-T11", options_query="mkt.campaign_options")
    code: str = field("Mã quảng cáo (nền tảng)", max_length=40, example="120212345678901234")
    name: str = field("Tên quảng cáo", "", max_length=300)
    adset_code: str = field("Mã nhóm quảng cáo", "", max_length=40, group="Nhóm quảng cáo")
    adset_name: str = field("Tên nhóm quảng cáo", "", max_length=300, group="Nhóm quảng cáo")
    title: str = field("Tiêu đề", "", max_length=500, group="Nội dung & liên kết")
    body: str = field("Nội dung", "", kind="textarea", max_length=8000, group="Nội dung & liên kết")
    media_type: Literal["", "image", "video", "carousel", "other"] = field("Loại", "", options=MEDIA, group="Nội dung & liên kết")
    thumbnail_url: str = field("Ảnh", "", max_length=2000, group="Nội dung & liên kết")
    video_url: str = field("Video", "", max_length=2000, group="Nội dung & liên kết")
    link_url: str = field("Đường dẫn đích", "", max_length=2000, group="Nội dung & liên kết")


@m.command("upsert_ad", resource="mkt", title="Thêm / sửa quảng cáo", importable=True, category="setup")
async def upsert_ad(ctx: Ctx, inp: UpsertAd) -> dict[str, Any]:
    """Một quảng cáo trên nền tảng thuộc MỘT chiến dịch (mã nền tảng là khoá). Nạp lại cùng mã ⇒ cập nhật tên / nội dung (nền tảng cho sửa)."""
    if not AD_CODE.fullmatch(inp.code) or (inp.adset_code and not AD_CODE.fullmatch(inp.adset_code)):
        raise Invalid("Mã quảng cáo / nhóm: chữ, số, gạch ngang, gạch dưới (tối đa 40 ký tự)")
    cid = await ctx.val("SELECT id FROM mkt.campaigns WHERE code = %s", (inp.campaign_code.upper(),))
    if cid is None:
        raise NotFound(f"Không có chiến dịch {inp.campaign_code}")
    aid = await ctx.val(
        """INSERT INTO mkt.ads (campaign_id, code, name, adset_code, adset_name, title, body, media_type, thumbnail_url, video_url, link_url)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (tenant_id, code) DO UPDATE SET campaign_id = EXCLUDED.campaign_id, name = EXCLUDED.name, adset_code = EXCLUDED.adset_code,
               adset_name = EXCLUDED.adset_name, title = EXCLUDED.title, body = EXCLUDED.body, media_type = EXCLUDED.media_type,
               thumbnail_url = EXCLUDED.thumbnail_url, video_url = EXCLUDED.video_url, link_url = EXCLUDED.link_url, updated_at = now()
           RETURNING id""",
        (cid, inp.code, inp.name.strip(), inp.adset_code, inp.adset_name.strip(), inp.title.strip(), inp.body.strip(), inp.media_type,
         inp.thumbnail_url.strip(), inp.video_url.strip(), inp.link_url.strip()))
    return {"ad_id": aid, "code": inp.code}


class AdDay(Input):
    # rà form nhóm B 18/11/2026: tiền là ô SỐ (trước đây ô chữ); số nền tảng báo và tiền gốc / tỷ giá gấp lại
    ad_code: str = field("Mã quảng cáo", max_length=40, options_query="mkt.ad_options")
    day: Day = field("Ngày", example="05/11/2026")
    amount_vnd: Decimal = field("Chi phí (VND, chưa làm tròn)", Decimal(0), ge=0, example="2618812.80", kind="number",
                                help="Giữ số lẻ khi quy đổi ngoại tệ — dòng chiến dịch × ngày làm tròn MỘT lần ở tổng")
    impressions: int | None = field("Lượt hiển thị", None, ge=0, group="Số nền tảng báo")
    clicks: int | None = field("Lượt nhấp", None, ge=0, group="Số nền tảng báo")
    leads_platform: int | None = field("Lead form (nền tảng báo)", None, ge=0, help="Trống = nền tảng chưa báo (khác 0)", group="Số nền tảng báo")
    inbox: int | None = field("Inbox (cuộc nhắn mới)", None, ge=0, help="Trống = nền tảng chưa báo (khác 0)", group="Số nền tảng báo")
    amount_native: Decimal | None = field("Chi phí theo tiền gốc", None, ge=0, kind="number", group="Tiền gốc & tỷ giá")
    currency_native: str = field("Tiền gốc", "", max_length=3, placeholder="USD", group="Tiền gốc & tỷ giá")
    fx_rate: Decimal | None = field("Tỷ giá", None, gt=0, kind="number", group="Tiền gốc & tỷ giá")
    fx_source: str = field("Nguồn tỷ giá", "", max_length=120, group="Tiền gốc & tỷ giá")


@m.command("record_ad_day", resource="mkt", title="Nhập số quảng cáo theo ngày", importable=True)
async def record_ad_day(ctx: Ctx, inp: AdDay) -> dict[str, Any]:
    """Số một quảng cáo trong một ngày (nhập lại = ghi đè). Trong CÙNG giao dịch dựng lại dòng chiến dịch × ngày = tổng các quảng cáo: VND làm tròn MỘT lần
    ở tổng (không lệch vì làm tròn từng dòng); hiển thị / nhấp / inbox chỉ có tổng khi mọi quảng cáo của ngày đều có số; tiền gốc chỉ cộng khi cùng một loại tiền."""
    ccy = _native(inp.amount_native, inp.currency_native, inp.fx_rate)
    ad = await ctx.one("SELECT id, campaign_id FROM mkt.ads WHERE code = %s", (inp.ad_code,))
    if ad is None:
        raise NotFound(f"Không có quảng cáo {inp.ad_code} — thêm quảng cáo trước")
    await ctx.exec(
        """INSERT INTO mkt.ad_days (ad_id, day, amount_vnd, amount_native, currency_native, fx_rate, impressions, clicks, leads_platform, inbox)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (ad_id, day) DO UPDATE SET amount_vnd = EXCLUDED.amount_vnd, amount_native = EXCLUDED.amount_native,
               currency_native = EXCLUDED.currency_native, fx_rate = EXCLUDED.fx_rate, impressions = EXCLUDED.impressions, clicks = EXCLUDED.clicks,
               leads_platform = EXCLUDED.leads_platform, inbox = EXCLUDED.inbox""",
        (ad["id"], inp.day, inp.amount_vnd, inp.amount_native, ccy or None, inp.fx_rate, inp.impressions, inp.clicks, inp.leads_platform, inp.inbox))
    await ctx.exec(
        """INSERT INTO mkt.spend AS s (campaign_id, day, amount_vnd, leads_platform, impressions, clicks, amount_native, currency_native, fx_rate, fx_source, inbox)
           SELECT a.campaign_id, d.day, round(sum(d.amount_vnd))::bigint, coalesce(sum(d.leads_platform), 0)::int,
                  CASE WHEN count(d.impressions) = count(*) THEN sum(d.impressions) END, CASE WHEN count(d.clicks) = count(*) THEN sum(d.clicks) END,
                  CASE WHEN count(d.amount_native) = count(*) AND count(DISTINCT d.currency_native) = 1 THEN sum(d.amount_native) END,
                  CASE WHEN count(d.amount_native) = count(*) AND count(DISTINCT d.currency_native) = 1 THEN max(d.currency_native) END,
                  CASE WHEN count(d.fx_rate) = count(*) AND count(DISTINCT d.fx_rate) = 1 THEN max(d.fx_rate) END, %(src)s,
                  CASE WHEN count(d.inbox) = count(*) THEN sum(d.inbox)::int END
           FROM mkt.ad_days d JOIN mkt.ads a ON a.id = d.ad_id WHERE a.campaign_id = %(c)s AND d.day = %(d)s GROUP BY a.campaign_id, d.day
           ON CONFLICT (campaign_id, day) DO UPDATE SET amount_vnd = EXCLUDED.amount_vnd, leads_platform = EXCLUDED.leads_platform,
               impressions = EXCLUDED.impressions, clicks = EXCLUDED.clicks, amount_native = EXCLUDED.amount_native,
               currency_native = EXCLUDED.currency_native, fx_rate = EXCLUDED.fx_rate,
               fx_source = CASE WHEN EXCLUDED.fx_source <> '' THEN EXCLUDED.fx_source ELSE s.fx_source END, inbox = EXCLUDED.inbox""",
        {"c": ad["campaign_id"], "d": inp.day, "src": inp.fx_source.strip()})
    return {"ad_code": inp.ad_code, "day": inp.day}


class AdsFilter(Input):
    days: int = field("Số ngày gần nhất", 30, ge=1, le=366)
    campaign_code: str = field("Chiến dịch", "", max_length=30, options_query="mkt.campaign_options")


@m.query("ads", resource="mkt", title="Quảng cáo")
async def ads(ctx: Ctx, inp: AdsFilter) -> dict[str, Any]:
    """Từng quảng cáo: nội dung + số trong kỳ. Lead form / inbox là số NỀN TẢNG báo (đối chiếu), không phải lead vào CRM."""
    latest = await latest_spend_day(ctx, ads=True)
    rows = await ctx.all(
        """SELECT a.id, a.code, a.name, a.adset_name, a.title, a.media_type, a.thumbnail_url, c.code AS campaign_code, c.name AS campaign,
                  round(coalesce(sum(d.amount_vnd), 0))::bigint AS spend, count(d.id) AS spend_days, count(d.impressions) AS measured_days,
                  sum(d.impressions)::bigint AS impressions, sum(d.clicks)::bigint AS clicks,
                  CASE WHEN count(d.leads_platform) = count(d.id) THEN sum(d.leads_platform)::int END AS leads_platform,
                  CASE WHEN count(d.inbox) = count(d.id) THEN sum(d.inbox)::int END AS inbox
           FROM mkt.ads a JOIN mkt.campaigns c ON c.id = a.campaign_id
           JOIN mkt.ad_days d ON d.ad_id = a.id AND d.day > core.today() - %(n)s
           WHERE (%(c)s = '' OR c.code = upper(%(c)s)) GROUP BY a.id, c.code, c.name ORDER BY spend DESC""",
        {"n": inp.days, "c": inp.campaign_code})
    for r in rows:
        r.update(delivery(r["spend"], r))
        r["cost_per_form"] = _ratio(r["spend"], r["leads_platform"] or 0)
    return ui.table(rows, [
        ui.col("name", "Quảng cáo", width=220), ui.col("title", "Tiêu đề", width=220), ui.col("media_type", "Loại", "badge", labels=MEDIA),
        ui.col("campaign", "Chiến dịch", width=200), ui.col("adset_name", "Nhóm"), ui.col("spend", "Đã chi", "money"),
        ui.col("impressions", "Lượt hiển thị", "number"), ui.col("clicks", "Lượt nhấp", "number"), ui.col("ctr", "CTR %", "number"),
        ui.col("cpc", "Chi phí / nhấp", "money"), ui.col("leads_platform", "Lead form (nền tảng)", "number"), ui.col("inbox", "Inbox", "number"),
        ui.col("cost_per_form", "Chi phí / lead form", "money"),
    ], title="Quảng cáo", empty=(_spend_empty(latest, inp.days, "Số ngày gần nhất") if latest else
                                 "Chưa có số theo từng quảng cáo — nối nền tảng quảng cáo hoặc bấm “Nhập số quảng cáo theo ngày”."),
       kpis=[ui.kpi("Đã chi", sum(r["spend"] for r in rows), "money"), ui.kpi("Số quảng cáo có chi", sum(1 for r in rows if r["spend"]))],
       note="Lead form / inbox là số nền tảng quảng cáo báo để đối chiếu — lead tính hiệu quả là lead vào CRM (Hiệu quả quảng cáo).")


# ── XUẤT DỮ LIỆU TRẦN cho ứng dụng nối vào (0140 / 0198): một kỳ ≤ 92 ngày, cả công ty ──

class ExportIn(Input):
    date_from: Day = field("Từ ngày", example="01/11/2026")
    date_to: Day = field("Đến ngày", example="12/11/2026")


def _export_window(inp: ExportIn) -> tuple[date, date]:
    if inp.date_to < inp.date_from or (inp.date_to - inp.date_from).days > 91:
        raise Invalid("Kỳ xuất: Đến ngày ≥ Từ ngày, tối đa 92 ngày")
    return inp.date_from, inp.date_to


@m.query("export_ad_days", resource="mkt", title="Xuất số quảng cáo theo ngày", min_scope="all")
async def export_ad_days(ctx: Ctx, inp: ExportIn) -> list[dict[str, Any]]:
    """Dòng trần quảng cáo × ngày (không phải thẻ) cho ứng dụng nối vào đọc thay nguồn cũ. Tiền VND chưa làm tròn ở dòng — người đọc cộng rồi làm tròn MỘT lần."""
    f, t = _export_window(inp)
    rows = await ctx.all(
        """SELECT d.day, c.code AS campaign_code, c.name AS campaign_name, c.original_name AS campaign_original_name, c.plan_code,
                  c.offering_code, a.adset_code, a.adset_name, a.code AS ad_code, a.name AS ad_name,
                  d.amount_vnd, d.amount_native, d.currency_native, d.fx_rate, d.impressions, d.clicks, d.leads_platform, d.inbox
           FROM mkt.ad_days d JOIN mkt.ads a ON a.id = d.ad_id JOIN mkt.campaigns c ON c.id = a.campaign_id
           WHERE d.day BETWEEN %s AND %s ORDER BY d.day, c.code, a.code""", (f, t))
    for r in rows:
        for k in ("amount_vnd", "amount_native", "fx_rate"):
            r[k] = str(r[k]) if r[k] is not None else None   # chuỗi số thập phân: không mất chữ số qua JSON
        r["day"] = r["day"].isoformat()
    return rows


@m.query("export_campaigns", resource="mkt", title="Xuất chiến dịch", min_scope="all")
async def export_campaigns(ctx: Ctx, inp: NoInput) -> list[dict[str, Any]]:
    """Dòng trần chiến dịch: mã, tên hiện tại / tên gốc, kế hoạch, [[offering|l]], người chạy (email), trạng thái, ngân sách (``budget_vnd`` — thêm 14/11/2026:
    bên đồng bộ từ nền tảng gọi lại ``upsert_campaign`` phải mang đủ trường đang có, không thì ngân sách về 0)."""
    rows = await ctx.all(
        """SELECT c.code, c.name, c.original_name, c.plan_code, c.channel, c.offering_code, u.email AS owner_email, c.status, c.start_on, c.end_on,
                  c.budget_vnd
           FROM mkt.campaigns c JOIN core.users u ON u.id = c.owner_id ORDER BY c.code""")
    for r in rows:
        r["start_on"] = r["start_on"].isoformat() if r["start_on"] else None
        r["end_on"] = r["end_on"].isoformat() if r["end_on"] else None
    return rows


@m.search("search_campaigns", "Chiến dịch", resource="mkt", rank=58)
async def search_campaigns(ctx: Ctx, inp: SearchIn) -> list[dict[str, Any]]:
    """⌘K (0087): mã / tên chiến dịch (cả tên cũ trước khi đổi), mã kế hoạch — trong phạm vi người tìm xem được (RLS: người chạy / đội / cả phòng).
    Đúng mã lên trước, rồi chiến dịch đang chạy. Mở quảng cáo của chiến dịch (cả năm gần nhất)."""
    t = SearchTerms(inp.q)
    rows = await ctx.all(
        f"""SELECT c.code, c.name, c.channel, c.status, c.start_on FROM mkt.campaigns c
            WHERE {t.match("c.code", "c.name", "c.original_name", "c.plan_code")}
            ORDER BY {t.rank("c.code", "c.name")}, (c.status = 'running') DESC, c.start_on DESC LIMIT %(_n)s""", t.params)
    return [{"title": f"{r['code']} — {r['name']}", "subtitle": " · ".join(x for x in (CHANNELS.get(r["channel"], r["channel"]), f"từ {r['start_on']:%d/%m/%Y}" if r["start_on"] else "") if x),
             "badge": STATUS.get(r["status"], r["status"]), "tone": "accent" if r["status"] == "running" else "muted",
             "open": {"query": "mkt.ads", "input": {"campaign_code": r["code"], "days": 366}}} for r in rows]


class CampaignFilter(Input):
    status: str = field("Trạng thái", "", options={"": "Tất cả", **STATUS})


def _ratio(a: int, b: int) -> int | None:
    return round(a / b) if b else None


# Số giao nhận (0195): chỉ tính khi MỌI ngày có chi phí trong phạm vi đều có số đo — thiếu một ngày thì tổng lượt nhấp thấp giả ⇒ CPC cao giả. Trống ≠ 0.
DELIVERY_SQL = """count(s.id) AS spend_days, count(s.impressions) AS measured_days,
                  sum(s.impressions)::bigint AS impressions, sum(s.clicks)::bigint AS clicks"""


def delivery(spend: int, r: dict[str, Any], *, hide: bool = False) -> dict[str, Any]:
    """CTR (%), CPC, CPM của một dòng đã cộng ``DELIVERY_SQL``. Hàm thuần."""
    full = bool(r["spend_days"]) and r["measured_days"] == r["spend_days"] and not hide
    imp, clk = (r["impressions"] or 0, r["clicks"] or 0) if full else (0, 0)
    return {"impressions": r["impressions"] if full else None, "clicks": r["clicks"] if full else None,
            "ctr": round(100 * clk / imp, 2) if imp else None, "cpc": _ratio(spend, clk), "cpm": _ratio(spend * 1000, imp)}


@m.query("campaigns", resource="mkt", title="Chiến dịch")
async def campaigns(ctx: Ctx, inp: CampaignFilter) -> dict[str, Any]:
    rows = await ctx.all(
        f"""SELECT c.id, c.code, c.name, c.original_name, c.plan_code, c.channel, c.offering_code, c.budget_vnd, c.start_on, c.end_on, c.status,
                  u.name AS owner, coalesce(sum(s.amount_vnd), 0)::bigint AS spent, coalesce(sum(s.leads_platform), 0)::int AS leads_platform,
                  {DELIVERY_SQL}
           FROM mkt.campaigns c JOIN core.users u ON u.id = c.owner_id LEFT JOIN mkt.spend s ON s.campaign_id = c.id
           WHERE (%(s)s = '' OR c.status = %(s)s) GROUP BY c.id, u.name ORDER BY c.start_on DESC""", {"s": inp.status})
    stats = await ctx.call_system("crm.lead_stats", campaign_ids=[r["id"] for r in rows]) if rows else {}
    for r in rows:
        st = stats.get(str(r["id"]), {"leads": 0, "qualified": 0, "deals": 0})
        r.update(st)
        r.update(delivery(r["spent"], r))
        r["renamed"] = r["original_name"] if r["original_name"] and r["original_name"] != r["name"] else None
        r["cpl"] = _ratio(r["spent"], st["leads"])
        r["cpql"] = _ratio(r["spent"], st["qualified"])
        r["cpd"] = _ratio(r["spent"], st["deals"])
        r["budget_used"] = round(r["spent"] / r["budget_vnd"] * 100, 1) if r["budget_vnd"] else None
    spent = sum(r["spent"] for r in rows)
    leads = sum(r["leads"] for r in rows)
    deals = sum(r["deals"] for r in rows)
    measured = any(r["measured_days"] for r in rows)   # không nhập số hiển thị / nhấp ⇒ không có cột CTR / CPC / CPM (không đo ≠ 0)
    return ui.table(rows, [
        ui.col("code", "Mã"), ui.col("name", "Chiến dịch", width=220), ui.col("renamed", "Tên gốc (đã đổi tên)", width=200),
        ui.col("plan_code", "Kế hoạch"), ui.col("owner", "Người chạy"), ui.col("channel", "Kênh", "badge", labels=CHANNELS),
        ui.col("offering_code", "[[offering]]"), ui.col("status", "Trạng thái", "badge", labels=STATUS,
                                                tones={"running": "green", "paused": "orange", "ended": "gray", "draft": "blue"}),
        ui.col("budget_vnd", "Ngân sách", "money"), ui.col("spent", "Đã chi", "money"), ui.col("budget_used", "% ngân sách", "percent"),
        *((ui.col("impressions", "Lượt hiển thị", "number"), ui.col("clicks", "Lượt nhấp", "number"), ui.col("ctr", "CTR %", "number"),
           ui.col("cpc", "Chi phí / nhấp", "money"), ui.col("cpm", "Chi phí / 1.000 hiển thị", "money")) if measured else ()),
        ui.col("leads_platform", "Lead nền tảng", "number"), ui.col("leads", "Lead vào CRM", "number"),
        ui.col("qualified", "Lead chất lượng", "number"), ui.col("deals", "Giao dịch", "number"),
        ui.col("cpl", "Chi phí / lead", "money"), ui.col("cpql", "Chi phí / lead CL", "money"), ui.col("cpd", "Chi phí / GD", "money"),
    ], title="Chiến dịch", empty=scope_empty_note(ctx) if not inp.status else "Không có chiến dịch ở trạng thái này", kpis=[
        ui.kpi("Đã chi", spent, "money", hint="cộng dồn từ khi chạy"), ui.kpi("Lead vào CRM", leads), ui.kpi("Chi phí / lead", _ratio(spent, leads), "money"),
        ui.kpi("Giao dịch", deals, tone="green"), ui.kpi("Chi phí / giao dịch", _ratio(spent, deals), "money"),
    ], note="Lead chất lượng = đã lên Tiềm năng hoặc Giao dịch. Số đếm theo khách đi vào CRM từ chiến dịch, không theo dòng form.")


class SpendFilter(Input):
    days: int = field("Số ngày gần nhất", 30, ge=1, le=366)


def _spend_empty(latest: date | None, days: int, knob: str) -> str:
    """Câu cho bảng chi phí trống: chưa từng có chi phí / chi phí dừng trước kỳ (nói ngày, và cần mở bao nhiêu ngày để thấy)."""
    if latest is None:
        return "Chưa có chi phí nào trong phạm vi bạn xem — bấm “Nhập chi phí ngày” hoặc nối nền tảng quảng cáo."
    need = (today() - latest).days + 1
    return f"{days} ngày qua chưa có chi phí. Chi phí nhập gần nhất là ngày {latest:%d/%m/%Y} — đặt “{knob}” từ {need} để xem."


@m.query("spend", resource="mkt", title="Chi phí theo ngày")
async def spend(ctx: Ctx, inp: SpendFilter) -> dict[str, Any]:
    latest = await latest_spend_day(ctx)
    sql = """SELECT s.id, s.day, c.code, c.name, c.channel, s.amount_vnd, s.leads_platform, s.impressions, s.clicks,
                    s.amount_native, s.currency_native, s.fx_rate
             FROM mkt.spend s JOIN mkt.campaigns c ON c.id = s.campaign_id
             WHERE s.day > %s ORDER BY s.day DESC, c.code"""
    rows = await ctx.all(sql, (today() - timedelta(days=inp.days),))
    stale = False
    if not rows and latest is not None:   # chi phí dừng nhập (cũ hơn kỳ): hiện ``days`` ngày liền trước ngày nhập gần nhất thay vì màn trống (cùng cách kỳ "latest" của Hiệu quả quảng cáo)
        rows = await ctx.all(sql, (latest - timedelta(days=inp.days),))
        stale = True
    for r in rows:
        r["native"] = f"{r['amount_native']:,.2f} {r['currency_native']}" if r["amount_native"] is not None and r["currency_native"] != "VND" else None
        r["amount_native"] = float(r["amount_native"]) if r["amount_native"] is not None else None
        r["fx_rate"] = float(r["fx_rate"]) if r["fx_rate"] is not None else None
    return ui.table(rows, [
        ui.col("day", "Ngày", "date"), ui.col("code", "Chiến dịch"), ui.col("name", "Tên", width=220),
        ui.col("channel", "Kênh", "badge", labels=CHANNELS), ui.col("amount_vnd", "Chi phí", "money"), ui.col("native", "Tiền gốc"),
        ui.col("fx_rate", "Tỷ giá", "number"), ui.col("impressions", "Lượt hiển thị", "number"), ui.col("clicks", "Lượt nhấp", "number"),
        ui.col("leads_platform", "Lead nền tảng", "number"),
    ], title="Chi phí theo ngày", kpis=[ui.kpi("Tổng chi", sum(r["amount_vnd"] for r in rows), "money",
                                              hint=f"{inp.days} ngày tới {latest:%d/%m/%Y} (chi phí chưa nhập sau ngày này)" if stale and latest else f"{inp.days} ngày qua"),
                                       *((ui.kpi("Chi phí nhập gần nhất", f"{latest:%d/%m/%Y}", "text"),) if latest else ())],
       empty=_spend_empty(latest, inp.days, "Số ngày gần nhất"))


# ── Hợp đồng nội bộ ──

class CodeInput(Input):
    code: str


class CampaignLookup(Input):
    code: str
    ad_code: str = ""


@m.plug("crm.campaign", "Tra chiến dịch theo mã cho Khách hàng")
async def plug_campaign(ctx: Ctx, inp: CampaignLookup) -> dict[str, Any] | None:
    """Gắn lead với chiến dịch theo mã người nhập gõ — người nhập chưa chắc có quyền đọc Marketing. Trả {id, plan_code, ad_id} (mã kế hoạch để chia lead
    theo kế hoạch — 0204; mẫu quảng cáo của CHÍNH chiến dịch đó nếu nguồn gửi mã quảng cáo — 0210, mã lạ ⇒ None, lead vẫn vào), không ngân sách / chi phí."""
    row = await ctx.one("SELECT id, plan_code FROM mkt.campaigns WHERE code = %s", (inp.code.upper(),))
    if row is None:
        return None
    ad = await ctx.val("SELECT id FROM mkt.ads WHERE code = %s AND campaign_id = %s", (inp.ad_code.strip(), row["id"])) if inp.ad_code.strip() else None
    return {**row, "ad_id": ad}


class CampaignIdsIn(Input):
    campaign_ids: list[UUID]


@m.query("campaign_owners", resource="mkt", title="Người phụ trách chiến dịch", internal=True, agent="deny", callers=("leadflow",),
         why="Vòng quét chia lead (quyền hệ thống, không có người gọi) báo người phụ trách CHIẾN DỊCH khi lead của chiến dịch bị thu hồi (0214): "
             "campaign_ids là chiến dịch gắn trên lead vừa thu hồi (crm.contact_info). Trả mỗi chiến dịch {owner_id, name} — không ngân sách, không chi phí."
             " [id: module gọi tự có — campaign_ids lấy từ crm.contact_info của vòng quét hệ thống, không do người dùng gõ]")
async def campaign_owners(ctx: Ctx, inp: CampaignIdsIn) -> dict[str, dict[str, Any]]:
    rows = await ctx.all("SELECT id, owner_id, coalesce(nullif(name, ''), code) AS name FROM mkt.campaigns WHERE id = ANY(%s)", (inp.campaign_ids,))
    return {str(r["id"]): {"owner_id": r["owner_id"], "name": r["name"]} for r in rows}


class BudgetInput(Input):
    months: list[str]  # "MM/YYYY"


@m.query("budget_plan", resource="mkt", title="Ngân sách còn lại theo tháng", internal=True)
async def budget_plan(ctx: Ctx, inp: BudgetInput) -> dict[str, int]:
    """Cho dự phóng dòng tiền: ngân sách còn lại của chiến dịch đang chạy, chia đều theo số ngày còn lại tới ngày kết thúc
    (không có ngày kết thúc ⇒ coi như 90 ngày)."""
    t = today()
    out: dict[str, int] = {}
    for c in await ctx.all(
            """SELECT c.budget_vnd - coalesce(sum(s.amount_vnd), 0) AS rest, c.end_on FROM mkt.campaigns c
               LEFT JOIN mkt.spend s ON s.campaign_id = c.id WHERE c.status = 'running' GROUP BY c.id"""):
        rest = int(c["rest"])
        end: date = c["end_on"] or t + timedelta(days=90)
        days = (end - t).days + 1
        if rest <= 0 or days <= 0:
            continue
        per_day = rest / days
        d = t
        while d <= end:
            key = f"{d.month:02d}/{d.year}"
            if key in inp.months:
                out[key] = out.get(key, 0) + round(per_day)
            d += timedelta(days=1)
    return out


# ── HIỆU QUẢ QUẢNG CÁO (port có chọn lọc từ hợp đồng số liệu chuẩn của Phê Nâu — doc 108) ──
#
# Luật mang sang: (1) số 0 khác "chưa đo được"; (2) CPL = chi phí ÷ lead ĐẾM THEO NGƯỜI vào CRM từ chiến dịch, cùng kỳ; (3) kỳ xem rơi ra ngoài
# mốc dữ liệu tin cậy ⇒ ẨN các tỷ lệ (chi phí / lead, ROAS…), giữ số tuyệt đối; (4) giao dịch / doanh thu là NHÓM LEAD của kỳ theo trạng
# thái hiện tại, không phải "ký trong kỳ"; (5) gợi ý "cắt" bị chặn khi dữ liệu chưa tin cậy, và không bao giờ đổ lỗi cho sale.

# "latest" (mặc định): 30 ngày KẾT THÚC ở ngày gần nhất có chi phí trong phạm vi người xem — chi phí nhập trễ / dừng nhập thì màn không trống, kỳ
# ghi rõ ngày trên tiêu đề phụ và lưu ý nói chi phí dừng ở ngày nào. Kỳ theo lịch (7 / 30 ngày qua, tháng) vẫn chọn được.
PERIODS = {"latest": "30 ngày tới ngày có chi phí gần nhất", "last_7": "7 ngày qua", "last_30": "30 ngày qua", "this_month": "Tháng này",
           "last_month": "Tháng trước"}
PeriodKey = Literal["latest", "last_7", "last_30", "this_month", "last_month"]
CPL_MATURITY_DAYS = 3  # đuôi kỳ trễ hơn "chi phí đã nhập tới" quá ngần này ngày ⇒ chưa đủ
STALE_SPEND_DAYS = 2   # chi phí mới nhất cũ hơn ngần này ngày ⇒ báo "chi phí mới nhập tới ngày …" (cùng ngưỡng Sức khoẻ dữ liệu: chiến dịch trễ > 2 ngày)


def window(period: str, latest: date | None = None) -> tuple[date, date]:
    """Kỳ theo khoá. ``latest`` = ngày gần nhất có chi phí (chỉ kỳ "latest" dùng; chưa có chi phí nào ⇒ tính tới hôm nay)."""
    t = today()
    if period == "latest":
        end = min(latest, t) if latest else t
        return end - timedelta(days=29), end
    if period == "last_7":
        return t - timedelta(days=6), t
    if period == "last_30":
        return t - timedelta(days=29), t
    first = t.replace(day=1)
    if period == "this_month":
        return first, t
    last_end = first - timedelta(days=1)
    return last_end.replace(day=1), last_end


class OverviewIn(Input):
    period: PeriodKey = field("Kỳ", "latest", options=PERIODS)


async def latest_spend_day(ctx: Ctx, *, ads: bool = False) -> date | None:
    """Ngày gần nhất có chi phí (theo chiến dịch, hoặc theo từng quảng cáo khi ``ads``) trong PHẠM VI người xem (RLS) — để màn nói "chi phí mới nhập
    tới ngày …" thay vì trống im lặng."""
    if ads:
        return await ctx.val("SELECT max(day) FROM mkt.ad_days")
    return await ctx.val("SELECT max(day) FROM mkt.spend")


def stale_note(latest: date | None, d_from: date) -> str | None:
    """Câu báo khi chi phí dừng nhập: kỳ đang xem có thể trống / thiếu đuôi vì CHƯA NHẬP, không phải vì không chi."""
    if latest is None or (lag := (today() - latest).days) <= STALE_SPEND_DAYS:
        return None
    if latest < d_from:
        return (f"Chi phí quảng cáo mới nhập tới ngày {latest:%d/%m/%Y} ({lag} ngày trước) — kỳ đang xem chưa có chi phí nào. "
                f"Chọn Kỳ “{PERIODS['latest']}” để xem số gần nhất.")
    return f"Chi phí quảng cáo mới nhập tới ngày {latest:%d/%m/%Y} ({lag} ngày trước) — các ngày sau đó là chưa nhập, chưa phải không chi."


def scope_empty_note(ctx: Ctx) -> str:
    """Người xem không thấy chiến dịch nào: nói vì sao theo PHẠM VI quyền của chính họ (ma trận quyền công ty — không viết cứng vai)."""
    return {"own": "Bạn chưa thấy chiến dịch nào: theo quyền hiện tại, màn này chỉ hiện chiến dịch bạn là người chạy.",
            "unit": "Bạn chưa thấy chiến dịch nào: theo quyền hiện tại, màn này hiện chiến dịch bạn chạy hoặc của đội bạn tham gia.",
            "team": "Bạn chưa thấy chiến dịch nào: theo quyền hiện tại, màn này hiện chiến dịch bạn là người chạy hoặc của đội bạn làm trưởng.",
            }.get(ctx.p.scope("mkt"), "Công ty chưa có chiến dịch nào.") + (
        "" if ctx.p.scope("mkt") == "all" else " Muốn xem chiến dịch của cả phòng: nhờ quản trị giao chiến dịch (ô Người chạy) hoặc mở rộng phạm vi quyền Marketing.")


class CoverageIn(Input):
    spend_reliable_from: Day | None = field("Chi phí nhập đủ từ ngày", None, help="Trước ngày này chi phí chưa đủ ⇒ ẩn chi phí / lead")
    lead_reliable_from: Day | None = field("Lead vào CRM đủ từ ngày", None)
    spend_complete_through: Day | None = field("Chi phí đã nhập tới hết ngày", None, help="Cập nhật mỗi khi nhập thêm chi phí")


@m.command("set_coverage", resource="mkt", title="Khai mốc dữ liệu tin cậy", description="Đặt ngày chi phí / lead bắt đầu đủ và ngày chi phí đã "
           "nhập tới — để số chi phí / lead không bị hiểu sai khi dữ liệu chưa đủ. Trợ lý chỉ soạn đề xuất.", category="setup")
async def set_coverage(ctx: Ctx, inp: CoverageIn) -> dict[str, Any]:
    await ctx.exec(
        """INSERT INTO mkt.coverage (spend_reliable_from, lead_reliable_from, spend_complete_through) VALUES (%s, %s, %s)
           ON CONFLICT (tenant_id) DO UPDATE SET spend_reliable_from = EXCLUDED.spend_reliable_from, lead_reliable_from = EXCLUDED.lead_reliable_from,
                                                 spend_complete_through = EXCLUDED.spend_complete_through, updated_at = now()""",
        (inp.spend_reliable_from, inp.lead_reliable_from, inp.spend_complete_through))
    return {"ok": True}


async def _period_stats(ctx: Ctx, d_from: date, d_to: date) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Mỗi chiến dịch có chi phí hoặc lead trong kỳ + tổng. Số tiền / đếm; tỷ lệ tính sau (có thể bị ẩn)."""
    camps = await ctx.all(
        """SELECT c.id, c.code, c.name, c.channel, coalesce(sum(s.amount_vnd), 0)::bigint AS spend, count(s.id) AS spend_days,
                  count(s.impressions) AS measured_days, sum(s.impressions)::bigint AS impressions, sum(s.clicks)::bigint AS clicks
           FROM mkt.campaigns c LEFT JOIN mkt.spend s ON s.campaign_id = c.id AND s.day BETWEEN %(f)s AND %(t)s GROUP BY c.id""",
        {"f": d_from, "t": d_to})
    stats = await ctx.call_system("crm.lead_stats", campaign_ids=[c["id"] for c in camps], date_from=d_from, date_to=d_to) if camps else {}
    zero = {"leads": 0, "qualified": 0, "deals": 0, "open_leads": 0, "outcome_count": 0, "outcome_value_vnd": 0}
    rows = []
    for c in camps:
        c.update(stats.get(str(c["id"]), zero))
        if c["spend"] or c["leads"]:
            rows.append(c)
    tot = {k: sum(r[k] for r in rows) for k in ("spend", "leads", "qualified", "deals", "open_leads", "outcome_count", "outcome_value_vnd",
                                                "spend_days", "measured_days")}
    tot["impressions"] = sum(r["impressions"] or 0 for r in rows)
    tot["clicks"] = sum(r["clicks"] or 0 for r in rows)
    return rows, tot


def _pct(cur: int, prev: int) -> float | None:
    return round(100 * (cur - prev) / prev, 1) if prev else None


async def _reliability(ctx: Ctx, d_from: date, d_to: date) -> tuple[bool, list[str]]:
    """(kỳ CHƯA tin cậy?, lưu ý) theo mốc dữ liệu công ty khai (``mkt.coverage``) — một định nghĩa cho màn Hiệu quả quảng cáo và ``ad_results``."""
    cov = await ctx.one("SELECT spend_reliable_from, lead_reliable_from, spend_complete_through FROM mkt.coverage")
    notes: list[str] = []
    unsafe = False
    if cov is None or not any(cov.values()):
        notes.append("Chưa khai mốc dữ liệu tin cậy — số chi phí / lead chưa được kiểm độ đủ của dữ liệu (khai bằng nút “Khai mốc dữ liệu tin cậy” trên màn Hiệu quả quảng cáo).")
    else:
        start = max(x for x in (cov["spend_reliable_from"], cov["lead_reliable_from"]) if x) if (cov["spend_reliable_from"] or cov["lead_reliable_from"]) else None
        if start and d_from < start:
            unsafe = True
            notes.append(f"Kỳ xem bắt đầu trước {start:%d/%m/%Y} — dữ liệu chi phí / lead chưa đủ từ ngày đó, nên chi phí / lead và ROAS bị ẩn (số tuyệt đối vẫn hiện).")
        thr = cov["spend_complete_through"]
        if thr and (d_to - thr).days > CPL_MATURITY_DAYS:
            unsafe = True
            notes.append(f"Chi phí mới nhập tới {thr:%d/%m} — đuôi kỳ còn thiếu, chi phí / lead bị ẩn để khỏi thấp giả.")
    return unsafe, notes


async def _outcome_for_viewer(ctx: Ctx) -> tuple[dict[str, Any] | None, str | None, bool]:
    """Tên gọi hai con số kết quả bán (gói cắm ``crm.lead_outcome``) + người XEM có được thấy con số GIÁ TRỊ không. Gói ghi kết quả bán khai tài nguyên quyền
    của giá trị (``value_resource``, vd tiền doanh thu của công ty): người xem không có quyền đọc tài nguyên đó ⇒ thấy số giao dịch nhưng không thấy giá
    trị, ROAS — ai thấy là dữ liệu của ma trận quyền công ty, không viết cứng vai nào ở đây.

    Giá trị ở đây là số GỘP của cả chiến dịch (crm cộng bằng quyền hệ thống, gồm giao dịch của mọi người) ⇒ cần quyền đọc tài nguyên đó với phạm vi
    "all". Phạm vi own / unit / team chỉ cho thấy giá trị của MỘT PHẦN — mở số gộp cho người đó là lộ giá trị của người khác (rà 0239 F1, Codex 17/11/2026).
    Muốn cho người phạm vi hẹp thấy giá trị thì phải lọc theo người xem TRƯỚC khi cộng (cả tử số lẫn mẫu số ROAS) — chưa làm.

    Lớp 3 (1.59): trả (tên gọi, ``needs`` của phần giá trị = ``<tài nguyên>:all``, người xem có thấy không). Ô số / cột / mục gợi ý của giá trị khai
    ``needs=`` đó — cửa ra của executor bỏ phần đó (kể cả số trong dòng) cho người thiếu quyền; cờ thứ ba chỉ để chọn câu chữ."""
    info = await ctx.call_system("crm.outcome_info")
    need = ui.need((info or {}).get("value_resource"), "all")
    return info, need, bool(info) and ctx.sees(need)


class ResultsIn(Input):
    date_from: date
    date_to: date


@m.query("ad_results", resource="mkt", title="Kết quả quảng cáo theo kỳ (số gộp)", internal=True, agent="deny", family="company_figures",
         why="Gói khai consumes(\"company_figures\") cần kết quả quảng cáo của MỘT kỳ tuỳ ý để tự quyết (vd tác nhân theo dõi của gói kết nối nền tảng "
             "quảng cáo — chạy trong vòng quét bằng quyền hệ thống, chụp số lên thẻ đề xuất; màn của gói đọc lại bản chụp khai min_scope=\"all\"). Trả SỐ GỘP "
             "{reliable, notes, outcome, campaigns: [{code, name, status, channel, plan_code, offering_code, spend, spend_days, leads, qualified, open_leads, "
             "outcome_count, outcome_value_vnd, with_ad}], ads: [{code, name, campaign_code, adset_code, spend, leads_platform, leads, qualified, open_leads, "
             "outcome_count, outcome_value_vnd}]} — cùng định nghĩa với màn Hiệu quả quảng cáo / Mẫu quảng cáo ra khách (lead theo quảng cáo = lead biết mẫu, "
             "``with_ad`` của chiến dịch là độ phủ); không tên khách, không SĐT. Kỳ ≤ 92 ngày.")
async def ad_results(ctx: Ctx, inp: ResultsIn) -> dict[str, Any]:
    """Số của ``overview`` cho một kỳ bất kỳ (≤ 92 ngày): chiến dịch có chi phí hoặc lead trong kỳ (lead đếm theo ngày khách để lại thông tin; kết quả bán
    là NHÓM LEAD của kỳ theo trạng thái hiện tại — gói ghi kết quả bán cắm ``crm.lead_outcome``; không gói nào ⇒ ``outcome`` None = không đo được, không phải 0)
    + từng quảng cáo có chi trong kỳ (lead form là số NỀN TẢNG báo, trống = chưa báo). ``reliable`` False ⇒ bên gọi không được kết luận "cắt" (luật 5 ở trên)."""
    if inp.date_to < inp.date_from or (inp.date_to - inp.date_from).days > 91:
        raise Invalid("Kỳ: Đến ngày ≥ Từ ngày, tối đa 92 ngày")
    rows, _ = await _period_stats(ctx, inp.date_from, inp.date_to)
    meta = {r["id"]: r for r in await ctx.all("SELECT id, plan_code, offering_code, status FROM mkt.campaigns WHERE id = ANY(%s)",
                                                ([r["id"] for r in rows],))}
    unsafe, notes = await _reliability(ctx, inp.date_from, inp.date_to)
    ads = await ctx.all(
        """SELECT a.id, a.code, coalesce(nullif(a.name, ''), a.code) AS name, c.code AS campaign_code, a.adset_code,
                  round(coalesce(sum(d.amount_vnd), 0))::bigint AS spend,
                  CASE WHEN count(d.leads_platform) = count(d.id) THEN sum(d.leads_platform)::int END AS leads_platform
           FROM mkt.ad_days d JOIN mkt.ads a ON a.id = d.ad_id JOIN mkt.campaigns c ON c.id = a.campaign_id
           WHERE d.day BETWEEN %s AND %s GROUP BY a.id, c.code ORDER BY a.code""", (inp.date_from, inp.date_to))
    # lead theo QUẢNG CÁO = lead biết mẫu (crm.leads.ad_id, 0208) — cùng nguồn với Mẫu quảng cáo ra khách
    by_ad = await ctx.call_system("crm.lead_stats_by_ad", ad_ids=[a["id"] for a in ads], date_from=inp.date_from, date_to=inp.date_to) if ads else {}
    counts = ("leads", "qualified", "open_leads", "outcome_count", "outcome_value_vnd")
    return {"reliable": not unsafe, "notes": notes, "outcome": await ctx.call_system("crm.outcome_info"),
            "campaigns": [{"code": r["code"], "name": r["name"], "status": meta[r["id"]]["status"], "channel": r["channel"],
                           "plan_code": meta[r["id"]]["plan_code"], "offering_code": meta[r["id"]]["offering_code"],
                           **{k: int(r[k]) for k in ("spend", "spend_days", *counts)}, "with_ad": int(r.get("with_ad") or 0)}
                          for r in rows],
            "ads": [{**{k: v for k, v in a.items() if k != "id"}, **{k: int((by_ad.get(str(a["id"])) or {}).get(k, 0)) for k in counts}} for a in ads]}


@m.query("overview", resource="mkt", title="Hiệu quả quảng cáo")
async def overview(ctx: Ctx, inp: OverviewIn) -> dict[str, Any]:
    """Chi phí, lead, chi phí / lead của kỳ — và, khi công ty có gói ghi kết quả bán (giao dịch ký, phí…), kết quả đó + ROAS — so với kỳ liền trước cùng
    độ dài, kèm gợi ý và LƯU Ý về độ tin cậy của số. Số tỷ lệ tự ẩn khi kỳ xem nằm ngoài mốc dữ liệu tin cậy."""
    latest = await latest_spend_day(ctx)
    d_from, d_to = window(inp.period, latest)
    span = (d_to - d_from).days + 1
    p_to = d_from - timedelta(days=1)
    p_from = p_to - timedelta(days=span - 1)
    rows, cur = await _period_stats(ctx, d_from, d_to)
    _, prev = await _period_stats(ctx, p_from, p_to)
    unsafe, notes = await _reliability(ctx, d_from, d_to)
    if stale := stale_note(latest, d_from):
        notes.insert(0, stale)
    if not rows and not await ctx.val("SELECT count(*) FROM mkt.campaigns"):
        notes.insert(0, scope_empty_note(ctx))
    # thứ tự đọc (rà vai 18/11/2026): ô số → điều CẦN BIẾT của kỳ này (chi phí dừng nhập, dữ liệu chưa đủ…) → gợi ý → bảng; câu định nghĩa cố định
    # ("lead đếm theo khách…") xuống cuối, không chắn giữa ô số và bảng
    how = ["Lead đếm theo KHÁCH vào CRM từ chiến dịch (không theo dòng form), theo ngày khách để lại thông tin. Không chia lead cho lượt nhấp."]
    info, value_needs, see_value = await _outcome_for_viewer(ctx)   # gói ghi kết quả bán nói tên gọi hai con số; không có ⇒ số 0 là "không đo được", không hiện như "không bán được"
    if info:
        what = f"{info['count']} và {info['value'].lower()}" if see_value else info["count"]
        how.append(f"{what} là NHÓM LEAD của kỳ, xem theo trạng thái hiện tại — chưa phải “phát sinh trong kỳ”; nhóm còn đang chăm thì còn thấp.")
    if cur["leads"] and cur["open_leads"] / cur["leads"] >= 0.5:
        notes.append(f"{cur['open_leads']}/{cur['leads']} lead của kỳ còn đang chăm — chưa đủ chín để kết luận chiến dịch kém.")

    def ratio(a: int, b: int) -> int | None:
        return None if unsafe or not b else round(a / b)

    def roas(fee: int, spend: int) -> float | None:
        return None if unsafe or not spend or not fee else round(fee / spend, 2)

    def delta(key: str, cur_v: int, prev_v: int) -> str:
        d = _pct(cur_v, prev_v)
        return f"so kỳ trước {d:+.1f}%" if d is not None else ("kỳ trước chưa có số" if not prev_v else "")

    cpl = ratio(cur["spend"], cur["leads"])
    dl = delivery(cur["spend"], cur, hide=unsafe)
    kpis = [
        ui.kpi("Đã chi", cur["spend"], "money", hint=delta("spend", cur["spend"], prev["spend"])),
        ui.kpi("Lead vào CRM", cur["leads"], hint=delta("leads", cur["leads"], prev["leads"])),
        ui.kpi("Chi phí / lead", cpl, "money", hint="Ẩn: dữ liệu chưa đủ" if unsafe else ""),
        *((ui.kpi("Chi phí / nhấp", dl["cpc"], "money",
                  hint=f"CTR {dl['ctr']}%" if dl["ctr"] is not None else ("Ẩn: dữ liệu chưa đủ" if unsafe else "Chưa có số lượt nhấp đủ cả kỳ")),)
          if cur["measured_days"] else ()),   # công ty không nhập số giao nhận: không có ô này (không đo ≠ 0)
        *((ui.kpi(info["count"], cur["outcome_count"], tone="green" if cur["outcome_count"] else None),) if info else ()),
        *((ui.kpi(info["value"], cur["outcome_value_vnd"], "money", needs=value_needs),
           ui.kpi("ROAS", roas(cur["outcome_value_vnd"], cur["spend"]), "number", hint=info["value_hint"], needs=value_needs)) if info else ()),
    ]
    table_rows = []
    for r in rows:
        table_rows.append({"id": str(r["id"]), "code": r["code"], "name": r["name"], "channel": r["channel"], "spend": r["spend"], "leads": r["leads"],
                           **{k: v for k, v in delivery(r["spend"], r, hide=unsafe).items() if k in ("ctr", "cpc")},
                           "qualified": r["qualified"], "outcome": r["outcome_count"], "outcome_value": r["outcome_value_vnd"],
                           "cpl": ratio(r["spend"], r["leads"]), "cpql": ratio(r["spend"], r["qualified"]),
                           "cost_per_outcome": ratio(r["spend"], r["outcome_count"]), "roas": roas(r["outcome_value_vnd"], r["spend"])})
        # giá trị / ROAS trong dòng: cửa ra bỏ cùng cột (``needs``) — số không xuống máy người không có quyền
    table_rows.sort(key=lambda r: -r["spend"])
    page = ui.table(table_rows, [
        ui.col("code", "Mã"), ui.col("name", "Chiến dịch", width=200), ui.col("channel", "Kênh", "badge", labels=CHANNELS),
        ui.col("spend", "Đã chi", "money"),
        *((ui.col("ctr", "CTR %", "number"), ui.col("cpc", "Chi phí / nhấp", "money")) if cur["measured_days"] else ()),
        ui.col("leads", "Lead", "number"), ui.col("qualified", "Lead chất lượng", "number"),
        *((ui.col("outcome", info["count_short"], "number"),) if info else ()),
        *((ui.col("outcome_value", info["value_short"], "money", needs=value_needs),) if info else ()),
        ui.col("cpl", "Chi phí / lead", "money"), ui.col("cpql", "Chi phí / lead CL", "money"),
        *((ui.col("cost_per_outcome", f"Chi phí / {info['unit']}", "money"),) if info else ()),
        *((ui.col("roas", "ROAS", "number", needs=value_needs),) if info else ()),
    ], title="Theo chiến dịch", empty="Kỳ này chưa có chi phí hay lead")

    blocks: list[dict[str, Any]] = [ui.b_kpis(kpis)]
    if notes:
        blocks.append(ui.b_callout("warn" if unsafe or stale else "muted", "Cần biết trước khi đọc số", facts=notes))
    # Gợi ý: chỉ khi dữ liệu tin cậy (suy từ phenau marketing_report_service)
    recs: list[dict[str, Any]] = []
    if not unsafe:
        if info:   # gợi ý theo ROAS: mục khai ``needs`` của giá trị — chỉ người được thấy giá trị nhận
            for r in sorted((r for r in table_rows if r["roas"]), key=lambda r: -r["roas"])[:2]:
                recs.append(ui.gated({"title": f"Cân nhắc tăng ngân sách: {r['name']}",
                                      "subtitle": f"ROAS {r['roas']} · {r['outcome']} {info['count'].lower()} · chi {r['spend']:,} đ".replace(",", "."), "tone": "ok"},
                                     value_needs))
        for r in table_rows:
            if r["spend"] > 0 and r["leads"] == 0:
                recs.append({"title": f"Chi mà chưa thấy lead vào CRM: {r['name']}", "subtitle": "Kiểm tra trước khi cắt: lead chưa được nhập, form lỗi hay đúng là chưa có.", "tone": "warn"})
            elif info and r["leads"] >= 10 and r["outcome"] == 0:
                recs.append({"title": f"Có lead nhưng chưa có {info['count'].lower()}: {r['name']}", "subtitle": f"{r['leads']} lead; nhóm lead có thể còn đang chăm — xem chất lượng lead trước khi kết luận.", "tone": "muted"})
    if recs:
        blocks.append(ui.b_list(recs, "Gợi ý"))
    blocks.append(ui.b_table(page))
    blocks.append(ui.b_callout("muted", "Cách tính số", facts=how))
    return ui.card("Hiệu quả quảng cáo", blocks, subtitle=f"{PERIODS[inp.period]}: {d_from:%d/%m} – {d_to:%d/%m/%Y} · kỳ trước {p_from:%d/%m} – {p_to:%d/%m}", icon="chart.bar.xaxis",
                   actions=[ui.act("mkt.set_coverage", "Khai mốc dữ liệu tin cậy")] if ctx.p.can("mkt", "write") else [],
                   ask={"title": "Hỏi trợ lý về số này", "prompt": "Về hiệu quả quảng cáo kỳ này: "})


# ── MẪU QUẢNG CÁO RA KHÁCH (0210) ──
#
# Xếp mẫu quảng cáo theo KHÁCH THẬT (lead vào CRM → khách quan tâm trở lên → giao dịch / giá trị khi có gói ghi kết quả bán), không theo "lead form"
# nền tảng báo. Chỉ xếp hạng mẫu ĐỦ MẪU (chi phí + số lead tối thiểu, người xem chỉnh) — tránh "thắng" nhờ ăn may 1 lead. Lead biết mẫu nhờ nguồn gửi mã
# quảng cáo (crm.leads.ad_id); phần không biết mẫu nói rõ trong lưu ý. Cùng luật độ tin cậy với màn Hiệu quả quảng cáo (_reliability).

VERDICT = {"win": "Tốt nhất [[offering|l]]", "ok": "Đủ mẫu", "thin": "Chưa đủ mẫu"}


class CreativesIn(Input):
    period: PeriodKey = field("Kỳ", "latest", options=PERIODS)
    offering_code: str = field("[[offering]]", "", options_query="crm.offering_options", help="Trống = mọi [[offering|l]]")
    campaign_code: str = field("Chiến dịch", "", max_length=30, options_query="mkt.campaign_options",
                               help="Trống = mọi chiến dịch. So trong MỘT chiến dịch (cùng tệp khách, cùng ngân sách) là phép thử mẫu công bằng nhất")
    min_spend_vnd: Money = field("Chi phí tối thiểu để xếp hạng", 200_000, ge=0, example="200.000", group="Điều kiện xếp hạng",
                                 help="Mẫu chi ít hơn mức này chưa đủ để kết luận — vẫn hiện, không xếp hạng")
    min_leads: int = field("Số lead vào CRM tối thiểu", 2, ge=1, le=1000, group="Điều kiện xếp hạng")


@m.query("creatives", resource="mkt", title="Mẫu quảng cáo ra khách")
async def creatives(ctx: Ctx, inp: CreativesIn) -> dict[str, Any]:
    """Mẫu quảng cáo nào ra khách thật: chi phí của mẫu, lead nền tảng báo, lead vào CRM, khách quan tâm trở lên, kết quả bán (khi có gói ghi), chi phí
    cho từng mức. Mẫu đủ mẫu được xếp theo chi phí / khách quan tâm (chưa ai quan tâm ⇒ theo chi phí / lead); mẫu tốt nhất của mỗi [[offering|l]] được
    đánh dấu khi có ít nhất hai mẫu đủ mẫu để so."""
    latest = await latest_spend_day(ctx, ads=True)
    d_from, d_to = window(inp.period, latest)
    ads = await ctx.all(
        """SELECT a.id, a.code, coalesce(nullif(a.name, ''), nullif(a.title, ''), a.code) AS name, c.id AS campaign_id, c.code AS campaign_code,
                  c.name AS campaign_name, c.offering_code, coalesce(sum(d.amount_vnd), 0)::bigint AS spend, CASE WHEN count(d.leads_platform) = count(d.id) THEN sum(d.leads_platform)::int END AS platform_leads
           FROM mkt.ads a JOIN mkt.campaigns c ON c.id = a.campaign_id
           LEFT JOIN mkt.ad_days d ON d.ad_id = a.id AND d.day BETWEEN %(f)s AND %(t)s
           WHERE (%(o)s = '' OR c.offering_code = %(o)s) AND (%(c)s = '' OR c.code = %(c)s)
           GROUP BY a.id, c.id""", {"f": d_from, "t": d_to, "o": inp.offering_code.strip().upper(), "c": inp.campaign_code.strip().upper()})
    stats = await ctx.call_system("crm.lead_stats_by_ad", ad_ids=[a["id"] for a in ads], date_from=d_from, date_to=d_to) if ads else {}
    camp_ids = sorted({a["campaign_id"] for a in ads}, key=str)
    camp = await ctx.call_system("crm.lead_stats", campaign_ids=camp_ids, date_from=d_from, date_to=d_to) if camp_ids else {}
    info, value_needs, see_value = await _outcome_for_viewer(ctx)
    unsafe, notes = await _reliability(ctx, d_from, d_to)
    stale = None
    if not ads:   # công ty chưa có số theo từng quảng cáo — nói cách có số, không để bảng trống im lặng
        notes.insert(0, "Chưa có số theo từng mẫu quảng cáo — cần nối nền tảng quảng cáo hoặc nhập file số quảng cáo theo ngày (màn Quảng cáo). "
                        "Chi phí / lead theo CHIẾN DỊCH xem ở Hiệu quả quảng cáo.")
    elif stale := stale_note(latest, d_from):
        notes.insert(0, stale)
    zero = {"leads": 0, "qualified": 0, "deals": 0, "open_leads": 0, "outcome_count": 0, "outcome_value_vnd": 0}

    def ratio(a: int, b: int) -> int | None:
        return None if unsafe or not b else round(a / b)

    rows: list[dict[str, Any]] = []
    for a in ads:
        st = stats.get(str(a["id"]), zero)
        if not a["spend"] and not st["leads"]:
            continue
        enough = a["spend"] >= inp.min_spend_vnd and st["leads"] >= inp.min_leads
        rows.append({"id": str(a["id"]), "code": a["code"], "name": a["name"], "campaign": a["campaign_name"] or a["campaign_code"],
                     "offering": a["offering_code"], "spend": a["spend"], "platform_leads": a["platform_leads"], "leads": st["leads"],
                     "qualified": st["qualified"], "outcome": st["outcome_count"], "outcome_value": st["outcome_value_vnd"],
                     "cpl": ratio(a["spend"], st["leads"]), "cpql": ratio(a["spend"], st["qualified"]),
                     "cost_per_outcome": ratio(a["spend"], st["outcome_count"]), "verdict": "ok" if enough else "thin"})

    def score(r: dict[str, Any]) -> tuple[int, float]:
        # có khách quan tâm thì xếp theo chi phí / khách quan tâm; chưa có ⇒ đứng sau, xếp theo chi phí / lead
        return (0, r["cpql"]) if r["cpql"] is not None else (1, r["cpl"] if r["cpl"] is not None else float("inf"))

    winners: list[dict[str, Any]] = []
    if not unsafe:
        for off in sorted({r["offering"] for r in rows}):
            ok = sorted((r for r in rows if r["offering"] == off and r["verdict"] == "ok"), key=score)
            if len(ok) >= 2:   # "tốt nhất" chỉ có nghĩa khi có mẫu khác đủ mẫu để so
                ok[0]["verdict"] = "win"
                winners.append(ok[0])
    rows.sort(key=lambda r: ({"win": 0, "ok": 1, "thin": 2}[r["verdict"]], *score(r), -r["spend"]))

    total = sum(c.get("leads", 0) for c in camp.values())
    known = sum(c.get("with_ad", 0) for c in camp.values())
    if total and not known:
        notes.insert(0, "Chưa lead nào của các chiến dịch này biết mẫu quảng cáo — nguồn lead chưa gửi mã quảng cáo, nên cột lead theo mẫu đang trống.")
    elif total and known < total:
        notes.append(f"{known}/{total} lead của các chiến dịch này biết mẫu quảng cáo; số theo mẫu chỉ tính phần biết mẫu.")
    how = [f"Chỉ xếp hạng mẫu chi từ {inp.min_spend_vnd:,} đ và có từ {inp.min_leads} lead vào CRM (đổi ở “Điều kiện xếp hạng”); mẫu chưa đủ vẫn hiện để theo dõi."
           .replace(",", "."),
           "Lead nền tảng là số Meta / kênh báo (lượt điền form) — chênh nhiều với lead vào CRM thì kiểm form rác hoặc lead chưa được nhập."]
    if info:
        how.append(f"{info['count']} và {info['value'].lower() if see_value else ''} là NHÓM LEAD của kỳ, theo trạng thái hiện tại — nhóm còn đang chăm thì còn thấp."
                     .replace(" và  là", " là"))

    kpis = [ui.kpi("Mẫu có chi phí", sum(1 for r in rows if r["spend"])), ui.kpi("Đủ mẫu để so", sum(1 for r in rows if r["verdict"] != "thin")),
            ui.kpi("Lead biết mẫu", known, hint=f"trên {total} lead của các chiến dịch" if total else "")]
    recs = [{"title": f"Mẫu tốt nhất {r['offering'] or 'chưa gắn [[offering|l]]'}: {r['name']}",
             "subtitle": (f"Chi phí / khách quan tâm {r['cpql']:,} đ" if r["cpql"] is not None else f"Chi phí / lead {r['cpl']:,} đ").replace(",", ".")
                         + f" · {r['qualified']} khách quan tâm / {r['leads']} lead — cân nhắc tăng ngân sách hoặc nhân bản", "tone": "ok"} for r in winners]
    if not unsafe:
        recs += [{"title": f"Chi đủ mẫu mà chưa ra lead vào CRM: {r['name']}", "subtitle": "Kiểm form / nguồn trước khi tắt: lead chưa được nhập hay đúng là chưa có.",
                  "tone": "warn"} for r in rows if r["spend"] >= inp.min_spend_vnd and r["leads"] == 0][:5]
    table = ui.table(rows, [
        ui.col("name", "Mẫu quảng cáo", width=220), ui.col("campaign", "Chiến dịch", width=180), ui.col("offering", "[[offering]]"),
        ui.col("verdict", "Đánh giá", "badge", labels=VERDICT), ui.col("spend", "Đã chi", "money"),
        ui.col("platform_leads", "Lead nền tảng", "number"), ui.col("leads", "Lead vào CRM", "number"),
        ui.col("qualified", "Khách quan tâm", "number"),
        *((ui.col("outcome", info["count_short"], "number"),) if info else ()),
        *((ui.col("outcome_value", info["value_short"], "money", needs=value_needs),) if info else ()),
        ui.col("cpl", "Chi phí / lead", "money"), ui.col("cpql", "Chi phí / khách quan tâm", "money"),
        *((ui.col("cost_per_outcome", f"Chi phí / {info['unit']}", "money"),) if info else ()),
    ], title="Theo mẫu quảng cáo", empty="Kỳ này chưa có chi phí hay lead theo mẫu quảng cáo")
    blocks: list[dict[str, Any]] = [ui.b_kpis(kpis)]
    if notes:
        blocks.append(ui.b_callout("warn" if unsafe or stale else "muted", "Cần biết trước khi đọc số", facts=notes))
    if recs:
        blocks.append(ui.b_list(recs, "Gợi ý"))
    blocks.append(ui.b_table(table))
    blocks.append(ui.b_callout("muted", "Cách tính số", facts=how))
    return ui.card("Mẫu quảng cáo ra khách", blocks, subtitle=f"{PERIODS[inp.period]}: {d_from:%d/%m} – {d_to:%d/%m/%Y}", icon="sparkles",
                   ask={"title": "Hỏi trợ lý về mẫu quảng cáo", "prompt": "Về các mẫu quảng cáo kỳ này: "})


@m.query("cashflow_lines", resource="mkt", title="Dòng tiền dự phóng của marketing", internal=True, agent="deny", callers=("acct",),
         why="acct.cashflow (báo cáo dòng tiền dự phóng, min_scope all — người xem có quyền Kế toán cả công ty): dòng thu / chi dự phóng của gói theo tháng. Trả {lines: [{key, label, kind, by_month}], kpis} — số tổng, không từng giao dịch.")
async def cashflow_lines(ctx: Ctx, inp: BudgetInput) -> dict[str, Any]:
    """Cho dự phóng dòng tiền của Tài chính: ngân sách quảng cáo còn lại của chiến dịch đang chạy, chia theo tháng."""
    return {"lines": [{"key": "out_marketing", "label": "Chi quảng cáo", "kind": "out", "by_month": await budget_plan(ctx, inp)}]}


# ── ĐỒNG CHI TRẢ (0196) ──
#
# Một đội / một người góp X% chi phí quảng cáo của một kế hoạch (mọi chiến dịch cùng ``plan_code``) hoặc một chiến dịch, trong một khoảng ngày; phần còn
# lại công ty chịu. Tỷ lệ là dữ liệu có ngày hiệu lực. Khoản bên góp phải trả là SỐ TÍNH (chi phí thật × tỷ lệ) — ghi khoản phải thu / trừ hoa hồng là
# việc của sổ cái / gói dùng con số này, không ở đây. Quyền lead đi theo nguồn tiền (lead của chiến dịch đồng chi trả chia trong đội góp) — luật chia lead.

class CostShareIn(Input):
    plan_code: str = field("Kế hoạch", "", max_length=40, help="Áp cho mọi chiến dịch cùng mã kế hoạch — chọn Kế hoạch HOẶC Chiến dịch")
    campaign_code: str = field("Chiến dịch", "", max_length=30, options_query="mkt.campaign_options")
    team_code: str = field("Đội góp", "", options_query="admin.team_options", help="Chọn Đội HOẶC Người")
    user_email: str = field("Người góp", "", options_query="admin.member_options")
    share_pct: Percent = field("Phần góp (%)", example=30, help="Phần chi phí bên này chịu; phần còn lại công ty chịu")
    valid_from: Day = field("Từ ngày", example="01/11/2026")
    valid_to: Day | None = field("Đến ngày", None, help="Trống = tới khi kết thúc thoả thuận")
    note: str = field("Ghi chú", "", max_length=500, example="Đội A góp 30% chi phí kế hoạch tháng 11")


async def _scope(ctx: Ctx, plan_code: str, campaign_code: str) -> tuple[str, UUID | None]:
    if bool(plan_code.strip()) == bool(campaign_code.strip()):
        raise Invalid("Chọn đúng một: Kế hoạch hoặc Chiến dịch")
    if plan_code.strip():
        if not PLAN.fullmatch(plan_code.strip()):
            raise Invalid("Mã kế hoạch: chữ, số, gạch ngang, gạch dưới (tối đa 40 ký tự)")
        return plan_code.strip(), None
    cid = await ctx.val("SELECT id FROM mkt.campaigns WHERE code = %s", (campaign_code.strip().upper(),))
    if cid is None:
        raise NotFound(f"Không có chiến dịch {campaign_code}")
    return "", cid


@m.command("set_cost_share", resource="mkt", action="manage", title="Khai đồng chi trả quảng cáo", category="setup", importable=True,
           agent="propose", description="Một đội hoặc một người góp một phần chi phí quảng cáo của một kế hoạch / chiến dịch trong một khoảng ngày.")
async def set_cost_share(ctx: Ctx, inp: CostShareIn) -> dict[str, Any]:
    """Tổng phần góp của mọi bên cho cùng phạm vi, ở bất kỳ ngày nào, không vượt 100% (phần còn lại công ty chịu). Người góp / đội góp tra bằng quyền
    người gọi (0125); RLS của người gọi chặn khai cho phạm vi ngoài quyền."""
    plan, cid = await _scope(ctx, inp.plan_code, inp.campaign_code)
    if bool(inp.team_code.strip()) == bool(inp.user_email.strip()):
        raise Invalid("Chọn đúng một bên góp: Đội hoặc Người")
    if inp.valid_to is not None and inp.valid_to < inp.valid_from:
        raise Invalid("Đến ngày phải sau Từ ngày")
    bps = round(Decimal(str(inp.share_pct)) * 100)
    if not 1 <= bps <= 10000:
        raise Invalid("Phần góp từ 0,01% tới 100%")
    team = await ctx.val("SELECT id FROM core.teams WHERE code = %s", (inp.team_code.strip(),)) if inp.team_code.strip() else None
    if inp.team_code.strip() and team is None:
        raise NotFound(f"Không có đội {inp.team_code}")
    user = await _member(ctx, inp.user_email) if inp.user_email.strip() else None
    # Tổng phần góp là hàm bậc thang theo ngày ⇒ lớn nhất tại một ngày BẮT ĐẦU nào đó trong khoảng mới: chỉ xét các mốc đó.
    taken = await ctx.val(
        """WITH same AS (SELECT share_bps, valid_from, coalesce(valid_to, 'infinity'::date) AS valid_to FROM mkt.cost_shares
                         WHERE plan_code = %(plan)s AND campaign_id IS NOT DISTINCT FROM %(cid)s),
                pts AS (SELECT %(f)s::date AS d UNION SELECT valid_from FROM same WHERE valid_from BETWEEN %(f)s AND %(t)s)
           SELECT coalesce(max((SELECT sum(share_bps) FROM same WHERE valid_from <= pts.d AND valid_to >= pts.d)), 0) FROM pts""",
        {"f": inp.valid_from, "t": inp.valid_to or date.max, "plan": plan, "cid": cid})
    if int(taken) + bps > 10000:
        raise Invalid(f"Tổng phần góp cho phạm vi này sẽ vượt 100% (đã khai {int(taken) / 100:g}% trong khoảng ngày đó)")
    sid = await ctx.val(
        """INSERT INTO mkt.cost_shares (plan_code, campaign_id, team_id, user_id, share_bps, valid_from, valid_to, note, created_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, core.me()) RETURNING id""",
        (plan, cid, team, user, bps, inp.valid_from, inp.valid_to, inp.note.strip()))
    return {"cost_share_id": sid, "share_pct": bps / 100}


class EndCostShareIn(Input):
    cost_share_id: UUID = field("Thoả thuận", hidden=True)
    valid_to: Day = field("Kết thúc sau ngày", example="30/11/2026")


@m.command("end_cost_share", resource="mkt", action="manage", title="Kết thúc đồng chi trả", agent="propose")
async def end_cost_share(ctx: Ctx, inp: EndCostShareIn) -> dict[str, Any]:
    """Không xoá (khoản đã tính cho các ngày trước vẫn đúng) — chỉ đặt ngày kết thúc; không lùi ngày kết thúc qua ngày bắt đầu."""
    row = await ctx.one("SELECT valid_from FROM mkt.cost_shares WHERE id = %s FOR UPDATE", (inp.cost_share_id,))
    if row is None:
        raise NotFound("Không thấy thoả thuận đồng chi trả")
    if inp.valid_to < row["valid_from"]:
        raise Invalid("Ngày kết thúc trước ngày bắt đầu")
    await ctx.exec("UPDATE mkt.cost_shares SET valid_to = %s WHERE id = %s", (inp.valid_to, inp.cost_share_id))
    return {"cost_share_id": inp.cost_share_id, "valid_to": inp.valid_to}


SHARE_ROWS = """SELECT s.id, s.plan_code, c.code AS campaign_code, t.name AS team, u.name AS person, s.share_bps, s.valid_from, s.valid_to, s.note
                FROM mkt.cost_shares s LEFT JOIN mkt.campaigns c ON c.id = s.campaign_id
                LEFT JOIN core.teams t ON t.id = s.team_id LEFT JOIN core.users u ON u.id = s.user_id"""


@m.query("cost_shares", resource="mkt", title="Đồng chi trả quảng cáo")
async def cost_shares(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await ctx.all(SHARE_ROWS + " ORDER BY s.valid_from DESC, s.plan_code, c.code")
    for r in rows:
        r["scope"] = f"Kế hoạch {r['plan_code']}" if r["plan_code"] else f"Chiến dịch {r['campaign_code']}"
        r["party"] = r["team"] or r["person"]
        r["share"] = r["share_bps"] / 100
    return ui.table(rows, [
        ui.col("scope", "Phạm vi"), ui.col("party", "Bên góp"), ui.col("share", "Phần góp %", "number"),
        ui.col("valid_from", "Từ", "date"), ui.col("valid_to", "Đến", "date"), ui.col("note", "Ghi chú", width=220),
    ], title="Đồng chi trả quảng cáo", empty="Chưa có thoả thuận đồng chi trả",
       note="Phần góp tính trên chi phí THẬT đã chạy; phần còn lại công ty chịu.")


class StatementIn(Input):
    date_from: Day | None = field("Từ ngày", None, example="01/11/2026", help="Trống = đầu tháng này")
    date_to: Day | None = field("Đến ngày", None, example="30/11/2026", help="Trống = hôm nay")


def _vnd(x: Decimal) -> int:
    return int(x.quantize(Decimal(1), ROUND_HALF_UP))


@m.query("cost_share_statement", resource="mkt", title="Bảng kê đồng chi trả")
async def cost_share_statement(ctx: Ctx, inp: StatementIn) -> dict[str, Any]:
    """Mỗi thoả thuận: chi phí thật của phạm vi trong phần giao của kỳ xem và khoảng hiệu lực × tỷ lệ. Cộng chính xác (numeric), làm tròn VND MỘT lần
    cho mỗi bên góp (không lệch vì làm tròn từng ngày)."""
    d_from, d_to = inp.date_from or today().replace(day=1), inp.date_to or today()
    if d_to < d_from:
        raise Invalid("Đến ngày phải sau Từ ngày")
    rows = await ctx.all(
        """SELECT s.id, s.plan_code, c.code AS campaign_code, t.name AS team, u.name AS person, s.share_bps,
                  (SELECT coalesce(sum(sp.amount_vnd), 0) FROM mkt.spend sp JOIN mkt.campaigns k ON k.id = sp.campaign_id
                   WHERE (CASE WHEN s.plan_code <> '' THEN k.plan_code = s.plan_code ELSE k.id = s.campaign_id END)
                     AND sp.day BETWEEN greatest(s.valid_from, %(f)s) AND least(coalesce(s.valid_to, %(t)s), %(t)s))::numeric AS scope_spend
           FROM mkt.cost_shares s LEFT JOIN mkt.campaigns c ON c.id = s.campaign_id
           LEFT JOIN core.teams t ON t.id = s.team_id LEFT JOIN core.users u ON u.id = s.user_id
           WHERE s.valid_from <= %(t)s AND coalesce(s.valid_to, %(t)s) >= %(f)s ORDER BY s.plan_code, c.code""",
        {"f": d_from, "t": d_to})
    owed: dict[str, Decimal] = {}
    for r in rows:
        r["party"] = r["team"] or r["person"]
        r["scope"] = f"Kế hoạch {r['plan_code']}" if r["plan_code"] else f"Chiến dịch {r['campaign_code']}"
        exact = r["scope_spend"] * r["share_bps"] / 10000
        owed[r["party"]] = owed.get(r["party"], Decimal(0)) + exact
        r["share"] = r["share_bps"] / 100
        r["scope_spend"] = int(r["scope_spend"])
        r["owed"] = _vnd(exact)
    total_owed = sum((_vnd(v) for v in owed.values()), 0)
    return ui.table(rows, [
        ui.col("scope", "Phạm vi"), ui.col("party", "Bên góp"), ui.col("share", "Phần góp %", "number"),
        ui.col("scope_spend", "Chi phí của phạm vi", "money"), ui.col("owed", "Bên góp chịu", "money"),
    ], title=f"Bảng kê đồng chi trả {d_from:%d/%m} – {d_to:%d/%m/%Y}", empty="Kỳ này không có thoả thuận đồng chi trả nào",
       kpis=[ui.kpi("Bên góp chịu (tổng)", total_owed, "money"), ui.kpi("Số bên góp", len(owed))],
       note="Chi phí thật × phần góp, theo ngày hiệu lực của từng thoả thuận. Chưa ghi sổ: khoản phải thu / trừ hoa hồng làm ở Tài chính.")


# ── Sức khoẻ dữ liệu (module health gom) ──

SPEND_STALE_D = 2    # chiến dịch đang chạy có chi phí trễ hơn ngần này ngày ⇒ chi phí / lead mấy ngày gần đây thấp giả
HEALTH_PERIOD_D = 30


def spend_health(campaigns: list[dict[str, Any]], leads: dict[str, int]) -> dict[str, Any]:
    """Hàm thuần (test được): trễ tính THEO TỪNG chiến dịch — một chiến dịch cập nhật hôm nay không che chiến dịch khác trễ 10 ngày (review 31/10)."""
    running = [c for c in campaigns if c["status"] == "running"]
    late = [c for c in running if c["last_day"] is None or (today() - c["last_day"]).days > SPEND_STALE_D]
    oldest = min((c["last_day"] for c in late if c["last_day"]), default=None)
    last = max((c["last_day"] for c in running if c["last_day"]), default=None)
    no_spend = [c for c in campaigns if leads.get(str(c["id"]), 0) > 0 and not c["spend"]]
    row: dict[str, Any] = {
        "title": "Chi phí quảng cáo (chiến dịch đang chạy)", "at": oldest if late else last, "stale": bool(late),
        "tone": "ok" if running else "muted", "open": "mkt.campaigns",
        "subtitle": (f"{len(late)}/{len(running)} chiến dịch trễ > {SPEND_STALE_D} ngày: " + ", ".join(c["code"] for c in late[:5])
                     if late else f"Cả {len(running)} chiến dịch đang chạy có chi phí"),
        "why": (f"{len(late)} chiến dịch đang chạy có chi phí trễ hơn {SPEND_STALE_D} ngày ⇒ chi phí / lead của các chiến dịch đó mấy ngày gần "
                "đây thấp giả")}
    if not running:
        row["badge"] = "không có chiến dịch chạy"
    elif late and oldest is None:
        row["badge"] = "chưa có chi phí"
    return {"rank": 30, "fresh": [row], "notes": [f"Chi phí quảng cáo của chiến dịch đang chạy trễ khi > {SPEND_STALE_D} ngày."], "issues": [
        {"key": "campaign_no_spend", "n": len(no_spend), "title": f"chiến dịch có lead nhưng chưa có chi phí ({HEALTH_PERIOD_D} ngày)",
         "subtitle": ", ".join(c["code"] for c in no_spend[:5]), "open": "mkt.campaigns",
         "why": "{n} chiến dịch có lead mà chưa nhập chi phí ⇒ chi phí / lead KHÔNG tính được (không phải bằng 0)"}]}


@m.query("health_report", resource="mkt", title="Sức khoẻ dữ liệu marketing", internal=True, agent="deny", callers=("health",),
         why="health._collect (Sức khoẻ dữ liệu — health.overview, min_scope all): tình trạng dữ liệu của module này cả công ty. Trả {rank, issues, notes, fresh} — câu + số đếm, có thể kèm mã để sửa; không thân dữ liệu nghiệp vụ.")
async def health_report(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Phần của marketing trong "Sức khoẻ dữ liệu": chi phí chiến dịch đang chạy có trễ không; chiến dịch có lead (Khách hàng — gói bắt buộc
    của Marketing) mà chưa có chi phí ⇒ chi phí / lead không tính được."""
    since = today() - timedelta(days=HEALTH_PERIOD_D - 1)
    rows = await ctx.all(
        """SELECT c.id, c.code, c.name, c.status, max(s.day) AS last_day,
                  coalesce(sum(s.amount_vnd) FILTER (WHERE s.day >= %s), 0) AS spend
           FROM mkt.campaigns c LEFT JOIN mkt.spend s ON s.campaign_id = c.id GROUP BY c.id ORDER BY c.code""", (since,))
    return spend_health([{**r, "id": str(r["id"])} for r in rows], await ctx.call_system("crm.campaign_leads", since=since))


# NGHIỆP VỤ CÔNG TY CÓ DÙNG (api 1.63, ``Screen(feature=)``): công ty không làm việc này thì tắt công tắc ⇒ màn không lên menu của vai nào — một ô
# tick ở Cài đặt công ty, không phải thu hồi quyền từng vai. Mặc định BẬT (như trước khi có công tắc).
USE_ADS = m.setting("use_ads", "Công ty theo dõi từng quảng cáo (dưới chiến dịch)", default=True,
                    help="Bật: Marketing có các màn Quảng cáo, Mẫu quảng cáo ra khách, Sổ thay đổi quảng cáo, Chuyển đổi gửi về kênh — cần đã nối kênh quảng cáo "
                         "hoặc nhập tay từng quảng cáo. " + "Tắt: ẩn với mọi vai (dữ liệu đã có giữ nguyên, bật lại là thấy). Chiến dịch và chi phí theo ngày không đổi.")
USE_COST_SHARE = m.setting("use_cost_share", "Chi phí quảng cáo chia cho nhiều đội / người (đồng chi trả)", default=True,
                           help="Bật: Marketing có màn Đồng chi trả và Bảng kê đồng chi trả. " + "Tắt: ẩn với mọi vai (dữ liệu đã có giữ nguyên, bật lại là thấy).")

# ---- Màn hình (0069): module tự khai phân hệ / góp màn vào phân hệ khác; workspaces.py chỉ ráp và lọc theo quyền
m.workspace(Workspace("marketing", "Marketing", "megaphone.fill", (
    Screen("overview", "Hiệu quả quảng cáo", "chart.line.uptrend.xyaxis.circle.fill", "mkt.overview", tint="pink"),
    Screen("creatives", "Mẫu quảng cáo ra khách", "sparkles", "mkt.creatives", tint="pink", feature=USE_ADS),
    Screen("campaigns", "Chiến dịch", "megaphone.fill", "mkt.campaigns", tint="pink",
           actions=(A("mkt.upsert_campaign", style="primary"), A("mkt.record_spend"))),
    Screen("spend", "Chi phí theo ngày", "banknote.fill", "mkt.spend", tint="orange", actions=(A("mkt.record_spend", style="primary"),)),
    Screen("ads", "Quảng cáo", "photo.on.rectangle", "mkt.ads", tint="pink", actions=(A("mkt.upsert_ad"), A("mkt.record_ad_day")), feature=USE_ADS),
    Screen("cost_shares", "Đồng chi trả", "person.2.fill", "mkt.cost_shares", tint="orange", actions=(A("mkt.set_cost_share", style="primary"),), feature=USE_COST_SHARE),
    Screen("cost_share_statement", "Bảng kê đồng chi trả", "doc.text.fill", "mkt.cost_share_statement", tint="orange", feature=USE_COST_SHARE),
), order=20))


# ── Tập dữ liệu (0100): chi phí quảng cáo theo chiến dịch × kênh × [[offering]] × tháng / ngày — cho app tự ghép / trang báo cáo quản trị.
# Số theo CHIẾN DỊCH (người chạy / đội chạy của chiến dịch); phần đồng chi trả giữa các đội / người (0196) KHÔNG chia ở đây — xem Bảng kê đồng chi trả.
m.dataset(
    "spend", "Chi phí quảng cáo", resource="mkt",
    description=("Mỗi dòng gốc là chi phí một chiến dịch trong một ngày (VND, đã quy đổi nếu nền tảng tính ngoại tệ). Lead / tin nhắn là số NỀN TẢNG "
                 "báo, không phải lead đã vào CRM. Đội = đội chạy chiến dịch, chưa chia theo đồng chi trả."),
    base="""SELECT s.day, s.amount_vnd, coalesce(s.impressions, 0) AS impressions, coalesce(s.clicks, 0) AS clicks,
                   coalesce(s.leads_platform, 0) AS leads_platform, coalesce(s.inbox, 0) AS inbox,
                   c.code AS campaign, c.channel, coalesce(c.offering_code, '') AS offering_code, coalesce(c.plan_code, '') AS plan_code,
                   coalesce(tm.name, '') AS team
            FROM mkt.spend s JOIN mkt.campaigns c ON c.id = s.campaign_id LEFT JOIN core.teams tm ON tm.id = c.team_id""",
    dims={"campaign": Dim("Chiến dịch", "t.campaign"), "channel": Dim("Kênh", "t.channel", labels=CHANNELS),
          "offering": Dim("[[offering]]", "t.offering_code"), "plan": Dim("Mã kế hoạch", "t.plan_code"), "team": Dim("Đội chạy", "t.team"),
          "month": Dim("Tháng", "to_char(t.day, 'YYYY-MM')", month=True), "on_day": Dim("Ngày", "to_char(t.day, 'YYYY-MM-DD')")},
    measures={"spend": Measure("Chi phí", "sum(t.amount_vnd)", "money"), "impressions": Measure("Lượt hiển thị", "sum(t.impressions)"),
              "clicks": Measure("Lượt bấm", "sum(t.clicks)"), "leads_platform": Measure("Lead nền tảng báo", "sum(t.leads_platform)"),
              "inbox": Measure("Tin nhắn nền tảng báo", "sum(t.inbox)"), "days": Measure("Số ngày × chiến dịch", "count(*)"),
              "ctr": Ratio("Tỷ lệ bấm (CTR)", "clicks", "impressions"), "cpl_platform": Ratio("Chi phí / lead nền tảng báo", "spend", "leads_platform", "money")},
    dates={"day": "Ngày chạy"},
    coverage="SELECT spend_complete_through AS complete_through, spend_reliable_from AS reliable_from FROM mkt.coverage",
)


# Báo cáo quản trị (0166): trang quảng cáo trong menu của ban giám đốc — cùng truy vấn, không số riêng
m.screens("mgmt", (Screen("mgmt_ads", "Hiệu quả quảng cáo", "megaphone.fill", "mkt.overview", group="Marketing", tint="pink", min_scope="all"),
                   Screen("mgmt_creatives", "Mẫu quảng cáo ra khách", "sparkles", "mkt.creatives", group="Marketing", tint="pink", min_scope="all", feature=USE_ADS),
                   Screen("mgmt_campaigns", "Chiến dịch", "chart.line.uptrend.xyaxis", "mkt.campaigns", group="Marketing", tint="pink", min_scope="all")), rank=30)

# Phần QUYẾT + LÀM của vòng quảng cáo, không phụ thuộc kênh (0209) — gói kênh chỉ đọc số vào đây + cắm điểm thực thi
# isort: off
from barista.modules.mkt import ad_control  # noqa: E402, F401 — submodule (not the ``ads`` query above)
from barista.modules.mkt import findings  # noqa: E402, F401 — phát hiện cho Báo cáo chạy song song (0230), chỉ đọc
# isort: on
