"""GÓI THẾ VAI NGÀNH của test lõi (ADR 0190): một gói ngành GIẢ — "Đơn dịch vụ" (``orderdesk``) — khai với lõi đúng những thứ một gói ngành thật khai,
để mọi cơ chế lõi mà gói ngành dùng có test CHẠY ĐƯỢC KHI KHÔNG CÀI GÓI NGÀNH NÀO (bộ test của lõi). Nối dài ``tests/test_dummy_pack.py``
(thước đo "thêm gói không sửa lõi") và ``tests/money_source.py`` (nguồn tiền giả).

Gói chỉ import ``barista.api`` như gói ngoài lõi. Nó khai: gói + vai / quyền / chuỗi duyệt / mẫu quy trình mặc định; bảng riêng (``schema.sql``); loại đối
tượng quy trình bám; ba bộ kiểm; hai loại việc theo dõi nhiều ngày; việc module sinh kèm bộ kiểm; hai chỉ số; một tập dữ liệu; ô số liệu cho Soạn thảo;
từ vựng; điều kiện chặn tắt gói; tài khoản sổ cái của riêng mình (một tài khoản cơ chế); bên nghe sự kiện của Tài chính; và dùng các họ thao tác mở
``receivable`` / ``settlement`` / ``identity`` / ``follow_up`` / ``paper_draft`` / ``ledger_ref`` bằng quyền hệ thống.

Nghiệp vụ cố ý NHỎ: một đơn có hai đợt khách trả; ký đơn ⇒ phí thành khoản phải thu; phí về ⇒ thưởng người phụ trách về ví theo tỷ lệ đã thu; huỷ đơn
qua duyệt ⇒ gỡ phần đã lập. Đủ để lõi phải làm mọi việc nó làm cho một gói ngành — không hơn.

Thêm ở bước 2b (cho bản lõi của test ứng dụng / báo cáo / sức khoẻ / duyệt / trợ lý): ô số có nguồn khoan trên danh sách, ``line_options``, phần sức khoẻ dữ
liệu (``health_report``), lệnh có căn cứ duyệt (``settle``), báo cáo năm ``min_scope="all"`` qua họ ``company_figures``, câu gợi ý của trợ lý (``Pack.hints``),
và module phụ TUỲ CHỌN ``orderlines`` (``register_lines()``) cắm ``crm.offering`` / ``crm.offering_options`` / ``crm.lead_story``.

``register()`` khai gói vào sổ đăng ký (fixture của ``tests/standin/conftest.py`` gọi, rồi trả sổ về như cũ sau file test)."""

from datetime import date, timedelta
from typing import Any, LiteralString
from uuid import NAMESPACE_URL, UUID, uuid5

from barista.api import Action as A
from barista.api import (
    CheckIn,
    Conflict,
    Ctx,
    Dim,
    DrillTo,
    Event,
    Flow,
    Hints,
    Input,
    Invalid,
    Measure,
    MetricIn,
    Module,
    NoInput,
    NotFound,
    Open,
    Pack,
    Screen,
    SearchIn,
    SubjectRef,
    WorkIn,
    dong,
    field,
    ledger,
    metrics,
    today,
    ui,
)
from barista.api import brochure as bro

PACK = MODULE = "orderdesk"
ORDER = "orderdesk.order"                  # loại đối tượng quy trình bám
FEE = "orderdesk.fee"                      # nguồn khoản phải thu phí đơn bên Tài chính (tham chiếu = mã đơn)
BONUS = "orderdesk.bonus"                  # nguồn nghĩa vụ phải trả thưởng người phụ trách
REVENUE, RECEIVABLE, EXPENSE = "revenue:order_fee", "receivable:order_clients", "expense:order_bonus"
MECHANISM = "revenue:order_rounding"       # tài khoản cơ chế của gói (0189) — không vào danh mục khoản thu / chi
DUE_DAYS = 30
STATUS = {"open": "Đang mở", "signed": "Đã ký", "cancelled": "Đã huỷ"}
TONES = {"open": "blue", "signed": "green", "cancelled": "gray"}
LINES = {"TV": "Tư vấn", "LD": "Lắp đặt"}

# Mẫu quy trình mặc định bám theo đơn — cùng hình với mẫu của một gói ngành: mục người xác nhận, mục có bộ kiểm, mục tiền công ty ([riêng: acct]),
# việc tự sinh của chặng (của người phụ trách / của đội), hạn chặng, lời dặn trợ lý.
ORDER_FLOW = """# Tiếp nhận & cọc | Kế toán | tối đa 7 ngày
- Phiếu đặt hàng đã ký [xác nhận: @KT]
- Tiền cọc đã nhận = orderdesk.stage_paid seq=1
> Gửi phiếu đặt hàng bản gốc về kế toán | 2 ngày
> Đối chiếu phiếu đặt hàng với bản gốc @KT | 3 ngày
# Hợp đồng | Quản trị
- Giấy tờ khách (bản chụp) [xác nhận: @QT]
- Hợp đồng dịch vụ đã ký = orderdesk.signed
> Gửi bản chụp giấy tờ khách | 2 ngày
# Thanh toán & phí | Kế toán | tối đa 45 ngày, báo @KT
- Khách thanh toán đợt 2 = orderdesk.stage_paid seq=2
- Phí dịch vụ đã thu = orderdesk.fee_paid [riêng: acct]
> Gửi đề nghị thanh toán phí cho khách @KT | 3 ngày
~ Khách chưa thanh toán đợt 2: soạn tin nhắc cho người phụ trách gửi, không hứa giảm giá. Phí chưa về: nhắc kế toán gửi đề nghị thanh toán."""
FLOW_KEY = "don_dich_vu"
ITEM_SLIP, ITEM_PAPERS, ITEM_FEE = "phieu_dat_hang_da_ky", "giay_to_khach_ban_chup", "phi_dich_vu_da_thu"
STAGE_1, STAGE_2, STAGE_3 = "tiep_nhan_coc", "hop_dong", "thanh_toan_phi"

ORDER_FIELDS = [
    {"key": "order_code", "label": "Mã đơn"}, {"key": "customer_name", "label": "Tên khách hàng"},
    {"key": "order_title", "label": "Nội dung đơn"}, {"key": "amount_vnd", "label": "Giá trị đơn", "kind": "amount"},
    {"key": "signed_on", "label": "Ngày ký", "kind": "date"}, {"key": "owner_name", "label": "Người phụ trách"},
    {"key": "customer_address", "label": "Địa chỉ liên hệ của khách", "person": True},   # đơn không giữ ⇒ người soạn nhập (ô ``person``)
    {"key": "stages", "label": "Lịch thanh toán", "kind": "rows",
     "columns": [{"key": "seq", "label": "Đợt", "kind": "number"}, {"key": "label", "label": "Nội dung"},
                 {"key": "due_on", "label": "Hạn", "kind": "date"}, {"key": "amount_vnd", "label": "Số tiền", "kind": "amount"}]},
]
ORDER_SLIP = """# Phiếu xác nhận đơn dịch vụ
Hôm nay, {{draft_date|dai}}, {{entity_name}} xác nhận đơn {{order_code}} của {{customer_name}}: {{order_title}}.
Giá trị đơn: {{amount_vnd}} (bằng chữ: {{amount_vnd|chu}}). Lịch thanh toán:
{{stages}}
{{#if owner_name}}
Người phụ trách: {{owner_name}}.
{{/if}}"""


# ── Đầu vào ──

class Create(Input):
    title: str = field("Nội dung đơn", max_length=200)
    customer: str = field("Khách hàng", max_length=200)
    owner_email: str = field("Người phụ trách (email)", "", max_length=200)
    amount_vnd: int = field("Giá trị đơn", ge=1)
    line: str = field("Dòng dịch vụ", "TV", options=LINES)
    code: str = field("Mã đơn", "", max_length=40)
    deposit_due_on: date | None = field("Hạn đặt cọc", None)


class Ref(Input):
    code: str = field("Mã đơn", max_length=40)


