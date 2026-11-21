"""KHOẢN LƯƠNG (có phiên bản) và MỨC TRẢ (hiệu lực theo ngày) — chính sách là dữ liệu của công ty (0150 §5). Đổi = dòng mới, không sửa dòng cũ; trợ lý
không đổi (quyết tiền của người khác)."""

import re
from typing import Any, Literal
from uuid import UUID

from barista.api import Ctx, Day, Denied, Input, Invalid, Money, NoInput, NotFound, field, ledger, money, regulations, ui
from barista.modules.pay import CALCULATORS, COMPONENT_KIND, LAW, RATE_UNITS, m


def law_codes() -> frozenset[str]:
    """Mã quy tắc luật (dòng phiếu mang mã này) — khoản công ty không dùng được. Dẫn từ hợp đồng mã của bộ ``vn-payroll`` (engine + khoản đóng đã phát
    hành), không giữ danh sách thứ hai."""
    return regulations.known_codes(LAW)


def _need_policy(ctx: Ctx) -> None:
    if not ctx.p.has_scope("pay.policy", "all"):
        raise Denied("Cần quyền khoản lương & mức trả cả công ty")


async def components_now(ctx: Ctx) -> dict[str, dict[str, Any]]:
    """Phiên bản mới nhất của mọi khoản (kể cả đã ngừng — bên gọi lọc)."""
    rows = await ctx.all("SELECT DISTINCT ON (code) * FROM pay.components ORDER BY code,version DESC")
    return {r["code"]: r for r in rows}


class UpsertComponent(Input):
    code: str = field("Mã khoản", max_length=30, example="TRA_THEO_CONG", help="Chữ HOA không dấu, số, gạch dưới")
    label: str = field("Tên trên phiếu", max_length=120, example="Thù lao theo công")
    kind: Literal["earning", "deduction"] = field("Loại", "earning", options=COMPONENT_KIND)
    calculator: Literal["rate_x_qty", "from_source", "fixed", "prorata", "overtime", "manual", "advance_offset"] = field(
        "Cách tính", "rate_x_qty", options=CALCULATORS)
    taxable: bool = field("Tính vào thu nhập chịu thuế", True)
    expense_account: str = field("Tài khoản chi phí", "expense:payroll", options_query="pay.expense_account_options",
                                 help="Cấn tiền ứng thì để trống")
    source: str = field("Nguồn", "", max_length=40, example="timekeeping",
                        help="Tiền nguồn đã tính: khoá gói nguồn (trống = mọi nguồn chưa có khoản riêng). Cấn ứng: nguồn tiền ứng được cấn")
    active: bool = field("Đang dùng", True)


@m.command("upsert_component",
           purpose="Thêm hoặc sửa khoản lương để phiếu lương có đúng các khoản thu nhập và khấu trừ của công ty.", reversible="Lưu lại phiên bản mới với giá trị cũ (bản cũ giữ trong lịch sử)",
           resource="pay.policy", action="manage", title="Thêm / sửa khoản lương", agent="deny", risk="money")
async def upsert_component(ctx: Ctx, inp: UpsertComponent) -> dict[str, Any]:
    _need_policy(ctx)
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,29}", inp.code) or inp.code in law_codes():
        raise Invalid("Mã khoản: chữ HOA không dấu, số, gạch dưới (mã quy tắc luật không dùng được)")
    if not re.fullmatch(r"[a-z_]{0,40}", inp.source):
        raise Invalid("Nguồn là khoá gói chữ thường")
    if inp.calculator in ("rate_x_qty", "from_source", "fixed", "prorata", "overtime") and inp.kind != "earning":
        raise Invalid("Cách tính này là thu nhập")
    if inp.calculator == "advance_offset":
        if inp.kind != "deduction" or not inp.source:
            raise Invalid("Cấn tiền ứng là khấu trừ và cần nguồn tiền ứng")
        account = ""
    else:
        account = inp.expense_account
        if account not in ledger.accounts("expense"):
            raise Invalid("Tài khoản chi phí chưa được khai trong sổ cái")
    old = (await components_now(ctx)).get(inp.code)
    version = (old["version"] + 1) if old else 1
    await ctx.exec("""INSERT INTO pay.components(code,version,label,kind,calculator,taxable,expense_account,source,active,created_by)
                      VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,core.me())""",
                   (inp.code, version, inp.label.strip(), inp.kind, inp.calculator, inp.taxable, account, inp.source, inp.active))
    return {"code": inp.code, "version": version, "summary": f"Khoản {inp.label.strip()} ({inp.code}) phiên bản {version}"}


@m.query("expense_account_options", resource="pay.policy", title="Tài khoản chi phí")
async def expense_account_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return [{"value": code, "label": f"{name} ({code})"} for code, name in sorted(ledger.accounts("expense").items())]


