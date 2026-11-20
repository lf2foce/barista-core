"""TIỀN ĐẶT TẠI ĐỐI TÁC (0182) — tiền công ty đặt cọc / ký quỹ ở bên khác theo một thoả thuận (giữ hàng của nhà cung cấp, đặt cọc thuê, ký quỹ dự
thầu…) là TÀI SẢN của công ty còn nằm ở bên kia, không phải chi phí. Dùng chung mọi ngành: module này không biết hàng, đối tác hay thoả thuận nào — gói nguồn
(khai ``consumes("placement")`` — vd hàng nhận theo thoả thuận của một gói ngành) giữ nghiệp vụ, ở đây chỉ có CỬA GHI SỔ hẹp, mỗi loại bút toán một khuôn cố định:

* ``pay``     Nợ ``placed:<khoá>`` / Có tiền gửi — công ty chuyển tiền đặt cho đối tác (ngày chuyển, mã giao dịch ngân hàng làm tham chiếu).
* ``open``    Nợ ``placed:<khoá>`` / Có ``placed_interest:<khoá>`` + đối ứng số dư đầu kỳ — nạp số đang có lúc chuyển sang hệ thống.
* ``apply_deposit`` Nợ ``deposit:<khoá khách>`` / Có ``placed:<khoá>`` — tiền cọc khách đã nộp cho công ty (qua đối soát sao kê, khoản phải thu của CHÍNH
  gói nguồn) được ghép vào phần tiền đặt: quyền với đối tác chuyển sang khách, tiền của công ty tại đối tác giảm.
* ``accrue``  Nợ ``expense:late_interest`` / Có ``placed_interest:<khoá>`` — lãi chậm thanh toán phải trả đối tác (âm = miễn / giảm, có duyệt ở gói nguồn).
* ``settle``  chấm dứt thoả thuận: đối tác trừ phạt (Nợ ``expense:forfeit``), lãi họ tính (Nợ ``placed_interest`` phần đã dồn, chênh lệch vào chi phí lãi), một
  dòng điều chỉnh có lý do (tài khoản doanh thu / chi phí đã khai) — tất cả Có ``placed:<khoá>``; phần còn lại vẫn là tiền tại đối tác chờ hoàn.
* ``place_refund`` tiền hoàn về: MỘT dòng sao kê (bất biến 5 — 0057), Nợ tiền về chưa phân bổ / Có ``placed:<khoá>``.
* ``place_reverse`` sửa = bút toán đảo (dòng sao kê của tiền hoàn được mở lại).

Tài khoản theo đối tượng mang tên module nguồn (``placed:<module>-<khoá>``) ⇒ module chỉ đụng được tài khoản của chính nó, không cần bảng sở hữu
(khoá có gạch nối mà trùng tiền tố tài khoản của một module khác — ``a`` + ``b-1`` với ``a_b`` + ``1`` — bị từ chối: ``_accounts``).
Hạn mức sau khi ghi: tiền đặt ≥ 0, lãi phải trả ≥ 0 (dưới khoá tài khoản — kernel/ledger ``limits``). Kỳ đã khoá ⇒ từ chối rõ trước khi ghi.
"""

import re
from datetime import date
from typing import Any, Literal
from uuid import UUID

from barista.api import Conflict, Ctx, Input, Invalid, NotFound, entities, today
from barista.kernel import ledger, registry
from barista.modules.acct import m
from barista.modules.acct.cash_in import _accept, _cash_line
from barista.modules.acct.common import _vnd, locked_message
from barista.modules.acct.payouts import _owned_accounts

ledger.declare({
    "expense:late_interest": ("Chi phí lãi chậm thanh toán", "expense"),
    "expense:forfeit": ("Tổn thất khi chấm dứt thoả thuận (phạt, mất tiền đặt)", "expense"),
})