class Sign(Input):
    code: str = field("Mã đơn", max_length=40)
    signed_on: date | None = field("Ngày ký", None)


class Cancel(Input):
    code: str = field("Mã đơn", max_length=40)
    reason: str = field("Lý do", max_length=500)


class StageRef(Input):
    code: str = field("Mã đơn", max_length=40)
    seq: int = field("Đợt", ge=1)


class Settle(Input):
    code: str = field("Mã đơn", max_length=40)
    amount_vnd: int | None = field("Số tiền khách trả", None, ge=1, help="Trống = toàn bộ phần còn phải trả theo lịch thanh toán LÚC GHI")


class ReportIn(Input):
    year: int = field("Năm", 0, ge=0, le=9999, help="Trống = năm nay")


class Filter(Input):
    status: str = field("Trạng thái", "", options={"": "Tất cả", **STATUS})
    line: str = field("Dòng dịch vụ", "", options={"": "Tất cả", **LINES})
    signed_from: date | None = field("Ký từ", None)
    signed_before: date | None = field("Ký trước", None)


class FeeRef(Input):
    code: str = field("Khoản phí đơn", max_length=60, example="PD-DH-0001")


class SeenIn(Input):
    subject_kind: str
    ids: list[str]


class FieldValuesIn(Input):
    subject_kind: str
    ref: str


# ── Lệnh / truy vấn của người dùng ──

async def _order(ctx: Ctx, code: str, *, lock: bool = False) -> dict[str, Any]:
    sql: LiteralString = "SELECT * FROM orderdesk.orders WHERE code = upper(%s)"
    d = await ctx.one(sql + (" FOR UPDATE" if lock else ""), (code.strip(),))
    if d is None:
        raise NotFound(f"Không thấy đơn {code} (trong phạm vi bạn được xem)")
    return d


def _changed(ctx: Ctx, order_id: Any) -> None:
    ctx.emit("subject.changed", kind=ORDER, id=order_id)


