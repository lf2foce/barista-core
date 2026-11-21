"""TIỀN VÀO (0057): một giao dịch ngân hàng = một dòng ``acct.statement_lines`` (``_cash_line`` / nạp sao kê); xác nhận dòng ghi sổ đủ số tiền một lần
(``_accept``); phiếu thu là phần PHÂN BỔ của dòng vào một khoản phải thu (``_allocate``) — ghi sổ theo bản chất tiền, phí thu hộ một công thức
(``fee_earned``). Phiếu thu nhập tay (``acct.record_payment``) cũng đi qua dòng tiền."""

from datetime import date
from typing import Any, Literal
from uuid import UUID

from psycopg import errors as pg

from barista.api import (
    Conflict,
    Ctx,
    Day,
    Input,
    Invalid,
    Money,
    entities,
    field,
    today,
)
from barista.kernel import ledger
from barista.modules.acct import m
from barista.modules.acct.common import _vnd, locked_message, receivable_subject
from barista.modules.acct.receivables import AR_STATUS, _rest, _target

# ── Tiền vào: một giao dịch ngân hàng = một dòng; phiếu thu = phần phân bổ của dòng (quyết định 0057) ──
#
# Mỗi đồng vào tài khoản là MỘT dòng acct.statement_lines — mã giao dịch ngân hàng duy nhất ở ĐÂY, nên cùng một mã không thể thành hai lần thu.
# Xác nhận dòng ⇒ ghi sổ ĐỦ số tiền một lần (tiền gửi / "tiền về chưa phân bổ"); phân bổ từng phần vào khoản phải thu (mọi gói lập qua
# acct.issue_receivable — 0057 / 0079) chuyển tiền từ "chưa phân bổ" sang đúng tài khoản theo bản chất. DB giữ 0 ≤ đã phân bổ ≤ số tiền; dòng chỉ
# "xong" khi phân bổ hết — tiền thừa nằm ở "chưa phân bổ", không biến mất. Phiếu thu nhập tay cũng đi qua đây (dòng nguồn "manual").

def fee_earned(fee: int, paid: int, amount: int) -> int:
    """Phí của mình đã phát sinh khi khoản thu hộ ``amount`` đã thu LUỸ KẾ ``paid`` — MỘT công thức cho ghi nhận (mỗi lần thu ghi phần chênh với số đã
    ghi) và dự phóng. Số nguyên chính xác, làm tròn nửa lên (không qua số thực: phí 100.000.000 trên 1.000.000.007, thu 714.285.720 ⇒ 71.428.571, không
    phải 71.428.572 — rà Codex 20/11 lõi tiền). Thu đủ ⇒ đúng bằng phí."""
    if amount <= 0:
        return 0
    return (2 * fee * paid + amount) // (2 * amount)



async def _cash_line(ctx: Ctx, *, amount: int, received_on: date, bank_ref: str, memo: str, entity: UUID | None = None) -> dict[str, Any]:
    """Dòng tiền vào cho phiếu thu nhập tay: mã giao dịch đã có (đã nạp sao kê / đã ghi) ⇒ dùng đúng dòng đó nếu còn đủ tiền chưa phân bổ,
    không thì từ chối; chưa có ⇒ tạo dòng "manual". Trả dòng đã khoá."""
    ref = bank_ref.strip()
    if ref:
        line = await ctx.one("SELECT * FROM acct.statement_lines WHERE bank_ref = %s FOR UPDATE", (ref,))
        if line is not None:
            rest = line["amount_vnd"] + line["deducted_vnd"] - line["allocated_vnd"]
            if line["status"] == "ignored" or rest < amount:
                raise Conflict(f"Mã giao dịch {ref} đã ghi" + (f" — còn chưa phân bổ {_vnd(rest)}" if rest > 0 else ""))
            return line
    try:
        async with ctx.conn.transaction():
            lid = await ctx.val("""INSERT INTO acct.statement_lines (received_on, amount_vnd, memo, bank_ref, source, created_by, entity_id)
                                   VALUES (%s, %s, %s, %s, 'manual', core.me(), %s) RETURNING id""",
                                (received_on, amount, memo, ref, entity))
    except pg.UniqueViolation as e:   # cùng mã ghi đồng thời ở hai nơi
        raise Conflict(f"Mã giao dịch {ref} đã ghi") from e
    line = await ctx.one("SELECT * FROM acct.statement_lines WHERE id = %s FOR UPDATE", (lid,))
    assert line is not None
    return line