KEY = re.compile(r"[a-z0-9][a-z0-9-]{0,59}")
FAMILY = "placement"   # 0186: lõi không nêu tên gói nguồn — gói khai consumes("placement")
WHY_COMMON = (" Khuôn bút toán cố định theo loại, tài khoản theo đối tượng mang tên module gọi (placed:<module>-<khoá>, placed_interest:…) — không đụng "
              "được tài khoản của module khác; hạn mức tiền đặt ≥ 0 / lãi phải trả ≥ 0 dưới khoá tài khoản; kỳ đã khoá ⇒ Invalid; chống lặp theo "
              "(source, source_ref) — gọi lại khác nội dung ⇒ Conflict. Trả {journal_id} (tiền hoàn: thêm line_id). Không trả số dư của ai khác.")


def _accounts(source: str, key: str) -> tuple[str, str]:
    if not re.fullmatch(r"[a-z_]+\.[a-z_]+", source) or not KEY.fullmatch(key):
        raise Invalid("Nguồn / khoá tiền đặt không đúng dạng")
    owner = source.split(".")[0]
    mine = f"{owner.replace('_', '-')}-{key}"
    # tên tài khoản không có bảng sở hữu ⇒ khoá của module này không được rơi vào vùng tên của module KHÁC (``a`` + ``b-1`` ≡ ``a_b`` + ``1``)
    if any(o != owner and mine.startswith(o.replace("_", "-") + "-") and len(o) > len(owner) for o in registry.MODULES):
        raise Invalid("Khoá tiền đặt trùng vùng tên tài khoản của module khác — đổi khoá")
    return f"placed:{mine}", f"placed_interest:{mine}"


async def _ensure(ctx: Ctx, placed: str, interest: str, label: str) -> None:
    await ledger.ensure_account(ctx, placed, f"Tiền đặt tại đối tác — {label}"[:200], "asset")
    await ledger.ensure_account(ctx, interest, f"Lãi chậm thanh toán phải trả — {label}"[:200], "liability")


class Place(Input):
    source: str                         # <module gọi>.<loại>
    source_ref: str                     # mã nghiệp vụ duy nhất của bút toán ở module gọi
    kind: Literal["pay", "open", "apply_deposit", "accrue", "settle"]
    key: str                            # khoá thoả thuận ở module gọi (chữ thường, số, gạch)
    label: str
    on: date
    entity_id: UUID | None = None
    amount_vnd: int = 0                 # pay / apply_deposit / accrue (âm = giảm lãi)
    interest_vnd: int = 0               # open: lãi đang nợ lúc nạp; settle: lãi đối tác tính trên biên bản
    deposit_key: str = ""               # apply_deposit: deposit:<khoá> của khoản cọc khách (khoản phải thu của chính module gọi)
    bank_ref: str = ""                  # pay: mã giao dịch ngân hàng (tham chiếu bút toán)
    penalty_vnd: int = 0                # settle
    accrued_vnd: int = 0                # settle: phần lãi đã dồn được tất toán (≤ số đang dồn)
    adjustment_vnd: int = 0             # settle: đối tác trừ thêm (+) / cộng lại (−)
    adjustment_account: str = ""
    adjustment_reason: str = ""


@m.command("place", resource="acct", title="Ghi sổ tiền đặt tại đối tác", internal=True, agent="deny", family=FAMILY,
           why="Gói khai consumes(\"placement\") (vd hàng nhận theo thoả thuận với đối tác — 0182): lệnh của người có quyền ghi / quản lý thoả thuận (đã kiểm ở module gọi, "
               "số tiền từ biên bản / lịch / phép tính lãi của module gọi; ghép tiền khách chỉ từ khoản phải thu cọc do CHÍNH module gọi lập, nghe "
               "acct.payment_received)." + WHY_COMMON)