@m.query("components", resource="pay.policy", title="Khoản lương")
async def components(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = list((await components_now(ctx)).values())
    for r in rows:
        r["state"] = "on" if r["active"] else "off"
    return ui.table(
        rows,
        [ui.col("code", "Mã"), ui.col("label", "Tên trên phiếu"), ui.col("kind", "Loại", "badge", labels=COMPONENT_KIND),
         ui.col("calculator", "Cách tính", "badge", labels=CALCULATORS), ui.col("source", "Nguồn"), ui.col("expense_account", "Tài khoản chi phí"),
         ui.col("version", "Phiên bản", "number"), ui.col("state", "Đang dùng", "badge", labels={"on": "Có", "off": "Ngừng"},
                                                           tones={"on": "green", "off": "gray"})],
        title="Khoản lương", empty="Chưa có khoản lương — thêm ít nhất một khoản để lập kỳ",
        note="Đổi khoản = phiên bản mới; phiếu đã tính giữ phiên bản đã dùng",
    )


class SetRate(Input):
    component: str = field("Khoản", options_query="pay.rate_component_options")
    unit: Literal["session", "hour", "shift", "day", "piece", "month"] = field("Đơn vị", "session", options=RATE_UNITS)
    amount_vnd: Money = field("Mức", ge=0, example="300k", help="0 = từ ngày này không trả")
    role_key: str = field("Loại việc / vai", "", max_length=60, options_query="pay.role_options",
                          help="Trống = mức chung của đơn vị; có mã ⇒ chỉ dòng công mang mã đó")
    party_id: UUID | None = field("Riêng cho người", None, kind="select", options_query="parties.options",
                                  help="Trống = mọi người. Mức tháng cố định thì bắt buộc chọn người")
    effective_from: Day = field("Từ ngày", example="01/11/2026")
    note: str = field("Ghi chú", "", max_length=300)


@m.command("set_rate",
           purpose="Đặt mức trả theo tháng hoặc theo công để phiếu lương tính đúng từ ngày hiệu lực.", reversible="Đặt lại mức cũ với ngày hiệu lực mới (mức chỉ thêm, lịch sử giữ nguyên)",
           resource="pay.policy", action="manage", title="Đặt mức trả", agent="deny")
async def set_rate(ctx: Ctx, inp: SetRate) -> dict[str, Any]:
    """Mức chỉ thêm (lịch sử giữ nguyên); áp dòng cụ thể nhất có hiệu lực vào ngày làm: riêng người > đúng loại việc > mức chung."""
    _need_policy(ctx)
    c = (await components_now(ctx)).get(inp.component)
    want = ("fixed", "prorata") if inp.unit == "month" else ("rate_x_qty",)
    if not c or not c["active"] or c["calculator"] not in want:
        raise Invalid("Mức tháng gắn khoản cố định / theo ngày công; mức theo công gắn khoản công × mức (đang dùng)")
    if inp.unit == "month" and inp.party_id is None:
        raise Invalid("Mức tháng cố định là của một người — chọn người")
    role = inp.role_key.strip().lower()
    if not re.fullmatch(r"[a-z0-9_.]{0,60}", role):
        raise Invalid("Mã loại việc: chữ thường không dấu, số, gạch dưới, dấu chấm")
    name = ""
    if inp.party_id is not None:
        seen = {o["value"]: o["label"] for o in await ctx.call("parties.options")}
        if str(inp.party_id) not in seen:
            raise NotFound("Không thấy cá nhân")
        name = seen[str(inp.party_id)]
        who = await ctx.call_system("parties.resolve_for_payment", party_id=inp.party_id)
        if who["user_id"] is not None and who["user_id"] == ctx.p.user_id:
            raise Denied("Không tự đặt mức trả riêng cho chính mình — nhờ người phụ trách khác")
    rid = await ctx.val("""INSERT INTO pay.rates(component,unit,role_key,party_id,amount_vnd,effective_from,note,created_by)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,core.me()) RETURNING id""",
                        (inp.component, inp.unit, role, inp.party_id, inp.amount_vnd, inp.effective_from, inp.note))
    return {"rate_id": rid, "summary": f"{c['label']}: {money(inp.amount_vnd)} / {RATE_UNITS[inp.unit].lower()}"
                                       f"{' · ' + role if role else ''}{' · ' + name if name else ''} từ {inp.effective_from:%d/%m/%Y}"}


@m.query("rate_component_options", resource="pay.policy", title="Khoản có mức")
async def rate_component_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return [{"value": c["code"], "label": f"{c['label']} ({c['code']})"} for c in (await components_now(ctx)).values()
            if c["active"] and c["calculator"] in ("rate_x_qty", "fixed", "prorata")]


@m.query("role_options", resource="pay.policy", title="Loại việc đã có trong sổ công")
async def role_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    rows = await ctx.all("SELECT DISTINCT role_key FROM pay.work_entries WHERE role_key<>'' ORDER BY 1 LIMIT 200")
    return [{"value": "", "label": "Mức chung"}] + [{"value": r["role_key"], "label": r["role_key"]} for r in rows]


@m.query("rates", resource="pay.policy", title="Mức trả")
async def rates(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await ctx.all("SELECT * FROM pay.rates ORDER BY component,unit,role_key,effective_from DESC,created_at DESC LIMIT 2000")
    comps = await components_now(ctx)
    ids = list({r["party_id"] for r in rows if r["party_id"]})
    names = await ctx.call_system("parties.names", party_ids=ids) if ids else {}
    for r in rows:
        r["component_label"] = comps.get(r["component"], {}).get("label", r["component"])
        r["who"] = names.get(str(r["party_id"]), {}).get("name", "") if r["party_id"] else "Mọi người"
    return ui.table(
        rows,
        [ui.col("component_label", "Khoản"), ui.col("unit", "Đơn vị", "badge", labels=RATE_UNITS), ui.col("role_key", "Loại việc / vai"),
         ui.col("who", "Áp cho"), ui.col("amount_vnd", "Mức", "money"), ui.col("effective_from", "Từ ngày", "date"), ui.col("note", "Ghi chú")],
        title="Mức trả", limit=2000, empty="Chưa có mức trả nào",
        note="Mức chỉ thêm. Mỗi dòng công lấy mức cụ thể nhất có hiệu lực vào ngày làm: riêng người > đúng loại việc > mức chung",
    )