async def _accept(ctx: Ctx, line: dict[str, Any]) -> None:
    """Tiền đã vào tài khoản ⇒ ghi sổ đủ số tiền MỘT lần cho dòng (chống lặp theo dòng)."""
    if line["accepted_at"] is not None:
        return
    await ledger.post(ctx, key=f"acct.cashin:{line['id']}", memo=f"Tiền về {line['bank_ref'] or line['memo'][:60]}", source="acct.cashin",
                      ref=line["bank_ref"], lines=[("cash:bank", line["amount_vnd"]), ("unapplied:bank", -line["amount_vnd"])],
                      entity=line["entity_id"],   # tiền vào tài khoản ngân hàng của pháp nhân nào ⇒ sổ pháp nhân đó (0099)
                      on=line["received_on"])     # 0119: ngày TIỀN VỀ của dòng sao kê, không phải ngày kế toán bấm
    line["accepted_at"] = await ctx.val("UPDATE acct.statement_lines SET accepted_at = now() WHERE id = %s RETURNING accepted_at", (line["id"],))


def _payer_gate(line: dict[str, Any], t: dict[str, Any], confirmed: bool) -> None:
    """0156: tiền CÓ GHI BÊN CHUYỂN do người CHỌN khoản (không phải khớp theo mã trong nội dung) mà tên bên chuyển không liên quan tới bên trả của khoản ⇒
    phải xác nhận rõ ("Đúng bên trả") — chặn phân bổ tiền của người này vào khoản của người khác cùng pháp nhân chỉ vì chọn nhầm / cố ý. Tên so không dấu,
    một bên chứa bên kia là đủ; một bên trống ⇒ không kiểm được, cho qua."""
    from barista.api import name_key
    payer, party = name_key(line.get("payer") or ""), name_key(t.get("party") or "")
    if payer and party and payer not in party and party not in payer and not confirmed:
        raise Invalid(f"Tiền do “{line['payer']}” chuyển, khoản {t['code']} của “{t['party']}” — tên khác nhau. Đúng là tiền của bên trả này thì chọn "
                      "“Đúng bên trả”, không thì chọn khoản khác")


