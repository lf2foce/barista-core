"""CHI PHÍ VẬN HÀNH — phiếu chi phí / hoá đơn mua vào (ADR 0170), chung mọi ngành.

* PHIẾU (``acct.bills`` + ``acct.bill_lines``): bên bán (party), ngày chứng từ, pháp nhân, các dòng khoản mục chi (danh mục khoản CHI của công ty — mỗi mục
  một tài khoản chi phí, kể cả tài khoản riêng ``expense:<mã>`` công ty khai), số tiền GỒM thuế, thuế GTGT ghi trên hoá đơn bên bán, chiều chi phí:
  [[offering]] (gói khai qua điểm cắm ``acct.cost_offering``), đội / bộ phận (``core.teams``), đối tượng tập hợp chi phí (``acct.cost_objects`` — dữ liệu
  công ty). Chiều nào ghi mà không có trong danh mục ⇒ từ chối, không tự tạo.
* HAI CÁCH TRẢ: ghi nợ nhà cung cấp ⇒ Nợ chi phí / Có ``payable:<bên bán>`` + một nghĩa vụ ``acct.payables`` (nguồn ``acct.bill``) — chi sau bằng đường đề
  nghị chi sẵn có (``acct.request_payment`` → chuỗi duyệt chi nghĩa vụ → lệnh chi → xác nhận chuyển khoản); đã trả ngay ⇒ Nợ chi phí / Có tiền gửi
  ngân hàng hoặc tiền mặt. Một phiếu = MỘT bút toán (``acct.bill:<id>``), ngày hạch toán = ngày chứng từ, kỳ đã khoá ⇒ từ chối.
* THUẾ GTGT ĐẦU VÀO (lát B của 0165, phần tối thiểu): thuế suất / tiền thuế lưu theo dòng như DỮ LIỆU chứng từ. Ghi vào ``tax:vat_input`` CHỈ khi công tắc
  ``acct.vat_enabled`` bật VÀ quy tắc ``vn-vat:INPUT_CREDIT`` tại ngày chứng từ đã được kế toán xác minh VÀ phiếu đủ điều kiện theo quy tắc đó (số hoá đơn;
  trả tiền mặt từ ngưỡng ⇒ không). Không thì phần thuế NẰM TRONG chi phí và phiếu ghi rõ vì sao (``vat_note``) — quyết định chụp trên phiếu.
* HUỶ (``cancel_expense``): tiền đi ngược ⇒ chuỗi duyệt ``receivable_correction`` (người xin không tự duyệt); bút toán ĐẢO đúng bút toán phiếu; ghi nợ nhà
  cung cấp mà đã có lệnh chi ⇒ không huỷ (huỷ lệnh chi trước / thu hồi từ bên bán là lát sau).
* NHẬP (``importable``): khoá tự nhiên = mã dòng (``ref``) nếu có, không thì bên bán + số chứng từ + pháp nhân; ghi lại cùng khoá cùng nội dung ⇒ không ghi
  lần hai, khác nội dung ⇒ xung đột. Không có cả hai ⇒ chỉ chống lặp theo nội dung dòng của đường nhập lõi.
* BÁO CÁO: danh sách phiếu, tập dữ liệu chi phí theo khoản mục × tháng × chiều (0100), báo cáo KẾT QUẢ KINH DOANH đọc SỔ CÁI theo loại tài khoản
  (``pnl_data`` — hình dạng ghi ở ADR 0170 cho trang báo cáo quản trị).
"""

import json
from collections import defaultdict
from datetime import date
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import Field

from barista.api import Action as A
from barista.api import (
    Conflict,
    Ctx,
    Day,
    Dim,
    Input,
    Invalid,
    Measure,
    Money,
    NoInput,
    NotFound,
    Screen,
    code_from_name,
    company_settings,
    entities,
    field,
    name_key,
    regulations,
    today,
    ui,
)
from barista.kernel import ledger
from barista.modules.acct import G_OUT, G_REPORT, G_SETUP, USE_COST_OBJECTS, m
from barista.modules.acct.common import _vnd, locked_message
from barista.modules.acct.corrections import _day, _need_approval, _need_books, category_of
from barista.modules.acct.settlements import _accounts, _balances, _lock
from barista.modules.acct.vat import LAW, VAT_ON, split

INPUT = "tax:vat_input"
CASH = "cash:on_hand"
BANK = "cash:bank"
ledger.declare({INPUT: ("Thuế GTGT đầu vào được khấu trừ", "asset"), CASH: ("Tiền mặt", "asset")})

MODE = {"paid": "Đã trả ngay", "payable": "Ghi nợ nhà cung cấp"}
PAY_FROM = {"bank": "Chuyển khoản ngân hàng", "cash": "Tiền mặt"}
PAY_ACCOUNT = {"bank": BANK, "cash": CASH}
VAT_RATE = {"": "Không ghi", "0": "0%", "5": "5%", "8": "8%", "10": "10%"}
VAT_TREATMENT = {"none": "Không có thuế", "input": "Khấu trừ đầu vào", "in_cost": "Nằm trong chi phí"}
STATE = {"active": "Hiệu lực", "cancelled": "Đã huỷ"}
CREDIT = "INPUT_CREDIT"


# ── Đối tượng tập hợp chi phí (dữ liệu công ty) ──

class SetCostObject(Input):
    code: str = field("Mã", "", example="CT-01", max_length=40, aliases=("Mã đối tượng", "Mã công trình"), group="Thêm chi tiết",
                      placeholder="Máy đặt từ tên", help="Chữ in hoa / số / . _ -. Để trống khi thêm mới; sửa đối tượng có sẵn thì mã đã điền sẵn")
    label: str = field("Tên", example="Công trình mẫu số 1", max_length=200, aliases=("Tên đối tượng", "Tên công trình"))
    kind: str = field("Nhóm", "", max_length=60, example="Công trình", help="Nhãn nhóm công ty tự đặt (công trình, hợp đồng…) — chỉ để lọc")
    active: bool = field("Đang dùng", True, group="Thêm chi tiết")


@m.command("set_cost_object", resource="acct", action="manage", title="Thêm / sửa đối tượng tập hợp chi phí", importable=True, category="setup",
           agent="propose")
async def set_cost_object(ctx: Ctx, inp: SetCostObject) -> dict[str, Any]:
    """Danh mục đối tượng tập hợp chi phí của CÔNG TY (công trình, hợp đồng, vụ việc…). Phiếu chi phí chỉ nhận mã đang dùng trong danh mục này."""
    import re

    code = inp.code.strip().upper()
    if not code and inp.label.strip():   # 0225: không bắt người dùng nghĩ ra mã — máy đặt theo tên
        code = code_from_name(inp.label, {r["code"] for r in await ctx.all("SELECT code FROM acct.cost_objects")}, limit=40)
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_.-]{0,39}", code):
        raise Invalid("Mã: chữ in hoa không dấu, số, . _ - (vd CT-01)")
    if not inp.label.strip():
        raise Invalid("Cần tên đối tượng")
    await ctx.exec("""INSERT INTO acct.cost_objects (code, label, kind, active, updated_by) VALUES (%s, %s, %s, %s, core.me())
                      ON CONFLICT (tenant_id, code) DO UPDATE SET label = EXCLUDED.label, kind = EXCLUDED.kind, active = EXCLUDED.active,
                        updated_by = core.me(), updated_at = now()""", (code, inp.label.strip(), inp.kind.strip(), inp.active))
    return {"code": code, "summary": f"Đối tượng chi phí {code}: {inp.label.strip()}"}