async def place(ctx: Ctx, inp: Place) -> dict[str, Any]:
    ctx.owned_by(inp.source.split(".")[0], f"Nguồn {inp.source}")
    placed, interest = _accounts(inp.source, inp.key)
    if not inp.label.strip() or not inp.source_ref.strip():
        raise Invalid("Cần nội dung và mã nghiệp vụ")
    lines: list[tuple[str, int]]
    limits: dict[str, tuple[int | None, int | None]] = {placed: (0, None), interest: (None, 0)}
    if inp.kind == "pay":
        if inp.amount_vnd <= 0:
            raise Invalid("Số tiền chuyển phải dương")
        lines = [(placed, inp.amount_vnd), ("cash:bank", -inp.amount_vnd)]
    elif inp.kind == "open":
        if inp.amount_vnd < 0 or inp.interest_vnd < 0 or not (inp.amount_vnd or inp.interest_vnd):
            raise Invalid("Số đầu kỳ: tiền đặt và lãi đang nợ không âm, không cùng bằng 0")
        lines = [(placed, inp.amount_vnd), (interest, -inp.interest_vnd), ("equity:opening", inp.interest_vnd - inp.amount_vnd)]
    elif inp.kind == "apply_deposit":
        dep = f"deposit:{inp.deposit_key}"
        if inp.amount_vnd <= 0 or not re.fullmatch(r"[a-z0-9_-]+", inp.deposit_key) or dep not in await _owned_accounts(ctx, [dep]):
            raise Invalid("Ghép tiền khách cần số dương và khoản cọc do chính module lập")
        lines = [(dep, inp.amount_vnd), (placed, -inp.amount_vnd)]
        limits[dep] = (None, 0)   # không ghép quá tiền khách đang có
    elif inp.kind == "accrue":
        if inp.amount_vnd == 0:
            raise Invalid("Số lãi bằng 0")
        lines = [("expense:late_interest", inp.amount_vnd), (interest, -inp.amount_vnd)]
    else:   # settle
        adj_kind = ledger.ACCOUNTS.get(inp.adjustment_account, ("", ""))[1]
        if min(inp.penalty_vnd, inp.interest_vnd, inp.accrued_vnd) < 0 or (inp.adjustment_vnd and (adj_kind not in ("revenue", "expense")
                                                                                                  or not inp.adjustment_reason.strip())):
            raise Invalid("Biên bản chấm dứt: phạt / lãi không âm; điều chỉnh cần tài khoản doanh thu / chi phí đã khai và lý do")
        out = inp.penalty_vnd + inp.interest_vnd + inp.adjustment_vnd
        lines = [("expense:forfeit", inp.penalty_vnd), (interest, inp.accrued_vnd), ("expense:late_interest", inp.interest_vnd - inp.accrued_vnd),
                 *([(inp.adjustment_account, inp.adjustment_vnd)] if inp.adjustment_vnd else []), (placed, -out)]
    lines = [(c, a) for c, a in lines if a]
    if len(lines) < 2:
        raise Invalid("Bút toán không có số")
    key = f"acct.place:{inp.source}:{inp.source_ref}"
    if (old := await ledger.replayed(ctx, key, lines)) is not None:
        return {"journal_id": old, "created": False}
    entity = inp.entity_id or await entities.default(ctx)
    if msg := await locked_message(ctx, entity, inp.on):
        raise Invalid(msg)
    await _ensure(ctx, placed, interest, inp.label)
    if inp.kind == "open":
        await ledger.ensure_account(ctx, "equity:opening", *ledger.ACCOUNTS["equity:opening"])
    posted = await ledger.post(ctx, key=key, memo=inp.label[:300], source="acct.place",
                                          ref=(inp.bank_ref or f"{inp.source}:{inp.source_ref}")[:200], lines=lines, on=inp.on, entity=entity,
                                          limits={c: v for c, v in limits.items() if any(c == x for x, _ in lines)})
    return {"journal_id": posted.journal_id, "created": posted.created}


class PlaceRefund(Input):
    source: str
    source_ref: str
    key: str
    label: str
    amount_vnd: int
    received_on: date
    bank_ref: str
    entity_id: UUID | None = None