async def _allocate(ctx: Ctx, line: dict[str, Any], ref: str, amount: int) -> dict[str, Any]:
    """Phân bổ ``amount`` của dòng (đã khoá) vào một khoản phải thu. Không vượt phần chưa phân bổ của dòng, không vượt số còn phải thu. Bút toán
    chuyển từ "chưa phân bổ" sang tài khoản theo BẢN CHẤT tiền; phát ``acct.payment_received`` — gói nguồn phản ứng (ghi nhận của gói, việc
    theo dõi, hồ sơ chuyển chặng…)."""
    t = await _target(ctx, ref)
    left = line["amount_vnd"] + line["deducted_vnd"] - line["allocated_vnd"]   # 0217: khấu trừ của bên trả mở thêm phần phân bổ được
    if amount <= 0 or amount > left:
        raise Invalid(f"Dòng tiền này chỉ còn {_vnd(left)} chưa phân bổ")
    if t["status"] in ("paid", "void"):
        raise Invalid(f"{ref.upper()} đang “{AR_STATUS.get(t['status'], t['status'])}”")
    if amount > _rest(t):
        raise Invalid(f"Thu vượt số còn phải thu ({_vnd(_rest(t))})")
    if line["entity_id"] != t["entity_id"]:   # 0099: thu hộ giữa hai pháp nhân cần bút toán nội bộ — chưa hỗ trợ, chặn rõ thay vì lệch sổ
        names = await entities.names(ctx)
        raise Invalid(f"Tiền về tài khoản của {names[str(line['entity_id'])]['name']}, khoản {t['code']} thuộc "
                      f"{names[str(t['entity_id'])]['name']} — thu hộ giữa hai pháp nhân chưa hỗ trợ (kế toán ghi tay bút toán nội bộ)")
    await _accept(ctx, line)
    paid = t["paid_vnd"] + amount
    status = "paid" if paid == t["amount_vnd"] else "partial"
    fee_now = 0
    if t["nature"] == "pass_through" and t["fee_vnd"]:
        taken = int(await ctx.val("SELECT coalesce(sum(fee_vnd), 0)::bigint FROM acct.payments WHERE receivable_id = %s", (t["id"],)) or 0)
        fee_now = fee_earned(t["fee_vnd"], paid, t["amount_vnd"]) - taken   # phí luỹ kế tới số đã thu trừ phần đã ghi ⇒ thu đủ = đúng bằng phí
    pid = await ctx.val("""INSERT INTO acct.payments (receivable_id, amount_vnd, fee_vnd, received_on, line_id, created_by, entity_id)
                           VALUES (%s, %s, %s, %s, %s, core.me(), %s) RETURNING id""",
                        (t["id"], amount, fee_now, line["received_on"], line["id"], t["entity_id"]))
    if t["nature"] in ("revenue", "deferred"):   # deferred (0147): thu tiền chỉ giảm phải thu — doanh thu nhận trước vẫn đứng ở `deferred:<pháp nhân>`
        lines = [("unapplied:bank", amount), (t["receivable_account"], -amount)]
    else:
        to = f"{'held' if t['nature'] == 'pass_through' else 'deposit'}:{t['payee_key']}"
        await ledger.ensure_account(ctx, to, f"{'Giữ hộ' if t['nature'] == 'pass_through' else 'Cọc giữ'} — {t['payee_name'] or t['party']}",
                                    "liability")
        lines = [("unapplied:bank", amount), (to, -(amount - fee_now))] + ([(t["revenue_account"], -fee_now)] if fee_now else [])
    await ledger.post(ctx, key=f"acct.payment:{pid}", memo=f"Thu {t['label']}", source="acct.payment", ref=t["code"], lines=lines,
                      entity=t["entity_id"], on=line["received_on"])   # phiếu thu mang ngày tiền về (như acct.payments.received_on)
    await ctx.exec("UPDATE acct.receivables SET paid_vnd = %s, status = %s WHERE id = %s", (paid, status, t["id"]))
    ctx.emit("acct.payment_received", receivable_id=t["id"], code=t["code"], source=t["source"], source_ref=t["source_ref"], paid_vnd=paid,
             amount_vnd=t["amount_vnd"], payment_vnd=amount)
    allocated = line["allocated_vnd"] + amount
    await ctx.exec("""UPDATE acct.statement_lines SET allocated_vnd = %s, status = CASE WHEN %s = amount_vnd + deducted_vnd THEN 'applied' ELSE status END,
                      decided_by = core.me(), decided_at = now() WHERE id = %s""", (allocated, allocated, line["id"]))
    line["allocated_vnd"] = allocated
    return {"ref": t["code"], "status": status, "amount_vnd": amount, "payment_id": pid, "fee_vnd": fee_now, "party": t["party"],
            "line_id": line["id"], "line_left": line["amount_vnd"] + line["deducted_vnd"] - allocated}


