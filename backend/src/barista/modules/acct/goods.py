"""SỔ CHO GÓI HÀNG HOÁ (0217) — hai hợp đồng hẹp của Tài chính mà gói tồn kho / bán hàng / vận chuyển cần, chung mọi ngành (module này không biết hàng,
đơn hay hãng nào):

* ``post_entry`` (họ ``journal``) — BÚT TOÁN KHÔNG DÙNG TIỀN của chính gói gọi: giá vốn khi giao hàng, hàng đi đường, hàng nhập chưa có hoá đơn nhà
  cung cấp, chênh lệch kiểm kê, hàng bán bị trả lại / phải trả khách. Gói không được tự gọi ``ledger.post`` (runbook 12) và 0057 chỉ mở cho gói lập khoản phải
  thu. Chỉ tài khoản CỐ ĐỊNH do CHÍNH gói nguồn khai (``ledger.declare`` ghi chủ — 0188) có tiền tố ``inventory`` / ``expense`` / ``revenue`` / ``payable``:
  không tiền mặt, không chưa phân bổ, không ví / giữ hộ / cọc / thuế, không tài khoản nền của Tài chính. Chống lặp theo (nguồn, tham chiếu); gọi lại khác số ⇒
  ``Conflict``. Sửa = bút toán ĐẢO đúng một bút toán gốc cùng nguồn (``reverses_ref``).
* ``settle_line`` (KHÔNG mở cho quyền hệ thống — chỉ ``ctx.call`` / ``call_as`` bằng QUYỀN NGƯỜI BẤM, kế toán: RLS + ma trận quyền Tài chính áp như ghi phiếu
  thu tay) — MỘT dòng sao kê (mã giao dịch ngân hàng) = tổng các khoản phải thu trong bảng kê của bên thu hộ − khấu trừ (phí, tiền bên đó đã hoàn khách). Đủ cả
  hoặc không gì: dòng chưa đụng tới, số khớp từng đồng, mỗi khoản (theo nguồn + tham chiếu) còn đủ số, cùng pháp nhân với dòng. Ghi: tiền về một lần → khấu trừ
  (Nợ tài khoản chi phí / phải trả của gói của bảng kê, Có chưa phân bổ — một dòng ``acct.line_deductions`` mỗi mục) → phân bổ từng khoản như phiếu thu thường
  (``acct.payment_received``). Dòng xong khi phân bổ = số tiền + khấu trừ (CHECK ở DB — migration finance/075).
"""

import re
from datetime import date
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from barista.api import Conflict, Ctx, Input, Invalid, NotFound, entities, today
from barista.kernel import ledger
from barista.modules.acct import _accept, _allocate, _vnd, locked_message, m

ENTRY_PREFIXES = ("inventory", "expense", "revenue", "payable")   # bút toán không tiền: hàng, chi phí, doanh thu, phải trả — không gì đụng tiền
DEDUCTION_PREFIXES = ("expense", "payable")                        # bên trả giữ lại: phí (chi phí) / tiền đã trả hộ khách (giảm phải trả)
SOURCE = re.compile(r"[a-z_]+\.[a-z_]+")
MAX_LINES = 50
MAX_ITEMS = 2000


def owned_account(code: str, module: str, prefixes: tuple[str, ...]) -> bool:
    """Tài khoản CỐ ĐỊNH đã khai, tiền tố cho phép, do chính gói của ``module`` khai (không phải sơ đồ nền của Tài chính — 0188)."""
    from barista.kernel import registry

    if code.split(":")[0] not in prefixes or code not in ledger.ACCOUNTS:
        return False
    owners = ledger.OWNERS.get(code, set())
    roots = {ledger.code_root(op.handler.__module__) for name, op in registry.OPS.items() if name.startswith(module + ".")}
    return bool(roots & owners) and ledger.SHARED_ROOT not in roots


class EntryLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account: str
    amount_vnd: int        # dương = Nợ, âm = Có


