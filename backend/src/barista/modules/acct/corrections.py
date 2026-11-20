"""0156 — sửa tiền mà KHÔNG có số âm trong bảng: mỗi sửa là một bản ghi chỉ thêm, trỏ vào cái nó sửa, đi cùng bút toán trong sổ cái.

* Giấy báo có (``credit_receivable``): giảm số phải thu của MỘT khoản (bên trả giảm / thu hồi phí, lập sai số). Nợ doanh thu / Có phải thu. Khoản đã thu
  nhiều hơn số mới ⇒ phần thu thừa tự đảo về "tiền về chưa phân bổ" (không biến mất, phân bổ được vào khoản khác của cùng bên trả).
* Đảo phiếu thu (``reverse_receipt``): ghi nhầm khoản / nhầm số ⇒ đảo một phần hoặc toàn bộ, phiếu mới nhất trước; tiền quay về "chưa phân bổ" của đúng
  dòng sao kê gốc. Nợ phải thu / Có tiền về chưa phân bổ.
* Tiền về chưa có khoản phải thu (``record_unapplied``): tiền đã vào tài khoản (vd bên trả chuyển khi giao dịch mới đặt cọc) — vào sổ ngay ở "tiền về
  chưa phân bổ", hiện ở Đối soát sao kê, tự khớp khi khoản phải thu mang mã ghi trong nội dung được lập.
* Danh mục KHOẢN THU / KHOẢN CHI (``set_category``): dữ liệu công ty, mỗi mục một tài khoản sổ cái đã khai; chưa khai ⇒ mặc định = các tài khoản doanh
  thu / chi phí module đã khai.

Chỉ khoản bản chất DOANH THU: tiền thu hộ / cọc trả lại người khác đi qua chi trả có duyệt (``request_remittance``), không đảo ngầm ở đây.
"""

import re
from datetime import date
from typing import Any, Literal
from uuid import uuid4

from barista.api import (
    Conflict,
    Ctx,
    Day,
    Denied,
    Input,
    Invalid,
    Money,
    NoInput,
    NotFound,
    entities,
    field,
    name_key,
    snake_from_name,
    today,
    ui,
)
from barista.kernel import ledger
from barista.modules.acct import _accept, _target, _vnd, locked_message, m, void_books

# ── Danh mục khoản thu / chi ──

# tài khoản CƠ CHẾ (điều chỉnh đầu kỳ, phí ứng theo chính sách): không phải mục người chọn khi phân loại — module khai tài khoản tự đánh dấu
# (``ledger.declare(..., mechanism=…)`` ⇒ ``ledger.MECHANISM``, 0189); ở đây không nêu mã nào.
DIRECTION = {"in": "Khoản thu", "out": "Khoản chi"}


def default_categories(direction: str) -> dict[str, dict[str, str]]:
    """Mặc định khi công ty chưa khai danh mục của chiều đó: mỗi tài khoản doanh thu (thu) / chi phí (chi) module đã khai là một mục, mã = phần sau dấu
    hai chấm. Gói bật thêm ⇒ có thêm mục; công ty khai danh mục riêng ⇒ danh sách của công ty thay mặc định."""
    kind = "revenue" if direction == "in" else "expense"
    out: dict[str, dict[str, str]] = {}
    for code, (name, k) in sorted(ledger.ACCOUNTS.items()):
        if k == kind and code not in ledger.MECHANISM:
            out.setdefault(code.split(":", 1)[1], {"code": code.split(":", 1)[1], "direction": direction, "label": name, "account": code})
    return out


async def categories(ctx: Ctx, direction: str) -> dict[str, dict[str, Any]]:
    rows = await ctx.all("SELECT code, direction, label, account FROM acct.categories WHERE direction = %s AND active ORDER BY code", (direction,))
    return {r["code"]: dict(r) for r in rows} if rows else default_categories(direction)


async def category_of(ctx: Ctx, value: str, direction: str) -> dict[str, Any]:
    """Mục theo mã hoặc tên (bỏ dấu, không phân biệt hoa thường)."""
    cats = await categories(ctx, direction)
    key = name_key(value)
    got = next((c for k, c in cats.items() if key in (name_key(k), name_key(c["label"]))), None)
    if got is None:
        raise Invalid(f"{DIRECTION[direction]} “{value}” chưa có trong danh mục — chọn: {', '.join(c['label'] for c in cats.values())} "
                      "(Kế toán › Danh mục khoản thu / chi)")
    return got