# Ô của MỘT dòng sao kê → (nhãn, tên cột hay gặp trong file ngân hàng) — MỘT nguồn cho form, mẫu Excel và bộ đọc sao kê theo tài khoản (``bank.py``);
# tên cột riêng của từng ngân hàng là DỮ LIỆU công ty (mẫu cột của tài khoản ngân hàng — 0151), không thêm vào đây.
STATEMENT_COLUMNS: dict[str, tuple[str, tuple[str, ...]]] = {
    "received_on": ("Ngày", ("Ngày giao dịch", "Ngày hiệu lực", "Ngày GD", "Ngày ghi sổ")),
    "amount_vnd": ("Số tiền", ("Số tiền ghi có", "Ghi có", "Có", "Số tiền có", "Phát sinh có", "Số tiền nhận")),
    "out_vnd": ("Số tiền ghi nợ", ("Ghi nợ", "Nợ", "Số tiền nợ", "Phát sinh nợ", "Số tiền chi")),
    "memo": ("Nội dung", ("Nội dung chuyển khoản", "Diễn giải", "Mô tả", "Nội dung giao dịch", "Chi tiết")),
    "payer": ("Bên chuyển", ("Tên đối ứng", "Người chuyển", "Đơn vị chuyển", "Tài khoản đối ứng")),
    "bank_ref": ("Mã giao dịch ngân hàng", ("Số tham chiếu", "Mã giao dịch", "Số bút toán", "Mã GD", "Số chứng từ", "Reference")),
    "bank_account": ("Tài khoản ngân hàng", ("Tài khoản nhận",)),
}


def _col(key: str) -> dict[str, Any]:
    label, aliases = STATEMENT_COLUMNS[key]
    return {"label": label, "aliases": aliases}


class StatementLine(Input):
    received_on: Day = field(**_col("received_on"), example="05/11/2026")
    amount_vnd: Money | None = field(**_col("amount_vnd"), default=None, example="162.000.000", help="Tiền VÀO")
    out_vnd: Money | None = field(**_col("out_vnd"), default=None, example="20.000.000",
                                  help="Tiền RA — chỉ để đối chiếu với lệnh chi đã ghi, không ghi sổ từ đây", group="Thêm chi tiết")
    memo: str = field(**_col("memo"), default="", max_length=1000, example="CONG TY ABC TT TT4K7Q2M")
    payer: str = field(**_col("payer"), default="", max_length=300)
    bank_ref: str = field(**_col("bank_ref"), default="", max_length=100, group="Thêm chi tiết")
    # ảnh (trợ lý đọc) ⇒ "image", còn lại "manual"; "excel" do SERVER đặt khi dòng tới từ tệp tải lên (ctx.from_file) — giá trị khác ở đây bị bỏ qua
    source: Literal["excel", "image", "manual"] = field("Nguồn", "manual", hidden=True)
    bank_account: str = field(**_col("bank_account"), default="", options_query="acct.bank_account_options",
                              help="Mã tài khoản ngân hàng của công ty — nạp file sao kê thì tự nhận ra theo số tài khoản / mẫu cột")
    entity_code: str = field("Pháp nhân nhận tiền", "", options_query="admin.entity_options", aliases=("Pháp nhân", "Công ty nhận"),
                             help="Tài khoản ngân hàng của pháp nhân nào — trống = theo tài khoản ngân hàng, không có thì pháp nhân mặc định", group="Thêm chi tiết")


async def _statement_rows(ctx: Ctx, grid: list[list[Any]]) -> list[dict[str, Any]]:
    from barista.modules.acct.bank import statement_rows
    return await statement_rows(ctx, grid)


@m.command("add_statement_line",
           purpose="Đưa dòng tiền ngân hàng vào hàng chờ đối soát để kế toán khớp với khoản phải thu — chưa ghi sổ cái.", reversible="Bấm Bỏ qua dòng ở Đối soát sao kê (dòng chưa ghi sổ)",
           resource="acct", title="Nạp dòng sao kê", importable=True, agent="propose", import_rows=_statement_rows)