class PostEntry(Input):
    source: str                          # <module gọi>.<loại>
    source_ref: str                      # mã nghiệp vụ duy nhất của bút toán ở module gọi
    memo: str
    on: date | None = None               # ngày hạch toán (không sau hôm nay); trống = hôm nay
    entity_id: UUID | None = None        # pháp nhân; trống = mặc định
    lines: list[EntryLine] = Field(default_factory=list)
    reverses_ref: str = ""               # tham chiếu bút toán GỐC ⇒ ghi bút toán đảo thay cho ``lines``
    reverses_source: str = ""            # nguồn của bút toán gốc — cùng module gọi; trống = ``source``


def _key(source: str, ref: str) -> str:
    return f"acct.entry:{source}:{ref}"


@m.command("post_entry", resource="acct", title="Ghi bút toán không dùng tiền của gói", internal=True, agent="deny", family="journal",
           why="Gói khai consumes(\"journal\") (vd gói tồn kho khi giao / nhận trả / kiểm kê đã duyệt, gói bán hàng khi khách trả đơn đã thu tiền): bút toán Nợ / Có "
               "giữa các tài khoản CỐ ĐỊNH do CHÍNH gói nguồn khai (ledger.declare ghi chủ — 0188), tiền tố inventory / expense / revenue / payable — không "
               "tiền, không chưa phân bổ, không ví / giữ hộ / cọc / thuế, không tài khoản nền của Tài chính; số tiền do module gọi tính (giá vốn bình quân, "
               "giá trị chứng từ). Nguồn phải mang tên module gọi (ctx.owned_by); chống lặp theo (source, source_ref), gọi lại khác số / khác pháp nhân / khác ngày đã nêu ⇒ Conflict; kỳ đã khoá ⇒ "
               "Invalid; reverses_ref (+ reverses_source cùng module) ⇒ đảo đúng một bút toán gốc của chính module. Trả {journal_id, created} — không số dư.")
