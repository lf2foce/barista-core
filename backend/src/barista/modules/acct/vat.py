"""THUẾ GTGT ĐẦU RA — lát A (ADR 0165), chung mọi ngành, luật Việt Nam.

* MỨC THUẾ là LUẬT: bộ quy định ``vn-vat`` theo NGÀY LẬP HOÁ ĐƠN (thuế suất 0 / 5 / 10%, không chịu thuế, không phải kê khai, khoảng ngày giảm 2%,
  cách làm tròn) — code không viết số nào. Quy tắc chưa được kế toán xác minh ⇒ chỉ XEM TRƯỚC được, không ghi sổ (như lương — 0150 §6).
* LOẠI THUẾ là DỮ LIỆU CÔNG TY (``acct.tax_categories``): mỗi loại chọn cách tính theo luật, có được giảm 2% không (danh mục loại trừ do kế toán đối chiếu —
  vd kinh doanh bất động sản không được giảm), số tiền của khoản phải thu là giá ĐÃ hay CHƯA gồm thuế, áp mặc định cho tài khoản doanh thu / nguồn nào.
  Câu hỏi chờ khách (phí môi giới chịu 8% hay 10%, học phí có thuộc diện không chịu thuế) là CHỌN LOẠI, không phải sửa code.
* GHI HOÁ ĐƠN (``issue_vat_invoice``): kế toán xuất hoá đơn ở phần mềm hoá đơn điện tử (đầu nối nhà cung cấp: lát sau) rồi ghi ký hiệu + số ở đây; code
  CHỤP mức + mã quy tắc lên bản ghi, tách thuế, ghi sổ: tổng hợp lại = Nợ phải thu (gồm thuế) / Có doanh thu (chưa thuế) / Có thuế GTGT đầu ra. Giá đã gồm
  thuế ⇒ bút toán chuyển phần thuế từ doanh thu sang thuế phải nộp; giá chưa gồm thuế ⇒ phải thu tăng thêm phần thuế.
* SỬA: huỷ hoá đơn (lập thay thế) = bút toán ĐẢO của bút toán thuế; điều chỉnh GIẢM = hoá đơn điều chỉnh (số riêng) + giảm phải thu qua đúng đường giấy
  báo có (0156) với phần thuế tách theo mức của hoá đơn gốc. Cả hai qua chuỗi duyệt ``receivable_correction`` (người xin không tự duyệt).
* TỔNG HỢP KỲ theo pháp nhân (``vat_summary``): doanh thu + thuế đầu ra theo mức ⇒ số cho tờ khai 01/GTGT. Thuế đầu vào: từ phiếu chi phí đã ghi khấu trừ (ADR 0170, expenses.py).

Công tắc ``acct.vat_enabled`` mặc định TẮT ⇒ công ty chưa bật không đổi gì (bản demo giữ nguyên).
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BeforeValidator

from barista.api import (
    Conflict,
    Ctx,
    Day,
    Input,
    Invalid,
    Money,
    NoInput,
    NotFound,
    company_settings,
    entities,
    field,
    regulations,
    snake_from_name,
    today,
    ui,
)
from barista.kernel import ledger, registry
from barista.modules.acct import _target, _vnd, locked_message, m
from barista.modules.acct.corrections import _day, _need_approval, _need_books, reduce_receivable

LAW = "vn-vat"
OUTPUT = "tax:vat_output"
TREATMENT = {"standard": "Thuế suất phổ thông", "five": "Thuế suất 5%", "zero": "Thuế suất 0%", "exempt": "Không chịu thuế GTGT",
             "not_declared": "Không phải kê khai, tính nộp"}
RULE = {"standard": "RATE_STANDARD", "five": "RATE_FIVE", "zero": "RATE_ZERO", "exempt": "EXEMPT", "not_declared": "NOT_DECLARED"}
PRICE = {"exclusive": "Chưa gồm thuế", "inclusive": "Đã gồm thuế"}
ROUNDING = {"unit_vnd": 1, "mode": "half_up", "per": "line"}   # cách làm tròn code biết — file khai cách khác ⇒ từ chối, không tính sai

# hợp đồng mã với bộ vn-vat (0154): mọi mã đọc theo tên — thiếu một mã ở phiên bản mới nhất là lỗi lúc nạp, không phải "tính thiếu"
regulations.declare(LAW, required=(*RULE.values(), "REDUCTION", "ROUNDING"), optional=("INPUT_CREDIT",))   # INPUT_CREDIT: khấu trừ đầu vào (0170, expenses.py)
ledger.declare({OUTPUT: ("Thuế GTGT đầu ra phải nộp", "liability")})

VAT_ON = m.setting(
    "vat_enabled", "Tính thuế GTGT đầu ra khi ghi hoá đơn", default=False,
    help="Bật: kế toán ghi hoá đơn GTGT đã xuất cho khoản phải thu (Kế toán › Hoá đơn GTGT) — code tách thuế theo loại thuế của công ty và bộ quy định vn-vat "
         "theo ngày lập, ghi sổ thuế GTGT đầu ra, có tổng hợp theo kỳ cho tờ khai 01/GTGT. Cần khai Loại thuế GTGT trước; quy tắc thuế chưa được kế toán "
         "xác minh thì chỉ xem trước được. Bật cũng là công ty khai thuế theo phương pháp khấu trừ: phiếu chi phí có hoá đơn mua vào ghi thuế GTGT đầu vào "
         "khi quy tắc khấu trừ đã xác minh (ADR 0170). Tắt: như trước — chỉ ghi số hoá đơn, không tính thuế; thuế trên phiếu chi phí nằm trong chi phí (ADR 0165).")


# ── Tính (hàm thuần, test thẳng) ──

@dataclass(frozen=True)
class Rate:
    treatment: str
    rate_bps: int | None   # None = không tính thuế (không chịu thuế / không kê khai)
    refs: tuple[str, ...]  # <bộ>@<phiên bản>:<mã> đã áp — chụp lên hoá đơn
    line: str              # chỉ tiêu tờ khai 01/GTGT theo bộ quy định
    reduced: bool
    rounding: dict[str, Any]

    @property
    def label(self) -> str:
        if self.rate_bps is None:
            return TREATMENT[self.treatment]
        pct = f"{self.rate_bps / 100:g}%"
        return f"Thuế suất {pct}" + (" (giảm 2%)" if self.reduced else "")


def rate_on(treatment: str, reducible: bool, day: date) -> Rate:
    """Mức áp cho một loại thuế vào NGÀY LẬP HOÁ ĐƠN: phiên bản vn-vat hiệu lực ngày đó; thuế suất phổ thông + loại "được giảm" + ngày trong một khoảng
    giảm của phiên bản ⇒ mức giảm. Ngày trước phiên bản đầu tiên ⇒ từ chối (không đoán luật cũ)."""
    if treatment not in RULE:
        raise Invalid(f"Cách tính thuế lạ: {treatment}")
    v = regulations.version_on(LAW, day)
    if v is None:
        first = (regulations.packs().get(LAW) or (None,))[0]
        raise Invalid(f"Chưa có quy định thuế GTGT cho ngày {day:%d/%m/%Y}" + (f" (bộ {LAW} bắt đầu từ {first.effective_from:%d/%m/%Y})" if first else ""))
    code = RULE[treatment]
    value = v.params[code]["value"]
    bps: int | None = value["rate_bps"]
    refs, reduced = [f"{v.ref}:{code}"], False
    if treatment == "standard" and reducible:
        refs.append(f"{v.ref}:REDUCTION")   # quyết định "giảm hay không" dựa vào quy tắc này — kể cả khi ngày đã ra khỏi khoảng giảm
        for w in v.params["REDUCTION"]["value"]["windows"]:
            if w["replaces"] == code and date.fromisoformat(w["from"]) <= day <= date.fromisoformat(w["to"]):
                bps, reduced = int(w["rate_bps"]), True
    if bps:
        refs.append(f"{v.ref}:ROUNDING")
    return Rate(treatment, bps, tuple(refs), str(value.get("report_line") or ""), reduced, dict(v.params["ROUNDING"]["value"]))


def _half_up(num: int, den: int) -> int:
    return (2 * num + den) // (2 * den)


def split(amount: int, rate_bps: int | None, price_mode: str, rounding: dict[str, Any] | None = None) -> tuple[int, int, int]:
    """(giá tính thuế, tiền thuế, tổng thanh toán) của MỘT dòng. Chưa gồm thuế: thuế = làm tròn(giá × mức). Đã gồm thuế: giá = làm tròn(tổng / (1 + mức)),
    thuế = tổng − giá (tổng giữ đúng số khách trả). Làm tròn tới đồng, nửa lên — cách khác trong bộ quy định ⇒ từ chối."""
    if rounding is not None and dict(rounding) != ROUNDING:
        raise Invalid(f"Cách làm tròn trong bộ quy định ({rounding}) chưa được code hỗ trợ — cần thêm loại quy tắc (runbook 13)")
    if amount <= 0:
        raise Invalid("Số tiền phải dương")
    if not rate_bps:
        return amount, 0, amount
    if price_mode == "exclusive":
        tax = _half_up(amount * rate_bps, 10_000)
        return amount, tax, amount + tax
    base = _half_up(amount * 10_000, 10_000 + rate_bps)
    return base, amount - base, amount


# ── Loại thuế của công ty ──

def _join_targets(v: Any) -> Any:
    """Ô chọn nhiều tài khoản gửi danh sách; Excel / trợ lý gửi chữ cách nhau dấu phẩy — cùng một giá trị lưu."""
    return ", ".join(str(x) for x in v) if isinstance(v, list | tuple) else v


class SetTaxCategory(Input):
    code: str = field("Mã loại", "", example="phi_dich_vu", max_length=40, group="Thêm chi tiết", placeholder="Máy đặt từ tên loại",
                      help="Chữ thường không dấu, số, gạch dưới. Để trống khi thêm loại mới; sửa loại có sẵn thì mã đã điền sẵn")
    label: str = field("Tên loại", example="Phí dịch vụ tư vấn", max_length=120)
    treatment: Literal["standard", "five", "zero", "exempt", "not_declared"] = field("Cách tính theo luật", options=TREATMENT)
    applies_to: Annotated[str, BeforeValidator(_join_targets)] = field(
        "Áp mặc định cho khoản doanh thu", "", kind="multiselect", aliases=("Áp mặc định cho",), options_query="acct.revenue_account_options", max_length=400,
        example=["revenue:services", "revenue:other_income"],
        help="Ghi hoá đơn không chọn loại thuế thì lấy loại này cho các khoản doanh thu đã chọn")
    reducible: bool = field("Được giảm 2% (10% ⇒ 8%)", False, group="Thêm chi tiết",
                            help="Chỉ thuế suất phổ thông. Kế toán đối chiếu danh mục KHÔNG được giảm của nghị định (vd tài chính, ngân hàng, viễn thông, "
                                 "bảo hiểm…) — code không tự suy ngành")
    price_mode: Literal["exclusive", "inclusive"] = field("Số tiền khoản phải thu là", "exclusive", options=PRICE, group="Thêm chi tiết",
                                                          help="Chưa gồm thuế: thuế cộng thêm vào phải thu. Đã gồm thuế: tách thuế ra khỏi số đó")
    note: str = field("Căn cứ chọn loại", "", max_length=500, group="Thêm chi tiết",
                      example="Kế toán trưởng xác nhận 12/11/2026 — thuộc Phụ lục I NĐ 174/2025")
    active: bool = field("Đang dùng", True, group="Thêm chi tiết")


@m.query("revenue_account_options", resource="acct", title="Tài khoản doanh thu")
async def revenue_account_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    """Các mục khoản thu của công ty (hoặc mặc định) theo tên người đọc; giá trị là tài khoản doanh thu của mục đó."""
    from barista.modules.acct.corrections import categories

    seen: set[str] = set()
    out = []
    for c in (await categories(ctx, "in")).values():
        if c["account"] not in seen:
            seen.add(c["account"])
            out.append({"value": c["account"], "label": c["label"]})
    for r in await ctx.all("SELECT DISTINCT source FROM acct.receivables ORDER BY source"):   # luật cũng nhận nguồn của khoản phải thu (gói.loại)
        mod, _, kind = r["source"].partition(".")
        if r["source"] not in seen and kind:
            seen.add(r["source"])
            out.append({"value": r["source"], "label": f"Nguồn: {registry.MODULES.get(mod, mod)} › {kind}"})
    return out


@m.command("set_tax_category", resource="acct", action="manage", title="Thêm / sửa loại thuế GTGT", importable=True, category="setup", agent="propose")
async def set_tax_category(ctx: Ctx, inp: SetTaxCategory) -> dict[str, Any]:
    """Loại thuế là DỮ LIỆU công ty — mức thuế không ở đây mà ở bộ quy định theo ngày hoá đơn. Sửa loại không đổi hoá đơn đã ghi (mức đã chụp)."""
    import re

    code = inp.code.strip().lower()
    if not code:   # 0225: không bắt người dùng nghĩ ra mã — máy đặt theo tên loại
        code = snake_from_name(inp.label, {r["code"] for r in await ctx.all("SELECT code FROM acct.tax_categories")})
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,39}", code):
        raise Invalid("Mã loại: chữ thường không dấu, số, gạch dưới (vd phi_dich_vu)")
    if inp.reducible and inp.treatment != "standard":
        raise Invalid("Chỉ thuế suất phổ thông mới có khoảng giảm 2%")
    targets = [x.strip() for x in inp.applies_to.split(",") if x.strip()]
    if bad := [x for x in targets if not re.fullmatch(r"(revenue:[a-z_]+|[a-z_]+\.[a-z_]+)", x)]:
        raise Invalid(f"“{bad[0]}” không phải tài khoản doanh thu (revenue:…) hay nguồn (gói.loại)")
    taken = await ctx.all("SELECT code, t FROM acct.tax_categories, unnest(applies_to) t WHERE active AND code <> %s AND t = ANY(%s)", (code, targets))
    if taken and inp.active:
        raise Invalid(f"“{taken[0]['t']}” đã áp mặc định cho loại {taken[0]['code']} — mỗi tài khoản / nguồn một loại mặc định")
    await ctx.exec("""INSERT INTO acct.tax_categories (code, label, treatment, reducible, price_mode, applies_to, note, active, updated_by)
                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s, core.me())
                      ON CONFLICT (tenant_id, code) DO UPDATE SET label = EXCLUDED.label, treatment = EXCLUDED.treatment, reducible = EXCLUDED.reducible,
                        price_mode = EXCLUDED.price_mode, applies_to = EXCLUDED.applies_to, note = EXCLUDED.note, active = EXCLUDED.active,
                        updated_by = core.me(), updated_at = now()""",
                   (code, inp.label.strip(), inp.treatment, inp.reducible, inp.price_mode, targets, inp.note.strip(), inp.active))
    return {"code": code, "summary": f"Loại thuế {code}: {TREATMENT[inp.treatment]}" + (" (được giảm 2%)" if inp.reducible else "")
            + f", số tiền {PRICE[inp.price_mode].lower()}"}


@m.query("tax_category_list", resource="acct", title="Loại thuế GTGT", min_scope="all")
async def tax_category_list(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await ctx.all("""SELECT code AS id, code, label, treatment, reducible, price_mode, array_to_string(applies_to, ', ') AS applies_to, note, active
                            FROM acct.tax_categories ORDER BY active DESC, code""")
    day = today()
    for r in rows:
        try:
            rate = rate_on(r["treatment"], r["reducible"], day)
            r["today"] = rate.label
            r["verified"] = "yes" if not regulations.require_verified(rate.refs) else "no"
        except Invalid as e:
            r["today"], r["verified"] = str(e), "no"
        r["state"] = "on" if r.pop("active") else "off"
    on = await company_settings.enabled(ctx.conn, ctx.p.tenant_id, VAT_ON)
    return ui.table(rows, [
        ui.col("code", "Mã"), ui.col("label", "Tên loại", width=220), ui.col("treatment", "Cách tính", "badge", labels=TREATMENT),
        ui.col("today", "Mức hôm nay"), ui.col("verified", "Quy tắc", "badge", labels={"yes": "Đã xác minh", "no": "Giả định — chưa ghi sổ được"},
                                              tones={"yes": "green", "no": "orange"}),
        ui.col("price_mode", "Số tiền khoản là", "badge", labels=PRICE), ui.col("applies_to", "Áp mặc định cho", width=220),
        ui.col("note", "Căn cứ chọn", width=260), ui.col("state", "", "badge", labels={"on": "Đang dùng", "off": "Ngừng"}, tones={"off": "gray"}),
    ], title="Loại thuế GTGT", empty="Chưa khai loại thuế nào — thêm loại (vd phí dịch vụ 10%, dịch vụ không chịu thuế) rồi mới ghi được hoá đơn GTGT",
        note=("" if on else "Công tắc “Tính thuế GTGT đầu ra” đang TẮT (Quản trị › Cài đặt công ty). ")
             + "Mức thuế theo quy định thuế GTGT có hiệu lực tại ngày lập hoá đơn; loại chỉ chọn cách tính. Được giảm 2% hay không: kế toán đối chiếu danh mục loại trừ.")


@m.query("tax_category_options", resource="acct", title="Loại thuế GTGT đang dùng")
async def tax_category_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return ui.options(await ctx.all("SELECT code AS value, label FROM acct.tax_categories WHERE active ORDER BY label"))


async def _category(ctx: Ctx, code: str, r: dict[str, Any]) -> dict[str, Any]:
    """Loại chọn rõ, hoặc loại công ty áp mặc định cho tài khoản doanh thu / nguồn của khoản (đúng một)."""
    if code.strip():
        got = await ctx.one("SELECT * FROM acct.tax_categories WHERE code = %s AND active", (code.strip().lower(),))
        if got is None:
            raise Invalid(f"Không có loại thuế đang dùng mã “{code.strip()}” (Kế toán › Loại thuế GTGT)")
        return got
    keys = [k for k in (r.get("revenue_account") or "", r["source"]) if k]
    rows = await ctx.all("SELECT * FROM acct.tax_categories WHERE active AND applies_to && %s::text[] ORDER BY code", (keys,))
    if len(rows) != 1:
        raise Invalid(f"Chọn loại thuế cho {r['code']} — " + ("chưa loại nào áp mặc định cho " + " / ".join(keys) if not rows
                                                            else "nhiều loại cùng áp: " + ", ".join(x["code"] for x in rows)))
    return rows[0]


async def _active(ctx: Ctx, receivable_id: UUID, *, lock: bool = False) -> dict[str, Any] | None:
    return await ctx.one("SELECT * FROM acct.vat_invoices WHERE receivable_id = %s AND kind = 'invoice' AND cancelled_on IS NULL"
                         + (" FOR UPDATE" if lock else ""), (receivable_id,))


async def has_active_invoice(ctx: Ctx, receivable_id: UUID) -> bool:
    """Khoản đã có hoá đơn GTGT còn hiệu lực ⇒ mọi đường giảm / huỷ khoản phải đi qua huỷ / điều chỉnh hoá đơn (một thẩm quyền — sổ thuế không lệch)."""
    return await _active(ctx, receivable_id) is not None


async def _plan(ctx: Ctx, r: dict[str, Any], category: str, price_mode: str, on: date) -> dict[str, Any]:
    """Phép tính của một hoá đơn trên khoản ``r`` — dùng chung cho xem trước và ghi thật (cùng một hàm, không hai cách tính)."""
    if r["status"] == "void":
        raise Invalid(f"{r['code']} đã huỷ")
    if r["source"].endswith(".opening") or (r.get("recognized_key") or "").startswith("acct.opening_batch:"):
        raise Invalid(f"{r['code']} là số dư đầu kỳ — hoá đơn đã xuất ở hệ trước, không tính thuế lại")
    if r["nature"] in ("pass_through", "deposit"):
        raise Invalid(f"{r['code']} là tiền thu hộ / cọc — thuế trên phần phí của khoản thu hộ là lát sau (ADR 0165)")
    cat = await _category(ctx, category, r)
    rate = rate_on(cat["treatment"], cat["reducible"], on)
    if r["nature"] == "deferred" and rate.rate_bps is not None:
        raise Invalid(f"{r['code']} là doanh thu nhận trước — hoá đơn có thuế cho khoản này là lát sau (ADR 0165); loại không chịu thuế / không kê khai thì được")
    mode = price_mode or cat["price_mode"]
    base, tax, gross = split(r["amount_vnd"], rate.rate_bps, mode, rate.rounding)
    return {"category": cat["code"], "category_label": cat["label"], "rate": rate, "price_mode": mode, "base": base, "tax": tax, "gross": gross,
            "added": tax if mode == "exclusive" else 0, "unverified": regulations.require_verified(rate.refs)}


def _no(symbol: str, number: str) -> tuple[str, str]:
    import re

    s, n = symbol.strip().upper(), number.strip()
    if not re.fullmatch(r"[0-9A-Z]{1,12}", s):
        raise Invalid("Ký hiệu hoá đơn: chữ in hoa / số, vd 1C26TAA")
    if not re.fullmatch(r"[0-9]{1,12}", n):
        raise Invalid("Số hoá đơn: chỉ chữ số, vd 0000123")
    return s, n


# ── Xem trước (chạy được với quy tắc chưa xác minh — số ghi rõ "giả định") ──

class PreviewVat(Input):
    code: str = field("Khoản phải thu", options_query="acct.receivable_options", example="TT4K7Q2M")
    category: str = field("Loại thuế", "", options_query="acct.tax_category_options", placeholder="Loại áp mặc định cho khoản", group="Thêm chi tiết")
    price_mode: Literal["", "exclusive", "inclusive"] = field("Số tiền của khoản", "", options={"": "Theo loại thuế", **PRICE}, group="Thêm chi tiết")
    issued_on: Day | None = field("Ngày lập", None, placeholder="Hôm nay")


@m.query("vat_preview", resource="acct", title="Xem trước thuế GTGT của một khoản")
async def vat_preview(ctx: Ctx, inp: PreviewVat) -> dict[str, Any]:
    """Số trên hoá đơn (giá tính thuế, thuế, tổng) và bút toán sẽ ghi — để kế toán gõ vào phần mềm hoá đơn điện tử trước khi xuất. Không ghi gì."""
    r = await ctx.one("SELECT * FROM acct.receivables WHERE code = %s", (inp.code.strip().upper(),))
    if r is None:
        raise NotFound(f"Không có khoản phải thu mã {inp.code}")
    on = inp.issued_on or today()
    p = await _plan(ctx, r, inp.category, inp.price_mode, on)
    rate: Rate = p["rate"]
    blocks: list[dict[str, Any]] = [ui.b_kpis([ui.kpi("Giá tính thuế", p["base"], "money"), ui.kpi(f"Thuế GTGT · {rate.label}", p["tax"], "money"),
                                               ui.kpi("Tổng thanh toán", p["gross"], "money")])]
    if p["unverified"]:
        blocks.append(ui.b_callout("warn", "Quy tắc thuế đang là GIẢ ĐỊNH — chưa ghi sổ được",
                                   "Kế toán xác minh từng quy tắc trong bộ quy định (runbook 13) rồi mới ghi được hoá đơn.", facts=p["unverified"]))
    posting = ([f"Nợ {r['revenue_account']} {_vnd(p['tax'])} / Có {OUTPUT} {_vnd(p['tax'])} (tách thuế khỏi doanh thu đã ghi)"] if p["price_mode"] == "inclusive"
               else [f"Nợ {r['receivable_account']} {_vnd(p['tax'])} / Có {OUTPUT} {_vnd(p['tax'])} (phải thu tăng thêm phần thuế)"]) if p["tax"] else [
        "Không có bút toán thuế (không tính thuế / thuế suất 0%) — hoá đơn vẫn vào tổng hợp kỳ"]
    blocks.append(ui.b_list([{"title": x, "tone": "accent"} for x in posting], "Bút toán khi ghi"))
    return ui.card(f"{r['code']} · {p['category_label']}", blocks, subtitle=f"Ngày lập {on:%d/%m/%Y} · số tiền khoản {PRICE[p['price_mode']].lower()} · "
                   + " · ".join(rate.refs), icon="percent")


# ── Ghi hoá đơn GTGT đã xuất ──

class IssueVat(Input):
    code: str = field("Khoản phải thu", options_query="acct.receivable_options", example="TT4K7Q2M", aliases=("Mã thanh toán",))
    symbol: str = field("Ký hiệu", example="1C26TAA", max_length=12, aliases=("Ký hiệu hoá đơn", "Ký hiệu mẫu số"))
    number: str = field("Số hoá đơn", example="0000123", max_length=12, aliases=("Số HĐ",))
    issued_on: Day | None = field("Ngày lập", None, example="12/11/2026", aliases=("Ngày hoá đơn", "Ngày HĐ"), placeholder="Hôm nay")
    category: str = field("Loại thuế", "", options_query="acct.tax_category_options", help="Trống = loại áp mặc định cho tài khoản doanh thu / nguồn của khoản", group="Thêm chi tiết")
    price_mode: Literal["", "exclusive", "inclusive"] = field("Số tiền của khoản", "", options={"": "Theo loại thuế", **PRICE}, group="Thêm chi tiết")


@m.command("issue_vat_invoice", resource="acct", title="Ghi hoá đơn GTGT đã xuất", importable=True, agent="propose", risk="money")
async def issue_vat_invoice(ctx: Ctx, inp: IssueVat) -> dict[str, Any]:
    """Hoá đơn đã xuất ở phần mềm hoá đơn điện tử ⇒ ghi ký hiệu + số + ngày lập (người lập = người ghi); code chụp mức thuế theo loại × ngày lập, tách thuế,
    ghi sổ thuế GTGT đầu ra, gắn số hoá đơn vào khoản (đối soát sao kê khớp theo số). Cùng ký hiệu + số ghi lại ⇒ không ghi lần hai."""
    if not await company_settings.enabled(ctx.conn, ctx.p.tenant_id, VAT_ON):
        raise Invalid("Công ty chưa bật “Tính thuế GTGT đầu ra” (Quản trị › Cài đặt công ty) — muốn chỉ ghi số hoá đơn thì dùng “Ghi số hoá đơn đã xuất”")
    _need_books(ctx)
    symbol, number = _no(inp.symbol, inp.number)
    on = inp.issued_on or today()
    if on > today():
        raise Invalid(f"Ngày lập {on:%d/%m/%Y} sau hôm nay")
    t = await _target(ctx, inp.code)
    if old := await _active(ctx, t["id"], lock=True):
        if (old["symbol"], old["number"]) == (symbol, number):
            return {"invoice_id": old["id"], "code": t["code"], "tax_vnd": old["tax_vnd"], "created": False, "summary": f"{t['code']}: hoá đơn {symbol}-{number} đã ghi"}
        raise Conflict(f"{t['code']} đã có hoá đơn {old['symbol']}-{old['number']} còn hiệu lực — huỷ hoá đơn đó trước khi ghi hoá đơn thay thế")
    if await ctx.val("SELECT 1 FROM acct.vat_invoices WHERE entity_id = %s AND symbol = %s AND number = %s", (t["entity_id"], symbol, number)):
        raise Conflict(f"Ký hiệu {symbol} số {number} đã dùng trong pháp nhân này — số đã cấp không cấp lại")
    p = await _plan(ctx, t, inp.category, inp.price_mode, on)
    if p["unverified"]:
        raise Invalid("Quy tắc thuế chưa được kế toán xác minh: " + ", ".join(p["unverified"])
                      + " — xem trước được (Xem trước thuế GTGT), chưa ghi sổ được (runbook 13)")
    if msg := await locked_message(ctx, t["entity_id"], on):
        raise Invalid(msg)
    invoice_no = f"{symbol}-{number}"
    if other := await ctx.val("SELECT code FROM acct.receivables WHERE entity_id = %s AND upper(invoice_no) = %s AND id <> %s AND status <> 'void'",
                              (t["entity_id"], invoice_no, t["id"])):
        raise Conflict(f"Số hoá đơn {invoice_no} đã ghi cho {other}")
    rate: Rate = p["rate"]
    vid, jid = uuid4(), None
    if p["tax"]:
        debit = t["revenue_account"] if p["price_mode"] == "inclusive" else t["receivable_account"]
        jid = await ledger.post(ctx, key=f"acct.vat:{vid}", memo=f"Thuế GTGT {rate.label.lower()} — hoá đơn {invoice_no} ({t['code']})"[:300],
                                source="acct.vat_invoice", ref=t["code"], lines=[(debit, p["tax"]), (OUTPUT, -p["tax"])], entity=t["entity_id"], on=on)
    await ctx.exec("""INSERT INTO acct.vat_invoices (id, receivable_id, entity_id, kind, symbol, number, issued_on, category, treatment, rate_bps, price_mode,
                                                     base_vnd, tax_vnd, gross_vnd, rule_refs, journal_id, issued_by)
                      VALUES (%s, %s, %s, 'invoice', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, core.me())""",
                   (vid, t["id"], t["entity_id"], symbol, number, on, p["category"], rate.treatment, rate.rate_bps, p["price_mode"], p["base"], p["tax"],
                    p["gross"], list(rate.refs), jid))
    if p["added"]:   # giá chưa gồm thuế ⇒ bên trả nợ thêm phần thuế (khoản đã thu đủ trước đó mở lại phần thuế)
        await ctx.exec("""UPDATE acct.receivables SET amount_vnd = amount_vnd + %s, fee_vnd = fee_vnd + %s,
                          status = CASE WHEN status = 'paid' THEN 'partial' ELSE status END WHERE id = %s""", (p["added"], p["added"], t["id"]))
    await ctx.exec("UPDATE acct.receivables SET invoice_no = %s, invoice_on = %s, invoiced_by = core.me() WHERE id = %s", (invoice_no, on, t["id"]))
    ctx.emit("acct.invoice_recorded", receivable_id=t["id"], code=t["code"], source=t["source"], source_ref=t["source_ref"], invoice_no=invoice_no)
    return {"invoice_id": vid, "code": t["code"], "base_vnd": p["base"], "tax_vnd": p["tax"], "gross_vnd": p["gross"], "rate_bps": rate.rate_bps,
            "created": True, "summary": f"{t['code']}: hoá đơn {invoice_no} — {rate.label}, thuế {_vnd(p['tax'])}, tổng {_vnd(p['gross'])}"
            + (f" (phải thu tăng {_vnd(p['added'])})" if p["added"] else "")}


# ── Huỷ (lập thay thế) / điều chỉnh giảm — tiền đi ngược ⇒ chuỗi duyệt receivable_correction ──

class CancelVat(Input):
    code: str = field("Khoản phải thu", options_query="acct.receivable_any_options", example="TT4K7Q2M", aliases=("Mã thanh toán",))
    reason: str = field("Lý do", max_length=500, example="Sai tên người mua — lập hoá đơn thay thế")
    cancelled_on: Day | None = field("Ngày huỷ", None, placeholder="Hôm nay", help="Không trước ngày lập")


@m.command("cancel_vat_invoice", resource="acct", title="Huỷ hoá đơn GTGT (để lập thay thế)", agent="propose", risk="money")
async def cancel_vat_invoice(ctx: Ctx, inp: CancelVat) -> dict[str, Any]:
    """Hoá đơn sai (đã huỷ / thay thế ở phần mềm hoá đơn điện tử) ⇒ bút toán ĐẢO của bút toán thuế; bản ghi giữ nguyên, chỉ điền ngày / lý do huỷ; khoản phải
    thu trả về như trước khi xuất (giá chưa gồm thuế: bỏ phần thuế đã cộng) rồi ghi được hoá đơn thay thế. Đã có hoá đơn điều chỉnh ⇒ không huỷ được."""
    _need_books(ctx)
    _need_approval(ctx)
    if not inp.reason.strip():
        raise Invalid("Cần lý do huỷ")
    t = await _target(ctx, inp.code)
    inv = await _active(ctx, t["id"], lock=True)
    if inv is None:
        raise NotFound(f"{t['code']} không có hoá đơn GTGT còn hiệu lực")
    if await ctx.val("SELECT 1 FROM acct.vat_invoices WHERE adjusts = %s LIMIT 1", (inv["id"],)):
        raise Invalid(f"Hoá đơn {inv['symbol']}-{inv['number']} đã có hoá đơn điều chỉnh — xử lý tiếp bằng điều chỉnh, không huỷ")
    on = _day(inp.cancelled_on, inv["issued_on"], "huỷ")
    if msg := await locked_message(ctx, t["entity_id"], on):
        raise Invalid(msg)
    added = inv["tax_vnd"] if inv["price_mode"] == "exclusive" else 0
    if added and t["paid_vnd"] > t["amount_vnd"] - added:
        raise Invalid(f"{t['code']} đã thu cả phần thuế ({_vnd(t['paid_vnd'])}) — đảo phần phiếu thu vượt {_vnd(t['amount_vnd'] - added)} trước rồi huỷ")
    cj = None
    if inv["journal_id"]:
        cj = await ledger.reverse(ctx, original_key=f"acct.vat:{inv['id']}", key=f"acct.vat_cancel:{inv['id']}",
                                  memo=f"Huỷ hoá đơn {inv['symbol']}-{inv['number']} ({t['code']}): {inp.reason.strip()}"[:300], source="acct.vat_cancel", on=on)
    await ctx.exec("""UPDATE acct.vat_invoices SET cancelled_on = %s, cancelled_by = core.me(), cancel_reason = %s, cancel_journal_id = %s WHERE id = %s""",
                   (on, inp.reason.strip(), cj, inv["id"]))
    if added:
        new = t["amount_vnd"] - added
        status = "open" if t["paid_vnd"] == 0 else "paid" if t["paid_vnd"] == new else "partial"
        await ctx.exec("UPDATE acct.receivables SET amount_vnd = %s, fee_vnd = %s, status = %s WHERE id = %s", (new, new, status, t["id"]))
    await ctx.exec("UPDATE acct.receivables SET invoice_no = '', invoice_on = NULL, invoiced_by = NULL WHERE id = %s", (t["id"],))
    return {"invoice_id": inv["id"], "code": t["code"], "reversed_tax_vnd": inv["tax_vnd"],
            "summary": f"Huỷ hoá đơn {inv['symbol']}-{inv['number']} của {t['code']}" + (f" — đảo thuế {_vnd(inv['tax_vnd'])}" if inv["tax_vnd"] else "")}


class AdjustVat(Input):
    code: str = field("Khoản phải thu", options_query="acct.receivable_any_options", example="TT4K7Q2M", aliases=("Mã thanh toán",))
    reduce_vnd: Money = field("Số giảm (đã gồm thuế)", ge=1, example="11.000.000", aliases=("Số giảm", "Số tiền"))
    reason: str = field("Lý do", max_length=500, example="Bên mua được giảm phí theo biên bản 03")
    symbol: str = field("Ký hiệu hoá đơn điều chỉnh", example="1C26TAA", max_length=12)
    number: str = field("Số hoá đơn điều chỉnh", example="0000124", max_length=12)
    adjusted_on: Day | None = field("Ngày lập", None, placeholder="Hôm nay", help="Không trước ngày hoá đơn gốc")


@m.command("adjust_vat_invoice", resource="acct", title="Điều chỉnh giảm hoá đơn GTGT", agent="propose", risk="money")
async def adjust_vat_invoice(ctx: Ctx, inp: AdjustVat) -> dict[str, Any]:
    """Hoá đơn điều chỉnh GIẢM (số riêng) cho hoá đơn gốc còn hiệu lực: phần thuế tách theo MỨC CỦA HOÁ ĐƠN GỐC (lần giảm cuối lấy đúng phần thuế còn lại —
    tổng thuế giảm không lệch đồng nào); giảm phải thu qua đường giấy báo có (0156 — đã thu thừa ⇒ về tiền chưa phân bổ). Bút toán: Nợ doanh thu (giá) + Nợ
    thuế GTGT đầu ra (thuế) / Có phải thu (tổng)."""
    _need_books(ctx)
    _need_approval(ctx)
    if not inp.reason.strip():
        raise Invalid("Cần lý do điều chỉnh")
    symbol, number = _no(inp.symbol, inp.number)
    t = await _target(ctx, inp.code)
    inv = await _active(ctx, t["id"], lock=True)
    if inv is None:
        raise NotFound(f"{t['code']} không có hoá đơn GTGT còn hiệu lực")
    if old := await ctx.one("SELECT * FROM acct.vat_invoices WHERE entity_id = %s AND symbol = %s AND number = %s", (t["entity_id"], symbol, number)):
        if old["adjusts"] == inv["id"] and old["gross_vnd"] == inp.reduce_vnd:
            return {"invoice_id": old["id"], "code": t["code"], "created": False, "summary": f"{t['code']}: hoá đơn điều chỉnh {symbol}-{number} đã ghi"}
        raise Conflict(f"Ký hiệu {symbol} số {number} đã dùng trong pháp nhân này")
    if t["nature"] != "revenue":
        raise Invalid(f"{t['code']}: điều chỉnh giảm chỉ cho khoản doanh thu ở lát A (doanh thu nhận trước: lát sau)")
    done = await ctx.one("SELECT coalesce(sum(gross_vnd), 0)::bigint AS g, coalesce(sum(tax_vnd), 0)::bigint AS t FROM acct.vat_invoices WHERE adjusts = %s",
                         (inv["id"],))
    assert done is not None
    left_gross, left_tax = inv["gross_vnd"] - int(done["g"]), inv["tax_vnd"] - int(done["t"])
    if inp.reduce_vnd > left_gross or inp.reduce_vnd > t["amount_vnd"]:
        raise Invalid(f"Số giảm vượt phần còn lại của hoá đơn ({_vnd(min(left_gross, t['amount_vnd']))})")
    on = _day(inp.adjusted_on, inv["issued_on"], "lập")
    if msg := await locked_message(ctx, t["entity_id"], on):
        raise Invalid(msg)
    if inp.reduce_vnd == left_gross:
        base, tax = left_gross - left_tax, left_tax
    else:
        base, tax, _ = split(inp.reduce_vnd, inv["rate_bps"], "inclusive", ROUNDING)
        tax = min(tax, left_tax)
        base = inp.reduce_vnd - tax
    vid = uuid4()
    lines = [(t["revenue_account"], base), (OUTPUT, tax), (t["receivable_account"], -inp.reduce_vnd)]
    out = await reduce_receivable(ctx, t, inp.reduce_vnd, reason=inp.reason.strip(), ref=f"{symbol}-{number}", on=on, lines=lines,
                                  key=f"acct.vat_adjust:{vid}", source="acct.vat_adjustment",
                                  memo=f"Điều chỉnh giảm hoá đơn {inv['symbol']}-{inv['number']} bằng {symbol}-{number} ({t['code']}): {inp.reason.strip()}")
    await ctx.exec("""INSERT INTO acct.vat_invoices (id, receivable_id, entity_id, kind, adjusts, symbol, number, issued_on, category, treatment, rate_bps,
                                                     price_mode, base_vnd, tax_vnd, gross_vnd, rule_refs, journal_id, reason, issued_by)
                      VALUES (%s, %s, %s, 'adjustment', %s, %s, %s, %s, %s, %s, %s, 'inclusive', %s, %s, %s, %s, %s, %s, core.me())""",
                   (vid, t["id"], t["entity_id"], inv["id"], symbol, number, on, inv["category"], inv["treatment"], inv["rate_bps"], base, tax,
                    inp.reduce_vnd, inv["rule_refs"], out["journal_id"], inp.reason.strip()))
    return {"invoice_id": vid, "code": t["code"], "base_vnd": base, "tax_vnd": tax, "gross_vnd": inp.reduce_vnd, "amount_vnd": out["amount_vnd"],
            "status": out["status"], "released_vnd": out["released_vnd"], "created": True,
            "summary": f"Điều chỉnh giảm {_vnd(inp.reduce_vnd)} (thuế {_vnd(tax)}) hoá đơn {inv['symbol']}-{inv['number']} của {t['code']}"
            + (f" · {_vnd(out['released_vnd'])} đã thu thừa về tiền chưa phân bổ" if out["released_vnd"] else "")}


# ── Danh sách & tổng hợp kỳ ──

INVOICE_STATE = {"active": "Còn hiệu lực", "cancelled": "Đã huỷ", "adjustment": "Điều chỉnh giảm"}


class InvoiceFilter(Input):
    q: str = field("Tìm", "", max_length=80, help="Mã khoản, ký hiệu / số hoá đơn, bên mua")


@m.query("vat_invoices", resource="acct", title="Hoá đơn GTGT", min_scope="all")
async def vat_invoices(ctx: Ctx, inp: InvoiceFilter) -> dict[str, Any]:
    rows = await ctx.all(
        """SELECT v.id, r.code, r.party, v.kind, v.symbol || '-' || v.number AS invoice, v.issued_on, v.category, v.treatment, v.rate_bps, v.price_mode,
                  v.base_vnd, v.tax_vnd, v.gross_vnd, v.cancelled_on, v.cancel_reason, v.reason, u.name AS issued_by, e.code AS entity,
                  a.symbol || '-' || a.number AS adjusts
           FROM acct.vat_invoices v JOIN acct.receivables r ON r.id = v.receivable_id JOIN core.legal_entities e ON e.id = v.entity_id
           LEFT JOIN core.users u ON u.id = v.issued_by LEFT JOIN acct.vat_invoices a ON a.id = v.adjusts
           WHERE %(q)s = '' OR concat_ws(' ', r.code, r.party, v.symbol || '-' || v.number) ILIKE '%%' || %(q)s || '%%'
           ORDER BY v.issued_on DESC, v.created_at DESC LIMIT 2000""", {"q": inp.q.strip()})
    for r in rows:
        r["state"] = "adjustment" if r["kind"] == "adjustment" else "cancelled" if r["cancelled_on"] else "active"
        r["rate"] = TREATMENT[r["treatment"]] if r["rate_bps"] is None else f"{r['rate_bps'] / 100:g}%"
    live = [r for r in rows if r["state"] == "active"]
    many = await entities.many(ctx)
    return ui.table(rows, [
        ui.col("state", "Trạng thái", "badge", labels=INVOICE_STATE, tones={"active": "green", "cancelled": "gray", "adjustment": "orange"}),
        ui.col("invoice", "Ký hiệu - số"), ui.col("issued_on", "Ngày lập", "date"), ui.col("code", "Khoản phải thu"), ui.col("party", "Bên mua"),
        ui.col("rate", "Mức"), ui.col("base_vnd", "Giá tính thuế", "money"), ui.col("tax_vnd", "Thuế GTGT", "money"), ui.col("gross_vnd", "Tổng", "money"),
        ui.col("adjusts", "Điều chỉnh cho"), ui.col("category", "Loại thuế"), ui.col("issued_by", "Người lập"),
        ui.col("cancelled_on", "Ngày huỷ", "date"), ui.col("cancel_reason", "Lý do huỷ / điều chỉnh", width=220),
        *([ui.col("entity", "Pháp nhân")] if many else []),
    ], title="Hoá đơn GTGT", limit=2000, kpis=[ui.kpi("Hoá đơn còn hiệu lực", len(live)), ui.kpi("Thuế GTGT trên hoá đơn còn hiệu lực",
                                                                                                  sum(r["tax_vnd"] for r in live), "money")],
        empty="Chưa ghi hoá đơn GTGT nào — bật “Tính thuế GTGT đầu ra”, khai Loại thuế GTGT, rồi ghi hoá đơn từ Khoản phải thu",
        note="Hoá đơn xuất ở phần mềm hoá đơn điện tử; ở đây ghi ký hiệu / số và tính, ghi sổ thuế (chưa nối thẳng nhà cung cấp hoá đơn điện tử). Mỗi dòng chỉ thêm — "
             "huỷ / điều chỉnh là bút toán mới.")


FILING_LAW, FILING_CODE = "vn-tax", "GTGT"   # bộ quy định nghĩa vụ kê khai (books khai) — mã tờ khai GTGT tháng


def working_month(now: date) -> date:
    """Ngày đầu THÁNG KẾ TOÁN ĐANG LÀM (rà vai kế toán 18/11/2026): tới hạn nộp tờ khai GTGT của tháng trước (bộ quy định ``vn-tax``, mã ``GTGT`` —
    hiện "ngày 20 tháng sau") kế toán vẫn đang khoá sổ / kê khai tháng trước ⇒ màn xuất MISA / tổng hợp thuế mở tháng trước; sau hạn đó mở tháng này.
    Không có quy định áp được ⇒ tháng này (như trước). Hạn lấy từ bộ quy định — không viết cứng ngày ở đây (bất biến 5)."""
    v = regulations.version_on(FILING_LAW, now)
    rule = next((r for r in v.obligations if r.code == FILING_CODE and r.cadence == "monthly"), None) if v else None
    prev = (now.replace(day=1) - timedelta(days=1)).replace(day=1)
    return prev if rule is not None and now <= regulations.raw_due(prev, rule.months, rule.day) else now.replace(day=1)


class VatPeriod(Input):
    period: str = field("Kỳ", "", options_query="acct.vat_period_options", example="10/2026", max_length=7, primary=True,
                        placeholder="Tháng đang kê khai",
                        help="Tháng trước tới hạn nộp tờ khai thì là tháng trước, sau đó là tháng này")
    entity_code: str = field("Pháp nhân", "", options_query="admin.entity_options", placeholder="Pháp nhân mặc định", primary=True,
                             help="Mỗi mã số thuế một tờ khai")


def period_bounds(text: str, now: date) -> tuple[date, date, str]:
    import re

    s = text.strip().upper()
    if not s:
        start = working_month(now)
        return start, (start + timedelta(days=32)).replace(day=1) - timedelta(days=1), f"{start:%m/%Y}"
    if mm := re.fullmatch(r"(\d{1,2})/(\d{4})", s):
        if not 1 <= int(mm.group(1)) <= 12:
            raise Invalid("Tháng 1–12")
        start = date(int(mm.group(2)), int(mm.group(1)), 1)
        return start, (start + timedelta(days=32)).replace(day=1) - timedelta(days=1), f"{start:%m/%Y}"
    if qq := re.fullmatch(r"Q([1-4])/(\d{4})", s):
        start = date(int(qq.group(2)), 3 * int(qq.group(1)) - 2, 1)
        end = (date(start.year + (start.month == 10), (start.month + 2) % 12 + 1, 1)) - timedelta(days=1)
        return start, end, f"Quý {qq.group(1)}/{qq.group(2)}"
    raise Invalid("Kỳ ghi MM/YYYY (tháng) hoặc Qn/YYYY (quý), vd 10/2026 hoặc Q4/2026")


@m.query("vat_period_options", resource="acct", title="Kỳ kê khai GTGT")
async def vat_period_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    """Kỳ chọn sẵn (không bắt gõ MM/YYYY hay Qn/YYYY): 8 quý rồi 24 tháng gần nhất, tính từ ngày hôm nay của công ty."""
    t = today()
    q, y = (t.month - 1) // 3 + 1, t.year
    out = []
    for _ in range(8):
        out.append({"value": f"Q{q}/{y}", "label": f"Quý {q}/{y}"})
        q, y = (q - 1, y) if q > 1 else (4, y - 1)
    out += month_choices(t)
    return out


def month_choices(t: date, n: int = 24) -> list[dict[str, str]]:
    """``n`` tháng gần nhất tính từ ``t`` (gồm tháng của ``t``), giá trị ``MM/YYYY`` — như ô ``month`` của MISA / GTGT."""
    out, y, mo = [], t.year, t.month
    for _ in range(n):
        out.append({"value": f"{mo:02d}/{y}", "label": f"Tháng {mo}/{y}"})
        y, mo = (y, mo - 1) if mo > 1 else (y - 1, 12)
    return out


@m.query("month_options", resource="acct", title="Tháng kê khai")
async def month_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return month_choices(today())


@m.query("vat_summary", resource="acct", title="Tổng hợp thuế GTGT theo kỳ", min_scope="all")
async def vat_summary(ctx: Ctx, inp: VatPeriod) -> dict[str, Any]:
    """Doanh thu (giá tính thuế) và thuế GTGT ĐẦU RA của một pháp nhân trong kỳ, theo mức — số cho phần bán ra của tờ khai 01/GTGT. Hoá đơn tính vào kỳ
    có ngày lập; huỷ / điều chỉnh giảm trừ vào kỳ có ngày huỷ / ngày lập hoá đơn điều chỉnh. Thuế đầu vào: từ phiếu chi phí đã khấu trừ (0170)."""
    a, b, label = period_bounds(inp.period, today())
    eid = await entities.resolve(ctx, inp.entity_code) if inp.entity_code.strip() else await entities.default(ctx)
    ent = (await entities.names(ctx)).get(str(eid), {})
    grouped = await ctx.all(
        """SELECT treatment, rate_bps, sum(base)::bigint AS base_vnd, sum(tax)::bigint AS tax_vnd, count(*) AS n FROM (
             SELECT treatment, rate_bps, base_vnd AS base, tax_vnd AS tax FROM acct.vat_invoices
              WHERE entity_id = %(e)s AND kind = 'invoice' AND issued_on BETWEEN %(a)s AND %(b)s
             UNION ALL SELECT treatment, rate_bps, -base_vnd, -tax_vnd FROM acct.vat_invoices
              WHERE entity_id = %(e)s AND kind = 'invoice' AND cancelled_on BETWEEN %(a)s AND %(b)s
             UNION ALL SELECT treatment, rate_bps, -base_vnd, -tax_vnd FROM acct.vat_invoices
              WHERE entity_id = %(e)s AND kind = 'adjustment' AND issued_on BETWEEN %(a)s AND %(b)s) x
           GROUP BY treatment, rate_bps ORDER BY treatment, rate_bps""", {"e": eid, "a": a, "b": b})
    rows: list[dict[str, Any]] = []
    unverified: set[str] = set()
    for g in grouped:
        line = ""
        try:
            rate = rate_on(g["treatment"], False, b)
            line = rate.line
        except Invalid:
            pass
        rows.append({"id": f"{g['treatment']}:{g['rate_bps']}", "label": TREATMENT[g["treatment"]] if g["rate_bps"] is None
                     else f"Thuế suất {g['rate_bps'] / 100:g}%" + (" (giảm 2% — kèm phụ lục giảm thuế)" if g["treatment"] == "standard" and g["rate_bps"] < 1000 else ""),
                     "line": line, "base_vnd": g["base_vnd"], "tax_vnd": g["tax_vnd"] if g["rate_bps"] is not None else None, "n": g["n"], "note": ""})
    for r in await ctx.all("""SELECT DISTINCT unnest(rule_refs) AS ref FROM acct.vat_invoices WHERE entity_id = %(e)s
                              AND (issued_on BETWEEN %(a)s AND %(b)s OR cancelled_on BETWEEN %(a)s AND %(b)s)""", {"e": eid, "a": a, "b": b}):
        unverified.update(regulations.require_verified([r["ref"]]))
    # 0170: thuế đầu vào từ phiếu chi phí đã ghi khấu trừ (ngày chứng từ trong kỳ; phiếu huỷ trừ vào kỳ của ngày huỷ)
    inp_ = await ctx.one(
        """SELECT coalesce(sum(base), 0)::bigint AS base, coalesce(sum(tax), 0)::bigint AS tax, count(*) AS n FROM (
             SELECT total_vnd - input_vnd AS base, input_vnd AS tax FROM acct.bills
              WHERE entity_id = %(e)s AND vat_treatment = 'input' AND bill_on BETWEEN %(a)s AND %(b)s
             UNION ALL SELECT -(total_vnd - input_vnd), -input_vnd FROM acct.bills
              WHERE entity_id = %(e)s AND vat_treatment = 'input' AND cancelled_on BETWEEN %(a)s AND %(b)s) x""", {"e": eid, "a": a, "b": b})
    assert inp_ is not None
    rows.append({"id": "input", "label": "Thuế GTGT đầu vào được khấu trừ", "line": "25", "base_vnd": int(inp_["base"]), "tax_vnd": int(inp_["tax"]),
                 "n": int(inp_["n"]),
                 "note": "Từ phiếu chi phí có hoá đơn mua vào đã ghi khấu trừ. Hàng nhập khẩu, phân bổ thuế cho hoạt động không chịu thuế, "
                         "bảng kê mua vào: CHƯA LÀM — kế toán đối chiếu cổng hoá đơn"})
    sales = [r for r in rows if r["id"] != "input"]
    taxed = sum(r["base_vnd"] for r in sales if r["tax_vnd"] is not None)
    out_tax = sum(r["tax_vnd"] or 0 for r in sales)
    exempt = sum(r["base_vnd"] or 0 for r in rows if r["id"].startswith("exempt:"))
    kpis = [ui.kpi("Doanh thu chịu thuế", taxed, "money"), ui.kpi("Thuế GTGT đầu ra", out_tax, "money"),
            ui.kpi("Không chịu thuế", exempt, "money")]
    if ctx.p.can("ledger", "read"):   # sổ cái là nguồn: tổng thuế trên hoá đơn phải bằng phát sinh có của tài khoản thuế đầu ra trong kỳ
        books = -sum(x["amount_vnd"] for x in await ledger.journal_lines(ctx, a, b, eid) if x["code"] == OUTPUT)
        kpis.append(ui.kpi("Sổ cái thuế đầu ra", books, "money", tone="green" if books == out_tax else "red",
                           hint="khớp hoá đơn" if books == out_tax else f"lệch {_vnd(books - out_tax)} — bút toán ghi tay vào {OUTPUT}?"))
        in_books = sum(x["amount_vnd"] for x in await ledger.journal_lines(ctx, a, b, eid) if x["code"] == "tax:vat_input")
        kpis.append(ui.kpi("Thuế GTGT đầu vào (phiếu chi phí)", int(inp_["tax"]), "money", tone="green" if in_books == int(inp_["tax"]) else "red",
                           hint="khớp sổ cái" if in_books == int(inp_["tax"]) else f"sổ cái {_vnd(in_books)} — bút toán ghi tay vào tax:vat_input?"))
    outside = int(await ctx.val("""SELECT count(*) FROM acct.receivables r WHERE r.entity_id = %(e)s AND r.invoice_no <> '' AND r.status <> 'void'
                                   AND r.invoice_on BETWEEN %(a)s AND %(b)s
                                   AND NOT EXISTS (SELECT 1 FROM acct.vat_invoices v WHERE v.receivable_id = r.id AND v.kind = 'invoice'
                                                   AND v.cancelled_on IS NULL)""", {"e": eid, "a": a, "b": b}) or 0)
    if outside:
        kpis.append(ui.kpi("Số hoá đơn ghi ngoài, chưa tính thuế", outside, tone="orange", hint="ghi bằng “Ghi số hoá đơn đã xuất” — không có trong tổng hợp này"))
    header = []
    if unverified:
        header.append(ui.b_callout("warn", "Có quy tắc thuế chưa xác minh", "Số trong kỳ dựa trên quy tắc giả định của bộ quy định — kế toán soát trước khi nộp.",
                                   facts=sorted(unverified)))
    return ui.table(rows, [
        ui.col("label", "Hàng hoá, dịch vụ bán ra", width=260), ui.col("line", "Chỉ tiêu 01/GTGT"), ui.col("base_vnd", "Giá tính thuế", "money"),
        ui.col("tax_vnd", "Thuế GTGT", "money"), ui.col("n", "Số hoá đơn / điều chỉnh"), ui.col("note", "Ghi chú", width=280),
    ], title=f"Tổng hợp thuế GTGT · {label} · {ent.get('code', '')}", kpis=kpis, header=header or None,
        note="Đầu ra theo hoá đơn đã ghi (ngày lập); huỷ / điều chỉnh giảm trừ vào kỳ của ngày huỷ / ngày lập điều chỉnh. Số chỉ tiêu theo mẫu 01/GTGT "
             "(TT 80/2021; TT 89/2026 từ kỳ 07/2026) — kế toán đối chiếu mẫu đang nộp. Đầu vào: từ phiếu chi phí; số thuế phải nộp: chưa tính ở đây.")