async def add_statement_line(ctx: Ctx, inp: StatementLine) -> dict[str, Any]:
    """Nạp một dòng sao kê để đối soát (chưa ghi sổ cái — chỉ là hàng chờ, nên trợ lý nạp thẳng được từ ảnh sao kê). Tiền VÀO ⇒ hàng chờ đối soát
    khoản phải thu; tiền RA ⇒ dòng đối chiếu với lệnh chi đã ghi (0151). Không có tiền ⇒ bỏ qua; cùng mã giao dịch đã nạp ⇒ bỏ qua (nhập lại
    cùng sao kê không nhân đôi). File nhiều ngân hàng: mẫu cột từng tài khoản là dữ liệu công ty (Kế toán › Tài khoản ngân hàng)."""
    ctx.require("acct", "write")
    from barista.modules.acct.bank import add_out_line, bank_account
    account = await bank_account(ctx, inp.bank_account)
    eid = await entities.resolve(ctx, inp.entity_code) if inp.entity_code.strip() or account is None else account["entity_id"]
    if account is not None and eid != account["entity_id"]:
        raise Invalid(f"Tài khoản {account['code']} thuộc pháp nhân khác pháp nhân ghi trên dòng")
    # NGUỒN do server quyết (review 10/11): "excel" CHỈ khi dòng tới từ tệp tải lên qua đường nhập của lõi; ô ẩn trong đầu vào chỉ chọn được ảnh / tay
    source = "excel" if ctx.from_file else ("image" if inp.source == "image" else "manual")
    if (not inp.amount_vnd or inp.amount_vnd <= 0) and inp.out_vnd and inp.out_vnd > 0:
        return await add_out_line(ctx, account=account, entity=eid, paid_on=inp.received_on, amount=inp.out_vnd, memo=inp.memo.strip(),
                                  payee=inp.payer.strip(), bank_ref=inp.bank_ref.strip(), source=source)
    if not inp.amount_vnd or inp.amount_vnd <= 0:
        return {"skipped": "không có tiền vào / ra"}
    if msg := await locked_message(ctx, eid, inp.received_on):   # 0119: dòng tiền thuộc kỳ đã khoá không ghi sổ được ⇒ từ chối ngay lúc nạp
        raise Invalid(msg)
    lid = await ctx.val(
        """INSERT INTO acct.statement_lines (received_on, amount_vnd, memo, payer, bank_ref, source, created_by, entity_id, bank_account_id)
           VALUES (%s, %s, %s, %s, %s, %s, core.me(), %s, %s) ON CONFLICT (tenant_id, bank_ref) WHERE bank_ref <> '' DO NOTHING RETURNING id""",
        (inp.received_on, inp.amount_vnd, inp.memo.strip(), inp.payer.strip(), inp.bank_ref.strip(), source, eid,
         account["id"] if account else None))
    if lid is None:
        return {"skipped": f"mã giao dịch {inp.bank_ref} đã nạp"}
    return {"line_id": lid}


class Payment(Input):
    code: str = field("Khoản phải thu", options_query="acct.receivable_options", example="TT4K7Q2M", aliases=("Mã thanh toán", "Mã khoản phải thu"))
    amount_vnd: Money = field("Số tiền thu", ge=1, example="15tr")
    received_on: Day | None = field("Ngày nhận", None, example="05/11/2026", placeholder="Hôm nay")
    bank_ref: str = field("Mã giao dịch ngân hàng", "", max_length=100, help="Có mã ⇒ nhập lại sao kê không bị ghi hai lần", group="Thêm chi tiết")
    confirm_payer: bool = field("Đúng bên trả", False, help="Chỉ khi dòng tiền đã nạp (theo mã giao dịch) ghi bên chuyển khác bên trả của khoản mà đúng là tiền của họ", group="Thêm chi tiết")
    keep_excess: bool = field("Giữ phần thừa chưa phân bổ", False, aliases=("Thu thừa", "Phần thừa"),
                              help="Bên trả chuyển nhiều hơn số còn phải thu: ghi đủ số còn phải thu, phần thừa nằm ở tiền về chưa phân bổ. "
                                   "Không chọn ⇒ số vượt bị từ chối (chặn gõ nhầm)", group="Thêm chi tiết")