async def post_entry(ctx: Ctx, inp: PostEntry) -> dict[str, Any]:
    if not SOURCE.fullmatch(inp.source) or not inp.source_ref.strip() or len(inp.source_ref) > 200 or not inp.memo.strip():
        raise Invalid("Nguồn / tham chiếu / nội dung bút toán không đúng dạng")
    module = inp.source.split(".")[0]
    ctx.owned_by(module, f"Nguồn {inp.source}")
    key = _key(inp.source, inp.source_ref)
    if inp.reverses_ref:
        if inp.lines:
            raise Invalid("Bút toán đảo không kèm dòng — lấy đúng dòng của bút toán gốc")
        src = inp.reverses_source or inp.source
        if not SOURCE.fullmatch(src) or src.split(".")[0] != module:
            raise Invalid("Chỉ đảo được bút toán của chính module gọi")
        orig = await ledger.journal(ctx, _key(src, inp.reverses_ref))
        if orig is None:
            raise NotFound(f"Không có bút toán {src} {inp.reverses_ref} để đảo")
        if (old := await ledger.journal(ctx, key)) is not None:
            if sorted(old["lines"]) != sorted((c, -a) for c, a in orig["lines"]) or old["entity_id"] != orig["entity_id"]:
                raise Conflict("IDEMPOTENCY_CONFLICT: bút toán đảo đã ghi với số / pháp nhân khác bút toán gốc")
            if inp.on is not None and old["posted_on"] != inp.on:
                raise Conflict(f"IDEMPOTENCY_CONFLICT: bút toán đảo đã ghi ngày {old['posted_on']:%d/%m/%Y}, không phải {inp.on:%d/%m/%Y}")
            return {"journal_id": old["id"], "created": False}
        if msg := await locked_message(ctx, orig["entity_id"], inp.on or today()):
            raise Invalid(msg)
        jid = await ledger.reverse(ctx, original_key=_key(src, inp.reverses_ref), key=key, memo=inp.memo[:300], source=inp.source, on=inp.on)
        return {"journal_id": jid, "created": True}
    want: dict[str, int] = {}
    for ln in inp.lines:
        want[ln.account] = want.get(ln.account, 0) + ln.amount_vnd
    lines = sorted((c, a) for c, a in want.items() if a)
    if len(lines) < 2 or len(lines) > MAX_LINES or sum(a for _, a in lines) != 0:
        raise Invalid(f"Bút toán “{inp.memo[:80]}” cần ít nhất hai dòng và cân Nợ / Có")
    if bad := [c for c, _ in lines if not owned_account(c, module, ENTRY_PREFIXES)]:
        raise Invalid(f"Tài khoản không ghi được qua hợp đồng này (phải là tài khoản cố định của chính gói, tiền tố {' / '.join(ENTRY_PREFIXES)}): "
                      f"{', '.join(bad)}")
    if (old := await ledger.journal(ctx, key)) is not None:
        if sorted(old["lines"]) != lines:
            raise Conflict("IDEMPOTENCY_CONFLICT: bút toán đã ghi với số khác")
        # Gọi lại KHÁC nội dung không phải "đã ghi": pháp nhân / ngày hạch toán mà bên gọi NÊU RÕ phải khớp bút toán cũ. Không nêu (trống = mặc định / hôm
        # nay) ⇒ nhận bút toán cũ — lần thử lại không đổi kết quả chỉ vì pháp nhân mặc định hay ngày đã đổi. Sửa pháp nhân = đảo rồi ghi lại.
        if inp.entity_id is not None and old["entity_id"] != inp.entity_id:
            raise Conflict("IDEMPOTENCY_CONFLICT: bút toán đã ghi vào pháp nhân khác — đảo rồi ghi lại, không ghi đè")
        if inp.on is not None and old["posted_on"] != inp.on:
            raise Conflict(f"IDEMPOTENCY_CONFLICT: bút toán đã ghi ngày {old['posted_on']:%d/%m/%Y}, không phải {inp.on:%d/%m/%Y}")
        return {"journal_id": old["id"], "created": False}
    entity = inp.entity_id or await entities.default(ctx)
    on = inp.on or today()
    if msg := await locked_message(ctx, entity, on):
        raise Invalid(msg)
    jid = await ledger.post(ctx, key=key, memo=inp.memo[:300], source=inp.source, ref=inp.source_ref[:200], lines=lines, on=on, entity=entity)
    return {"journal_id": jid, "created": True}


class SettleItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    source_ref: str
    amount_vnd: int


class Deduction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account: str
    amount_vnd: int
    memo: str = ""


class SettleLine(Input):
    bank_ref: str                       # mã giao dịch ngân hàng của dòng sao kê (kế toán đã nạp)
    source: str                         # bảng kê của bên thu hộ ở gói gọi (vd <module>.cod_batch) — chủ của tài khoản khấu trừ
    source_ref: str                     # số bảng kê
    items: list[SettleItem]
    deductions: list[Deduction] = Field(default_factory=list)