class SetCategory(Input):
    code: str = field("Mã mục", "", example="thuong_ban_hang", max_length=40, group="Thêm chi tiết", placeholder="Máy đặt từ tên mục",
                      help="Chữ thường không dấu, số, gạch dưới. Để trống khi thêm mục mới; sửa mục có sẵn thì mã đã điền sẵn")
    direction: Literal["in", "out"] = field("Chiều", "in", options=DIRECTION)
    label: str = field("Tên mục", example="Thưởng bán hàng", max_length=100)
    account: str = field("Ghi vào tài khoản", "", options_query="acct.category_account_options", options_with=("direction",),
                         example="revenue:other_income", max_length=60, aliases=("Tài khoản sổ cái",), placeholder="Khoản chi: tài khoản riêng theo tên mục",
                         help="Khoản thu: chọn tài khoản doanh thu. Khoản chi: để trống thì mục có tài khoản chi phí riêng, đặt tên theo tên mục")
    active: bool = field("Đang dùng", True, group="Thêm chi tiết")


class AccountPick(Input):
    direction: str = field("Chiều", "in")


@m.query("category_account_options", resource="acct", title="Tài khoản sổ cái cho mục khoản thu / chi")
async def category_account_options(ctx: Ctx, inp: AccountPick) -> list[dict[str, str]]:
    """Tài khoản doanh thu (khoản thu) hoặc chi phí (khoản chi) đã khai — chữ người đọc, mã tài khoản chỉ là giá trị (0225: không bắt gõ khoá sổ cái)."""
    kind = "expense" if inp.direction == "out" else "revenue"
    return [{"value": c, "label": name} for c, (name, k) in sorted(ledger.ACCOUNTS.items(), key=lambda kv: kv[1][0]) if k == kind and c not in ledger.MECHANISM]


@m.command("set_category", resource="acct", action="manage", title="Thêm / sửa mục khoản thu / chi", importable=True, category="setup")
async def set_category(ctx: Ctx, inp: SetCategory) -> dict[str, Any]:
    """Danh mục của CÔNG TY (dữ liệu). Khai mục đầu tiên của một chiều ⇒ mặc định của chiều đó không còn áp — khai đủ mục công ty dùng."""
    code = inp.code.strip().lower()
    if not code:   # 0225: không bắt người dùng nghĩ ra mã — máy đặt theo tên mục
        code = snake_from_name(inp.label, {r["code"] for r in await ctx.all("SELECT code FROM acct.categories")})
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,39}", code):
        raise Invalid("Mã mục: chữ thường không dấu, số, gạch dưới (vd thuong_ban_hang)")
    kind = "revenue" if inp.direction == "in" else "expense"
    account = inp.account.strip()
    if not account and inp.direction == "out":   # khoản chi không chọn tài khoản ⇒ tài khoản chi phí riêng theo mã mục (chỉ chữ + gạch dưới)
        account = "expense:" + (re.sub(r"[^a-z_]", "", code.replace("-", "_")) or "khac")[:39]
    if not account:
        raise Invalid("Khoản thu cần chọn tài khoản doanh thu để ghi vào")
    # 0170: khoản CHI được trỏ tài khoản chi phí RIÊNG của công ty (expense:<mã>, chưa module nào khai) — sổ cái tạo tài khoản đó, tên = tên mục, ở lần ghi
    # đầu (phiếu chi phí); nhờ vậy báo cáo kết quả kinh doanh tách được từng khoản mục ngay trên sổ cái. Khoản thu vẫn chỉ trỏ tài khoản doanh thu đã khai.
    own = inp.direction == "out" and account not in ledger.ACCOUNTS and re.fullmatch(r"expense:[a-z][a-z_]{1,39}", account) is not None
    if not own and (ledger.ACCOUNTS.get(account, ("", ""))[1] != kind or account in ledger.MECHANISM):
        raise Invalid(f"{DIRECTION[inp.direction]} ghi vào một tài khoản {'doanh thu' if kind == 'revenue' else 'chi phí'} đã khai: "
                      + ", ".join(c for c, (_, k) in sorted(ledger.ACCOUNTS.items()) if k == kind and c not in ledger.MECHANISM)
                      + (" — hoặc tài khoản chi phí riêng dạng expense:<mã> (vd expense:thue_van_phong)" if kind == "expense" else ""))
    await ctx.exec("""INSERT INTO acct.categories (code, direction, label, account, active) VALUES (%s, %s, %s, %s, %s)
                      ON CONFLICT (tenant_id, code) DO UPDATE SET direction = EXCLUDED.direction, label = EXCLUDED.label, account = EXCLUDED.account,
                      active = EXCLUDED.active""", (code, inp.direction, inp.label.strip(), account, inp.active))
    return {"code": code, "summary": f"{DIRECTION[inp.direction]} {code}: {inp.label.strip()} → {account}" + (" (tài khoản riêng của công ty)" if own else "")}