@m.command("place_refund", resource="acct", title="Ghi tiền đặt được hoàn về", internal=True, agent="deny", family=FAMILY,
           why="Gói khai consumes(\"placement\"): người có quyền ghi tiền của thoả thuận ghi tiền đối tác hoàn sau chấm dứt / trả hàng — tiền vào là MỘT dòng sao kê theo mã giao "
               "dịch ngân hàng (đã nạp ⇒ dùng đúng dòng đó nếu còn đủ phần chưa phân bổ; chưa có ⇒ tạo dòng nhập tay), xác nhận dòng vào sổ một lần, "
               "phân bổ Nợ tiền về chưa phân bổ / Có placed:<module>-<khoá>." + WHY_COMMON)
async def place_refund(ctx: Ctx, inp: PlaceRefund) -> dict[str, Any]:
    ctx.owned_by(inp.source.split(".")[0], f"Nguồn {inp.source}")
    placed, interest = _accounts(inp.source, inp.key)
    if inp.amount_vnd <= 0 or not inp.bank_ref.strip():
        raise Invalid("Tiền hoàn cần số dương và mã giao dịch ngân hàng")
    lines = [("unapplied:bank", inp.amount_vnd), (placed, -inp.amount_vnd)]
    key = f"acct.place:{inp.source}:{inp.source_ref}"
    if (old := await ledger.replayed(ctx, key, lines)) is not None:
        return {"journal_id": old, "created": False}
    entity = inp.entity_id or await entities.default(ctx)
    if msg := await locked_message(ctx, entity, inp.received_on):
        raise Invalid(msg)
    line = await _cash_line(ctx, amount=inp.amount_vnd, received_on=inp.received_on, bank_ref=inp.bank_ref, memo=inp.label[:300], entity=entity)
    if line["entity_id"] != entity:
        raise Invalid("Tiền về tài khoản của pháp nhân khác pháp nhân đứng tên thoả thuận — ghi tay bút toán nội bộ")
    await _accept(ctx, line)
    if (room := line["amount_vnd"] + line["deducted_vnd"] - line["allocated_vnd"]) < inp.amount_vnd:
        raise Conflict(f"Dòng sao kê {inp.bank_ref} chỉ còn {_vnd(room)} chưa phân bổ")
    await _ensure(ctx, placed, interest, inp.label)
    posted = await ledger.post(ctx, key=key, memo=inp.label[:300], source="acct.place", ref=inp.bank_ref.strip()[:200], lines=lines,
                               on=line["received_on"], entity=entity, limits={placed: (0, None)})
    if not posted.created:   # thua yêu cầu song song cùng khoá: bên thắng đã phân bổ dòng sao kê — không phân bổ lần hai
        return {"journal_id": posted.journal_id, "line_id": line["id"], "created": False}
    jid = posted.journal_id
    await ctx.exec("INSERT INTO acct.placement_receipts (line_id, amount_vnd, journal_id, source, source_ref) VALUES (%s, %s, %s, %s, %s)",
                   (line["id"], inp.amount_vnd, jid, inp.source, inp.source_ref))   # phần phân bổ của dòng có dòng riêng (bất biến dòng sao kê)
    await ctx.exec("""UPDATE acct.statement_lines SET allocated_vnd = allocated_vnd + %s,
                          status = CASE WHEN allocated_vnd + %s = amount_vnd + deducted_vnd THEN 'applied' ELSE status END, decided_by = core.me(), decided_at = now()
                      WHERE id = %s""", (inp.amount_vnd, inp.amount_vnd, line["id"]))
    return {"journal_id": jid, "line_id": line["id"], "created": True}


class PlaceReverse(Input):
    source: str
    source_ref: str                     # mã nghiệp vụ của bút toán ĐẢO
    original_ref: str                   # mã nghiệp vụ của bút toán gốc (cùng nguồn)
    key: str
    label: str
    on: date | None = None


@m.command("place_reverse", resource="acct", title="Đảo bút toán tiền đặt", internal=True, agent="deny", family=FAMILY,
           why="Gói khai consumes(\"placement\"): lệnh đảo đã qua chuỗi duyệt của gói nguồn (người xin không tự duyệt) — đảo đúng một bút toán gốc CÙNG nguồn; bút toán tiền hoàn "
               "⇒ dòng sao kê gốc mở lại phần đã phân bổ; sau khi đảo tiền đặt / lãi phải trả không được âm." + WHY_COMMON)