@m.command("settle_line", resource="acct", title="Đối soát một dòng tiền về theo bảng kê của bên thu hộ", internal=True, agent="deny")
async def settle_line(ctx: Ctx, inp: SettleLine) -> dict[str, Any]:
    """Chạy bằng QUYỀN NGƯỜI BẤM (gói gọi ``ctx.call`` / ``call_as(ctx.p)`` — không khai hợp đồng quyền hệ thống nên ``call_system`` bị từ chối). Xem đầu file."""
    if not SOURCE.fullmatch(inp.source) or not inp.source_ref.strip() or len(inp.source_ref) > 200:
        raise Invalid("Nguồn / số bảng kê không đúng dạng")
    module = inp.source.split(".")[0]
    if not inp.items or len(inp.items) > MAX_ITEMS or any(i.amount_vnd <= 0 for i in inp.items) or any(d.amount_vnd <= 0 for d in inp.deductions):
        raise Invalid("Bảng kê trống, quá dài hoặc có số tiền không dương")
    keys = [(i.source, i.source_ref) for i in inp.items]
    if len(set(keys)) != len(keys):
        raise Invalid("Bảng kê có khoản phải thu lặp lại")
    if bad := [d.account for d in inp.deductions if not owned_account(d.account, module, DEDUCTION_PREFIXES)]:
        raise Invalid(f"Khấu trừ vào tài khoản không cho phép (phải là tài khoản chi phí / phải trả cố định của gói {module}): {', '.join(bad)}")
    line = await ctx.one("SELECT * FROM acct.statement_lines WHERE bank_ref = %s FOR UPDATE", (inp.bank_ref.strip(),))
    if line is None or not inp.bank_ref.strip():
        raise NotFound(f"Chưa có dòng sao kê mã {inp.bank_ref} — nạp sao kê trước")
    if line["status"] != "open" or line["allocated_vnd"] or line["deducted_vnd"]:
        raise Conflict(f"Dòng sao kê {inp.bank_ref} đã ghi / đã bỏ qua — không đối soát lại")
    gross, ded = sum(i.amount_vnd for i in inp.items), sum(d.amount_vnd for d in inp.deductions)
    if gross - ded != line["amount_vnd"]:
        raise Invalid(f"Bảng kê {_vnd(gross)} − khấu trừ {_vnd(ded)} = {_vnd(gross - ded)}, dòng sao kê {_vnd(line['amount_vnd'])} — lệch, không ghi gì")
    found = {(r["source"], r["source_ref"]): r for r in await ctx.all(
        """SELECT r.code, r.source, r.source_ref, r.status, r.entity_id, r.amount_vnd - r.paid_vnd AS rest FROM acct.receivables r
           JOIN unnest(%s::text[], %s::text[]) AS k(s, ref) ON r.source = k.s AND r.source_ref = k.ref""",
        ([s for s, _ in keys], [r for _, r in keys]))}
    if missing := [ref for k, ref in zip(keys, (i.source_ref for i in inp.items), strict=True) if k not in found
                   or found[k]["status"] not in ("open", "partial")]:
        raise Invalid(f"{len(missing)} mục không có khoản phải thu đang chờ thu (hoặc không thuộc phạm vi người bấm): {', '.join(missing[:5])}")
    if over := [i.source_ref for i in inp.items if i.amount_vnd > found[(i.source, i.source_ref)]["rest"]]:
        raise Invalid(f"{len(over)} mục vượt số còn phải thu: {', '.join(over[:5])}")
    if other := [i.source_ref for i in inp.items if found[(i.source, i.source_ref)]["entity_id"] != line["entity_id"]]:
        raise Invalid(f"{len(other)} mục thuộc pháp nhân khác pháp nhân nhận tiền — ghi tay bút toán nội bộ: {', '.join(other[:5])}")
    if msg := await locked_message(ctx, line["entity_id"], line["received_on"]):
        raise Invalid(msg)
    await _accept(ctx, line)
    if ded:
        jid = await ledger.post(ctx, key=f"acct.deduct:{line['id']}", memo=f"Bên trả giữ lại — bảng kê {inp.source_ref}"[:300], source="acct.deduction",
                                ref=line["bank_ref"], lines=[*((d.account, d.amount_vnd) for d in inp.deductions), ("unapplied:bank", -ded)],
                                entity=line["entity_id"], on=line["received_on"])
        async with ctx.conn.cursor() as cur:
            await cur.executemany(
                """INSERT INTO acct.line_deductions (line_id, account, amount_vnd, memo, source, source_ref, journal_id, created_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, core.me())""",
                [(line["id"], d.account, d.amount_vnd, d.memo[:300], inp.source, inp.source_ref[:200], jid) for d in inp.deductions])
        await ctx.exec("UPDATE acct.statement_lines SET deducted_vnd = %s WHERE id = %s", (ded, line["id"]))
        line["deducted_vnd"] = ded
    for i in inp.items:
        await _allocate(ctx, line, found[(i.source, i.source_ref)]["code"], i.amount_vnd)
    return {"line_id": line["id"], "receivables": len(inp.items), "gross_vnd": gross, "deducted_vnd": ded}