async def create(ctx: Ctx, inp: Create) -> dict[str, Any]:
    """Đơn mới: hai đợt khách trả (cọc 30%, phần còn lại khi ký), phí công ty 3%, thưởng người phụ trách 1%."""
    owner: UUID | None = ctx.p.user_id
    if inp.owner_email and inp.owner_email.lower() != ctx.p.email:
        owner = await ctx.val("SELECT u.id FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id WHERE u.email = %s AND mb.active",
                              (inp.owner_email.lower(),))
        if owner is None:
            raise NotFound(f"Không có thành viên {inp.owner_email}")
    code = (inp.code.strip().upper() or f"DH-{await ctx.val("SELECT count(*) + 1 FROM orderdesk.orders"):04d}-{str(owner)[:4].upper()}")
    if await ctx.val("SELECT 1 FROM orderdesk.orders WHERE code = %s", (code,)):
        raise Invalid(f"Mã đơn {code} đã có")
    oid = await ctx.val("""INSERT INTO orderdesk.orders (code, title, customer, line, owner_id, team_id, amount_vnd, fee_vnd, bonus_vnd)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                        (code, inp.title.strip(), inp.customer.strip(), inp.line, owner, await ctx.home_team(owner), inp.amount_vnd,
                         inp.amount_vnd * 3 // 100, inp.amount_vnd // 100))
    deposit = inp.amount_vnd * 30 // 100
    for seq, label, due, amount in ((1, "Đặt cọc", inp.deposit_due_on or today() + timedelta(days=3), deposit),
                                    (2, "Thanh toán khi ký", today() + timedelta(days=DUE_DAYS), inp.amount_vnd - deposit)):
        await ctx.exec("INSERT INTO orderdesk.stages (order_id, seq, label, due_on, amount_vnd) VALUES (%s, %s, %s, %s, %s)", (oid, seq, label, due, amount))
    ctx.emit("subject.created", kind=ORDER, id=oid)
    return {"order_id": oid, "code": code, "summary": f"Mở đơn {code} — {inp.customer.strip()}, {dong(inp.amount_vnd)}"}


async def sign(ctx: Ctx, inp: Sign) -> dict[str, Any]:
    d = await _order(ctx, inp.code, lock=True)
    if d["status"] != "open":
        raise Invalid(f"Đơn {d['code']} {STATUS[d['status']].lower()} — không ký được")
    on = inp.signed_on or today()
    if not await ctx.call_system("acct.period_open", on=on.isoformat()):   # họ ledger_ref: ngày ký phải còn trong kỳ mở (phí ghi doanh thu ngày đó)
        raise Invalid(f"Kỳ kế toán chứa ngày {on:%d/%m/%Y} đã khoá")
    await ctx.exec("UPDATE orderdesk.orders SET status = 'signed', signed_on = %s, signed_at = now() WHERE id = %s", (on, d["id"]))
    ctx.emit("orderdesk.order_signed", order_id=d["id"], code=d["code"], customer=d["customer"], amount_vnd=d["amount_vnd"], fee_vnd=d["fee_vnd"],
             owner_id=d["owner_id"], team_id=d["team_id"], signed_on=on)
    _changed(ctx, d["id"])
    return {"order_id": d["id"], "code": d["code"], "summary": f"Ký đơn {d['code']} ngày {on:%d/%m/%Y}"}


async def cancel(ctx: Ctx, inp: Cancel) -> dict[str, Any]:
    """Huỷ đơn — đi qua chuỗi duyệt mặc định của gói (luật duyệt là dữ liệu: ``Pack.approval_rules``)."""
    d = await _order(ctx, inp.code, lock=True)
    if d["status"] == "cancelled":
        raise Invalid(f"Đơn {d['code']} đã huỷ")
    await ctx.exec("UPDATE orderdesk.orders SET status = 'cancelled', cancel_reason = %s WHERE id = %s", (inp.reason.strip(), d["id"]))
    ctx.emit("orderdesk.order_cancelled", order_id=d["id"], code=d["code"], was_signed=d["status"] == "signed")
    _changed(ctx, d["id"])
    return {"order_id": d["id"], "code": d["code"], "summary": f"Huỷ đơn {d['code']}: {inp.reason.strip()}"}


async def pay_stage(ctx: Ctx, inp: StageRef) -> dict[str, Any]:
    d = await _order(ctx, inp.code, lock=True)
    n = await ctx.exec("UPDATE orderdesk.stages SET paid_vnd = amount_vnd, paid_on = core.today() WHERE order_id = %s AND seq = %s", (d["id"], inp.seq))
    if not n:
        raise NotFound(f"Đơn {d['code']} không có đợt {inp.seq}")
    ctx.emit("orderdesk.stage_paid", order_id=d["id"], code=d["code"], seq=inp.seq)
    _changed(ctx, d["id"])
    return {"code": d["code"], "seq": inp.seq, "summary": f"Ghi khách trả đủ đợt {inp.seq} của {d['code']}"}


async def settle_basis(ctx: Ctx, inp: Settle) -> dict[str, str]:
    """Căn cứ (0071): ô số tiền để trống ⇒ lấy phần còn phải trả theo lịch thanh toán LÚC CHẠY — đề xuất soạn lúc còn 4 tỷ, lịch đổi thành 4,5 tỷ ⇒ người
    bấm sau đó không được ghi thu theo số mình chưa thấy. Người tự ghi số tiền ⇒ số còn lại không còn là căn cứ (lịch thì vẫn). Đọc có KHOÁ dòng đơn ⇒
    không ai đổi được giữa lúc so và lúc ghi."""
    d = await ctx.one("SELECT id, code FROM orderdesk.orders WHERE code = upper(%s) FOR UPDATE", (inp.code.strip(),))
    if d is None:
        raise NotFound(f"Không thấy đơn {inp.code}")
    plan = await ctx.all("SELECT seq, due_on, amount_vnd - paid_vnd AS rest FROM orderdesk.stages WHERE order_id = %s ORDER BY seq", (d["id"],))
    stamp = [(s["seq"], str(s["due_on"]), s["rest"] if inp.amount_vnd is None else None) for s in plan]
    return {f"Số còn phải trả / lịch thanh toán đơn {d['code']}": str(stamp)}


async def settle(ctx: Ctx, inp: Settle) -> dict[str, Any]:
    """Ghi khách trả phần còn lại của đơn (trống số tiền ⇒ trả hết theo lịch lúc ghi), dồn lần lượt vào các đợt chưa đủ. Lệnh có CĂN CỨ (``basis=``)."""
    d = await _order(ctx, inp.code, lock=True)
    if d["status"] == "cancelled":
        raise Invalid(f"Đơn {d['code']} đã huỷ")
    plan = await ctx.all("SELECT seq, amount_vnd - paid_vnd AS rest FROM orderdesk.stages WHERE order_id = %s AND paid_vnd < amount_vnd ORDER BY seq",
                         (d["id"],))
    rest = sum(s["rest"] for s in plan)
    if rest <= 0:
        raise Conflict(f"Đơn {d['code']} đã trả đủ — không còn gì để ghi")
    amount = inp.amount_vnd or rest
    if amount > rest:
        raise Invalid(f"Số tiền {dong(amount)} vượt phần còn phải trả của {d['code']} ({dong(rest)})")
    left = amount
    for s in plan:
        take = min(left, s["rest"])
        if take <= 0:
            break
        await ctx.exec("""UPDATE orderdesk.stages SET paid_vnd = paid_vnd + %s, paid_on = CASE WHEN paid_vnd + %s >= amount_vnd THEN core.today() END
                          WHERE order_id = %s AND seq = %s""", (take, take, d["id"], s["seq"]))
        if take == s["rest"]:
            ctx.emit("orderdesk.stage_paid", order_id=d["id"], code=d["code"], seq=s["seq"])
        left -= take
    _changed(ctx, d["id"])
    return {"code": d["code"], "paid_vnd": amount, "summary": f"Ghi khách trả {dong(amount)} cho {d['code']}"}


async def follow_stage(ctx: Ctx, inp: StageRef) -> dict[str, Any]:
    """Việc thu MỘT đợt cho người phụ trách đơn, gắn SẴN bộ kiểm của gói (0117): module sinh việc thì module gắn bộ kiểm; bấm lại ⇒ việc cũ."""
    d = await _order(ctx, inp.code)
    s = await ctx.one("SELECT label, due_on, amount_vnd, paid_vnd FROM orderdesk.stages WHERE order_id = %s AND seq = %s", (d["id"], inp.seq))
    if s is None:
        raise NotFound(f"Đơn {d['code']} không có đợt {inp.seq}")
    if d["status"] == "cancelled" or s["paid_vnd"] >= s["amount_vnd"]:
        raise Invalid(f"{s['label']} của {d['code']} không còn phải thu")
    out = await ctx.call_system("flow.module_task", title=f"Thu {s['label'].lower()} — {d['customer']} ({d['code']})",
                                notes=f"Còn {dong(s['amount_vnd'] - s['paid_vnd'])}. Việc tự đóng khi ghi nhận khách trả đủ.",
                                assignee_id=d["owner_id"], due_on=max(s["due_on"] or today(), today()), subject_kind=ORDER, subject_id=d["id"],
                                done_check="orderdesk.stage_paid", done_args=f"seq={inp.seq}",
                                source_kind="orderdesk.stage", source_ref=f"{d['code']}#{inp.seq}")
    return {"task_id": out["task_id"], "summary": f"Việc thu {s['label'].lower()} của {d['code']} " + ("đang mở" if out["existed"] else f"giao cho {out['who']}")}


async def list_orders(ctx: Ctx, inp: Filter) -> dict[str, Any]:
    rows = await ctx.all(
        """SELECT o.id, o.code, o.title, o.customer, o.line, u.name AS owner, o.amount_vnd, o.fee_vnd, o.status, o.created_on, o.signed_on
           FROM orderdesk.orders o JOIN core.users u ON u.id = o.owner_id
           WHERE (%(s)s = '' OR o.status = %(s)s) AND (%(l)s = '' OR o.line = %(l)s)
             AND (%(f)s::date IS NULL OR o.signed_on >= %(f)s) AND (%(t)s::date IS NULL OR o.signed_on < %(t)s)
           ORDER BY o.created_on DESC, o.code LIMIT 2000""", {"s": inp.status, "l": inp.line, "f": inp.signed_from, "t": inp.signed_before})
    signed = [r for r in rows if r["status"] == "signed"]
    signed_only = {**inp.model_dump(mode="json"), "status": "signed"}   # nguồn của ô số (0097): đúng bộ lọc đang xem, chỉ đơn đã ký
    return ui.table(rows, [
        ui.col("code", "Mã đơn"), ui.col("customer", "Khách hàng"), ui.col("title", "Nội dung", width=240),
        ui.col("line", "Dòng dịch vụ", "badge", labels=LINES), ui.col("owner", "Người phụ trách"),
        ui.col("status", "Trạng thái", "badge", labels=STATUS, tones=TONES), ui.col("signed_on", "Ngày ký", "date"),
        ui.col("amount_vnd", "Giá trị", "money"), ui.col("fee_vnd", "Phí", "money"),
    ], title="[[service_order]]", limit=2000, empty="Chưa có đơn",
        kpis=[ui.kpi("Đơn", len(rows)), ui.kpi("Đã ký", len(signed), tone="green" if signed else None, drill=ui.drill("orderdesk.list", signed_only)),
              ui.kpi("Giá trị đã ký", sum(r["amount_vnd"] for r in signed), "money", drill=ui.drill("orderdesk.list", signed_only, sum="amount_vnd")),
              ui.kpi("Phí đã ký", sum(r["fee_vnd"] for r in signed), "money", drill=ui.drill("orderdesk.list", signed_only, sum="fee_vnd"))])


async def line_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    """Truy vấn lựa chọn của gói (tên kết thúc ``_options``) — tham số chọn của app tự ghép / ô chọn của form đọc từ đây."""
    return ui.options([{"value": k, "label": v} for k, v in LINES.items()])


async def case_file(ctx: Ctx, inp: Ref) -> dict[str, Any]:
    """Hồ sơ đơn theo chặng (quy trình bám theo đơn quản trị khai): thẻ của ``flow.case`` — mục tiền công ty chỉ hiện số cho người có quyền."""
    d = await _order(ctx, inp.code)
    page: dict[str, Any] = await ctx.call("flow.case", subject_kind=ORDER, subject_id=d["id"])
    # module CHỦ hồ sơ đặt nút "Soạn ấn phẩm" trên thẻ của mình (ADR 0249) — gói Ấn phẩm tắt / người xem không chạy được ⇒ nút tự ẩn
    page["actions"] = [*(page.get("actions") or []), ui.act("compose.brochure", "Soạn ấn phẩm", {"kind": "demo_order", "subject": str(d["id"])})]
    return page


async def draft_fee_request(ctx: Ctx, inp: FeeRef) -> dict[str, Any]:
    """Giấy đề nghị khách thanh toán phí của một đơn (số liệu từ khoản phí bên Tài chính — cần quyền kế toán); lưu dự thảo qua họ ``paper_draft``."""
    src = await _fee_of(ctx, inp.code)
    if src is None:
        raise NotFound(f"Không thấy khoản phí {inp.code}")
    fee, d = src
    rest = fee["amount_vnd"] - fee["paid_vnd"]
    if fee["status"] == "void" or rest <= 0:
        raise Invalid(f"Khoản phí {fee['code']} không còn phải thu")
    company = await ctx.call_system("papers.company")
    content = {"paragraphs": [f"Căn cứ đơn dịch vụ {d['code']} ký ngày {d['signed_on']:%d/%m/%Y} giữa {company['company_name'] or '[tên công ty]'} và {d['customer']};",
                              f"Chúng tôi đề nghị Quý khách thanh toán phí dịch vụ còn lại: {dong(rest)}."],
               "table": {"columns": ["STT", "Đơn", "Giá trị đơn", "Phí dịch vụ"], "align": ["c", "l", "r", "r"],   # giấy có BẢNG (0102)
                         "rows": [["1", d["code"], dong(d["amount_vnd"]), dong(fee["amount_vnd"])]], "total": ["Còn phải thanh toán", dong(rest)]},
               "after": ["Trân trọng cảm ơn./."], "receivers": ["Như trên;", "Lưu: VT, KT."], "signature": "single"}
    out: dict[str, Any] = await ctx.call_system("papers.save_draft", kind="order_fee_request", kind_label="Đề nghị thanh toán phí đơn",
                                                title="Giấy đề nghị thanh toán", abbr="ĐNTT", summary=f"V/v thanh toán phí đơn {d['code']}",
                                                recipient=d["customer"], content=content, ref=fee["code"])
    return out


# ── Đối tượng quy trình bám, bộ kiểm ──

async def subject_order(ctx: Ctx, inp: SubjectRef) -> dict[str, Any]:
    d = await ctx.one("SELECT * FROM orderdesk.orders WHERE id = %s", (inp.subject_id,))
    if d is None:
        raise NotFound("Không thấy đơn")
    return {"title": f"{d['customer']} — {d['title']} ({d['code']})", "owner_id": d["owner_id"], "team_id": d["team_id"],
            "state": "cancelled" if d["status"] == "cancelled" else "active", "open": {"query": "orderdesk.case_file", "input": {"code": d["code"]}}}


async def stage_paid(ctx: Ctx, inp: CheckIn) -> dict[str, Any]:
    seq = int(inp.args.get("seq", 1))
    r = await ctx.one("SELECT amount_vnd, paid_vnd, paid_on FROM orderdesk.stages WHERE order_id = %s AND seq = %s", (inp.subject_id, seq))
    if r is None:
        return {"status": "missing", "value": f"không có đợt {seq}"}
    value = f"{r['paid_vnd']:,} / {r['amount_vnd']:,} đ".replace(",", ".")
    if r["paid_vnd"] >= r["amount_vnd"]:
        return {"status": "verified", "value": value, "at": r["paid_on"]}
    return {"status": "received" if r["paid_vnd"] > 0 else "missing", "value": value}


async def signed(ctx: Ctx, inp: CheckIn) -> dict[str, Any]:
    d = await ctx.one("SELECT status, signed_on, signed_at FROM orderdesk.orders WHERE id = %s", (inp.subject_id,))
    if d is None or d["status"] != "signed":
        return {"status": "missing"}
    return {"status": "verified", "value": f"ngày ký {d['signed_on']:%d/%m/%Y}", "at": d["signed_at"]}


async def fee_paid(ctx: Ctx, inp: CheckIn) -> dict[str, Any]:
    """Số của Tài chính đọc qua họ ``receivable`` — quy trình ẩn số với người thiếu quyền mà mẫu khai ở mục (``[riêng: acct]``), không phải ở đây."""
    fees = await ctx.call_system("acct.receivables_for", source_prefix=FEE, ref_prefixes=[str(inp.subject_id)])
    fee = next((r for r in fees if r["status"] != "void"), None)
    if fee is None:
        return {"status": "missing"}
    value = f"{fee['paid_vnd']:,} / {fee['amount_vnd']:,} đ".replace(",", ".")
    return {"status": "verified" if fee["status"] == "paid" else ("received" if fee["status"] == "partial" else "missing"), "value": value}


# ── Việc theo dõi nhiều ngày ──

async def _fee_of(ctx: Ctx, fee_code: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Khoản phí (mã PD-<mã đơn>) + đơn của nó, BẰNG QUYỀN NGƯỜI GỌI với đơn (RLS) — không thấy đơn ⇒ ``NotFound`` từ ``_order``; phí chưa lập ⇒ None."""
    d = await _order(ctx, fee_code.strip().upper().removeprefix("PD-"))
    fees = await ctx.call_system("acct.receivables_for", source_prefix=FEE, ref_prefixes=[str(d["id"])])
    return (fees[0], d) if fees else None


async def work_order_fee(ctx: Ctx, inp: WorkIn) -> dict[str, Any]:
    """Bằng quyền người phụ trách: khoản phí của một đơn — xong khi thu đủ (code), huỷ khi khoản huỷ; chưa lập ⇒ chờ."""
    no = inp.subject.strip().upper()
    src = await _fee_of(ctx, no)
    if src is None:
        return {"ref": no, "title": f"Thu phí đơn {no}", "state": "wait", "facts": {"note": "Khoản phí chưa lập — kiểm lại sau."},
                "summary": "Khoản phí chưa lập — kiểm lại sau.", "task": "", "fallback": ""}
    fee, d = src
    due: date = fee["due_on"]
    rest = fee["amount_vnd"] - fee["paid_vnd"]
    late = max(0, (today() - due).days)
    state = "cancelled" if fee["status"] == "void" else "done" if rest <= 0 else "open"
    facts = {"invoice": fee["code"], "order": d["code"], "customer": d["customer"], "amount": fee["amount_vnd"], "paid": fee["paid_vnd"], "rest": rest,
             "due": due.isoformat(), "late_days": late}
    return {
        "ref": fee["code"], "title": f"Thu phí đơn {d['code']}", "state": state, "facts": facts,
        "summary": f"Còn phải thu {dong(rest)} / {dong(fee['amount_vnd'])}, hạn {due:%d/%m/%Y}" + (f" — quá hạn {late} ngày." if late else "."),
        "closing": f"Đã thu đủ {dong(fee['amount_vnd'])} — đóng việc." if state == "done" else "Khoản phí đã huỷ — dừng theo dõi.",
        "task": (f"Khách {d['customer']} chưa trả đủ phí {fee['code']} (đơn {d['code']}): còn {dong(rest)} / {dong(fee['amount_vnd'])}, hạn {facts['due']}, "
                 f"đã quá hạn {late} ngày.\nHãy: (1) tìm trong kho tài liệu quy trình xử lý công nợ quá hạn; (2) soạn DỰ THẢO đề nghị thanh toán cho "
                 f"{fee['code']}; (3) viết 2–3 câu báo người phụ trách. Số tiền chép đúng như trên, không bịa số."),
        "fallback": f"{d['customer']} quá hạn {late} ngày khoản phí {dong(rest)} ({fee['code']}). Em đã soạn dự thảo đề nghị thanh toán — bạn xem rồi ban hành.",
        "fallback_no_draft": f"{d['customer']} quá hạn {late} ngày khoản phí {dong(rest)} ({fee['code']}). Bạn liên hệ khách để đôn đốc.",
        "draft": {"command": "orderdesk.draft_fee_request", "input": {"code": fee["code"]}, "title": "Dự thảo đề nghị thanh toán"},
    }


async def work_order_stage(ctx: Ctx, inp: WorkIn) -> dict[str, Any]:
    """Bằng quyền người phụ trách: một đợt khách trả — xong khi trả đủ (code), huỷ khi đơn huỷ."""
    code, _, seq = inp.subject.strip().upper().partition("#")
    if not seq.isdigit():
        raise Invalid("Ghi theo dạng mã đơn#số đợt, vd DH-0001#2")
    d = await _order(ctx, code)
    s = await ctx.one("SELECT seq, label, due_on, amount_vnd, paid_vnd FROM orderdesk.stages WHERE order_id = %s AND seq = %s", (d["id"], int(seq)))
    if s is None:
        raise NotFound(f"Không thấy đợt {seq} của {code} (trong phạm vi bạn được xem)")
    due: date | None = s["due_on"]
    rest = s["amount_vnd"] - s["paid_vnd"]
    late = max(0, (today() - due).days) if due else 0
    state = "cancelled" if d["status"] == "cancelled" else "done" if rest <= 0 else "open"
    return {
        "ref": f"{d['code']}#{s['seq']}", "title": f"{d['customer']} trả {s['label'].lower()} ({d['code']})", "state": state,
        "facts": {"order": d["code"], "seq": s["seq"], "customer": d["customer"], "rest": rest, "due": due.isoformat() if due else "", "late_days": late},
        "summary": f"Khách {d['customer']} còn phải trả {dong(rest)} ({s['label']})" + (f" — quá hạn {late} ngày." if late else "."),
        "closing": f"Đã thu đủ {dong(s['amount_vnd'])} — đóng việc." if state == "done" else "Đơn đã huỷ — dừng theo dõi.",
        "task": f"Khách {d['customer']} chưa trả đủ {s['label'].lower()} của đơn {d['code']}: còn {dong(rest)}, quá hạn {late} ngày. Viết 2–3 câu báo người phụ trách.",
        "fallback": f"Khách {d['customer']} quá hạn {late} ngày khoản {dong(rest)} ({s['label']}). Bạn gọi nhắc khách.",
    }


# ── Bên nghe: đơn ký / huỷ, tiền phí về (sự kiện của Tài chính) ──

async def _still_signed(ctx: Ctx, order_id: Any) -> bool:
    """Trạng thái HIỆN TẠI thắng thứ tự sự kiện: bên nghe "đã ký" chạy trễ sau khi đơn đã huỷ ⇒ không lập gì. Khoá chia sẻ tới hết lượt."""
    return await ctx.val("SELECT status FROM orderdesk.orders WHERE id = %s FOR SHARE", (order_id,)) == "signed"


async def issue_fee(ctx: Ctx, event: Event) -> None:
    """Ký đơn ⇒ phí thành khoản phải thu của Tài chính (họ ``receivable``): doanh thu + phải thu vào tài khoản CỦA GÓI, mã PD-<đơn>. Lõi giữ một khoản
    cho mỗi (nguồn, tham chiếu) — bên nghe này không tự chống lặp."""
    p = event.payload
    if not p["fee_vnd"] or not await _still_signed(ctx, p["order_id"]):
        return
    on = date.fromisoformat(p["signed_on"])
    await ctx.call_system("acct.issue_receivable", source=FEE, source_ref=str(p["order_id"]), code=f"PD-{p['code']}", label=f"Phí đơn {p['code']}",
                          nature="revenue", party=p["customer"], amount_vnd=p["fee_vnd"], revenue_account=REVENUE, receivable_account=RECEIVABLE,
                          match_keys=[p["code"]], issued_on=on.isoformat(), due_on=(on + timedelta(days=DUE_DAYS)).isoformat(), followed=True,
                          when_locked="task")


async def open_fee_work(ctx: Ctx, event: Event) -> None:
    """Ký đơn ⇒ mở việc "thu phí đơn" cho người giữ quyền ghi Kế toán cả công ty (chọn theo MA TRẬN QUYỀN — không viết cứng vai)."""
    p = event.payload
    if not p["fee_vnd"] or not await _still_signed(ctx, p["order_id"]):
        return
    owners = await ctx.call_system("admin.members_with_grant", resource="acct", action="write", scope="all")
    if owners:
        await ctx.call_system("work.open", kind="order_fee", owner_id=owners[0], subject_ref=f"PD-{p['code']}", title=f"Thu phí đơn {p['code']}",
                              note=f"Mở khi ký đơn {p['code']}: theo dõi khách trả {dong(p['fee_vnd'])} phí tới khi đối soát đủ.")


async def earn_on_fee(ctx: Ctx, event: Event) -> None:
    """Mỗi lần phí về (khoản phải thu nguồn của gói): thưởng người phụ trách về ví = thưởng × (đã thu luỹ kế / phí) — ghi nghĩa vụ phải trả qua họ
    ``settlement``. Mức đích tính từ CĂN CỨ THU HIỆN TẠI (không từ số trong sự kiện) và chỉ tăng ⇒ sự kiện tới trễ / giao lại không cộng thêm."""
    p = event.payload
    if p["source"] != FEE:
        return
    d = await ctx.one("SELECT * FROM orderdesk.orders WHERE id = %s FOR UPDATE", (p["source_ref"],))
    facts = await ctx.call_system("acct.receipt_facts", source=FEE, refs=[p["source_ref"]])
    if d is None or not facts:
        return
    paid, total = int(facts[0]["paid_vnd"]), int(facts[0]["amount_vnd"])
    target = d["bonus_vnd"] * paid // total if total > 0 and d["status"] == "signed" else d["earned_vnd"]
    if target > d["earned_vnd"]:
        party = (await ctx.call_system("parties.ensure_member", user_id=d["owner_id"]))["party_id"]
        ref = f"{d['id']}:{target}"
        await ctx.call_system("acct.recognize_payable", source=BONUS, source_ref=ref, party_id=party, amount_vnd=target - d["earned_vnd"], tax_vnd=0,
                              tax_basis={}, expense_account=EXPENSE, label=f"Thưởng đơn {d['code']}", basis=f"Phí đơn {d['code']} đã thu {dong(paid)}")
        await ctx.exec("UPDATE orderdesk.orders SET earned_vnd = %s, earn_refs = earn_refs || %s::text WHERE id = %s", (target, ref, d["id"]))
    _changed(ctx, d["id"])
    await ctx.call_system("work.wake", kind="order_fee", subject_ref=p["code"], text=f"Có phiếu thu mới — luỹ kế {dong(paid)}.")


async def wake_on_stage(ctx: Ctx, event: Event) -> None:
    p = event.payload
    await ctx.call_system("work.wake", kind="order_stage", subject_ref=f"{p['code']}#{p['seq']}", text="Có ghi nhận khách trả tiền.")


async def void_fee(ctx: Ctx, event: Event) -> None:
    """Huỷ đơn ⇒ huỷ khoản phí chưa thu (đảo đúng bút toán ghi nhận). Đã thu một phần ⇒ không tự huỷ: sự kiện dừng ở Sự kiện lỗi cho kế toán xử lý tay."""
    out = await ctx.call_system("acct.void_receivables", source=FEE, ref_prefix=str(event.payload["order_id"]), due_after="0001-01-01")
    if out["kept"]:
        raise Invalid(f"Đơn {event.payload['code']} đã huỷ nhưng phí {', '.join(out['kept'])} đã thu một phần — kế toán xử lý tay")


async def claw_back(ctx: Ctx, event: Event) -> None:
    """Huỷ đơn ⇒ phần thưởng đã về ví bị điều chỉnh về 0 (họ ``settlement``: điều chỉnh nghĩa vụ chưa chi, thu hồi phần đã chi)."""
    d = await ctx.one("SELECT id, code, earn_refs FROM orderdesk.orders WHERE id = %s FOR UPDATE", (event.payload["order_id"],))
    if d is None or not d["earn_refs"]:
        return
    await ctx.call_system("acct.void_payables", source=BONUS, refs=list(d["earn_refs"]), action_key=f"cancel:{d['id']}", reason=f"Huỷ đơn {d['code']}")
    await ctx.exec("UPDATE orderdesk.orders SET earned_vnd = 0 WHERE id = %s", (d["id"],))


# ── Chỉ số, tìm nhanh, chặn tắt gói, ô số liệu cho Soạn thảo ──

_SIGNED: LiteralString = "o.status = 'signed' AND (%(ctx)s = '' OR o.line = %(ctx)s)"


async def metric_signed(ctx: Ctx, inp: MetricIn) -> dict[str, Any]:
    return await metrics.run(ctx, inp, frm="orderdesk.orders o", where=_SIGNED, day="o.signed_on", person="o.owner_id", team="o.team_id")


async def metric_fee(ctx: Ctx, inp: MetricIn) -> dict[str, Any]:
    return await metrics.run(ctx, inp, frm="orderdesk.orders o", where=_SIGNED, day="o.signed_on", person="o.owner_id", team="o.team_id",
                             value="sum(o.fee_vnd)")


async def search_orders(ctx: Ctx, inp: SearchIn) -> list[dict[str, Any]]:
    rows = await ctx.all("SELECT code, customer, status FROM orderdesk.orders WHERE code ILIKE %s OR customer ILIKE %s ORDER BY created_on DESC LIMIT 8",
                         (f"%{inp.q}%", f"%{inp.q}%"))
    return [{"title": f"{r['code']} — {r['customer']}", "badge": STATUS[r["status"]], "open": {"query": "orderdesk.case_file", "input": {"code": r["code"]}}}
            for r in rows]


async def pack_blockers(ctx: Ctx, inp: NoInput) -> list[str]:
    n = await ctx.val("SELECT count(*) FROM orderdesk.orders WHERE status = 'signed' AND earned_vnd < bonus_vnd")
    return [f"Còn {n} đơn đã ký chưa thu đủ phí"] if n else []


async def plug_field_catalog(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    return {"subject_kind": ORDER, "label": "Đơn dịch vụ", "ref_label": "Mã đơn", "fields": ORDER_FIELDS,
            "starters": [{"key": "phieu_xac_nhan_don", "name": "Phiếu xác nhận đơn", "abbr": "PXN", "body": ORDER_SLIP, "signers": ["BÊN CUNG CẤP", "KHÁCH HÀNG"]}]}


async def plug_subjects_seen(ctx: Ctx, inp: SeenIn) -> list[str] | None:
    if inp.subject_kind != ORDER:
        return None
    return [str(r["id"]) for r in await ctx.all("SELECT id FROM orderdesk.orders WHERE id::text = ANY(%s)", (inp.ids,))]


async def plug_field_values(ctx: Ctx, inp: FieldValuesIn) -> dict[str, Any] | None:
    """Đọc bằng quyền NGƯỜI SOẠN (RLS của đơn)."""
    if inp.subject_kind != ORDER:
        return None
    d = await ctx.one("SELECT * FROM orderdesk.orders WHERE code = upper(%s)", (inp.ref,))
    if d is None:
        raise NotFound(f"Không thấy đơn {inp.ref} — sai mã, hoặc bạn không có quyền xem đơn này")
    plan = await ctx.all("SELECT seq, label, due_on, amount_vnd FROM orderdesk.stages WHERE order_id = %s ORDER BY seq", (d["id"],))
    return {"subject_id": d["id"], "ref": d["code"], "title": f"{d['customer']} — {d['title']}", "blocked": "đơn đã huỷ" if d["status"] == "cancelled" else "",
            "values": {"order_code": d["code"], "customer_name": d["customer"], "order_title": d["title"], "amount_vnd": d["amount_vnd"],
                       "signed_on": d["signed_on"], "owner_name": await ctx.val("SELECT name FROM core.users WHERE id = %s", (d["owner_id"],)),
                       "stages": plan}}


async def health_report(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Phần của gói trong "Sức khoẻ dữ liệu" (module ``health`` chỉ gom — 0078): kiểm XUYÊN đơn × Tài chính đặt ở gói bắt buộc cả hai. Khoản phí đọc qua
    họ ``receivable`` (mọi khoản của nguồn mình); đơn đọc bằng quyền hệ thống của lần gom (``health.overview`` — ``min_scope="all"``)."""
    live = [r for r in await ctx.call_system("acct.receivables_for", source_prefix=FEE, ref_prefixes=[""]) if r["status"] != "void"]
    have = {r["source_ref"] for r in live}
    signed_orders = await ctx.all("SELECT id::text AS id, code FROM orderdesk.orders WHERE status = 'signed' AND fee_vnd > 0 ORDER BY code")
    missing = [o for o in signed_orders if o["id"] not in have]
    overdue = [r for r in live if r["status"] == "open" and r["paid_vnd"] == 0 and r["due_on"] < today()]
    return {
        "rank": 50, "notes": ["Phí đơn ghi nhận chỉ để biết, không tô màu (theo lịch ký đơn)."],
        "fresh": [{"title": "Phí đơn ghi nhận", "subtitle": "Gần nhất", "at": max((r["created_at"] for r in live), default=None), "stale": False,
                   "tone": "muted", "open": "orderdesk.list"}],
        "issues": [
            {"key": "order_fee_overdue", "n": len(overdue), "title": "khoản phí đơn quá hạn mà khách chưa trả đồng nào", "open": "acct.receivables",
             "subtitle": f"{dong(sum(r['amount_vnd'] for r in overdue))} · hạn sớm nhất {min(r['due_on'] for r in overdue):%d/%m/%Y}" if overdue else "",
             "why": "{n} khoản phí đơn quá hạn chưa thu ⇒ dự phóng dòng tiền có thể lạc quan"},
            {"key": "order_signed_no_fee", "n": len(missing), "title": "đơn đã ký chưa có khoản phí", "open": "orderdesk.list",
             "subtitle": ", ".join(o["code"] for o in missing[:5]),
             "why": "{n} đơn ký chưa có khoản phí ⇒ doanh thu ghi nhận và công nợ khách thấp hơn thật"}]}


async def report_year(ctx: Ctx, inp: ReportIn) -> dict[str, Any]:
    """Trang báo cáo quản trị của gói (0166): đơn ký từng tháng của một năm so với chỉ tiêu CẢ CÔNG TY (họ ``company_figures`` — chỉ khi gói KPI bật và
    có chỉ tiêu ở đội gốc), bố cục chia cột ``w`` + biểu đồ ``combo``. Số toàn công ty ⇒ ``min_scope="all"``."""
    year = inp.year or today().year
    first, nxt = date(year, 1, 1), date(year + 1, 1, 1)
    by = {r["m"]: r for r in await ctx.all(
        """SELECT to_char(signed_on, 'YYYY-MM') AS m, count(*) AS n, sum(amount_vnd)::bigint AS value FROM orderdesk.orders
           WHERE status = 'signed' AND signed_on >= %s AND signed_on < %s GROUP BY 1""", (first, nxt))}
    keys = [f"{year}-{mo:02d}" for mo in range(1, 13)]
    months = [{"id": k, "month": f"T{k[5:]}", "n": by[k]["n"] if k in by else 0, "value": by[k]["value"] if k in by else 0} for k in keys]
    blocks = [ui.span(ui.b_kpis([ui.kpi("Đơn ký", sum(r["n"] for r in months)), ui.kpi("Giá trị đã ký", sum(r["value"] for r in months), "money")]), 12)]
    found = await ctx.call_system_optional("kpi.company_targets", metric="orderdesk.metric_signed", since=first, until=nxt)
    tg = {x["period"]: x["target"] for x in found or []}
    if tg:
        even = len(set(tg.values())) == 1 and len(tg) > 1
        note = f"Chỉ tiêu năm {year} = tổng chỉ tiêu các tháng đã khai ở KPI: {sum(tg.values())} đơn" + (
            "; các tháng đang BẰNG NHAU — chia đều, chưa theo mùa vụ." if even else ".")
        blocks.append(ui.span(ui.b_chart("combo", [r["month"] for r in months], [
            ui.line("Đơn ký", [r["n"] for r in months], "blue", as_="bar"),
            ui.line("Chỉ tiêu công ty", [tg.get(k) for k in keys], "orange", as_="line")], title=f"Đơn ký so với chỉ tiêu công ty — {year}", note=note), 12))
    blocks.append(ui.span(ui.b_table(ui.table(months, [ui.col("month", "Tháng"), ui.col("n", "Đơn ký", "number"), ui.col("value", "Giá trị", "money")],
                                              title="Đơn ký theo tháng", empty="Chưa có đơn ký")), 12))
    return ui.card("Đơn ký theo năm", blocks, subtitle=f"Năm {year} · theo ngày ký, không tính đơn đã huỷ", icon="chart.bar.fill")


# ── Danh mục dịch vụ cắm vào Khách hàng (0110) — module THỨ HAI của gói, KHAI RIÊNG (``register_lines``): công ty thế vai mặc định không có danh mục
# (mã thứ khách quan tâm là chữ người nhập — các test khác dựa vào đó); test về điểm cắm tự khai thêm trong file của mình, sổ đăng ký trả về sau file. ──

LINES_MODULE = "orderlines"


class LineCode(Input):
    code: str


class LeadRef(Input):
    lead_id: UUID = field("Lead", hidden=True)


async def plug_offering(ctx: Ctx, inp: LineCode) -> dict[str, Any] | None:
    """Mọi đường nhập của Khách hàng tra thứ khách quan tâm qua đây: MÃ dòng dịch vụ hoặc TÊN (không phân biệt hoa thường). Không khớp ⇒ None
    (Khách hàng báo)."""
    key = inp.code.strip().casefold()
    code = next((c for c, name in LINES.items() if key in (c.casefold(), name.casefold())), None)
    return {"id": uuid5(NAMESPACE_URL, f"orderdesk.line:{code}"), "code": code, "name": LINES[code]} if code else None


async def plug_offering_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return [{"value": c, "label": name} for c, name in LINES.items()]


async def plug_lead_story(ctx: Ctx, inp: LeadRef) -> dict[str, Any]:
    """Phần gói góp lên hành trình khách — chạy bằng quyền NGƯỜI XEM (``resource=``): mốc "Mở đơn", lối mở danh sách đơn, nút mở đơn."""
    return {"steps": [{"label": "Mở đơn dịch vụ", "at": None, "note": "", "owner": ""}],
            "open": [{"title": "Đơn dịch vụ", "query": "orderdesk.list", "input": {}}],
            "actions": [ui.act("orderdesk.create", "Mở đơn", {"line": "TV"})]}


class BrochureAsk(Input):
    """Hỏi của ``compose.brochure`` (lõi, ADR 0224) — gói thế vai góp hai loại tờ để test lõi chạy đủ đường mà không cần gói ngành thật."""
    op: str = field("Việc", "kinds", hidden=True)
    kind: str = field("Loại", "", hidden=True)
    subject: str = field("Giới thiệu gì", "", hidden=True)
    lead_id: UUID | None = field("Khách", None, hidden=True)


async def plug_brochure(ctx: Ctx, inp: BrochureAsk) -> dict[str, Any] | None:
    if inp.op == "kinds":
        return {"kinds": [bro.kind("demo_orders", "Tờ giới thiệu dịch vụ (thế vai)", "Đơn của bạn + ảnh / pháp lý theo phạm vi DEMO"),
                          bro.kind("demo_lead", "Tờ riêng cho khách (thế vai)", "Lời chào đúng tên khách bạn được xem", customer=True),
                          bro.kind("demo_order", "Tờ rơi một đơn (thế vai)", "Một đơn bạn được xem", subject=ORDER, piece="flyer", customer=True)]}
    if inp.op == "subjects":   # hồ sơ người soạn chọn được cho công thức có hồ sơ — RLS của người soạn (plug chạy bằng quyền họ)
        if inp.kind != "demo_order":
            return None
        rows = await ctx.all("SELECT id, code, title FROM orderdesk.orders WHERE %s = '' OR title ILIKE %s OR code ILIKE %s ORDER BY code",
                             (inp.subject, f"%{inp.subject}%", f"%{inp.subject}%"))
        return {"subjects": [{"value": str(r["id"]), "label": f"{r['code']} · {r['title']}"} for r in rows]}
    tail = [{**bro.images("DEMO", limit=1, slot="cover"), "need": "Cần ảnh thử"}, bro.images("DEMO", limit=4, skip=1, slot="grid"), bro.heading("Pháp lý dự án", level=1),
            bro.excerpts("legal", "DEMO", limit=1), bro.paragraph("Cảm ơn quý khách đã quan tâm", slot="footer")]
    if inp.kind == "demo_orders":
        return {"recipe": bro.recipe("Giới thiệu dịch vụ", [bro.paragraph("Dịch vụ đang triển khai", slot="cover"), bro.heading("Đơn gần đây", level=1),
                                                             bro.data("orderdesk.list", "Đơn của bạn", {}), *tail], brief="Giới thiệu dịch vụ từ đơn của tôi")}
    if inp.kind == "demo_order":
        o = await ctx.one("SELECT id, code, title FROM orderdesk.orders WHERE id = %s", (UUID(inp.subject),))   # RLS người soạn: không thấy đơn ⇒ dừng
        if o is None:
            raise NotFound("Không thấy đơn")
        hello = []
        if inp.lead_id:
            lead = await ctx.call_as("crm.lead_need", ctx.p, lead_id=inp.lead_id)
            hello = [bro.paragraph(f"Dành riêng cho anh/chị {lead['name']}", slot="cover")]
        return {"recipe": bro.recipe("Dịch vụ dành cho quý khách", [*hello, bro.heading("Đơn gần đây", level=1), bro.data("orderdesk.list", "Đơn của bạn", {}),
                                                                    bro.paragraph("Liên hệ ngay để được tư vấn", slot="footer")],
                                     brief="Tờ rơi một đơn", template="editorial", subject_label=f"{o['code']} · {o['title']}")}
    if inp.kind == "demo_lead":
        lead = await ctx.call_as("crm.lead_need", ctx.p, lead_id=inp.lead_id)   # RLS của người soạn: không thấy khách ⇒ NotFound
        return {"recipe": bro.recipe(f"Tờ dành cho {lead['name']}", [bro.paragraph(f"Dành riêng cho anh/chị {lead['name']}", slot="cover"),
                                                                     bro.data("orderdesk.list", "Đơn của bạn", {}), *tail], brief="Tờ riêng cho khách")}
    return None


def register_lines() -> Module:
    """Khai module danh mục của gói thế vai (gọi SAU ``register()``, trong lúc fixture ``standin`` còn giữ gói): ba điểm cắm của Khách hàng."""
    m = Module(LINES_MODULE, "Danh mục dịch vụ", pack=PACK)
    m.plug("crm.offering", "Tra dòng dịch vụ theo mã cho Khách hàng")(plug_offering)
    m.plug("crm.offering_options", "Danh sách dòng dịch vụ cho Khách hàng", resource="orderdesk")(plug_offering_options)
    m.plug("crm.lead_story", "Đơn dịch vụ trên hành trình khách", resource="orderdesk")(plug_lead_story)
    return m


def register() -> Module:
    """Khai gói thế vai vào sổ đăng ký — đúng thứ tự một module đầu của gói ngành: ``Pack`` → ``Module`` → ``consumes`` → tài khoản → thao tác."""
    Pack(PACK, "Đơn dịch vụ (gói thế vai của test)", "Gói ngành giả — ví dụ của lõi cho mọi cơ chế gói ngành dùng", kind="industry",
         requires=("finance",), rules="- ĐƠN DỊCH VỤ: đơn huỷ phải qua duyệt.\n",
         grants=(("admin", "orderdesk", "all", "read write manage"), ("director", "orderdesk", "all", "read"),
                 ("accountant", "orderdesk", "all", "read"), ("team_lead", "orderdesk", "team", "read write"), ("sale", "orderdesk", "own", "read write")),
         chains=(("order_cancel", "Huỷ đơn dịch vụ", ({"kind": "role", "role": "director"},)),),
         approval_rules=(("orderdesk.cancel", "order_cancel"),),
         flows=(Flow(FLOW_KEY, "Hồ sơ đơn dịch vụ (giả định)", ORDER, True, ORDER_FLOW, follow=True),),
         vocab={"service_order": "Đơn dịch vụ"}, rank_metric="orderdesk.metric_signed",
         # bộ công cụ trợ lý (0218): gói ngành góp nhóm của mình vào bộ của vai gói khác — sale / trưởng nhóm mang cả nhóm đơn, giám đốc / kế toán vài công cụ
         tools={"sale": ("orderdesk",), "team_lead": ("orderdesk",), "director": ("orderdesk.list", "orderdesk.report_year"), "accountant": ("orderdesk.list",)},
         # câu gợi ý / câu xuyên module gói GÓP cho vai của gói khác (1.10): chỉ hiện khi gói bật và người đó chạy được thao tác; mục thứ ba = số cả công ty
         hints={"sale": Hints(suggest=(("orderdesk.list", "Tôi đang có những đơn dịch vụ nào chưa ký?"),)),
                "director": Hints(suggest=(("orderdesk.report_year", "Năm nay công ty ký bao nhiêu đơn dịch vụ, so với chỉ tiêu thế nào?", "orderdesk"),),
                                  showcase=(("Đơn dịch vụ nào đã ký mà phí chưa về, dòng tiền tháng này ra sao?", ("orderdesk.list", "acct.cashflow")),)),
                "accountant": Hints(suggest=(("orderdesk.report_year", "Tổng giá trị đơn dịch vụ đã ký năm nay là bao nhiêu?", "orderdesk"),))})
    m = Module(MODULE, "Đơn dịch vụ", pack=PACK, resources={"orderdesk": "Đơn dịch vụ"})
    m.consumes("receivable", "settlement", "identity", "follow_up", "paper_draft", "ledger_ref")
    m.consumes("company_figures")   # số gộp toàn công ty cho trang báo cáo ``min_scope="all"`` của gói (chỉ tiêu cả công ty của KPI)
    ledger.declare({REVENUE: ("Doanh thu phí đơn dịch vụ (thử)", "revenue"), RECEIVABLE: ("Phải thu khách đơn dịch vụ (thử)", "asset"),
                    EXPENSE: ("Chi thưởng theo đơn (thử)", "expense"), MECHANISM: ("Làm tròn phí đơn (thử)", "revenue")}, mechanism=(MECHANISM,))
    m.term("service_order", "Đơn", "một đơn khách đặt công ty làm dịch vụ, có lịch thanh toán và phí")
    m.trigger("orderdesk.order_signed", "Ký đơn dịch vụ", {"code": "Mã đơn", "amount_vnd": "Giá trị đơn (đồng)"}, open=("orderdesk.case_file", {"code": "code"}))

    m.command("create", resource="orderdesk", title="Mở [[service_order|l]]", agent="propose", importable=True)(create)
    m.command("sign", resource="orderdesk", title="Ký [[service_order|l]]", agent="propose")(sign)
    m.command("cancel", resource="orderdesk", title="Huỷ [[service_order|l]]", agent="propose")(cancel)
    m.command("pay_stage", resource="orderdesk", title="Ghi khách trả một đợt", agent="propose")(pay_stage)
    m.command("follow_stage", resource="orderdesk", title="Giao việc thu một đợt", agent="propose")(follow_stage)
    m.command("settle", resource="orderdesk", title="Ghi khách trả phần còn lại", agent="propose", basis=settle_basis)(settle)
    m.command("draft_fee_request", resource="papers", title="Soạn đề nghị thanh toán phí đơn", agent="allow", shows=("papers.view", "paper_id"))(draft_fee_request)
    m.query("list", resource="orderdesk", title="[[service_order]]")(list_orders)
    m.query("case_file", resource="orderdesk", title="Hồ sơ [[service_order|l]]")(case_file)
    m.query("report_year", resource="orderdesk", title="Đơn ký theo năm (báo cáo quản trị)", min_scope="all")(report_year)
    m.query("line_options", resource="orderdesk", title="Dòng dịch vụ")(line_options)

    m.subject(ORDER, "Đơn dịch vụ")(subject_order)
    m.checker("stage_paid", "Khách đã trả đủ một đợt", ORDER, "seq=<số đợt>")(stage_paid)
    m.checker("signed", "Hợp đồng dịch vụ đã ký", ORDER)(signed)
    m.checker("fee_paid", "Khách đã trả phí dịch vụ", ORDER)(fee_paid)
    m.work_kind("order_fee", "Thu phí đơn", subject_help="Mã khoản phí đơn (PD-…)", tools=("kb.search", "orderdesk.draft_fee_request"))(work_order_fee)
    m.work_kind("order_stage", "Khách trả tiền đơn", subject_help="Mã đơn#số đợt, vd DH-0001#2", tools=("kb.search",))(work_order_stage)

    m.on("orderdesk.order_signed")(issue_fee)
    m.on("orderdesk.order_signed")(open_fee_work)
    m.on("acct.payment_received", replaces=("orderdesk.wake_on_fee",))(earn_on_fee)   # bên nghe đổi tên: việc còn giao cho tên cũ chạy bằng bên này
    m.on("orderdesk.stage_paid")(wake_on_stage)
    m.on("orderdesk.order_cancelled")(void_fee)
    m.on("orderdesk.order_cancelled")(claw_back)

    m.metric("metric_signed", "Đơn ký", "đơn", resource="orderdesk")(metric_signed)
    m.metric("metric_fee", "Phí của đơn đã ký", "đ", resource="orderdesk")(metric_fee)
    m.search("search_orders", "Đơn dịch vụ", resource="orderdesk", rank=35)(search_orders)
    m.pack_blockers()(pack_blockers)
    m.query("health_report", resource="orderdesk", title="Sức khoẻ dữ liệu đơn dịch vụ", internal=True, agent="deny", callers=("health",),
            why="health._collect (Sức khoẻ dữ liệu — health.overview, min_scope all): tình trạng dữ liệu của module này cả công ty. Trả {rank, issues, "
                "notes, fresh} — câu + số đếm, có thể kèm mã đơn để sửa; không thân dữ liệu nghiệp vụ.")(health_report)
    m.plug("papers.field_catalog", "Ô số liệu đơn cho Soạn thảo")(plug_field_catalog)
    m.plug("papers.subjects_seen", "Đơn người xem đọc được", resource="orderdesk")(plug_subjects_seen)
    m.plug("papers.field_values", "Số liệu một đơn cho Soạn thảo", resource="orderdesk")(plug_field_values)
    m.plug("compose.brochure", "Tờ giới thiệu thế vai cho Soạn với AI", resource="orderdesk")(plug_brochure)

    m.screens("sales", (
        Screen("orders", "[[service_order]]", "shippingbox", "orderdesk.list", tint="indigo", actions=(A("orderdesk.create", style="primary"),),
               row_actions=(A("orderdesk.sign", bind={"code": "code"}, when={"status": ["open"]}, style="primary"),
                            A("orderdesk.cancel", bind={"code": "code"}, when={"status": ["open", "signed"]}, style="destructive")),
               open=Open("orderdesk.case_file", {"code": "code"}, "Hồ sơ đơn")),
    ), rank=35)
    m.dataset(
        "orders", "Đơn dịch vụ", resource="orderdesk", description="Mỗi dòng gốc là một đơn (mở → ký → huỷ).",
        base="""SELECT o.status, o.line, o.amount_vnd, o.fee_vnd, o.signed_on, o.created_on, u.name AS owner, coalesce(tm.name, '') AS team
                FROM orderdesk.orders o JOIN core.users u ON u.id = o.owner_id LEFT JOIN core.teams tm ON tm.id = o.team_id""",
        dims={"line": Dim("Dòng dịch vụ", "t.line", labels=LINES), "status": Dim("Trạng thái", "t.status", labels=STATUS),
              "owner": Dim("Người phụ trách", "t.owner"), "team": Dim("Đội", "t.team"),
              "signed_month": Dim("Tháng ký", "to_char(t.signed_on, 'YYYY-MM')", month=True),
              "created_month": Dim("Tháng mở", "to_char(t.created_on, 'YYYY-MM')", month=True)},
        measures={"count": Measure("Số đơn", "count(*)"), "value": Measure("Giá trị", "sum(t.amount_vnd)", "money"),
                  "fee": Measure("Phí", "sum(t.fee_vnd)", "money")},
        dates={"signed_on": "Ngày ký", "created_on": "Ngày mở"},
        drill=DrillTo("orderdesk.list", dims={"line": "line", "status": "status"}, dates={"signed_on": ("signed_from", "signed_before")},
                      measures={"count": None, "value": "amount_vnd", "fee": "fee_vnd"}, base={"status": ""}),
    )
    return m