@m.query("category_list", resource="acct", title="Danh mục khoản thu / chi", min_scope="all")
async def category_list(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    own = await ctx.all("SELECT code, direction, label, account, active FROM acct.categories ORDER BY direction, code")
    rows: list[dict[str, Any]] = []
    for d in ("in", "out"):
        mine = [r for r in own if r["direction"] == d]
        rows += ([{**r, "id": r["code"], "state": "on" if r["active"] else "off"} for r in mine] if mine else
                 [{**c, "id": c["code"], "state": "default"} for c in default_categories(d).values()])
    return ui.table(rows, [ui.col("direction", "Chiều", "badge", labels=DIRECTION, tones={"in": "green", "out": "orange"}), ui.col("code", "Mã"),
                           ui.col("label", "Tên mục", width=220), ui.col("account", "Tài khoản sổ cái"),
                           ui.col("state", "", "badge", labels={"on": "Đang dùng", "off": "Ngừng", "default": "Mặc định"}, tones={"default": "gray"})],
                    title="Danh mục khoản thu / chi", note="Chưa khai ⇒ mỗi tài khoản doanh thu / chi phí đã khai là một mục mặc định. Khai một mục của chiều "
                                                           "nào ⇒ danh mục của công ty thay mặc định của chiều đó.")


@m.query("income_category_options", resource="acct", title="Mục khoản thu")
async def income_category_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return [{"value": k, "label": c["label"]} for k, c in (await categories(ctx, "in")).items()]


@m.query("expense_category_options", resource="acct", title="Mục khoản chi")
async def expense_category_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return [{"value": k, "label": c["label"]} for k, c in (await categories(ctx, "out")).items()]


class CategoryIn(Input):
    value: str
    direction: Literal["in", "out"]


@m.query("category", resource=None, title="Một mục khoản thu / chi", internal=True, agent="deny", family="ledger_ref",
         why="Lệnh nhập của gói khai consumes(\"ledger_ref\") (vd thêm một khoản phải thu / nạp lịch sử chi của gói) kiểm mục người nhập ghi trên dòng (mã "
             "hoặc tên) có trong danh mục khoản thu / chi của công ty. "
             "Trả {code, direction, label, account} — chỉ danh mục, không số tiền.")
async def category(ctx: Ctx, inp: CategoryIn) -> dict[str, Any]:
    return await category_of(ctx, inp.value, inp.direction)


# ── Đảo phiếu thu: phiếu mới nhất trước, tiền về lại "chưa phân bổ" của đúng dòng sao kê ──

def _need_books(ctx: Ctx) -> None:
    if not ctx.p.has_scope("acct", "all"):
        raise Denied("Cần quyền Kế toán cả công ty")


def _need_approval(ctx: Ctx) -> None:
    """ĐÓNG KHI THIẾU LUẬT (0156, review): lệnh sửa tiền chỉ chạy SAU phiếu do người khác duyệt. Có luật duyệt ⇒ executor đã biến lần gọi thành phiếu, tới
    được đây chỉ khi đã duyệt; tới được đây mà chưa có người duyệt ⇒ công ty chưa có luật (chưa cài mặc định / luật bị xoá) ⇒ từ chối, không chạy thẳng."""
    if not ctx.p.approved_by:
        raise Invalid("Chưa có luật duyệt cho lệnh này — cài mặc định của gói Tài chính hoặc tạo luật duyệt cho chuỗi receivable_correction "
                      "(Quản trị › Luật duyệt), rồi gửi lại")


def _day(on: date | None, floor: date, what: str) -> date:
    day = on or today()
    if day > today():
        raise Invalid(f"Ngày {what} sau hôm nay")
    if day < floor:
        raise Invalid(f"Ngày {what} không được trước {floor:%d/%m/%Y}")
    return day


async def _guard(ctx: Ctx, t: dict[str, Any], amount: int) -> None:
    """Gói nguồn của khoản (điểm cắm ``acct.receipt_change_guard``, quyền hệ thống, chỉ nguồn + tham chiếu + số đảo) cho biết đảo này có làm nguồn
    kẹt không — vd hoa hồng mở theo phí đã thu mà đã chi / có khấu trừ thì không thu hồi tự động được. Có chặn ⇒ từ chối TRƯỚC khi ghi gì."""
    got = [msg for part in await ctx.plugs("acct.receipt_change_guard", system=True, source=t["source"], source_ref=t["source_ref"], reduce_vnd=amount)
           for msg in part]
    if got:
        raise Invalid("Chưa đảo được — " + "; ".join(got[:3]))


async def release(ctx: Ctx, t: dict[str, Any], amount: int, *, reason: str, corrects: str, on: date, guard: bool = False) -> int:
    """Đảo ``amount`` trên các phiếu thu còn hiệu lực của khoản ``t`` (đã khoá), phiếu mới nhất trước. Mỗi phần: một dòng ``acct.payment_reversals`` +
    bút toán Nợ phải thu / Có tiền về chưa phân bổ, dòng sao kê gốc giảm phần đã phân bổ (mở lại). Phát ``acct.receipt_reversed`` — gói nguồn tính lại
    (vd hoa hồng mở theo phí đã thu). Trả số đã đảo."""
    if t["nature"] not in ("revenue", "deferred"):   # deferred (0147): phiếu thu cùng một bút toán Nợ chưa phân bổ / Có phải thu — đảo được như doanh thu
        raise Invalid("Chỉ đảo được phiếu thu của khoản doanh thu — tiền thu hộ / cọc trả lại qua chi trả có duyệt")
    if amount <= 0 or amount > t["paid_vnd"]:
        raise Invalid(f"Số đảo phải dương và không vượt số đã thu của {t['code']} ({_vnd(t['paid_vnd'])})")
    if msg := await locked_message(ctx, t["entity_id"], on):
        raise Invalid(msg)
    if guard:   # lệnh có người bấm: nguồn chặn được; chuyển giao dịch (hệ thống) tự xử lý theo đường huỷ giao dịch của nguồn
        await _guard(ctx, t, amount)
    left = amount
    payments = await ctx.all(
        """SELECT p.id, p.line_id, p.received_on,
                  p.amount_vnd - (SELECT coalesce(sum(x.amount_vnd), 0) FROM acct.payment_reversals x WHERE x.payment_id = p.id) AS left_vnd
           FROM acct.payments p WHERE p.receivable_id = %s ORDER BY p.received_on DESC, p.created_at DESC, p.id DESC""", (t["id"],))
    for p in payments:
        part = min(left, int(p["left_vnd"]))
        if part <= 0:
            continue
        if on < p["received_on"]:
            raise Invalid(f"Ngày đảo không được trước ngày thu {p['received_on']:%d/%m/%Y}")
        await ctx.one("SELECT id FROM acct.statement_lines WHERE id = %s FOR UPDATE", (p["line_id"],))
        rid = uuid4()
        jid = await ledger.post(ctx, key=f"acct.payment_reversal:{rid}", memo=f"Đảo phiếu thu {t['code']}: {reason}"[:300], source="acct.payment_reversal",
                                ref=t["code"], lines=[(t["receivable_account"], part), ("unapplied:bank", -part)], entity=t["entity_id"], on=on)
        await ctx.exec("""INSERT INTO acct.payment_reversals (id, payment_id, amount_vnd, reason, corrects, reversed_on, journal_id, created_by)
                          VALUES (%s, %s, %s, %s, %s, %s, %s, core.me())""", (rid, p["id"], part, reason, corrects, on, jid))
        await ctx.exec("""UPDATE acct.statement_lines SET allocated_vnd = allocated_vnd - %s, status = 'open', decided_by = core.me(), decided_at = now()
                          WHERE id = %s""", (part, p["line_id"]))
        left -= part
        if not left:
            break
    if left:
        raise Invalid("Số đảo vượt phần còn hiệu lực của các phiếu thu")
    paid = t["paid_vnd"] - amount
    status = "open" if paid == 0 else "partial"
    await ctx.exec("UPDATE acct.receivables SET paid_vnd = %s, status = %s WHERE id = %s", (paid, status, t["id"]))
    t["paid_vnd"], t["status"] = paid, status
    ctx.emit("acct.receipt_reversed", receivable_id=t["id"], code=t["code"], source=t["source"], source_ref=t["source_ref"], paid_vnd=paid,
             amount_vnd=t["amount_vnd"], reversed_vnd=amount)
    return amount


class ReverseReceipt(Input):
    code: str = field("Khoản phải thu", options_query="acct.receivable_any_options", example="HD-GD-HSR-12A01", aliases=("Mã thanh toán", "Mã khoản phải thu"))
    amount_vnd: Money = field("Số tiền đảo", ge=1, example="20.000.000", aliases=("Số tiền", "Số đảo"))
    reason: str = field("Lý do", max_length=500, example="Ghi nhầm khoản — tiền của giao dịch khác", aliases=("Lý do đảo",))
    corrects: str = field("Chứng từ được sửa", "", max_length=120, aliases=("Mã dòng gốc", "Số chứng từ"),
                          help="Mã dòng / chứng từ bị sửa — ghi lại cùng mã thì không đảo lần hai", group="Thêm chi tiết")
    reversed_on: Day | None = field("Ngày đảo", None, placeholder="Hôm nay", help="Không trước ngày thu")


async def _reverse_basis(ctx: Ctx, inp: ReverseReceipt) -> dict[str, str]:
    """Căn cứ (0071) kiêm KIỂM TRẦN NGAY LÚC XIN (0162): số đảo vượt số đã thu của khoản ⇒ báo ở bước xin / chạy thử (báo cáo nhập, thẻ đề xuất), không để
    phiếu qua duyệt rồi mới lỗi. Chứng từ đã đảo trước đó (``corrects``) là lần gọi lại hợp lệ ⇒ không kiểm. Căn cứ trả ra KHÔNG phụ thuộc số đã thu —
    nhiều phiếu đảo cùng một khoản duyệt lần lượt thì số đã thu đổi giữa hai lần duyệt, không được coi là "căn cứ đã đổi"."""
    no = inp.code.strip().upper()
    t = await ctx.one("SELECT id, code, paid_vnd FROM acct.receivables WHERE code = %s", (no,))
    if t is None:
        raise NotFound(f"Không có khoản phải thu mã {no}")
    again = inp.corrects.strip() and await ctx.val(
        "SELECT 1 FROM acct.payment_reversals x JOIN acct.payments p ON p.id = x.payment_id WHERE p.receivable_id = %s AND x.corrects = %s LIMIT 1",
        (t["id"], inp.corrects.strip()))
    if not again and inp.amount_vnd > t["paid_vnd"]:
        raise Invalid(f"Số đảo {_vnd(inp.amount_vnd)} vượt số đã thu của {t['code']} ({_vnd(t['paid_vnd'])})")
    return {"Khoản phải thu": t["code"]}


@m.command("reverse_receipt", resource="acct", title="Đảo phiếu thu về tiền chưa phân bổ", importable=True, agent="propose", risk="money",
           basis=_reverse_basis)
async def reverse_receipt(ctx: Ctx, inp: ReverseReceipt) -> dict[str, Any]:
    """Phiếu thu ghi sai (nhầm khoản, nhầm số, dòng âm của hệ cũ) ⇒ đảo một phần / toàn bộ — không sửa, không xoá phiếu gốc. Tiền vẫn nằm trong tài
    khoản ngân hàng: về "tiền về chưa phân bổ" của dòng sao kê gốc, rồi phân bổ vào khoản đúng (Đối soát sao kê) hoặc hoàn lại."""
    _need_books(ctx)
    _need_approval(ctx)
    t = await _target(ctx, inp.code)
    if inp.corrects.strip():
        done = await ctx.val("""SELECT sum(x.amount_vnd)::bigint FROM acct.payment_reversals x JOIN acct.payments p ON p.id = x.payment_id
                                WHERE p.receivable_id = %s AND x.corrects = %s""", (t["id"], inp.corrects.strip()))
        if done is not None:
            if int(done) != inp.amount_vnd:
                raise Conflict(f"{t['code']}: chứng từ {inp.corrects.strip()} đã đảo {_vnd(int(done))} — khác số lần này")
            return {"code": t["code"], "reversed_vnd": int(done), "created": False, "summary": f"{t['code']}: đã đảo trước đó"}
    if not inp.reason.strip():
        raise Invalid("Cần lý do đảo")
    on = _day(inp.reversed_on, t["issued_on"], "đảo")
    await release(ctx, t, inp.amount_vnd, reason=inp.reason.strip(), corrects=inp.corrects.strip(), on=on, guard=True)
    return {"code": t["code"], "reversed_vnd": inp.amount_vnd, "paid_vnd": t["paid_vnd"], "created": True,
            "summary": f"Đảo {_vnd(inp.amount_vnd)} của {t['code']} — tiền nằm ở tiền về chưa phân bổ, phân bổ lại ở Đối soát sao kê"}


# ── Giấy báo có: giảm số phải thu ──

class CreditNote(Input):
    code: str = field("Khoản phải thu", options_query="acct.receivable_any_options", example="HD-GD-HSR-12A01", aliases=("Mã thanh toán", "Mã khoản phải thu"))
    amount_vnd: Money = field("Số tiền giảm", ge=1, example="15.000.000", aliases=("Số tiền", "Số giảm"))
    reason: str = field("Lý do", max_length=500, example="Bên trả thu hồi một phần phí theo biên bản", aliases=("Lý do giảm",))
    corrects: str = field("Chứng từ", "", max_length=120, aliases=("Số biên bản", "Mã dòng gốc", "Số chứng từ"),
                          help="Biên bản / mã dòng gốc — ghi lại cùng chứng từ thì không giảm lần hai", group="Thêm chi tiết")
    credited_on: Day | None = field("Ngày ghi", None, placeholder="Hôm nay")


@m.command("credit_receivable", resource="acct", title="Giảm phải thu (giấy báo có)", importable=True, agent="propose", risk="money")
async def credit_receivable(ctx: Ctx, inp: CreditNote) -> dict[str, Any]:
    """Giảm số phải thu của một khoản doanh thu: bên trả được giảm / thu hồi một phần, hoặc lập sai số. Nợ doanh thu / Có phải thu, một dòng
    ``acct.receivable_credits`` (số dương + lý do + chứng từ). Đã thu nhiều hơn số mới ⇒ phần thừa tự đảo về tiền về chưa phân bổ. Giảm hết ⇒ khoản
    thành "đã huỷ". Hoa hồng mở theo phí đã thu tính trên số GỐC (không tăng vì phí giảm) — chính sách khác cần quyết định riêng."""
    _need_books(ctx)
    _need_approval(ctx)
    t = await _target(ctx, inp.code)
    ref = inp.corrects.strip()
    if ref and (done := await ctx.val("SELECT amount_vnd FROM acct.receivable_credits WHERE receivable_id = %s AND corrects = %s", (t["id"], ref))):
        if int(done) != inp.amount_vnd:
            raise Conflict(f"{t['code']}: chứng từ {ref} đã giảm {_vnd(int(done))} — khác số lần này")
        return {"code": t["code"], "credited_vnd": int(done), "created": False, "summary": f"{t['code']}: đã giảm trước đó"}
    if t["nature"] != "revenue" or t["status"] == "void":
        raise Invalid(f"{t['code']}: chỉ giảm được khoản doanh thu còn hiệu lực (khoản doanh thu nhận trước: giảm giá / hoàn phí là lát C của 0147)")
    from barista.modules.acct.vat import has_active_invoice
    if await has_active_invoice(ctx, t["id"]):   # 0165: một thẩm quyền — giảm khoản đã có hoá đơn GTGT phải tách thuế, không giảm cả số vào doanh thu
        raise Invalid(f"{t['code']} đã ghi hoá đơn GTGT — giảm bằng “Điều chỉnh giảm hoá đơn GTGT” (tách phần thuế) hoặc huỷ hoá đơn trước")
    if not inp.reason.strip():
        raise Invalid("Cần lý do giảm")
    if inp.amount_vnd > t["amount_vnd"]:
        raise Invalid(f"Số giảm vượt số phải thu hiện tại của {t['code']} ({_vnd(t['amount_vnd'])})")
    on = _day(inp.credited_on, t["issued_on"], "ghi")
    if msg := await locked_message(ctx, t["entity_id"], on):
        raise Invalid(msg)
    out = await reduce_receivable(ctx, t, inp.amount_vnd, reason=inp.reason.strip(), ref=ref, on=on, source="acct.credit_note",
                                  lines=[(t["revenue_account"], inp.amount_vnd), (t["receivable_account"], -inp.amount_vnd)],
                                  memo=f"Giảm phải thu {t['code']}: {inp.reason.strip()}")
    over = out["released_vnd"]
    return {"code": t["code"], "credited_vnd": inp.amount_vnd, "amount_vnd": out["amount_vnd"], "status": out["status"], "released_vnd": over, "created": True,
            "summary": f"Giảm {_vnd(inp.amount_vnd)} phải thu {t['code']}" + (f" · {_vnd(over)} đã thu thừa về tiền chưa phân bổ" if over > 0 else "")}


async def reduce_receivable(ctx: Ctx, t: dict[str, Any], amount: int, *, reason: str, ref: str, on: date, lines: list[tuple[str, int]], source: str,
                            memo: str, key: str = "") -> dict[str, Any]:
    """Giảm số phải thu của khoản ``t`` (đã khoá, đã kiểm) ``amount`` — MỘT đường cho giấy báo có và hoá đơn GTGT điều chỉnh giảm (0165): đã thu nhiều hơn
    số mới ⇒ phần thừa về tiền chưa phân bổ trước; bút toán ``lines`` (Có phải thu đúng ``amount``) + một dòng ``acct.receivable_credits``; giảm hết ⇒ khoản
    "đã huỷ". Trả {credit_id, journal_id, amount_vnd, status, released_vnd}."""
    over = amount - (t["amount_vnd"] - t["paid_vnd"])
    if over > 0:   # số mới thấp hơn số đã thu ⇒ phần thu thừa về chưa phân bổ trước, rồi mới giảm
        await release(ctx, t, over, reason=f"Giảm phải thu: {reason}", corrects=ref, on=on, guard=True)
    cid = uuid4()
    jid = await ledger.post(ctx, key=key or f"acct.credit:{cid}", memo=memo[:300], source=source, ref=t["code"], lines=lines, entity=t["entity_id"], on=on)
    await ctx.exec("""INSERT INTO acct.receivable_credits (id, receivable_id, amount_vnd, reason, corrects, credited_on, journal_id, created_by)
                      VALUES (%s, %s, %s, %s, %s, %s, %s, core.me())""", (cid, t["id"], amount, reason, ref, on, jid))
    if amount == t["amount_vnd"]:   # giảm hết: khoản thành "đã huỷ" (số phải thu không về 0 — ràng buộc số dương giữ nguyên)
        await ctx.exec("UPDATE acct.receivables SET status = 'void', credited_vnd = credited_vnd + %s WHERE id = %s", (amount, t["id"]))
        status, new = "void", 0
    else:
        new = t["amount_vnd"] - amount
        status = "paid" if t["paid_vnd"] == new else ("open" if t["paid_vnd"] == 0 else "partial")
        await ctx.exec("""UPDATE acct.receivables SET amount_vnd = %s, fee_vnd = %s, credited_vnd = credited_vnd + %s, status = %s WHERE id = %s""",
                       (new, new, amount, status, t["id"]))
    return {"credit_id": cid, "journal_id": jid, "amount_vnd": new, "status": status, "released_vnd": max(0, over)}


# ── Tiền về chưa có khoản phải thu ──

class Unapplied(Input):
    amount_vnd: Money = field("Số tiền", ge=1, example="50.000.000", aliases=("Số tiền về",))
    received_on: Day = field("Ngày tiền về", example="05/11/2026", aliases=("Ngày", "Ngày nhận"))
    memo: str = field("Nội dung / tham chiếu", max_length=1000, example="CDT MAU TT PHI GD-HSR-12A01", aliases=("Nội dung", "Nội dung chuyển khoản"),
                      help="Ghi mã liên quan (vd mã giao dịch) — khoản phải thu mang mã đó lập sau sẽ tự khớp ở Đối soát sao kê")
    payer: str = field("Bên chuyển", "", max_length=300, group="Thêm chi tiết")
    bank_ref: str = field("Mã giao dịch ngân hàng", "", max_length=100, help="Có mã ⇒ nhập lại không ghi hai lần", group="Thêm chi tiết")
    entity_code: str = field("Pháp nhân nhận tiền", "", options_query="admin.entity_options", placeholder="Pháp nhân mặc định", group="Thêm chi tiết")


@m.command("record_unapplied", resource="acct", title="Ghi tiền về chưa phân bổ", importable=True, agent="propose", risk="money")
async def record_unapplied(ctx: Ctx, inp: Unapplied) -> dict[str, Any]:
    """Tiền ĐÃ vào tài khoản công ty nhưng chưa có khoản phải thu để ghi vào (vd bên trả chuyển tiền phí khi giao dịch mới đặt cọc — phí chỉ thành khoản
    phải thu lúc ký): ghi sổ ngay (tiền gửi / tiền về chưa phân bổ), dòng nằm ở Đối soát sao kê tới khi phân bổ. Không tự tạo khoản phải thu, không đoán."""
    _need_books(ctx)
    if not inp.memo.strip():
        raise Invalid("Cần nội dung / tham chiếu để sau này phân bổ đúng khoản")
    eid = await entities.resolve(ctx, inp.entity_code) if inp.entity_code.strip() else await entities.default(ctx)
    if msg := await locked_message(ctx, eid, inp.received_on):
        raise Invalid(msg)
    ref = inp.bank_ref.strip()
    if ref and (old := await ctx.one("SELECT * FROM acct.statement_lines WHERE bank_ref = %s FOR UPDATE", (ref,))):
        if old["amount_vnd"] != inp.amount_vnd or old["status"] == "ignored" or old["entity_id"] != eid:
            raise Conflict(f"Mã giao dịch {ref} đã nạp với số tiền / pháp nhân khác hoặc đã bỏ qua")
        await _accept(ctx, old)   # dòng sao kê đang chờ ⇒ xác nhận tiền đã về (vào sổ), vẫn chờ phân bổ
        return {"line_id": old["id"], "created": False, "summary": f"Mã {ref} đã có — đã xác nhận vào sổ, chờ phân bổ"}
    lid = await ctx.val("""INSERT INTO acct.statement_lines (received_on, amount_vnd, memo, payer, bank_ref, source, created_by, entity_id)
                           VALUES (%s, %s, %s, %s, %s, 'manual', core.me(), %s) RETURNING id""",
                        (inp.received_on, inp.amount_vnd, inp.memo.strip(), inp.payer.strip(), ref, eid))
    line = await ctx.one("SELECT * FROM acct.statement_lines WHERE id = %s FOR UPDATE", (lid,))
    if line is None:
        raise NotFound("Không ghi được dòng tiền")
    await _accept(ctx, line)
    return {"line_id": lid, "created": True, "summary": f"Ghi {_vnd(inp.amount_vnd)} tiền về chưa phân bổ — phân bổ ở Đối soát sao kê"}


@m.query("unapplied", resource="acct", title="Tiền về chưa phân bổ", min_scope="all")
async def unapplied(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Tiền đã vào sổ mà chưa ghi vào khoản nào (trả thừa, đảo phiếu thu, tiền về trước khi có khoản phải thu). Tổng các dòng = số dư tài khoản "tiền
    về chưa phân bổ" của sổ cái (trừ số dư đầu kỳ nạp theo lô)."""
    rows = await ctx.all("""SELECT id, received_on, payer, memo, bank_ref, amount_vnd, allocated_vnd, amount_vnd + deducted_vnd - allocated_vnd AS left_vnd
                            FROM acct.statement_lines WHERE status = 'open' AND accepted_at IS NOT NULL AND amount_vnd + deducted_vnd > allocated_vnd
                            ORDER BY received_on, id LIMIT 2000""")
    total = int(await ctx.val("""SELECT coalesce(sum(amount_vnd + deducted_vnd - allocated_vnd), 0)::bigint FROM acct.statement_lines
                                 WHERE status = 'open' AND accepted_at IS NOT NULL""") or 0)
    books = -await ledger.balance(ctx, "unapplied:bank")
    return ui.table(rows, [ui.col("received_on", "Ngày", "date"), ui.col("left_vnd", "Chưa phân bổ", "money"), ui.col("payer", "Bên chuyển"),
                           ui.col("memo", "Nội dung", width=280), ui.col("amount_vnd", "Số tiền dòng", "money"),
                           ui.col("allocated_vnd", "Đã phân bổ", "money"), ui.col("bank_ref", "Số tham chiếu NH")],
                    title="Tiền về chưa phân bổ", kpis=[ui.kpi("Chưa phân bổ", total, "money", tone="orange" if total else "green"),
                                                         ui.kpi("Sổ cái", books, "money", tone="green" if books == total else "red",
                                                                hint="khớp" if books == total else f"chênh {_vnd(books - total)} — số dư đầu kỳ / bút toán ghi tay")],
                    empty="Không có tiền nào chờ phân bổ", note="Phân bổ ở Đối soát sao kê (Ghi phiếu thu, chọn khoản).")


# ── Chuyển giao dịch (nguồn thay thế): tiền đã thu của khoản cũ về chưa phân bổ, khoản cũ huỷ ──

class ReleaseIn(Input):
    source: str
    source_ref: str
    reason: str


@m.command("release_receipts", resource="acct", title="Chuyển tiền đã thu của khoản bị thay về chưa phân bổ", internal=True, agent="deny",
           family="receivable", risk="money",
           why="Gói khai consumes(\"receivable\") khi bên nghe sự kiện huỷ chạy cho một đối tượng nguồn ĐƯỢC THAY bằng đối tượng khác (chuyển nhượng / đổi "
               "hàng — 0156; không người bấm), nguồn của chính bên gọi (ctx.owned_by): "
               "đảo mọi phiếu thu còn hiệu lực của khoản về tiền chưa phân bổ của dòng sao kê gốc (tiền không rời ngân hàng), rồi huỷ khoản (đảo ghi "
               "nhận). Trả {released_vnd, voided}: chỉ mã khoản + tổng.")
async def release_receipts(ctx: Ctx, inp: ReleaseIn) -> dict[str, Any]:
    ctx.owned_by(inp.source.split(".")[0], f"Nguồn {inp.source}")
    rows = await ctx.all("SELECT * FROM acct.receivables WHERE source = %s AND source_ref = %s AND status <> 'void' FOR UPDATE",
                         (inp.source, inp.source_ref))
    total, voided = 0, []
    from barista.modules.acct.vat import has_active_invoice
    for t in rows:
        if await has_active_invoice(ctx, t["id"]):   # 0165: khoản đã có hoá đơn GTGT — huỷ khoản mà không huỷ / điều chỉnh hoá đơn sẽ lệch sổ thuế
            raise Invalid(f"{t['code']} đã ghi hoá đơn GTGT — kế toán huỷ / điều chỉnh hoá đơn trước rồi chuyển giao dịch")
        if t["paid_vnd"]:
            total += await release(ctx, t, t["paid_vnd"], reason=inp.reason, corrects="", on=today())
        await ctx.exec("UPDATE acct.receivables SET status = 'void' WHERE id = %s", (t["id"],))
        await void_books(ctx, t)
        voided.append(t["code"])
    return {"released_vnd": total, "voided": voided}