@m.query("cost_objects", resource="acct", title="Đối tượng tập hợp chi phí", min_scope="all")
async def cost_objects(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await ctx.all("SELECT code AS id, code, label, kind, CASE WHEN active THEN 'on' ELSE 'off' END AS state FROM acct.cost_objects ORDER BY active DESC, code")
    return ui.table(rows, [ui.col("code", "Mã"), ui.col("label", "Tên", width=260), ui.col("kind", "Nhóm"),
                           ui.col("state", "", "badge", labels={"on": "Đang dùng", "off": "Ngừng"}, tones={"off": "gray"})],
                    title="Đối tượng tập hợp chi phí", empty="Chưa có đối tượng nào — thêm (hoặc nhập Excel) công trình / hợp đồng cần theo dõi chi phí")


@m.query("cost_object_options", resource="acct", title="Đối tượng tập hợp chi phí đang dùng")
async def cost_object_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return ui.options(await ctx.all("SELECT code AS value, code || ' — ' || label AS label FROM acct.cost_objects WHERE active ORDER BY code"))


@m.query("offering_options", resource="acct", title="[[offering]] cho phiếu chi phí")
async def offering_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return [o for part in await ctx.plugs("acct.cost_offerings") for o in part]   # gói tắt / không quyền xem danh mục ⇒ trống


# ── Tính (hàm thuần, test thẳng) ──

def line_vat(amount: int, rate: str, vat: int | None) -> tuple[int | None, int]:
    """(thuế suất bps, tiền thuế) của một dòng: tiền thuế ghi trên hoá đơn là căn cứ; chỉ ghi thuế suất ⇒ tách từ số tiền ĐÃ gồm thuế (làm tròn như 0165)."""
    bps = int(rate) * 100 if rate else None
    if vat is not None:
        if vat < 0 or vat >= amount:
            raise Invalid(f"Tiền thuế {_vnd(vat)} phải nhỏ hơn số tiền {_vnd(amount)} (số tiền là số đã gồm thuế)")
        return bps, vat
    return bps, (split(amount, bps, "inclusive")[1] if bps else 0)


def credit_decision(*, enabled: bool, version: Any, doc_no: str, vat: int, total: int, pay_account: str) -> tuple[str, str, list[str]]:
    """Thuế trên phiếu ghi vào thuế đầu vào được khấu trừ hay nằm trong chi phí — (cách xử lý, lý do, mã quy tắc đã xét). Đóng khi không rõ: công tắc tắt,
    không có quy tắc, quy tắc chưa xác minh ⇒ nằm trong chi phí."""
    if vat == 0:
        return "none", "", []
    if not enabled:
        return "in_cost", "Công ty chưa bật thuế GTGT (Quản trị › Cài đặt công ty) — thuế nằm trong chi phí", []
    if version is None or CREDIT not in version.params:
        return "in_cost", "Chưa có quy tắc khấu trừ thuế đầu vào cho ngày chứng từ — thuế nằm trong chi phí", []
    ref = f"{version.ref}:{CREDIT}"
    if regulations.require_verified([ref]):
        return "in_cost", f"Quy tắc khấu trừ {ref} chưa được kế toán xác minh — thuế nằm trong chi phí (runbook 13)", [ref]
    rule = version.params[CREDIT]["value"]
    if rule.get("invoice_required") and not doc_no:
        return "in_cost", "Không có số hoá đơn mua vào — không đủ điều kiện khấu trừ, thuế nằm trong chi phí", [ref]
    if pay_account == CASH and total >= int(rule.get("noncash_from_vnd") or 0):
        return "in_cost", f"Trả tiền mặt từ {_vnd(int(rule['noncash_from_vnd']))} trở lên — không đủ điều kiện khấu trừ, thuế nằm trong chi phí", [ref]
    return "input", "", [ref]


# ── Ghi phiếu chi phí ──

class ExpenseLine(Input):
    category: str = field("Khoản mục chi phí", options_query="acct.expense_category_options", example="marketing")
    amount_vnd: Money = field("Số tiền (gồm thuế)", ge=1, example="11.000.000")
    vat_rate: Literal["", "0", "5", "8", "10"] = field("Thuế suất GTGT", "", options=VAT_RATE)
    vat_vnd: Money | None = field("Tiền thuế GTGT", None, help="Như trên hoá đơn bên bán; trống = tách theo thuế suất")
    offering_code: str = field("[[offering]]", "", options_query="acct.offering_options", max_length=60)
    team_code: str = field("Bộ phận", "", options_query="admin.team_options", max_length=30)
    cost_object: str = field("Đối tượng chi phí", "", options_query="acct.cost_object_options", max_length=40)
    memo: str = field("Diễn giải dòng", "", max_length=300)


_LINE_KEYS = ("category", "amount_vnd", "vat_rate", "vat_vnd", "offering_code", "team_code", "cost_object")


class RecordExpense(Input):
    bill_on: Day | None = field("Ngày chứng từ", None, example="05/11/2026", aliases=("Ngày", "Ngày chi", "Ngày hạch toán", "Ngày hoá đơn"),
                                placeholder="Hôm nay", help="Là ngày hạch toán")
    category: str = field("Khoản mục chi phí", "", options_query="acct.expense_category_options", example="marketing",
                          aliases=("Khoản mục", "Mục chi phí", "Loại chi phí", "Khoản mục chi"), help="Mã hoặc tên mục khoản chi (Kế toán › Danh mục)")
    amount_vnd: Money | None = field("Số tiền (gồm thuế)", None, example="11.000.000", aliases=("Số tiền", "Thành tiền", "Số tiền chi", "Tổng tiền"))
    vat_rate: Literal["", "0", "5", "8", "10"] = field("Thuế suất GTGT", "", options=VAT_RATE, aliases=("Thuế suất", "% VAT"), group="Thuế & chứng từ")
    vat_vnd: Money | None = field("Tiền thuế GTGT", None, aliases=("Tiền thuế", "Thuế GTGT", "VAT"), help="Như trên hoá đơn bên bán; trống = tách theo thuế suất", group="Thuế & chứng từ")
    offering_code: str = field("[[offering]]", "", options_query="acct.offering_options", max_length=60, aliases=("Dự án", "Mã dự án"), group="Phân bổ chi phí")
    team_code: str = field("Bộ phận", "", options_query="admin.team_options", max_length=30, aliases=("Phòng ban", "Đội", "Mã bộ phận"), group="Phân bổ chi phí")
    cost_object: str = field("Đối tượng chi phí", "", options_query="acct.cost_object_options", max_length=40,
                             aliases=("Đối tượng tập hợp chi phí", "Công trình", "Mã công trình", "Mã đối tượng"), group="Phân bổ chi phí")
    supplier: str = field("Bên bán", "", options_query="parties.options", max_length=200, aliases=("Nhà cung cấp", "Người bán", "Đơn vị bán"),
                          help="Chọn trong danh mục hoặc ghi tên — chưa có thì tạo; ghi nợ nhà cung cấp thì bắt buộc")
    supplier_kind: Literal["organization", "person"] = field("Bên bán là", "organization", options={"organization": "Tổ chức", "person": "Cá nhân"}, group="Thuế & chứng từ")
    supplier_tax_code: str = field("Mã số thuế bên bán", "", max_length=20, aliases=("MST", "Mã số thuế"), group="Thuế & chứng từ")
    doc_no: str = field("Số hoá đơn / chứng từ", "", max_length=60, aliases=("Số hoá đơn", "Số HĐ", "Số chứng từ"),
                        help="Cùng bên bán + số + pháp nhân ghi lại thì không ghi lần hai", group="Thuế & chứng từ")
    ref: str = field("Mã dòng", "", max_length=80, aliases=("Mã tham chiếu", "Mã phiếu"),
                     help="Mã riêng của dòng trong file nguồn — nhập lại cùng mã thì không ghi lần hai", group="Thuế & chứng từ")
    memo: str = field("Diễn giải", "", max_length=500, aliases=("Nội dung", "Diễn giải"))
    mode: Literal["paid", "payable"] = field("Thanh toán", "paid", options=MODE, aliases=("Hình thức",))
    paid_from: Literal["bank", "cash"] = field("Trả từ", "bank", options=PAY_FROM, help="Chỉ khi đã trả ngay", group="Cách trả")
    bank_account: str = field("Tài khoản ngân hàng", "", options_query="acct.bank_account_options", max_length=40,
                              help="Đã chuyển khoản từ tài khoản nào — để đối chiếu với tiền ra của sao kê", group="Cách trả")
    bank_ref: str = field("Mã giao dịch ngân hàng", "", max_length=100, group="Cách trả")
    due_on: Day | None = field("Hạn trả", None, help="Chỉ cần khi ghi nợ nhà cung cấp", group="Cách trả")
    entity_code: str = field("Pháp nhân", "", options_query="admin.entity_options", aliases=("Công ty",), placeholder="Theo tài khoản ngân hàng / mặc định", group="Phân bổ chi phí")
    lines: list[ExpenseLine] = Field(default_factory=list, title="Chia nhiều khoản mục", description="Một hoá đơn có nhiều khoản mục: mỗi dòng một khoản; để trống = một dòng theo các ô trên",
                                     json_schema_extra={"ui": {"group": "Chia nhiều khoản mục"}})


async def _offering(ctx: Ctx, code: str) -> str:
    if not code.strip():
        return ""
    if hits := await ctx.plugs("acct.cost_offering", system=True, code=code.strip()):
        return str(hits[0]["code"])
    if not await ctx.plugged("acct.cost_offering"):
        raise Invalid("Công ty chưa bật gói nào khai [[offering]] — để trống ô [[offering]] hoặc dùng Đối tượng chi phí")
    raise Invalid(f"Không có [[offering]] mã “{code.strip()}”")


async def _team(ctx: Ctx, code: str) -> dict[str, Any] | None:
    if not code.strip():
        return None
    t = await ctx.one("SELECT id, code, name FROM core.teams WHERE upper(code) = upper(%s)", (code.strip(),))
    if t is None:
        raise Invalid(f"Không có đội / bộ phận mã “{code.strip()}” (Quản trị › Đội)")
    return t


async def _cost_object(ctx: Ctx, code: str) -> str:
    if not code.strip():
        return ""
    c = await ctx.one("SELECT code, active FROM acct.cost_objects WHERE code = upper(%s)", (code.strip(),))
    if c is None or not c["active"]:
        raise Invalid(f"Không có đối tượng chi phí đang dùng mã “{code.strip()}” (Kế toán › Đối tượng tập hợp chi phí)")
    return str(c["code"])


async def _supplier(ctx: Ctx, inp: RecordExpense) -> dict[str, Any] | None:
    """Bên bán: mã chọn trong danh mục (kiểm bằng QUYỀN NGƯỜI GHI), tên đã ghi ở phiếu trước (dùng lại đúng bên đó), hoặc tìm / tạo qua danh mục."""
    raw = inp.supplier.strip()
    if not raw:
        return None
    try:
        pid: UUID | None = UUID(raw)
    except ValueError:
        pid = None
    if pid is None:
        key = f"{name_key(raw)}:{inp.supplier_kind}"
        if not inp.supplier_tax_code.strip() and (old := await ctx.val(
                "SELECT supplier_id FROM acct.bills WHERE supplier_key = %s AND supplier_id IS NOT NULL ORDER BY created_at LIMIT 1", (key,))):
            pid = old
        else:
            pid = (await ctx.call_system("parties.ensure_supplier", name=raw, kind=inp.supplier_kind, tax_code=inp.supplier_tax_code))["party_id"]
        party = await ctx.call_system("parties.resolve_for_payment", party_id=pid)
        return {**party, "key": key}
    party = await ctx.call_as("parties.resolve_for_payment", ctx.p, party_id=pid)
    return {**party, "key": f"{name_key(party['display_name'])}:{party['kind']}"}


@m.command("record_expense", resource="acct", title="Ghi phiếu chi phí / hoá đơn mua vào", importable=True, agent="propose", risk="money")
async def record_expense(ctx: Ctx, inp: RecordExpense) -> dict[str, Any]:
    """Một chi phí vận hành của công ty (thuê văn phòng, quảng cáo, điện nước, dịch vụ mua ngoài…): Nợ chi phí theo khoản mục / Có tiền (đã trả ngay) hoặc Có
    phải trả bên bán (ghi nợ — chi sau qua Đề nghị chi ở Phải trả & ứng). Thuế GTGT trên hoá đơn mua vào ghi vào thuế đầu vào chỉ khi công ty bật thuế GTGT
    và quy tắc khấu trừ đã xác minh; không thì nằm trong chi phí (phiếu ghi rõ lý do). Nhập lại cùng mã dòng / cùng số chứng từ của bên bán ⇒ không ghi
    lần hai."""
    return await _record(ctx, inp, None)


async def _record(ctx: Ctx, inp: RecordExpense, transferred_from: UUID | None) -> dict[str, Any]:
    """Thân ``record_expense``. ``transferred_from``: phiếu cũ mà phiếu này THAY THẾ khi chuyển pháp nhân (0183) — chỉ ghi lúc tạo (phiếu chỉ thêm)."""
    _need_books(ctx)
    flat = {k: getattr(inp, k) for k in _LINE_KEYS}
    if inp.lines and any(v not in ("", None) for v in flat.values()):
        raise Invalid("Ghi nhiều dòng thì để trống các ô khoản mục / số tiền / thuế / chiều ở phần đầu phiếu")
    if not inp.lines and (not inp.category.strip() or not inp.amount_vnd or inp.amount_vnd <= 0):
        raise Invalid("Cần khoản mục chi phí và số tiền dương")
    raw_lines = inp.lines or [ExpenseLine(**{k: v for k, v in flat.items() if v is not None}, memo="")]
    on = inp.bill_on or today()
    if on > today():
        raise Invalid(f"Ngày chứng từ {on:%d/%m/%Y} sau hôm nay")
    from barista.modules.acct.bank import bank_account
    account = await bank_account(ctx, inp.bank_account) if inp.mode == "paid" and inp.paid_from == "bank" else None
    if inp.bank_account.strip() and account is None:
        raise Invalid("Tài khoản ngân hàng chỉ ghi khi đã trả ngay bằng chuyển khoản")
    eid = await entities.resolve(ctx, inp.entity_code) if inp.entity_code.strip() or account is None else account["entity_id"]
    if account is not None and eid != account["entity_id"]:
        raise Invalid(f"Tài khoản {account['code']} thuộc pháp nhân khác pháp nhân ghi trên phiếu")
    if inp.mode == "payable" and not inp.supplier.strip():
        raise Invalid("Ghi nợ nhà cung cấp cần bên bán")

    lines: list[dict[str, Any]] = []
    for i, ln in enumerate(raw_lines, 1):
        cat = await category_of(ctx, ln.category, "out")
        bps, vat = line_vat(ln.amount_vnd, ln.vat_rate, ln.vat_vnd)
        team = await _team(ctx, ln.team_code)
        lines.append({"no": i, "category": cat["code"], "label": cat["label"], "account": cat["account"], "amount": ln.amount_vnd, "bps": bps, "vat": vat,
                      "offering": await _offering(ctx, ln.offering_code), "team_id": team["id"] if team else None, "team": team["code"] if team else "",
                      "cost_object": await _cost_object(ctx, ln.cost_object), "memo": ln.memo.strip()})
    total, vat_total = sum(x["amount"] for x in lines), sum(x["vat"] for x in lines)
    doc_no, ref = inp.doc_no.strip().upper(), inp.ref.strip()
    party = await _supplier(ctx, inp)
    pay_account = PAY_ACCOUNT[inp.paid_from] if inp.mode == "paid" else ""
    natural = f"ref:{ref}" if ref else f"doc:{eid}:{party['id'] if party else '-'}:{doc_no}" if doc_no else None
    fingerprint = json.dumps({"entity": str(eid), "supplier": str(party["id"]) if party else "", "on": on.isoformat(), "mode": inp.mode, "pay": pay_account,
                              "doc": doc_no, "lines": [[x["category"], x["amount"], x["vat"], x["bps"], x["offering"], x["team"], x["cost_object"]] for x in lines]},
                             sort_keys=True, ensure_ascii=False)
    if natural and (old := await ctx.one("SELECT id, fingerprint, journal_id, total_vnd, cancelled_on FROM acct.bills WHERE natural_key = %s", (natural,))):
        if old["fingerprint"] != fingerprint:
            raise Conflict(f"IDEMPOTENCY_CONFLICT: phiếu {'mã ' + ref if ref else 'số ' + doc_no} đã ghi với nội dung khác — huỷ phiếu cũ rồi ghi lại bằng mã khác")
        return {"bill_id": old["id"], "total_vnd": old["total_vnd"], "created": False, "summary": f"Phiếu {'mã ' + ref if ref else 'số ' + doc_no} đã ghi trước đó"}
    if msg := await locked_message(ctx, eid, on):
        raise Invalid(msg)

    enabled = await company_settings.enabled(ctx.conn, ctx.p.tenant_id, VAT_ON)
    treatment, note, refs = credit_decision(enabled=enabled, version=regulations.version_on(LAW, on), doc_no=doc_no, vat=vat_total, total=total,
                                            pay_account=pay_account)
    for x in lines:
        x["input"] = x["vat"] if treatment == "input" else 0
        x["expense"] = x["amount"] - x["input"]
        if x["account"] not in ledger.ACCOUNTS:   # tài khoản chi phí riêng của công ty (0170) — tạo ở lần ghi đầu, tên = tên mục
            await ledger.ensure_account(ctx, x["account"], x["label"], "expense")
    who = party["display_name"] if party else ""
    label = (inp.memo.strip() or " · ".join(dict.fromkeys(x["label"] for x in lines)) + (f" — {who}" if who else ""))[:300]
    bid = uuid4()
    debit: dict[str, int] = defaultdict(int)
    for x in lines:
        debit[x["account"]] += x["expense"]
        debit[INPUT] += x["input"]
    payable_id = None
    if inp.mode == "payable":
        assert party is not None
        await _lock(ctx, party["id"], eid)
        credit, _, _ = await _accounts(ctx, party)
    else:
        credit = pay_account
    jid = (await ledger.post(ctx, key=f"acct.bill:{bid}", memo=f"Chi phí: {label}"[:300], source="acct.bill", ref=doc_no or ref, entity=eid, on=on,
                            lines=[*debit.items(), (credit, -total)])).journal_id
    if inp.mode == "payable":
        assert party is not None
        from psycopg.types.json import Jsonb
        payable_id = await ctx.val(
            """INSERT INTO acct.payables (party_id, owner_id, entity_id, source, source_ref, label, amount_vnd, expense_account, payload, journal_id)
               VALUES (%s, %s, %s, 'acct.bill', %s, %s, %s, %s, %s, %s) RETURNING id""",
            (party["id"], party["user_id"], eid, str(bid), label, total, lines[0]["account"], Jsonb({"bill_id": str(bid), "doc_no": doc_no}), jid))
    await ctx.exec(
        """INSERT INTO acct.bills (id, entity_id, supplier_id, supplier_name, supplier_key, doc_no, ref, natural_key, fingerprint, bill_on, mode, pay_account,
                                   bank_account_id, bank_ref, due_on, memo, total_vnd, vat_vnd, input_vnd, vat_treatment, vat_note, rule_refs, payable_id,
                                   journal_id, transferred_from_id, created_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, core.me())""",
        (bid, eid, party["id"] if party else None, who, party["key"] if party else "", doc_no, ref, natural, fingerprint, on, inp.mode, pay_account,
         account["id"] if account else None, inp.bank_ref.strip(), inp.due_on if inp.mode == "payable" else None, label, total, vat_total,
         sum(x["input"] for x in lines), treatment, note, refs, payable_id, jid, transferred_from))
    async with ctx.conn.cursor() as cur:
        await cur.executemany(
            """INSERT INTO acct.bill_lines (bill_id, line_no, category, account, amount_vnd, vat_rate_bps, vat_vnd, input_vnd, expense_vnd, offering_code,
                                            team_id, cost_object, memo) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            [(bid, x["no"], x["category"], x["account"], x["amount"], x["bps"], x["vat"], x["input"], x["expense"], x["offering"], x["team_id"],
              x["cost_object"], x["memo"]) for x in lines])
    return {"bill_id": bid, "journal_id": jid, "payable_id": payable_id, "total_vnd": total, "vat_vnd": vat_total, "input_vnd": sum(x["input"] for x in lines),
            "vat_treatment": treatment, "vat_note": note, "created": True,
            "summary": f"Chi phí {_vnd(total)}: {label}" + (" — ghi nợ bên bán, chi ở Phải trả & ứng" if payable_id else "")
            + (f" · {note}" if note else "")}


# ── Huỷ phiếu: bút toán đảo, qua chuỗi duyệt (tiền đi ngược) ──

class CancelExpense(Input):
    bill_id: UUID = field("Phiếu chi phí", hidden=True)
    reason: str = field("Lý do", max_length=500, example="Ghi trùng phiếu — chứng từ đã ghi ở dòng khác")
    cancelled_on: Day | None = field("Ngày huỷ", None, placeholder="Hôm nay", help="Không trước ngày chứng từ")


@m.command("cancel_expense", resource="acct", title="Huỷ phiếu chi phí", agent="propose", risk="money")
async def cancel_expense(ctx: Ctx, inp: CancelExpense) -> dict[str, Any]:
    """Phiếu ghi sai / ghi trùng ⇒ bút toán ĐẢO đúng bút toán của phiếu (chi phí, thuế đầu vào, tiền / phải trả), phiếu giữ nguyên và ghi ngày + lý do huỷ.
    Ghi nợ nhà cung cấp mà đã có lệnh chi (kể cả đang chờ chuyển) ⇒ không huỷ được ở đây."""
    _need_books(ctx)
    _need_approval(ctx)
    if not inp.reason.strip():
        raise Invalid("Cần lý do huỷ")
    b = await ctx.one("SELECT * FROM acct.bills WHERE id = %s FOR UPDATE", (inp.bill_id,))
    if b is None:
        raise NotFound("Không thấy phiếu chi phí")
    if b["cancelled_on"] is not None:
        return {"bill_id": b["id"], "created": False, "summary": "Phiếu đã huỷ trước đó"}
    on = _day(inp.cancelled_on, b["bill_on"], "huỷ")
    if msg := await locked_message(ctx, b["entity_id"], on):
        raise Invalid(msg)
    if b["payable_id"] is not None:
        p = await ctx.one("SELECT * FROM acct.payables WHERE id = %s", (b["payable_id"],))
        assert p is not None
        await _lock(ctx, p["party_id"], p["entity_id"])
        if await ctx.val("""SELECT 1 FROM acct.payout_allocations a JOIN acct.payouts x ON x.id = a.payout_id
                            WHERE a.payable_id = %s AND x.status <> 'cancelled' LIMIT 1""", (p["id"],)):
            raise Invalid("Phiếu đã có lệnh chi cho bên bán — huỷ lệnh chi chưa chuyển trước; đã chuyển thì cần thu hồi từ bên bán (chưa làm — ADR 0170)")
    undo = await ledger.reverse(ctx, original_key=f"acct.bill:{b['id']}", key=f"acct.bill_cancel:{b['id']}",
                                memo=f"Huỷ phiếu chi phí: {inp.reason.strip()}"[:300], source="acct.bill_cancel", on=on)
    if undo is None:
        raise NotFound("Không thấy bút toán của phiếu")
    cj = undo.journal_id
    if b["payable_id"] is not None:
        await ctx.exec("INSERT INTO acct.payable_adjustments (payable_id, amount_vnd, action_key, reason, journal_id) VALUES (%s, %s, %s, %s, %s)",
                       (b["payable_id"], b["total_vnd"], f"cancel:{b['id']}", inp.reason.strip(), cj))
    await ctx.exec("UPDATE acct.bills SET cancelled_on = %s, cancelled_by = core.me(), cancel_reason = %s, cancel_journal_id = %s WHERE id = %s",
                   (on, inp.reason.strip(), cj, b["id"]))
    return {"bill_id": b["id"], "journal_id": cj, "created": True, "summary": f"Huỷ phiếu chi phí {_vnd(b['total_vnd'])}: {b['memo']}"}


_RATE_OF: dict[int, Literal["", "0", "5", "8", "10"]] = {0: "0", 500: "5", 800: "8", 1000: "10"}


# ── Chuyển pháp nhân (0183): đảo ở pháp nhân cũ + ghi lại ở pháp nhân mới; không bao giờ UPDATE entity_id của tiền đã ghi ──

class TransferExpense(Input):
    bill_id: UUID = field("Phiếu chi phí", hidden=True)
    entity_code: str = field("Pháp nhân mới", options_query="admin.entity_options", aliases=("Pháp nhân", "Công ty"))
    reason: str = field("Lý do chuyển", max_length=200, help="Vì sao ghi sai pháp nhân — ghi vào cả phiếu đảo và phiếu mới")
    on: Day | None = field("Ngày đảo ở pháp nhân cũ", None, placeholder="Hôm nay", help="Không trước ngày chứng từ", group="Thêm chi tiết")
    repost_on: Day | None = field("Ngày ghi ở pháp nhân mới", None, placeholder="Ngày chứng từ gốc", help="Kỳ của pháp nhân mới phải còn mở", group="Thêm chi tiết")


@m.command("transfer_expense_entity", resource="acct", title="Chuyển phiếu chi phí sang pháp nhân khác", agent="propose", risk="money")
async def transfer_expense_entity(ctx: Ctx, inp: TransferExpense) -> dict[str, Any]:
    """Phiếu ghi nhầm pháp nhân ⇒ bút toán ĐẢO ở pháp nhân cũ (phiếu cũ giữ nguyên, ghi huỷ + lý do) rồi GHI LẠI cùng nội dung ở pháp nhân mới (phiếu mới trỏ phiếu cũ).
    Tiền đi ngược ⇒ chuỗi duyệt ``receivable_correction``. Không chuyển được: phiếu đã huỷ; phiếu trả từ tài khoản ngân hàng của pháp nhân cũ (tiền đã ra khỏi tài khoản đó);
    ghi nợ nhà cung cấp mà đã có lệnh chi. Làm lại cùng phiếu ⇒ không ghi lần hai."""
    _need_books(ctx)
    _need_approval(ctx)
    reason = inp.reason.strip()
    if not reason:
        raise Invalid("Cần lý do chuyển pháp nhân")
    b = await ctx.one("SELECT * FROM acct.bills WHERE id = %s FOR UPDATE", (inp.bill_id,))
    if b is None:
        raise NotFound("Không thấy phiếu chi phí")
    if done := await ctx.one("SELECT id FROM acct.bills WHERE transferred_from_id = %s AND cancelled_on IS NULL", (b["id"],)):
        return {"bill_id": done["id"], "from_bill_id": b["id"], "created": False, "summary": "Phiếu đã chuyển pháp nhân trước đó"}
    if b["cancelled_on"] is not None:
        raise Invalid("Phiếu đã huỷ — không chuyển pháp nhân")
    new_eid = await entities.resolve(ctx, inp.entity_code)
    if new_eid == b["entity_id"]:
        raise Invalid("Phiếu đã ở pháp nhân này")
    if b["bank_account_id"] is not None:
        raise Invalid("Phiếu trả từ tài khoản ngân hàng của pháp nhân hiện tại — tiền đã ra khỏi tài khoản đó; đảo qua chuỗi huỷ rồi ghi lại bằng tài khoản của pháp nhân mới")
    lines = await ctx.all("""SELECT l.category, l.amount_vnd, l.vat_rate_bps, l.vat_vnd, l.offering_code, l.cost_object, l.memo, t.code AS team_code
                             FROM acct.bill_lines l LEFT JOIN core.teams t ON t.id = l.team_id WHERE l.bill_id = %s ORDER BY l.line_no""", (b["id"],))
    note = f"Chuyển pháp nhân: {reason}"
    await cancel_expense(ctx, CancelExpense(bill_id=b["id"], reason=note, cancelled_on=inp.on))   # đảo ở pháp nhân cũ (cùng chuỗi duyệt đã qua)
    again = RecordExpense(
        bill_on=inp.repost_on or b["bill_on"], mode=b["mode"], supplier=str(b["supplier_id"]) if b["supplier_id"] else "",
        paid_from="cash" if b["pay_account"] == CASH else "bank", doc_no=b["doc_no"], ref=f"transfer:{b['id']}", memo=b["memo"], due_on=b["due_on"],
        entity_code=inp.entity_code,
        lines=[ExpenseLine(category=x["category"], amount_vnd=x["amount_vnd"], vat_rate=_RATE_OF.get(x["vat_rate_bps"], ""),
                           vat_vnd=x["vat_vnd"], offering_code=x["offering_code"], team_code=x["team_code"] or "", cost_object=x["cost_object"], memo=x["memo"])
               for x in lines])
    new = await _record(ctx, again, b["id"])
    return {"bill_id": new["bill_id"], "from_bill_id": b["id"], "created": True, "summary": f"Chuyển phiếu {_vnd(b['total_vnd'])} sang pháp nhân {inp.entity_code}: {note}"}


# ── Danh sách phiếu ──

class ExpenseFilter(Input):
    date_from: Day | None = field("Từ ngày", None, placeholder="Đầu tháng này")
    date_to: Day | None = field("Đến ngày", None, placeholder="Hôm nay")
    q: str = field("Tìm", "", max_length=80, help="Bên bán, số chứng từ, mã dòng, diễn giải", primary=True)
    state: Literal["", "active", "cancelled"] = field("Trạng thái", "active", options={"active": "Hiệu lực", "cancelled": "Đã huỷ", "": "Tất cả"},
                                                      primary=True)
    entity_code: str = field("Pháp nhân", "", options_query="admin.entity_options", placeholder="Mọi pháp nhân")


@m.query("expenses", resource="acct", title="Phiếu chi phí", min_scope="all")
async def expenses(ctx: Ctx, inp: ExpenseFilter) -> dict[str, Any]:
    t = today()
    a, b = inp.date_from or t.replace(day=1), inp.date_to or t
    eid = await entities.resolve(ctx, inp.entity_code) if inp.entity_code.strip() else None
    rows = await ctx.all(
        """SELECT b.id, b.bill_on, b.supplier_name, b.doc_no, b.ref, b.memo, b.mode, b.total_vnd, b.vat_vnd, b.input_vnd, b.vat_treatment, b.vat_note,
                  b.payable_id, b.cancelled_on, b.cancel_reason, e.code AS entity,
                  CASE WHEN b.cancelled_on IS NULL THEN 'active' ELSE 'cancelled' END AS state,
                  (SELECT string_agg(DISTINCT l.category, ', ') FROM acct.bill_lines l WHERE l.bill_id = b.id) AS categories,
                  (SELECT string_agg(DISTINCT nullif(concat_ws(' / ', nullif(l.offering_code, ''), nullif(l.cost_object, '')), ''), ', ')
                     FROM acct.bill_lines l WHERE l.bill_id = b.id) AS dims
           FROM acct.bills b JOIN core.legal_entities e ON e.id = b.entity_id
           WHERE b.bill_on BETWEEN %(a)s AND %(b)s AND (%(e)s::uuid IS NULL OR b.entity_id = %(e)s)
             AND (%(s)s = '' OR (%(s)s = 'active') = (b.cancelled_on IS NULL))
             AND (%(q)s = '' OR concat_ws(' ', b.supplier_name, b.doc_no, b.ref, b.memo) ILIKE '%%' || %(q)s || '%%')
           ORDER BY b.bill_on DESC, b.created_at DESC LIMIT 2000""", {"a": a, "b": b, "e": eid, "s": inp.state, "q": inp.q.strip()})
    left = {r["id"]: int(r["left_vnd"]) for r in await _balances(ctx, "p.id = ANY(%s)", ([r["payable_id"] for r in rows if r["payable_id"]],))} \
        if any(r["payable_id"] for r in rows) else {}
    for r in rows:
        r["owed_vnd"] = left.get(r["payable_id"]) if r["payable_id"] else None
    live = [r for r in rows if r["state"] == "active"]
    many = await entities.many(ctx)
    return ui.table(rows, [
        ui.col("bill_on", "Ngày", "date"), ui.col("supplier_name", "Bên bán"), ui.col("memo", "Diễn giải", width=260),
        ui.col("categories", "Khoản mục"), ui.col("total_vnd", "Số tiền", "money"), ui.col("vat_vnd", "Thuế GTGT", "money"),
        ui.col("vat_treatment", "Thuế", "badge", labels=VAT_TREATMENT, tones={"input": "green", "in_cost": "orange"}),
        ui.col("mode", "Thanh toán", "badge", labels=MODE, tones={"payable": "blue"}), ui.col("owed_vnd", "Còn phải trả", "money"),
        ui.col("doc_no", "Số chứng từ"), ui.col("ref", "Mã dòng"), ui.col("dims", "[[offering]] / đối tượng"),
        ui.col("state", "Trạng thái", "badge", labels=STATE, tones={"cancelled": "gray"}), ui.col("vat_note", "Ghi chú thuế", width=240),
        ui.col("cancel_reason", "Lý do huỷ", width=200), *([ui.col("entity", "Pháp nhân")] if many else []),
    ], title=f"Phiếu chi phí {a:%d/%m} – {b:%d/%m/%Y}", limit=2000, kpis=[
        ui.kpi("Tổng chi phí (gồm thuế)", sum(r["total_vnd"] for r in live), "money"), ui.kpi("Số phiếu", len(live)),
        ui.kpi("Thuế đầu vào được khấu trừ", sum(r["input_vnd"] for r in live), "money"),
        ui.kpi("Còn phải trả bên bán", sum(r["owed_vnd"] or 0 for r in live), "money")],
        empty="Chưa có phiếu chi phí trong kỳ — ghi phiếu hoặc nhập Excel (ngày, số tiền, khoản mục, bộ phận, đối tượng chi phí)",
        note="Mỗi phiếu là một bút toán sổ cái (ngày chứng từ). Huỷ = bút toán đảo qua phiếu duyệt. Ghi nợ nhà cung cấp ⇒ chi ở Phải trả & ứng › Đề nghị chi.")


# ── Tập dữ liệu (0100): chi phí theo khoản mục × tháng × chiều — phiếu đã huỷ không tính ──
m.dataset(
    "expenses", "Chi phí vận hành", resource="acct",
    description="Mỗi dòng gốc là một dòng phiếu chi phí còn hiệu lực (phiếu đã huỷ không tính). expense = phần ghi vào chi phí; vat_input = phần thuế đã khấu trừ.",
    base="""SELECT l.category, l.account, l.amount_vnd, l.expense_vnd, l.input_vnd, l.vat_vnd, l.offering_code, coalesce(tm.code, '') AS team, l.cost_object,
                   b.bill_on, b.mode, b.supplier_name AS supplier, e.code AS entity
            FROM acct.bill_lines l JOIN acct.bills b ON b.id = l.bill_id JOIN core.legal_entities e ON e.id = b.entity_id
            LEFT JOIN core.teams tm ON tm.id = l.team_id WHERE b.cancelled_on IS NULL""",
    dims={"category": Dim("Khoản mục", "t.category"), "account": Dim("Tài khoản chi phí", "t.account"),
          "month": Dim("Tháng", "to_char(t.bill_on, 'YYYY-MM')", month=True), "supplier": Dim("Bên bán", "t.supplier"),
          "entity": Dim("Pháp nhân", "t.entity"), "team": Dim("Bộ phận", "t.team"), "offering": Dim("[[offering]]", "t.offering_code"),
          "cost_object": Dim("Đối tượng chi phí", "t.cost_object"), "mode": Dim("Thanh toán", "t.mode", labels=MODE)},
    measures={"expense": Measure("Chi phí (không gồm thuế được khấu trừ)", "sum(t.expense_vnd)", "money"),
              "amount": Measure("Số tiền gồm thuế", "sum(t.amount_vnd)", "money"), "vat_input": Measure("Thuế đầu vào được khấu trừ", "sum(t.input_vnd)", "money"),
              "vat": Measure("Thuế GTGT trên chứng từ", "sum(t.vat_vnd)", "money"), "count": Measure("Số dòng", "count(*)")},
    dates={"bill_on": "Ngày chứng từ"},
)


# ── Kết quả kinh doanh từ SỔ CÁI (doanh thu − chi phí theo tài khoản, theo tháng ngày hạch toán, theo pháp nhân) ──

class PnlIn(Input):
    date_from: Day | None = field("Từ ngày", None, placeholder="Đầu năm")
    date_to: Day | None = field("Đến ngày", None, placeholder="Hôm nay")
    entity_code: str = field("Pháp nhân", "", options_query="admin.entity_options", placeholder="Gộp mọi pháp nhân")


def _months(a: date, b: date) -> list[str]:
    out, y, mo = [], a.year, a.month
    while (y, mo) <= (b.year, b.month):
        out.append(f"{y:04d}-{mo:02d}")
        y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
    return out


def pnl_from(rows: list[dict[str, Any]], months: list[str], entity_codes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Hàm thuần: dòng phát sinh sổ cái (pháp nhân, tháng, tài khoản, loại, số ròng dương = nợ) ⇒ báo cáo. Doanh thu = −phát sinh ròng (bên Có), chi phí =
    phát sinh ròng (bên Nợ); lãi sau vận hành = doanh thu − chi phí. Mọi số dương theo bản chất (giảm doanh thu ⇒ số âm trong tháng đó)."""
    def section(kind: str) -> dict[str, Any]:
        sign = -1 if kind == "revenue" else 1
        acc: dict[str, dict[str, Any]] = {}
        for r in rows:
            if r["kind"] != kind:
                continue
            a = acc.setdefault(r["code"], {"account": r["code"], "name": r["name"], "by_month": dict.fromkeys(months, 0), "total": 0})
            a["by_month"][r["month"]] = a["by_month"].get(r["month"], 0) + sign * int(r["amount_vnd"])
            a["total"] += sign * int(r["amount_vnd"])
        lines = sorted(acc.values(), key=lambda x: (-x["total"], x["account"]))
        return {"lines": lines, "by_month": {mth: sum(x["by_month"].get(mth, 0) for x in lines) for mth in months}, "total": sum(x["total"] for x in lines)}

    rev, exp = section("revenue"), section("expense")
    profit = {mth: rev["by_month"][mth] - exp["by_month"][mth] for mth in months}
    per: dict[str, dict[str, Any]] = {}
    for r in rows:
        e = entity_codes.get(str(r["entity_id"]), {})
        x = per.setdefault(str(r["entity_id"]), {"entity": e.get("code", ""), "name": e.get("name", ""), "revenue": 0, "expenses": 0,
                                                 "operating_profit": 0, "by_month": dict.fromkeys(months, 0)})
        v = -int(r["amount_vnd"]) if r["kind"] == "revenue" else int(r["amount_vnd"])
        x["revenue" if r["kind"] == "revenue" else "expenses"] += v
        x["operating_profit"] += v if r["kind"] == "revenue" else -v
        x["by_month"][r["month"]] = x["by_month"].get(r["month"], 0) + (v if r["kind"] == "revenue" else -v)
    return {"months": months, "revenue": rev, "expenses": exp, "operating_profit": {"by_month": profit, "total": rev["total"] - exp["total"]},
            "by_entity": sorted(per.values(), key=lambda x: x["entity"])}


async def _pnl(ctx: Ctx, inp: PnlIn) -> dict[str, Any]:
    t = today()
    a, b = inp.date_from or date(t.year, 1, 1), inp.date_to or t
    if a > b:
        raise Invalid("Từ ngày sau đến ngày")
    eid = await entities.resolve(ctx, inp.entity_code) if inp.entity_code.strip() else None
    rows = await ledger.month_totals(ctx, a, b, ("revenue", "expense"), eid)
    ents = await entities.names(ctx)
    return {"from": a.isoformat(), "to": b.isoformat(), "entity": ents[str(eid)]["code"] if eid else "", "basis": "ledger",
            **pnl_from(rows, _months(a, b), ents)}


@m.query("pnl_data", resource="ledger", title="Kết quả kinh doanh (số liệu)", min_scope="all")
async def pnl_data(ctx: Ctx, inp: PnlIn) -> dict[str, Any]:
    """Doanh thu, chi phí theo tài khoản và lãi (lỗ) sau vận hành theo THÁNG NGÀY HẠCH TOÁN, gộp hoặc theo pháp nhân — số CHỈ từ sổ cái (tài khoản loại
    doanh thu / chi phí). Hình dạng cố định cho trang báo cáo quản trị (ADR 0170): {from, to, entity, basis, months, revenue: {lines: [{account, name,
    by_month, total}], by_month, total}, expenses: {…}, operating_profit: {by_month, total}, by_entity: [{entity, name, revenue, expenses, operating_profit,
    by_month}]}."""
    return await _pnl(ctx, inp)


@m.query("pnl", resource="ledger", title="Kết quả kinh doanh theo tháng", min_scope="all")
async def pnl(ctx: Ctx, inp: PnlIn) -> dict[str, Any]:
    d = await _pnl(ctx, inp)
    months: list[str] = d["months"]

    def row(rid: str, label: str, by: dict[str, int], total: int, kind: str) -> dict[str, Any]:
        return {"id": rid, "label": label, "kind": kind, **{f"m{mth.replace('-', '_')}": by.get(mth, 0) for mth in months}, "total": total}

    rows = [row(f"rev:{x['account']}", x["name"], x["by_month"], x["total"], "revenue") for x in d["revenue"]["lines"]]
    rows.append(row("revenue", "Tổng doanh thu", d["revenue"]["by_month"], d["revenue"]["total"], "sum"))
    rows += [row(f"exp:{x['account']}", x["name"], x["by_month"], x["total"], "expense") for x in d["expenses"]["lines"]]
    rows.append(row("expenses", "Tổng chi phí", d["expenses"]["by_month"], d["expenses"]["total"], "sum"))
    rows.append(row("profit", "Lãi (lỗ) sau vận hành", d["operating_profit"]["by_month"], d["operating_profit"]["total"], "sum"))
    profit = d["operating_profit"]["total"]
    return ui.table(rows, [
        ui.col("label", "Chỉ tiêu", width=260), ui.col("kind", "", "badge", labels={"revenue": "Doanh thu", "expense": "Chi phí", "sum": "Tổng"},
                                                      tones={"revenue": "green", "expense": "orange", "sum": "blue"}),
        *[ui.col(f"m{mth.replace('-', '_')}", f"{mth[5:]}/{mth[:4]}", "money") for mth in months], ui.col("total", "Cả kỳ", "money"),
    ], title=f"Kết quả kinh doanh {date.fromisoformat(d['from']):%d/%m/%Y} – {date.fromisoformat(d['to']):%d/%m/%Y}" + (f" · {d['entity']}" if d["entity"] else ""),
        kpis=[ui.kpi("Doanh thu", d["revenue"]["total"], "money"), ui.kpi("Chi phí", d["expenses"]["total"], "money"),
              ui.kpi("Lãi (lỗ) sau vận hành", profit, "money", tone="green" if profit >= 0 else "red")],
        note="Số từ sổ cái: tài khoản loại doanh thu / chi phí theo ngày hạch toán (huỷ / đảo nằm ở tháng của bút toán đảo). Chưa gồm thuế TNDN, khấu hao, "
             "phân bổ chi phí trả trước — chỉ những gì đã ghi sổ. Tiền thu hộ, tiền cọc, doanh thu nhận trước chưa ghi nhận không phải doanh thu.")


# ── Hợp đồng chỉ đọc cho gói so sánh với sổ cái (0219 — ngân sách so với thực tế) ──

class LedgerTotalsIn(Input):
    since: date
    until: date                                                   # gồm cả ngày này
    kinds: list[Literal["revenue", "expense"]] = Field(default_factory=lambda: ["revenue", "expense"])
    entity_code: str = ""                                         # trống = mọi pháp nhân (dòng vẫn theo từng pháp nhân)


def attribute(totals: list[dict[str, Any]], parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hàm thuần: phát sinh sổ cái (pháp nhân, tháng, tài khoản — số theo bản chất) + phần GẮN đối tượng chi phí của các bút toán phiếu chi phí ⇒ dòng
    (pháp nhân, tháng, tài khoản, đối tượng). Phần không gắn = tổng sổ cái − phần đã gắn (đối tượng ``""``) ⇒ cộng mọi đối tượng của một (pháp nhân,
    tháng, tài khoản) LUÔN bằng sổ cái. Phần gắn của tài khoản không có trong ``totals`` (lọc loại) bị bỏ."""
    out: dict[tuple[str, str, str, str], int] = defaultdict(int)
    kind: dict[str, str] = {}
    for t in totals:
        k = (t["entity"], t["month"], t["account"])
        kind[t["account"]] = t["kind"]
        out[(*k, "")] += int(t["amount_vnd"])
    for p in parts:
        if p["account"] not in kind or not p["cost_object"]:
            continue
        out[(p["entity"], p["month"], p["account"], p["cost_object"])] += int(p["amount_vnd"])
        out[(p["entity"], p["month"], p["account"], "")] -= int(p["amount_vnd"])
    return [{"entity": e, "month": mth, "account": a, "kind": kind[a], "cost_object": c, "amount_vnd": v}
            for (e, mth, a, c), v in sorted(out.items()) if v or not c]


@m.query("ledger_month_totals", resource="ledger", title="Phát sinh sổ cái theo tháng, tài khoản, đối tượng chi phí", internal=True, agent="deny",
         family="company_figures",
         why="Trang so sánh với sổ cái của gói khai consumes(\"company_figures\") (vd ngân sách so với thực tế — 0219; thao tác gọi khai min_scope=\"all\" "
             "— người xem có quyền cả công ty trên màn đó; bên gọi kiểm, acct không kiểm lại) đọc phát sinh CHỈ từ sổ cái: tài khoản doanh thu / chi phí, "
             "tháng của NGÀY HẠCH TOÁN, theo pháp nhân — cùng định nghĩa với Kết quả kinh doanh (pnl_data). Sổ cái không có cột đối tượng chi phí: phần "
             "chi phí của bút toán phiếu chi phí được chia theo dòng phiếu (đúng số đã ghi sổ, cùng ngày, cùng pháp nhân; huỷ ⇒ trừ ở tháng huỷ); phần còn "
             "lại là đối tượng trống — cộng mọi đối tượng = sổ cái. "
             "Chỉ SỐ GỘP: {basis, from, to, entity, rows: [{entity, month, account, kind, cost_object, amount_vnd}], accounts: {mã: {name, kind}}, "
             "entities: {mã: tên}, cost_objects: {mã: tên}} — không bút toán, không chứng từ, không bên bán.")
async def ledger_month_totals(ctx: Ctx, inp: LedgerTotalsIn) -> dict[str, Any]:
    """Số theo BẢN CHẤT như ``pnl_data``: doanh thu = phát sinh bên Có (dương), chi phí = phát sinh bên Nợ (dương); giảm / đảo ⇒ âm ở tháng của bút toán
    đảo. Kỳ tối đa 400 ngày."""
    if inp.since > inp.until:
        raise Invalid("Từ ngày sau đến ngày")
    if (inp.until - inp.since).days > 400:
        raise Invalid("Kỳ tối đa 400 ngày")
    kinds = tuple(dict.fromkeys(inp.kinds)) or ("revenue", "expense")
    eid = await entities.resolve(ctx, inp.entity_code) if inp.entity_code.strip() else None
    ents = await entities.names(ctx)
    code = {k: v["code"] for k, v in ents.items()}
    raw = await ledger.month_totals(ctx, inp.since, inp.until, kinds, eid)
    totals = [{"entity": code[str(r["entity_id"])], "month": r["month"], "account": r["code"], "kind": r["kind"],
               "amount_vnd": -int(r["amount_vnd"]) if r["kind"] == "revenue" else int(r["amount_vnd"])} for r in raw]
    parts: list[dict[str, Any]] = []
    if "expense" in kinds:
        prm = {"a": inp.since, "b": inp.until, "e": eid}
        parts = await ctx.all(
            """SELECT b.entity_id, to_char(b.bill_on, 'YYYY-MM') AS month, l.account, l.cost_object, sum(l.expense_vnd)::bigint AS amount_vnd
               FROM acct.bill_lines l JOIN acct.bills b ON b.id = l.bill_id
               WHERE l.cost_object <> '' AND b.bill_on BETWEEN %(a)s AND %(b)s AND (%(e)s::uuid IS NULL OR b.entity_id = %(e)s)
               GROUP BY 1, 2, 3, 4
               UNION ALL
               SELECT b.entity_id, to_char(b.cancelled_on, 'YYYY-MM'), l.account, l.cost_object, -sum(l.expense_vnd)::bigint
               FROM acct.bill_lines l JOIN acct.bills b ON b.id = l.bill_id
               WHERE l.cost_object <> '' AND b.cancelled_on BETWEEN %(a)s AND %(b)s AND (%(e)s::uuid IS NULL OR b.entity_id = %(e)s)
               GROUP BY 1, 2, 3, 4""", prm)
        parts = [{**p, "entity": code[str(p["entity_id"])]} for p in parts]
    names = {r["code"]: r["name"] for r in raw}
    objs = {r["code"]: r["label"] for r in await ctx.all("SELECT code, label FROM acct.cost_objects ORDER BY code")}
    rows = attribute(totals, parts)
    return {"basis": "ledger", "from": inp.since.isoformat(), "to": inp.until.isoformat(), "entity": code[str(eid)] if eid else "", "rows": rows,
            "accounts": {r["account"]: {"name": names.get(r["account"], r["account"]), "kind": r["kind"]} for r in rows},
            "entities": {v["code"]: v["name"] for v in ents.values()}, "cost_objects": objs}


class PnlAccountIn(Input):
    value: str


@m.query("pnl_account", resource=None, title="Một tài khoản doanh thu / chi phí", internal=True, agent="deny", family="ledger_ref",
         why="Lệnh nhập của gói khai consumes(\"ledger_ref\") (vd dòng ngân sách — 0219) kiểm chỉ tiêu người nhập ghi: mã tài khoản doanh thu / chi phí "
             "(revenue:… / expense:…) đã khai hoặc đang được một mục khoản thu / chi trỏ tới, hoặc mã / tên một mục khoản thu / chi (⇒ tài khoản của mục). "
             "Trả {account, name, kind} — chỉ danh mục, không số tiền.")
async def pnl_account(ctx: Ctx, inp: PnlAccountIn) -> dict[str, Any]:
    v = inp.value.strip()
    if ":" in v:
        code = v.lower()
        if code in ledger.ACCOUNTS and ledger.ACCOUNTS[code][1] in ("revenue", "expense"):
            return {"account": code, "name": ledger.ACCOUNTS[code][0], "kind": ledger.ACCOUNTS[code][1]}
        own = await ctx.one("SELECT account, label, direction FROM acct.categories WHERE account = %s ORDER BY active DESC, code LIMIT 1", (code,))
        if own is None:
            raise NotFound(f"Tài khoản {v} chưa khai và chưa có mục khoản thu / chi nào trỏ tới (Kế toán › Danh mục khoản thu / chi)")
        return {"account": code, "name": own["label"], "kind": "revenue" if own["direction"] == "in" else "expense"}
    for direction in ("out", "in"):
        try:
            c = await category_of(ctx, v, direction)
        except Invalid:
            continue
        return {"account": c["account"], "name": ledger.ACCOUNTS.get(c["account"], (c["label"], ""))[0] or c["label"],
                "kind": "revenue" if direction == "in" else "expense"}
    raise NotFound(f"“{v}” không phải mục khoản thu / chi hay tài khoản doanh thu / chi phí của công ty (Kế toán › Danh mục khoản thu / chi)")


class CostObjectIn(Input):
    code: str


@m.query("cost_object", resource=None, title="Một đối tượng tập hợp chi phí", internal=True, agent="deny", family="ledger_ref",
         why="Lệnh nhập của gói khai consumes(\"ledger_ref\") (vd dòng ngân sách theo đối tượng chi phí — 0219) kiểm mã người nhập ghi có trong danh mục "
             "đối tượng tập hợp chi phí của công ty. Trả {code, label, kind, active} — chỉ danh mục.")
async def cost_object(ctx: Ctx, inp: CostObjectIn) -> dict[str, Any]:
    c = await ctx.one("SELECT code, label, kind, active FROM acct.cost_objects WHERE code = upper(%s)", (inp.code.strip(),))
    if c is None:
        raise NotFound(f"Không có đối tượng chi phí {inp.code} — thêm ở Kế toán › Đối tượng tập hợp chi phí")
    return dict(c)


# ── Màn (0069) ──
m.screens("accounting", (
    Screen("expenses", "Phiếu chi phí", "cart", "acct.expenses", tint="orange", min_scope="all", group=G_OUT,
           actions=(A("acct.record_expense", "Ghi phiếu chi phí", style="primary"),),
           row_actions=(A("acct.cancel_expense", "Huỷ phiếu", bind={"bill_id": "id"}, when={"state": ["active"]}, style="destructive"),)),
), rank=25)
m.screens("accounting", (
    Screen("pnl", "Kết quả kinh doanh", "chart.bar.doc.horizontal", "acct.pnl", tint="green", min_scope="all", group=G_REPORT),
), rank=70)
m.screens("accounting", (
    Screen("cost_objects", "Đối tượng tập hợp chi phí", "folder", "acct.cost_objects", min_scope="all", group=G_SETUP, feature=USE_COST_OBJECTS,
           actions=(A("acct.set_cost_object", style="primary"),)),
), rank=90)