async def place_reverse(ctx: Ctx, inp: PlaceReverse) -> dict[str, Any]:
    ctx.owned_by(inp.source.split(".")[0], f"Nguồn {inp.source}")
    placed, interest = _accounts(inp.source, inp.key)
    orig_key = f"acct.place:{inp.source}:{inp.original_ref}"
    orig = await ledger.journal(ctx, orig_key)
    if orig is None:
        raise NotFound("Không thấy bút toán gốc của nguồn này")
    rows = [{"code": c, "amount_vnd": a} for c, a in orig["lines"]]
    if not any(r["code"] in (placed, interest) for r in rows):
        raise Invalid("Bút toán gốc không thuộc tiền đặt của khoá này")
    if msg := await locked_message(ctx, orig["entity_id"], inp.on or today()):
        raise Invalid(msg)
    undo = await ledger.reverse(ctx, original_key=orig_key, key=f"acct.place:{inp.source}:{inp.source_ref}", memo=inp.label[:300], source="acct.place",
                                on=inp.on)
    if undo is None:
        raise NotFound("Không thấy bút toán gốc của nguồn này")
    if not undo.created:   # đảo này đã ghi (gọi lại / thua đua) — phần mở lại dòng sao kê đã làm
        return {"journal_id": undo.journal_id, "created": False}
    jid = undo.journal_id
    receipt = await ctx.one("SELECT line_id, amount_vnd FROM acct.placement_receipts WHERE journal_id = %s", (orig["id"],))
    if receipt is not None:   # tiền hoàn bị đảo ⇒ dòng âm + phần đã phân bổ của dòng sao kê gốc mở lại (tiền vẫn ở ngân hàng — phân bổ lại ở Đối soát)
        await ctx.one("SELECT id FROM acct.statement_lines WHERE id = %s FOR UPDATE", (receipt["line_id"],))
        await ctx.exec("INSERT INTO acct.placement_receipts (line_id, amount_vnd, journal_id, source, source_ref) VALUES (%s, %s, %s, %s, %s)",
                       (receipt["line_id"], -receipt["amount_vnd"], jid, inp.source, inp.source_ref))
        await ctx.exec("""UPDATE acct.statement_lines SET allocated_vnd = allocated_vnd - %s, status = 'open', decided_by = core.me(), decided_at = now()
                          WHERE id = %s""", (receipt["amount_vnd"], receipt["line_id"]))
    for code, lo_ok in ((placed, True), (interest, False)):
        bal = await ledger.balance(ctx, code)
        if (lo_ok and bal < 0) or (not lo_ok and bal > 0):
            raise Invalid(f"Đảo bút toán này làm {'tiền đặt' if lo_ok else 'lãi phải trả'} âm — đảo các bút toán sau nó trước")
    return {"journal_id": jid, "created": True}


class PlaceBalances(Input):
    source: str
    keys: list[str]


@m.query("place_balances", resource="acct", title="Số dư tiền đặt theo khoá", internal=True, agent="deny", family=FAMILY,
         why="Gói khai consumes(\"placement\") đối chiếu sổ phụ của nó với sổ cái (người có quyền xem thoả thuận cả công ty) và màn tổng quan của gói: chỉ tài khoản placed / "
             "placed_interest mang tên module gọi, theo đúng các khoá được hỏi. Trả {khoá: {placed, interest}} — chỉ số tiền, không bút toán.")
async def place_balances(ctx: Ctx, inp: PlaceBalances) -> dict[str, dict[str, int]]:
    ctx.owned_by(inp.source.split(".")[0], f"Nguồn {inp.source}")
    out: dict[str, dict[str, int]] = {}
    for k in dict.fromkeys(inp.keys):
        placed, interest = _accounts(inp.source, k)
        out[k] = {"placed": await ledger.balance(ctx, placed), "interest": -await ledger.balance(ctx, interest)}
    return out