@m.command("record_payment",
           purpose="Ghi nhận khách đã trả tiền để số còn phải thu và sổ cái khớp với tiền thực về.", reversible="Lập phiếu đảo ở “Đảo phiếu thu” — tiền về lại tiền chưa phân bổ",
           resource="acct", title="Ghi tiền khách trả", importable=True)
async def record_payment(ctx: Ctx, inp: Payment) -> dict[str, Any]:
    """Phiếu thu vào một khoản phải thu, ghi sổ theo BẢN CHẤT tiền: doanh thu ⇒ giảm phải thu khách; thu hộ ⇒ phần phí của mình (theo tỷ lệ đã
    thu luỹ kế) là doanh thu, phần còn lại là nợ phải trả người nhận (held:…); cọc ⇒ nợ phải hoàn của đúng người đó (deposit:…).
    Bên trả chuyển NHIỀU HƠN số còn phải thu (0156): người ghi chọn "Giữ phần thừa chưa phân bổ" ⇒ ghi đủ số còn phải thu, phần thừa là TIỀN VỀ CHƯA
    PHÂN BỔ của dòng tiền này (trong sổ, hiện ở Đối soát sao kê, phân bổ được vào khoản khác) — tiền thật không bị từ chối cả phiếu, cũng không lặng lẽ
    mất; không chọn ⇒ từ chối như trước (chặn gõ nhầm số). Khoản đã huỷ ⇒ từ chối (tiền đã về thì acct.record_unapplied)."""
    t = await _target(ctx, inp.code)
    if t["status"] == "void":
        raise Invalid(f"{t['code']} đã huỷ — tiền đã về thì ghi “Tiền về chưa phân bổ”, rồi phân bổ vào khoản đúng")
    if inp.amount_vnd > _rest(t) and not inp.keep_excess:
        raise Invalid(f"Thu vượt số còn phải thu ({_vnd(_rest(t))}) — đúng là bên trả chuyển thừa thì chọn “Giữ phần thừa chưa phân bổ”")
    line = await _cash_line(ctx, amount=inp.amount_vnd, received_on=inp.received_on or today(), bank_ref=inp.bank_ref,
                            memo=f"Thu {t['code']}", entity=t["entity_id"])
    _payer_gate(line, t, inp.confirm_payer)   # dòng tiền đã nạp có ghi bên chuyển ⇒ phải là bên trả của khoản (0156)
    take = min(inp.amount_vnd, _rest(t))
    paid_before = t["paid_vnd"]
    out = await _allocate(ctx, line, t["code"], take) if take > 0 else None
    excess = inp.amount_vnd - take
    if excess:
        await _accept(ctx, line)   # tiền đã vào tài khoản: cả dòng vào sổ, phần thừa nằm ở "tiền về chưa phân bổ"
    return {"payment_id": out["payment_id"] if out else None, "status": out["status"] if out else t["status"], "fee_vnd": out["fee_vnd"] if out else 0,
            "applied_vnd": take, "unapplied_vnd": excess, "line_id": line["id"], "subject": receivable_subject(t),
            "changes": [{"label": "Đã thu", "before": _vnd(paid_before), "after": _vnd(paid_before + take)}] if take else [],
            "summary": (f"Ghi {_vnd(take)} vào {t['code']}" if take else f"{t['code']} đã thu đủ")
                       + (f" · thừa {_vnd(excess)} nằm ở tiền về chưa phân bổ (Đối soát sao kê)" if excess else "")}
