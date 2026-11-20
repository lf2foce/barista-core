"""0104 shared obligations, advances and settlement. All money uses the common ledger."""

import json
import re
from collections import defaultdict
from datetime import date
from typing import Any, LiteralString
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import Field

from barista.api import Conflict, Ctx, Denied, Input, Invalid, Money, NoInput, NotFound, entities, field, money, today, ui
from barista.kernel import ledger, registry
from barista.modules.acct import m


def _source(ctx: Ctx, source: str) -> None:
    if not re.fullmatch(r"[a-z_]+\.[a-z_]+", source):
        raise Invalid("Nguồn nghĩa vụ không hợp lệ")
    ctx.owned_by(source.split(".")[0], "Nghĩa vụ")


WITHHELD = "tax:withheld"   # tài khoản khấu trừ của Tài chính (khai ở ``acct/__init__``) — khấu trừ duy nhất gói tiêu thụ họ được ghi


def _via_family(ctx: Ctx, op: str) -> bool:
    """Bên gọi tới qua HỌ ``settlement`` (gói khai ``consumes`` — lõi không biết tên) chứ không có tên trong ``callers=`` của hợp đồng ``op`` (0186).
    Phần mở rộng của hợp đồng viết cho module lõi nêu tên (tài khoản nợ gộp, nhiều dòng chi phí, cấn ứng của nguồn khác, đánh dấu mật) không dành cho nó."""
    o = registry.get(op)
    return bool(ctx.caller) and o is not None and ctx.caller not in o.callers


def _own_account(ctx: Ctx, op: str, code: str) -> None:
    """0188: gói tiêu thụ họ chỉ ghi chi phí vào tài khoản do CHÍNH gói khai (hoặc sơ đồ nền của Tài chính) — không vào tài khoản của gói khác."""
    if _via_family(ctx, op) and not ledger.usable_by(code, ctx.caller or ""):
        raise Denied(f"Tài khoản {code} do gói khác khai — module {ctx.caller} không được ghi vào qua họ settlement (0188)")


async def _lock(ctx: Ctx, party: UUID, entity: UUID) -> None:
    await ctx.exec("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"settlement:{ctx.p.tenant_id}:{party}:{entity}",))


def _payload(inp: Input) -> dict[str, Any]:
    return json.loads(inp.model_dump_json())


def _liability_of(row: dict[str, Any]) -> str:
    """Tài khoản nợ phải trả của một nghĩa vụ / lệnh chi: tài khoản GỘP nguồn đã nêu lúc ghi (vd lương — không mở tài khoản sổ cái theo tên người,
    0150 §8), không thì ``payable:<người>`` như trước."""
    return str((row.get("payload") or {}).get("liability_account") or f"payable:{row['party_id']}")


async def _accounts(ctx: Ctx, party: dict[str, Any]) -> tuple[str, str, str]:
    out = []
    for prefix, kind in (("payable", "liability"), ("advance", "asset"), ("recovery", "asset")):
        code = f"{prefix}:{party['id']}"
        await ledger.ensure_account(ctx, code, party["display_name"], kind, party["user_id"])
        out.append(code)
    return out[0], out[1], out[2]


_BALANCES: LiteralString = """WITH base AS (SELECT p.* FROM acct.payables p WHERE {where}),
 adj AS (SELECT a.payable_id,sum(a.amount_vnd) v FROM acct.payable_adjustments a JOIN base b ON b.id=a.payable_id GROUP BY 1),
 paid AS (SELECT a.payable_id,sum(a.amount_vnd) v FROM acct.payout_allocations a JOIN base b ON b.id=a.payable_id
          JOIN acct.payouts x ON x.id=a.payout_id WHERE x.status<>'cancelled' GROUP BY 1),
 offset_on AS (SELECT o.payable_id,sum(o.amount_vnd) v FROM acct.advance_offsets o JOIN base b ON b.id=o.payable_id GROUP BY 1),
 offset_of AS (SELECT o.advance_id AS payable_id,sum(o.amount_vnd) v FROM acct.advance_offsets o JOIN base b ON b.id=o.advance_id GROUP BY 1),
 back AS (SELECT r.payable_id,sum(r.amount_vnd) v FROM acct.recovery_receipts r JOIN base b ON b.id=r.payable_id GROUP BY 1)
SELECT b.*,(CASE WHEN b.kind='payable'
                 THEN b.amount_vnd-b.tax_vnd-coalesce(adj.v,0)-coalesce(paid.v,0)-coalesce(offset_on.v,0)
                 ELSE b.amount_vnd+b.fee_vnd-coalesce(offset_of.v,0)-coalesce(adj.v,0)-coalesce(back.v,0) END)::bigint AS left_vnd
FROM base b LEFT JOIN adj ON adj.payable_id=b.id LEFT JOIN paid ON paid.payable_id=b.id LEFT JOIN offset_on ON offset_on.payable_id=b.id
     LEFT JOIN offset_of ON offset_of.payable_id=b.id LEFT JOIN back ON back.payable_id=b.id"""


async def _balances(ctx: Ctx, where: LiteralString, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """The one formula for what is left on an obligation/advance/recovery: pending transfers already removed, cancelled ones restored."""
    return await ctx.all(_BALANCES.replace("{where}", where), params)


async def _left(ctx: Ctx, pid: UUID) -> int:
    rows = await _balances(ctx, "p.id=%s AND p.kind='payable'", (pid,))
    return int(rows[0]["left_vnd"]) if rows else 0


async def _advance_left(ctx: Ctx, pid: UUID) -> int:
    rows = await _balances(ctx, "p.id=%s AND p.kind<>'payable'", (pid,))
    return int(rows[0]["left_vnd"]) if rows else 0


async def _offset(ctx: Ctx, payable: dict[str, Any], party: dict[str, Any], *, sources: tuple[str, ...] = (), exact: int = 0,
                  on: date | None = None) -> None:
    """Cấn tiền ứng vào nghĩa vụ vừa ghi. Mặc định (đường cũ): chỉ ứng CÙNG nguồn với nghĩa vụ + neo ví đầu kỳ, khi payload cho phép. ``sources``
    (0150): bên gọi nêu đúng các nguồn ứng được cấn và số ``exact`` đã tính trên phiếu — cấn đúng số đó hoặc từ chối (số ứng đã đổi ⇒ tính lại)."""
    if not sources and not payable["payload"].get("allow_offset", False):
        return
    if payable["payload"].get("liability_account"):
        liability, advance_code = _liability_of(payable), f"advance:{party['id']}"   # tài khoản ứng đã có nếu có tiền ứng để cấn
    else:
        liability, advance_code, _ = await _accounts(ctx, party)
    remaining = exact if sources else await _left(ctx, payable["id"])
    if sources:
        advances = await ctx.all(
            "SELECT * FROM acct.payables WHERE party_id=%s AND entity_id=%s AND kind='advance' AND split_part(source,'.',1)=ANY(%s) "
            "ORDER BY id FOR UPDATE",
            (payable["party_id"], payable["entity_id"], list(sources)),
        )
    else:
        # Only this owner's advances and historical wallet anchors; no cross-source automatic netting.
        advances = await ctx.all(
            """SELECT * FROM acct.payables WHERE party_id=%s AND entity_id=%s AND kind='advance'
               AND (split_part(source,'.',1)=%s OR source='acct.opening') ORDER BY id FOR UPDATE""",
            (payable["party_id"], payable["entity_id"], payable["source"].split(".")[0]),
        )
    for a in advances:
        amount = min(remaining, await _advance_left(ctx, a["id"]))
        if amount <= 0:
            continue
        jid = await ledger.post(
            ctx,
            key=f"acct.offset:{payable['id']}:{a['id']}",
            memo="Cấn tiền ứng",
            source="acct.offset",
            entity=payable["entity_id"],
            on=on or today(),
            lines=[(liability, amount), (advance_code, -amount)],
        )
        await ctx.exec(
            "INSERT INTO acct.advance_offsets(payable_id,advance_id,amount_vnd,journal_id) VALUES(%s,%s,%s,%s)",
            (payable["id"], a["id"], amount, jid),
        )
        remaining -= amount
    if sources and remaining:
        raise Invalid("Tiền ứng còn cấn được đã đổi so với lúc tính — tính lại rồi gửi lại")


class ExpenseLine(Input):
    account: str
    amount_vnd: int


class Deduction(Input):
    account: str
    amount_vnd: int
    basis: str


# Tham số thêm của 0150 (ngày hạch toán, nhiều dòng chi phí / khấu trừ, cấn ứng theo nguồn nêu rõ): để mặc định thì KHÔNG vào payload so chống lặp —
# nghĩa vụ ghi trước khi có các tham số này gọi lại vẫn khớp (bản cài đang chạy, sự kiện giao lại sau nâng cấp).
_NEW_DEFAULTS: dict[str, Any] = {"on": None, "expenses": [], "deductions": [], "allow_offset_from": [], "offset_vnd": 0, "liability_account": "",
                                  "confidential": "", "due_on": None}


class Recognize(Input):
    source: str
    source_ref: str
    party_id: UUID
    entity_id: UUID | None = None
    amount_vnd: int
    tax_vnd: int = 0
    tax_basis: dict[str, Any] = Field(default_factory=dict)
    expense_account: str
    label: str
    basis: str
    allow_offset: bool = False
    on: date | None = None   # ngày hạch toán (0119); trống = hôm nay
    expenses: list[ExpenseLine] = Field(default_factory=list)   # nhiều dòng chi phí; trống = một dòng expense_account
    deductions: list[Deduction] = Field(default_factory=list)   # khấu trừ giữ lại phải nộp (thuế…), thay cho tax_vnd
    allow_offset_from: list[str] = Field(default_factory=list)  # nguồn tiền ứng được cấn (khoá module)
    offset_vnd: int = 0                                          # số cấn ĐÚNG như đã tính; cần allow_offset_from
    liability_account: str = ""   # tài khoản nợ GỘP đã khai của nguồn (vd lương) thay cho payable:<người> — sổ cái không lộ số từng người
    confidential: str = ""        # tài nguyên quyền phải có phạm vi cả công ty mới thấy dòng (vd "pay.slips"); trống = như mọi nghĩa vụ
    due_on: date | None = None    # ngày đến hạn chi (0211 — vd ngày chi lương): Dòng tiền dự phóng xếp đúng tháng; trống = dồn vào tháng hiện tại


@m.command(
    "recognize_payable",
    resource="acct",
    title="Ghi nhận nghĩa vụ",
    internal=True,
    agent="deny",
    callers=("pay",), family="settlement",
    why="Nguồn chốt quyền hưởng / phiếu lương và policy trước gọi; chỉ ghi nghĩa vụ nguồn thuộc caller, party/entity hợp lệ, profile expense và tài "
    "khoản khấu trừ đã khai; ngày hạch toán không sau hôm nay, kỳ khoá thì sổ cái từ chối; cấn ứng chỉ nguồn bên gọi nêu và đúng số đã tính; "
    "sổ và nghĩa vụ cùng transaction; trả payable_id/journal_id, không dữ liệu bên khác. Gói khai consumes(\"settlement\") (0186 — vd quyền hưởng "
    "của người hưởng đã mở theo tiền thu được) chỉ dùng dạng CƠ BẢN: một dòng chi phí, tài khoản nợ payable:<người>, khấu trừ chỉ vào tax:withheld, "
    "cấn ứng chỉ tiền ứng CÙNG nguồn (allow_offset) — tài khoản nợ gộp, nhiều dòng chi phí, cấn ứng nguồn khác, đánh dấu mật chỉ cho module có tên "
    "trong callers= (Denied). Ngày đến hạn chi (due_on, 0211) mọi bên gọi nêu được — chỉ để Dòng tiền dự phóng xếp tháng, không đổi số."
    " [id: module gọi tự có — party_id lấy từ bộ phân bổ / quyền hưởng của gói nguồn (lệnh đặt người hưởng của gói đã kiểm party bằng quyền người đặt), "
    "hoặc từ phiếu lương của pay (sổ công do nguồn ghi, người do người lập kỳ thấy)]",
)
async def recognize_payable(ctx: Ctx, inp: Recognize) -> dict[str, Any]:
    _source(ctx, inp.source)
    if _via_family(ctx, "acct.recognize_payable") and (
            inp.expenses or inp.liability_account or inp.confidential or inp.allow_offset_from or inp.offset_vnd
            or any(d.account != WITHHELD for d in inp.deductions)):
        raise Denied("Nghĩa vụ của gói tiêu thụ họ settlement chỉ ở dạng cơ bản: một dòng chi phí, khấu trừ vào tax:withheld, cấn ứng cùng nguồn (0186)")
    _own_account(ctx, "acct.recognize_payable", inp.expense_account)
    if inp.amount_vnd <= 0 or not 0 <= inp.tax_vnd <= inp.amount_vnd or not inp.basis or not inp.label.strip():
        raise Invalid("Cần số tiền/căn cứ nghĩa vụ hợp lệ")
    if inp.tax_vnd and not inp.tax_basis:
        raise Invalid("Thuế cần bộ quy định và căn cứ đã xác minh")
    if ledger.ACCOUNTS.get(inp.expense_account, ("", ""))[1] != "expense":
        raise Invalid("Chưa khai profile chi phí")
    expenses = [(e.account, e.amount_vnd) for e in inp.expenses] or [(inp.expense_account, inp.amount_vnd)]
    if inp.expenses and (sum(a for _, a in expenses) != inp.amount_vnd or expenses[0][0] != inp.expense_account
                         or any(ledger.ACCOUNTS.get(c, ("", ""))[1] != "expense" or not a for c, a in expenses)):
        raise Invalid("Dòng chi phí phải là profile chi phí đã khai, khác 0, cộng đúng số nghĩa vụ")
    if inp.deductions and (inp.tax_vnd or any(d.amount_vnd <= 0 or not d.basis.strip() or ledger.ACCOUNTS.get(d.account, ("", ""))[1] != "liability"
                                              for d in inp.deductions)):
        raise Invalid("Khấu trừ cần tài khoản phải nộp đã khai, số dương và căn cứ (không kèm tax_vnd)")
    withheld = inp.tax_vnd + sum(d.amount_vnd for d in inp.deductions)
    if withheld > inp.amount_vnd:
        raise Invalid("Khấu trừ vượt số nghĩa vụ")
    if (inp.offset_vnd != 0) != bool(inp.allow_offset_from) or inp.offset_vnd < 0 or inp.offset_vnd > inp.amount_vnd - withheld \
            or any(not re.fullmatch(r"[a-z_]+", s) for s in inp.allow_offset_from):
        raise Invalid("Cấn ứng cần nguồn ứng và số cấn trong phần còn phải trả")
    if (inp.liability_account and ledger.ACCOUNTS.get(inp.liability_account, ("", ""))[1] != "liability") \
            or (inp.confidential and inp.confidential not in registry.RESOURCES):
        raise Invalid("Tài khoản nợ gộp phải là tài khoản phải trả đã khai; đánh dấu mật là một tài nguyên quyền đã khai")
    entity = inp.entity_id or await entities.default(ctx)
    if not await ctx.val("SELECT id FROM core.legal_entities WHERE id=%s AND active", (entity,)):
        raise NotFound("Không thấy pháp nhân")
    party = await ctx.call_system("parties.resolve_for_payment", party_id=inp.party_id)
    await _lock(ctx, inp.party_id, entity)
    payload = {k: v for k, v in _payload(inp).items() if k not in _NEW_DEFAULTS or v != _NEW_DEFAULTS[k]}
    old = await ctx.one(
        "SELECT id,journal_id,payload FROM acct.payables WHERE source=%s AND source_ref=%s", (inp.source, inp.source_ref)
    )
    if old:
        if old["payload"] != payload:
            raise Conflict("IDEMPOTENCY_CONFLICT: căn cứ nghĩa vụ đã đổi")
        return {"payable_id": old["id"], "journal_id": old["journal_id"]}
    code = inp.liability_account or (await _accounts(ctx, party))[0]
    pid = await ctx.val(
        """INSERT INTO acct.payables(party_id,owner_id,entity_id,source,source_ref,label,amount_vnd,tax_vnd,
                                     expense_account,payload,tax_basis,confidential,due_on)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (
            inp.party_id,
            party["user_id"],
            entity,
            inp.source,
            inp.source_ref,
            inp.label,
            inp.amount_vnd,
            withheld,
            inp.expense_account,
            Jsonb(payload),
            Jsonb(inp.tax_basis or ({"deductions": [d.model_dump() for d in inp.deductions]} if inp.deductions else {})),
            inp.confidential,
            inp.due_on,
        ),
    )
    lines = [*expenses, (code, -(inp.amount_vnd - withheld))]
    if inp.tax_vnd:
        await ledger.ensure_account(ctx, "tax:withheld", "Thuế đã khấu trừ phải nộp", "liability")
        lines.append(("tax:withheld", -inp.tax_vnd))
    lines += [(d.account, -d.amount_vnd) for d in inp.deductions]
    on = inp.on or today()
    jid = await ledger.post(
        ctx, key=f"acct.recognize:{pid}", memo=inp.label, source="acct.payable", ref=str(pid), entity=entity, lines=lines, on=on
    )
    # Only immutable journal reference is filled in the INSERT transaction.
    await ctx.exec("UPDATE acct.payables SET journal_id=%s WHERE id=%s", (jid, pid))
    payable = await ctx.one("SELECT * FROM acct.payables WHERE id=%s", (pid,))
    assert payable
    await _offset(ctx, payable, party, sources=tuple(inp.allow_offset_from), exact=inp.offset_vnd, on=inp.on)
    return {"payable_id": pid, "journal_id": jid}


class Claim(Input):
    source: str
    source_ref: str
    party_id: UUID
    entity_id: UUID | None = None
    amount_vnd: int
    expense_account: str
    label: str
    basis: str
    on: date | None = None   # ngày hạch toán (api 1.56 — nạp lịch sử: ngày của sự kiện làm quyền hưởng giảm); trống = hôm nay; không sau hôm nay


def _on(on: date | None) -> date:
    """Ngày hạch toán bên gọi nêu (nạp lịch sử ghi đúng ngày phát sinh — 0185) hoặc hôm nay; không nhận ngày sau hôm nay (kỳ khoá thì sổ cái từ chối)."""
    if on is not None and on > today():
        raise Invalid("Ngày hạch toán sau hôm nay")
    return on or today()


@m.command(
    "recognize_recovery",
    resource="acct",
    title="Lập khoản phải thu hồi từ người hưởng",
    internal=True,
    agent="deny",
    family="settlement",
    why="Gói khai consumes(\"settlement\"): nguồn thấy quyền hưởng giảm xuống dưới số đã chi NGOÀI hệ thống (lịch sử hệ cũ — bằng chứng, không phải bút "
    "toán) nên phần chi dư thành khoản "
    "đòi lại: chỉ nghĩa vụ nguồn thuộc caller, party hợp lệ, profile chi phí đã khai; Nợ phải thu hồi / Có chi phí cùng giao dịch, chống lặp theo "
    "source_ref; không chạm ngân hàng; trả payable_id/journal_id. Thu hồi tiền thật vẫn qua acct.recover_payment (người, có duyệt)."
    " [id: module gọi tự có — party_id lấy từ dòng quyền hưởng của gói nguồn (đã chốt bằng quyền người đặt / người nhập)]",
)
async def recognize_recovery(ctx: Ctx, inp: Claim) -> dict[str, Any]:
    """Khoản phải thu hồi (``kind='recovery'``) — chỉ thêm, đúng một chỗ trong sổ: tài khoản ``recovery:<người>`` ghi Nợ, chi phí ghi Có (chi phí hoa hồng
    đã ghi nhận ở hệ cũ nay giảm). Gọi lại cùng ``source_ref`` ⇒ trả khoản cũ (lệch số ⇒ xung đột)."""
    _source(ctx, inp.source)
    if inp.amount_vnd <= 0 or not inp.basis or not inp.label.strip():
        raise Invalid("Cần số tiền dương và căn cứ khoản phải thu hồi")
    if ledger.ACCOUNTS.get(inp.expense_account, ("", ""))[1] != "expense":
        raise Invalid("Chưa khai profile chi phí")
    _own_account(ctx, "acct.recognize_recovery", inp.expense_account)
    entity = inp.entity_id or await entities.default(ctx)
    party = await ctx.call_system("parties.resolve_for_payment", party_id=inp.party_id)
    await _lock(ctx, inp.party_id, entity)
    old = await ctx.one("SELECT id,journal_id,amount_vnd,kind FROM acct.payables WHERE source=%s AND source_ref=%s", (inp.source, inp.source_ref))
    if old:
        if old["kind"] != "recovery" or old["amount_vnd"] != inp.amount_vnd:
            raise Conflict("IDEMPOTENCY_CONFLICT: khoản phải thu hồi đã đổi")
        return {"payable_id": old["id"], "journal_id": old["journal_id"], "created": False}
    _, _, recovery = await _accounts(ctx, party)
    pid = await ctx.val(
        """INSERT INTO acct.payables(party_id,owner_id,entity_id,source,source_ref,kind,label,amount_vnd,expense_account,payload)
           VALUES(%s,%s,%s,%s,%s,'recovery',%s,%s,%s,%s) RETURNING id""",
        (inp.party_id, party["user_id"], entity, inp.source, inp.source_ref, inp.label, inp.amount_vnd, inp.expense_account,
         Jsonb({"basis": inp.basis})),
    )
    jid = await ledger.post(ctx, key=f"acct.recovery_claim:{pid}", memo=inp.label, source="acct.recovery_claim", ref=str(pid), entity=entity,
                            lines=[(recovery, inp.amount_vnd), (inp.expense_account, -inp.amount_vnd)], on=_on(inp.on))
    await ctx.exec("UPDATE acct.payables SET journal_id=%s WHERE id=%s", (jid, pid))
    return {"payable_id": pid, "journal_id": jid, "created": True}


class ReleaseClaim(Input):
    source: str
    ref_prefix: str
    party_id: UUID
    max_vnd: int
    action_key: str
    reason: str
    on: date | None = None   # ngày hạch toán (api 1.56); trống = hôm nay


@m.command(
    "release_recovery",
    resource="acct",
    title="Bớt khoản phải thu hồi khi quyền hưởng tăng lại",
    internal=True,
    agent="deny",
    family="settlement",
    why="Gói khai consumes(\"settlement\"): nguồn thấy quyền hưởng của người hưởng tăng lại sau khi đã lập khoản thu hồi cho phần chi dư: bớt tối đa "
    "max_vnd trên các khoản thu hồi CÒN MỞ của "
    "đúng người đó, source_ref bắt đầu bằng ref_prefix (cũ nhất trước), chỉ nguồn thuộc caller — điều chỉnh chỉ thêm + bút toán Nợ chi phí / Có phải thu hồi; chống lặp theo "
    "action_key; không chạm ngân hàng, không đụng phần đã thu hồi; trả số đã bớt."
    " [id: module gọi tự có — party_id lấy từ dòng quyền hưởng của gói nguồn]",
)
async def release_recovery(ctx: Ctx, inp: ReleaseClaim) -> dict[str, Any]:
    """Quyền hưởng tăng lại (vd phiếu thu đảo nhầm rồi thu đúng) trong khi khoản thu hồi chưa thu: bớt khoản thu hồi thay vì lập nghĩa vụ mới trả người đó —
    MỘT vị thế ròng, không trả hai lần. Phần đã thu về không bớt (người đó đã hoàn tiền ⇒ phần tăng thành nghĩa vụ như thường)."""
    _source(ctx, inp.source)
    if inp.max_vnd <= 0 or not inp.action_key or not inp.reason.strip() or not inp.ref_prefix:
        raise Invalid("Cần số dương, khoá và lý do")
    claims = await ctx.all(
        "SELECT * FROM acct.payables WHERE source=%s AND kind='recovery' AND party_id=%s AND starts_with(source_ref,%s) ORDER BY created_at,id",
        (inp.source, inp.party_id, inp.ref_prefix))
    if not claims:
        return {"released_vnd": 0}
    await _lock(ctx, inp.party_id, claims[0]["entity_id"])
    done = await ctx.val("SELECT sum(a.amount_vnd)::bigint FROM acct.payable_adjustments a WHERE a.payable_id=ANY(%s) AND a.action_key=%s",
                         ([c["id"] for c in claims], inp.action_key))
    if done is not None:
        return {"released_vnd": int(done)}   # giao lại: đã bớt rồi
    party = await ctx.call_system("parties.resolve_for_payment", party_id=inp.party_id)
    _, _, recovery = await _accounts(ctx, party)
    total = 0
    for c in claims:
        take = min(inp.max_vnd - total, await _advance_left(ctx, c["id"]))
        if take <= 0:
            continue
        jid = await ledger.post(ctx, key=f"acct.recovery_release:{c['id']}:{inp.action_key}", memo=inp.reason, source="acct.recovery_release",
                                ref=str(c["id"]), entity=c["entity_id"], lines=[(c["expense_account"], take), (recovery, -take)], on=_on(inp.on))
        await ctx.exec("INSERT INTO acct.payable_adjustments(payable_id,amount_vnd,action_key,reason,journal_id) VALUES(%s,%s,%s,%s,%s)",
                       (c["id"], take, inp.action_key, inp.reason, jid))
        total += take
        if total == inp.max_vnd:
            break
    return {"released_vnd": total}


class Prepare(Input):
    source: str
    source_ref: str
    party_id: UUID
    entity_id: UUID | None = None
    amount_vnd: int
    label: str
    recipient_instruction: str = ""
    advance: bool = False
    advance_limit_vnd: int = 0
    fee_vnd: int = 0
    payable_ids: list[UUID] = Field(default_factory=list)


@m.command(
    "prepare_payout",
    resource="acct",
    title="Lập chi đã duyệt",
    internal=True,
    agent="deny",
    family="settlement",
    why="Gói khai consumes(\"settlement\"): nguồn đã duyệt request/căn cứ/hạn mức; kiểm approved_by (phiếu do NGƯỜI KHÁC duyệt — không có thì Denied), "
    "ownership (chỉ nghĩa vụ / tiền ứng nguồn thuộc caller + mốc ví đầu kỳ acct.opening), party/entity, reserve nghĩa vụ hoặc exposure ứng "
    "dưới khoá, ghi clearing và payout nguyên tử; trả payout_id/status/amount, không chuyển ngân hàng (lệnh chờ kế toán chuyển khoản)."
    " [id: module gọi tự có — party_id lấy từ phiếu ứng / rút đã duyệt và bộ phân bổ của gói nguồn (lệnh đặt người hưởng của gói đã kiểm party bằng quyền người đặt)]",
)
async def prepare_payout(ctx: Ctx, inp: Prepare) -> dict[str, Any]:
    _source(ctx, inp.source)
    if not ctx.p.approved_by:
        raise Denied("Lập chi cần phiếu đã được người khác duyệt")
    if inp.amount_vnd <= 0 or inp.fee_vnd < 0 or (inp.fee_vnd and not inp.advance):
        raise Invalid("Số tiền chi không hợp lệ")
    party = await ctx.call_system("parties.resolve_for_payment", party_id=inp.party_id)
    entity = inp.entity_id or await entities.default(ctx)
    if not await ctx.val("SELECT id FROM core.legal_entities WHERE id=%s AND active", (entity,)):
        raise NotFound("Không thấy pháp nhân")
    await _lock(ctx, inp.party_id, entity)
    payload = _payload(inp)
    old = await ctx.one(
        "SELECT id,status,payload FROM acct.payouts WHERE source=%s AND source_ref=%s", (inp.source, inp.source_ref)
    )
    if old:
        if {k: v for k, v in old["payload"].items() if k != "liability_account"} != payload:
            raise Conflict("IDEMPOTENCY_CONFLICT: lệnh chi đã đổi")
        return {"payout_id": old["id"], "status": old["status"], "amount_vnd": inp.amount_vnd}
    owner = inp.source.split(".")[0]
    allocations: list[tuple[UUID, int]] = []
    lines: list[tuple[str, int]] = []
    confidential = ""
    if inp.advance:
        _, advance_code, _ = await _accounts(ctx, party)
        advances = await _balances(
            ctx,
            "p.party_id=%s AND p.entity_id=%s AND p.kind='advance' AND (split_part(p.source,'.',1)=%s OR p.source='acct.opening')",
            (inp.party_id, entity, owner),
        )
        exposure = sum(a["left_vnd"] for a in advances)
        if exposure + inp.amount_vnd + inp.fee_vnd > inp.advance_limit_vnd:
            raise Invalid("Vượt hạn mức ứng dưới khoá")
        aid = await ctx.val(
            """INSERT INTO acct.payables(party_id,owner_id,entity_id,source,source_ref,kind,label,amount_vnd,fee_vnd,payload)
               VALUES(%s,%s,%s,%s,%s,'advance',%s,%s,%s,%s) RETURNING id""",
            (
                inp.party_id,
                party["user_id"],
                entity,
                inp.source,
                inp.source_ref,
                inp.label,
                inp.amount_vnd,
                inp.fee_vnd,
                Jsonb(payload),
            ),
        )
        allocations.append((aid, inp.amount_vnd))
        lines = [(advance_code, inp.amount_vnd + inp.fee_vnd), ("payout:clearing", -inp.amount_vnd)]
        if inp.fee_vnd:
            # Generic finance profile, not an industry account.
            lines.append(("revenue:advance_charge", -inp.fee_vnd))
    else:
        left = inp.amount_vnd
        obligations = await ctx.all(
            """SELECT * FROM acct.payables WHERE party_id=%s AND entity_id=%s AND kind='payable'
               AND (split_part(source,'.',1)=%s OR source='acct.opening') AND (%s OR id=ANY(%s)) ORDER BY id FOR UPDATE""",
            (inp.party_id, entity, owner, not inp.payable_ids, inp.payable_ids),
        )
        for p in obligations:
            amount = min(left, max(0, await _left(ctx, p["id"])))
            if amount:
                allocations.append((p["id"], amount))
                left -= amount
        if left:
            raise Invalid("Nghĩa vụ còn được chi không đủ hoặc sai pháp nhân")
        # MỘT CỬA (review 10/11): mọi lần lập chi nghĩa vụ — đề nghị chi đơn / lô sau duyệt, rút ví — kiểm BẤT BIẾN "không chi vượt quyền đã mở" của gói nguồn
        # ngay đây, dưới khoá bên nhận, không phụ thuộc đường gọi hay cài đặt công ty
        from barista.modules.acct.bank import refuse_blocked
        await refuse_blocked(ctx, [a for a, _ in allocations])
        used = [p for p in obligations if p["id"] in {a for a, _ in allocations}]
        accts, marks = {_liability_of(p) for p in used}, {p["confidential"] for p in used}
        if len(accts) != 1 or len(marks) != 1:
            raise Invalid("Một lệnh chi không gộp nghĩa vụ khác tài khoản nợ / khác mức mật")
        liability, confidential = accts.pop(), marks.pop()
        if liability == f"payable:{party['id']}":
            await _accounts(ctx, party)   # như trước: tài khoản của người nhận có sẵn
        else:
            payload["liability_account"] = liability   # huỷ lệnh chi trả về đúng tài khoản gộp
        lines = [(liability, inp.amount_vnd), ("payout:clearing", -inp.amount_vnd)]
    pid = await ctx.val(
        """INSERT INTO acct.payouts(user_id,party_id,entity_id,source,source_ref,label,amount_vnd,recipient_name,
                                    recipient_instruction,payload,confidential)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (
            party["user_id"],
            inp.party_id,
            entity,
            inp.source,
            inp.source_ref,
            inp.label,
            inp.amount_vnd,
            party["display_name"],
            inp.recipient_instruction,
            Jsonb(payload),
            confidential,
        ),
    )
    for aid, amount in allocations:
        await ctx.exec(
            "INSERT INTO acct.payout_allocations(payout_id,payable_id,amount_vnd) VALUES(%s,%s,%s)", (pid, aid, amount)
        )
    await ledger.post(
        ctx, key=f"acct.prepare:{pid}", memo=inp.label, source="acct.prepare", ref=str(pid), entity=entity, lines=lines, on=today()
    )
    return {"payout_id": pid, "status": "pending_transfer", "amount_vnd": inp.amount_vnd}


class CancelPayout(Input):
    payout_id: UUID = field("Lệnh chi", hidden=True)
    reason: str = field("Lý do huỷ", kind="textarea", max_length=500, example="Sai số tài khoản, chưa gửi ngân hàng")
    investigation: str = field(
        "Kết quả tra soát",
        "",
        kind="textarea",
        max_length=500,
        help="Bắt buộc khi lệnh đang tra soát: ngân hàng xác nhận chưa chuyển, số phiếu / người xác nhận",
        example="Ngân hàng xác nhận chưa chuyển, phiếu tra soát số 12",
    )


async def _cancel(ctx: Ctx, p: dict[str, Any], reason: str, investigation: str = "") -> None:
    """Giải phóng phần giữ, không chạm tiền ngân hàng. Lệnh đang tra soát chỉ huỷ được khi người có quyền ghi kết quả tra soát:
    huỷ tự động (điều chỉnh từ nguồn) không có kết quả đó nên DỪNG, không tự mở lại tiền có thể đã chuyển."""
    if p["status"] == "cancelled":
        return
    if p["status"] not in ("pending_transfer", "unknown") or (p["status"] == "unknown" and not investigation.strip()):
        raise Invalid("Lệnh đang tra soát hoặc đã chuyển: cần ghi kết quả tra soát của ngân hàng trước khi huỷ, không tự huỷ")
    await _release_payout(ctx, p, reason)
    await ctx.exec("UPDATE acct.payouts SET status='cancelled',status_note=%s WHERE id=%s", (investigation.strip() or reason, p["id"]))


async def _release_payout(ctx: Ctx, p: dict[str, Any], reason: str) -> None:
    liability = _liability_of(p)
    allocations = await ctx.all(
        "SELECT a.amount_vnd,v.* FROM acct.payout_allocations a JOIN acct.payables v ON v.id=a.payable_id "
        "WHERE a.payout_id=%s ORDER BY v.id",
        (p["id"],),
    )
    if any(a["kind"] == "advance" for a in allocations):
        if len(allocations) != 1:
            raise Invalid("Cần tra soát lệnh ứng hỗn hợp")
        a = allocations[0]
        if await _advance_left(ctx, a["id"]) != a["amount_vnd"] + a["fee_vnd"]:
            raise Invalid("Ứng đã được cấn/hoàn: cần phương án điều chỉnh riêng")
        jid = await ledger.reverse(
            ctx, original_key=f"acct.prepare:{p['id']}", key=f"acct.cancel:{p['id']}", memo=reason, source="acct.cancel", on=today()
        )
        if jid is None:
            raise Invalid("Không thấy bút toán ứng cần đảo")
        await ctx.exec(
            "INSERT INTO acct.payable_adjustments(payable_id,amount_vnd,action_key,reason,journal_id) VALUES(%s,%s,%s,%s,%s)",
            (a["id"], a["amount_vnd"] + a["fee_vnd"], f"cancel:{p['id']}", reason, jid),
        )
        return
    if not allocations:
        if (p["payload"] or {}).get("legacy_request_kind") != "payout":
            raise Invalid("Lệnh ứng lịch sử cần xử lý bằng chứng đầu kỳ")
        await ledger.ensure_account(ctx, liability, p["recipient_name"], "liability", p["user_id"])
        jid = await ledger.post(
            ctx,
            key=f"acct.cancel:{p['id']}",
            memo=reason,
            source="acct.cancel",
            entity=p["entity_id"],
            lines=[("payout:clearing", p["amount_vnd"]), (liability, -p["amount_vnd"])],
            on=today(),
        )
        await ctx.exec(
            "INSERT INTO acct.payables(party_id,owner_id,entity_id,source,source_ref,label,amount_vnd,payload,journal_id) "
            "VALUES(%s,%s,%s,'acct.opening',%s,%s,%s,%s,%s)",
            (
                p["party_id"],
                p["user_id"],
                p["entity_id"],
                f"cancel:{p['id']}",
                reason,
                p["amount_vnd"],
                Jsonb({"cancelled_opening_payout": str(p["id"])}),
                jid,
            ),
        )
        return
    await ledger.post(
        ctx,
        key=f"acct.cancel:{p['id']}",
        memo=reason,
        source="acct.cancel",
        entity=p["entity_id"],
        lines=[("payout:clearing", p["amount_vnd"]), (liability, -p["amount_vnd"])],
        on=today(),
    )


@m.command("cancel_payout", resource="acct", action="manage", title="Huỷ lệnh chưa chuyển", agent="deny")
async def cancel_payout(ctx: Ctx, inp: CancelPayout) -> dict[str, Any]:
    if not ctx.p.has_scope("acct", "all"):
        raise Denied("Cần phạm vi cả công ty")
    if not inp.reason.strip():
        raise Invalid("Cần lý do huỷ")
    p = await ctx.one("SELECT * FROM acct.payouts WHERE id=%s", (inp.payout_id,))
    if not p:
        raise NotFound("Không thấy lệnh chi")
    await _lock(ctx, p["party_id"], p["entity_id"])
    p = await ctx.one("SELECT * FROM acct.payouts WHERE id=%s FOR UPDATE", (inp.payout_id,))
    assert p
    await _cancel(ctx, p, inp.reason, inp.investigation)
    return {"payout_id": p["id"], "status": "cancelled"}


class VoidSource(Input):
    source: str
    refs: list[str]
    action_key: str
    reason: str
    reduce_vnd: int | None = None
    on: date | None = None   # ngày hạch toán của điều chỉnh (api 1.56 — nạp lịch sử: ngày huỷ / ngày đảo thật); trống = hôm nay


@m.command(
    "void_payables",
    resource="acct",
    title="Điều chỉnh huỷ nghĩa vụ nguồn",
    internal=True,
    agent="deny",
    family="settlement",
    why="Gói khai consumes(\"settlement\"): nguồn đã xác nhận huỷ/giảm quyền theo workflow; chỉ nghĩa vụ nguồn caller/refs được nêu. Huỷ pending payout hoặc dừng "
    "nếu ngân hàng chưa rõ; đảo phần chưa chi, lập khoản thu hồi phần đã chi; không tăng tiền ngân hàng; trả tổng điều chỉnh.",
)
async def void_payables(ctx: Ctx, inp: VoidSource) -> dict[str, Any]:
    _source(ctx, inp.source)
    if not inp.action_key or not inp.reason.strip():
        raise Invalid("Cần căn cứ điều chỉnh")
    obligations = await ctx.all(
        "SELECT * FROM acct.payables WHERE source=%s AND source_ref=ANY(%s) ORDER BY party_id,entity_id,array_position(%s,source_ref)",
        (inp.source, inp.refs, inp.refs),
    )
    total = 0
    previous = await ctx.all(
        "SELECT a.amount_vnd,a.reason FROM acct.payable_adjustments a JOIN acct.payables p ON p.id=a.payable_id "
        "WHERE p.source=%s AND p.source_ref=ANY(%s) AND a.action_key=%s",
        (inp.source, inp.refs, inp.action_key),
    )
    if previous:
        total = sum(r["amount_vnd"] for r in previous)
        if any(r["reason"] != inp.reason for r in previous) or (inp.reduce_vnd is not None and total != inp.reduce_vnd):
            raise Conflict("IDEMPOTENCY_CONFLICT: điều chỉnh đã đổi")
        return {"adjusted_vnd": total}
    if inp.reduce_vnd is not None and inp.reduce_vnd <= 0:
        raise Invalid("Số điều chỉnh cần dương")
    for p in obligations:
        if inp.reduce_vnd is not None and total == inp.reduce_vnd:
            break
        await _lock(ctx, p["party_id"], p["entity_id"])
        if await ctx.val(
            "SELECT id FROM acct.payable_adjustments WHERE payable_id=%s AND action_key=%s", (p["id"], inp.action_key)
        ):
            continue
        if p["tax_vnd"] or len(p["payload"].get("expenses") or []) > 1 or p["payload"].get("liability_account"):
            raise Invalid("Nghĩa vụ có thuế / nhiều dòng chi phí cần phương án điều chỉnh riêng được kế toán xác nhận")
        prior = int(
            await ctx.val("SELECT coalesce(sum(amount_vnd),0) FROM acct.payable_adjustments WHERE payable_id=%s", (p["id"],))
        )
        amount = p["amount_vnd"] - prior
        if inp.reduce_vnd is not None:
            amount = min(amount, inp.reduce_vnd - total)
        if not amount:
            continue
        payouts = await ctx.all(
            """SELECT DISTINCT x.* FROM acct.payouts x JOIN acct.payout_allocations a ON a.payout_id=x.id
               WHERE a.payable_id=%s AND x.status IN ('pending_transfer','unknown') ORDER BY x.id""",
            (p["id"],),
        )
        for payout in payouts:
            await _cancel(ctx, payout, inp.reason)
        remaining = min(amount, max(0, await _left(ctx, p["id"])))
        offsets = await ctx.all(
            "SELECT o.*,o.amount_vnd+coalesce((SELECT sum(r.amount_vnd) FROM acct.advance_offsets r WHERE r.reverses=o.id),0) AS remaining "
            "FROM acct.advance_offsets o WHERE o.payable_id=%s AND o.amount_vnd>0 ORDER BY o.id",
            (p["id"],),
        )
        offset_amount = min(amount - remaining, sum(o["remaining"] for o in offsets))
        spent = amount - remaining - offset_amount
        party = await ctx.call_system("parties.resolve_for_payment", party_id=p["party_id"])
        liability, advance_code, recovery = await _accounts(ctx, party)
        lines = [(liability, remaining), (advance_code, offset_amount), (recovery, spent), (p["expense_account"], -amount)]
        jid = await ledger.post(
            ctx,
            key=f"acct.adjust:{p['id']}:{inp.action_key}",
            memo=inp.reason,
            source="acct.adjust",
            entity=p["entity_id"],
            lines=lines,
            on=_on(inp.on),
        )
        await ctx.exec(
            "INSERT INTO acct.payable_adjustments(payable_id,amount_vnd,action_key,reason,journal_id) VALUES(%s,%s,%s,%s,%s)",
            (p["id"], amount, inp.action_key, inp.reason, jid),
        )
        to_reverse = offset_amount
        for o in offsets:
            reversal = min(to_reverse, o["remaining"])
            if reversal:
                await ctx.exec(
                    "INSERT INTO acct.advance_offsets(payable_id,advance_id,amount_vnd,journal_id,reverses) VALUES(%s,%s,%s,%s,%s)",
                    (p["id"], o["advance_id"], -reversal, jid, o["id"]),
                )
                to_reverse -= reversal
        if spent:
            await ctx.exec(
                """INSERT INTO acct.payables(party_id,owner_id,entity_id,source,source_ref,kind,label,amount_vnd,payload,journal_id)
                   VALUES(%s,%s,%s,%s,%s,'recovery',%s,%s,%s,%s)""",
                (
                    p["party_id"],
                    p["owner_id"],
                    p["entity_id"],
                    inp.source,
                    f"recovery:{p['id']}:{inp.action_key}",
                    inp.reason,
                    spent,
                    Jsonb({"recovery_of": str(p["id"])}),
                    jid,
                ),
            )
        total += amount
    if inp.reduce_vnd is not None and total != inp.reduce_vnd:
        raise Invalid("Không đủ nghĩa vụ có căn cứ để điều chỉnh")
    return {"adjusted_vnd": total}


class Position(Input):
    user_id: UUID | None = None
    party_id: UUID | None = None


@m.query(
    "position",
    resource="acct",
    title="Vị thế nghĩa vụ nguồn",
    internal=True,
    family="settlement",
    why="Gói khai consumes(\"settlement\") hỏi ví của CHÍNH người gọi (bên gọi truyền user_id của principal, không id người khác — số dư người khác chỉ qua "
    "owner_positions khi có sổ cái all); chỉ tổng nghĩa vụ/ứng/thu hồi (kèm phải trả theo pháp nhân) của source caller và anchor ví đầu kỳ; không nguồn khác, ngân hàng/PII."
    " [id: chính người gọi — user_id là người đang xem ví của mình; số dư người khác chỉ qua owner_positions khi có sổ cái all, không qua thao tác này]",
)
async def position(ctx: Ctx, inp: Position) -> dict[str, Any]:
    rows = await _balances(
        ctx,
        "(p.owner_id=%s OR p.party_id=%s) AND (split_part(p.source,'.',1)=%s OR p.source='acct.opening')",
        (inp.user_id, inp.party_id, ctx.caller or ctx.module),
    )
    owed: dict[str, int] = {}
    for r in rows:
        if r["kind"] == "payable" and r["left_vnd"] > 0:
            owed[str(r["entity_id"])] = owed.get(str(r["entity_id"]), 0) + int(r["left_vnd"])
    return {**_totals(rows), "owed_by_entity": owed}


def _totals(rows: list[dict[str, Any]]) -> dict[str, int]:
    def total(kind: str) -> int:
        return max(0, sum(int(r["left_vnd"]) for r in rows if r["kind"] == kind and (r["left_vnd"] > 0 or kind != "payable")))

    return {"owed": total("payable"), "advanced": total("advance"), "recovery": total("recovery")}


@m.query(
    "owner_positions",
    resource="acct",
    title="Vị thế nghĩa vụ theo người có đăng nhập",
    internal=True,
    agent="deny",
    family="settlement",
    why="Màn ví cả công ty của gói khai consumes(\"settlement\"), chỉ khi người xem có sổ cái phạm vi all (bên gọi kiểm): tổng phải trả/đang ứng/thu hồi theo login cho nguồn "
    "caller + đầu kỳ; không người ngoài không login, không dòng chi tiết, không ngân hàng.",
)
async def owner_positions(ctx: Ctx, inp: NoInput) -> dict[str, dict[str, int]]:
    rows = await _balances(
        ctx, "p.owner_id IS NOT NULL AND (split_part(p.source,'.',1)=%s OR p.source='acct.opening')", (ctx.caller or ctx.module,)
    )
    by_owner: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_owner.setdefault(str(r["owner_id"]), []).append(r)
    return {owner: _totals(mine) for owner, mine in by_owner.items()}


@m.query(
    "party_open_vnd",
    resource="acct",
    title="Số còn dở của một bên",
    internal=True,
    agent="deny",
    callers=("parties",),
    why="parties.update báo trước khi ngừng dùng một bên: chỉ một số tổng còn phải trả/đang ứng/thu hồi của party đó, mọi nguồn — dòng đánh dấu "
    "mật chỉ được đếm (không số tiền); "
    "không dòng chi tiết, không ngân hàng; không chặn thao tác, người có quyền danh mục quyết."
    " [id: người gọi thấy — parties.update / merge đọc dòng định danh bằng RLS của người gọi (và đòi phạm vi cả công ty) rồi mới hỏi số tổng]",
)
async def party_open_vnd(ctx: Ctx, inp: Position) -> dict[str, int]:
    # quyền hệ thống bỏ qua chính sách mật ⇒ dòng mật (vd lương) chỉ ĐẾM, không cộng số tiền vào câu báo cho người quản lý danh mục (review 09/11)
    rows = await _balances(ctx, "p.party_id=%s", (inp.party_id,))
    hidden = sum(1 for r in rows if r["confidential"] and r["left_vnd"] > 0)
    return {**_totals([r for r in rows if not r["confidential"]]), "confidential": hidden}


@m.query(
    "my_settlements",
    resource=None,
    title="Nghĩa vụ của tôi",
    internal=True,
    family="settlement",
    agent="deny",
    why="Màn ví của principal ở gói khai consumes(\"settlement\"); chỉ lịch sử của login hiện tại, nguồn caller + opening, không dữ liệu người khác hoặc bank instructions.",
)
async def my_settlements(ctx: Ctx, inp: NoInput) -> list[dict[str, Any]]:
    owner = ctx.caller
    return await ctx.all(
        """SELECT id,created_at AS at,label AS memo,amount_vnd-tax_vnd AS amount_vnd,'recognized' AS state
           FROM acct.payables WHERE kind='payable' AND owner_id=core.me() AND (split_part(source,'.',1)=%s OR source='acct.opening')
           UNION ALL SELECT id,created_at,label,CASE WHEN status='cancelled' THEN 0 ELSE -amount_vnd END,status
           FROM acct.payouts WHERE user_id=core.me() AND split_part(source,'.',1)=%s
           UNION ALL SELECT a.id,a.created_at,a.reason,-a.amount_vnd,'adjusted'
           FROM acct.payable_adjustments a JOIN acct.payables p ON p.id=a.payable_id
           WHERE p.owner_id=core.me() AND p.kind='payable' AND (split_part(p.source,'.',1)=%s OR p.source='acct.opening')
           ORDER BY at DESC LIMIT 200""", (owner,owner,owner))



class SourceRefs(Input):
    source: str
    refs: list[str]


class ReceiptRefs(SourceRefs):
    before: date | None = None   # đã thu tính tới TRƯỚC ngày này (mốc cắt kỳ chi); trống = hiện tại


@m.query(
    "receipt_facts",
    resource="acct",
    title="Căn cứ thu hiện tại",
    internal=True,
    family="receivable",
    agent="deny",
    why="Gói khai consumes(\"receivable\") gọi theo source của chính nó (ctx.owned_by)/ref đã biết từ đối tượng của nó, chỉ trả phải thu/đã thu/status "
        "+ mục khoản thu + ngày thu gần nhất để mở quyền, không PII. before (api 1.54): đã thu TÍNH TỚI trước một ngày — gói mở quyền / lập kỳ chi theo mốc cắt. "
        "last_reversed_on (api 1.56): ngày đảo phiếu thu gần nhất trong cùng phạm vi — ngày của phần quyền vừa GIẢM.",
)
async def receipt_facts(ctx: Ctx, inp: ReceiptRefs) -> list[dict[str, Any]]:
    """``before`` trống ⇒ ``paid_vnd`` là số đã thu hiện tại. Có ``before`` ⇒ chỉ tính phiếu thu có ngày thu TRƯỚC ngày đó, trừ phần đã đảo trước ngày đó
    (phiếu thu và bản ghi đảo đều chỉ thêm ⇒ số của một mốc đã qua không đổi khi có thu / đảo sau mốc). ``last_received_on``: ngày thu gần nhất còn hiệu lực
    trong cùng phạm vi; ``last_reversed_on``: ngày đảo phiếu thu gần nhất trong cùng phạm vi; ``category``: mục khoản thu của khoản ("" = không phân loại)."""
    _source(ctx, inp.source)
    return await ctx.all(
        # 0156: số GỐC (trước giấy báo có) — mở quyền theo phí đã thu / phí gốc, giảm phí không làm hoa hồng mở nhanh hơn
        """SELECT r.source_ref, CASE WHEN r.status='void' THEN r.amount_vnd ELSE r.amount_vnd+r.credited_vnd END AS amount_vnd,
                  CASE WHEN %(before)s::date IS NULL THEN r.paid_vnd ELSE coalesce(p.paid, 0)::bigint END AS paid_vnd, r.status, r.category, p.last_received_on,
                  (SELECT max(x.reversed_on) FROM acct.payment_reversals x JOIN acct.payments y ON y.id = x.payment_id
                   WHERE y.receivable_id = r.id AND (%(before)s::date IS NULL OR x.reversed_on < %(before)s::date)) AS last_reversed_on
           FROM acct.receivables r
           LEFT JOIN LATERAL (
               SELECT sum(y.left_vnd) AS paid, max(y.received_on) FILTER (WHERE y.left_vnd > 0) AS last_received_on
               FROM (SELECT p.received_on, p.amount_vnd - coalesce((SELECT sum(x.amount_vnd) FROM acct.payment_reversals x
                                                                 WHERE x.payment_id = p.id AND (%(before)s::date IS NULL OR x.reversed_on < %(before)s::date)), 0)
                            AS left_vnd
                     FROM acct.payments p WHERE p.receivable_id = r.id AND (%(before)s::date IS NULL OR p.received_on < %(before)s::date)) y) p ON true
           WHERE r.source=%(source)s AND r.source_ref=ANY(%(refs)s)""",
        {"source": inp.source, "refs": inp.refs, "before": inp.before},
    )


PAYABLE_KIND = {"payable": "Phải trả", "advance": "Đang ứng", "recovery": "Phải thu hồi"}


class PayablesIn(Input):
    state: str = field("Tình trạng", "", options={"": "Tất cả (còn mở lên trước)", "open": "Còn mở", "done": "Đã xong"})
    q: str = field("Tìm", "", max_length=80, help="Tên người nhận hoặc nội dung nghĩa vụ")


@m.query("payables", resource="acct", min_scope="all", title="Khoản phải trả & ứng")
async def payables(ctx: Ctx, inp: PayablesIn) -> dict[str, Any]:
    balances = await _balances(ctx, "TRUE")
    # còn mở lên trước, rồi mới nhất — 500 dòng đầu là việc còn phải làm, không lẫn nghĩa vụ đã chi xong (rà vai kế toán 18/11/2026)
    balances.sort(key=lambda r: r["created_at"], reverse=True)
    balances.sort(key=lambda r: r["left_vnd"] == 0)
    pick = [r for r in balances if not inp.state or (r["left_vnd"] != 0) == (inp.state == "open")]
    if inp.q.strip():
        allnames = await ctx.call_system("parties.names", party_ids=list({str(r["party_id"]) for r in pick})) if pick else {}
        needle = inp.q.strip().lower()
        pick = [r for r in pick if needle in f"{allnames.get(str(r['party_id']), {}).get('name', '')} {r['label']}".lower()]
    rows = pick[:500]
    ids = list({str(r["party_id"]) for r in rows})
    names = await ctx.call_system("parties.names", party_ids=ids) if ids else {}
    ents = await entities.names(ctx)
    out = [
        {
            **{k: r[k] for k in ("id", "party_id", "entity_id", "label", "kind", "amount_vnd", "tax_vnd", "left_vnd", "created_at")},
            "party_name": names.get(str(r["party_id"]), {}).get("name", ""),
            "entity_name": ents.get(str(r["entity_id"]), {}).get("name", ""),
            "open": "yes" if r["left_vnd"] > 0 else "no",
        }
        for r in rows
    ]
    total = _totals(balances)
    hidden = await ctx.call_system("acct.confidential_open")
    seen = sum(int(r["left_vnd"]) for r in balances if r["kind"] == "payable" and r["confidential"] and r["left_vnd"] > 0)
    rest = max(0, hidden["owed"] - seen)
    return ui.table(
        out,
        [
            ui.col("party_name", "Người nhận"),
            ui.col("entity_name", "Pháp nhân"),
            ui.col("label", "Nghĩa vụ", width=240),
            ui.col("kind", "Loại", "badge", labels=PAYABLE_KIND, tones={"payable": "blue", "advance": "orange", "recovery": "red"}),
            ui.col("amount_vnd", "Số tiền", "money"),
            ui.col("tax_vnd", "Thuế giữ lại", "money"),
            ui.col("left_vnd", "Còn lại", "money"),
            ui.col("created_at", "Ghi nhận lúc", "datetime"),
        ],
        title="Khoản phải trả & ứng",
        limit=500,
        kpis=[
            ui.kpi("Còn phải trả", total["owed"], "money", hint="Mọi nghĩa vụ: hoa hồng (gồm phần ở Ví hoa hồng) + bên bán / nhà cung cấp"),
            ui.kpi("Đang ứng", total["advanced"], "money", tone="orange" if total["advanced"] else None),
            ui.kpi("Phải thu hồi", total["recovery"], "money", tone="red" if total["recovery"] else None,
                   hint="Đã chi nhiều hơn quyền hưởng (giao dịch huỷ / điều chỉnh)" if total["recovery"] else ""),
            *([ui.kpi("Phải trả mật (ẩn chi tiết)", rest, "money", hint="Vd lương: tổng trên tài khoản gộp của sổ cái, chi tiết ở màn của nghiệp vụ đó (kỳ lương)")] if rest else []),
        ],
    )


@m.query("confidential_open", resource="acct", title="Tổng nghĩa vụ mật còn phải trả", internal=True, agent="deny", callers=("acct",),
         why="Màn Khoản phải trả & ứng và Dòng tiền dự phóng (min_scope all) của người đọc kế toán: số tổng còn phải trả của mọi nghĩa vụ đánh "
             "dấu mật (để số trên màn khớp sổ cái, dòng tiền không thiếu khoản chi người xem không được thấy chi tiết) — tổng chung + tổng theo NGÀY "
             "ĐẾN HẠN + tổng lệnh chi mật đã lập đang chờ chuyển khoản (0211); không người, không dòng, không nguồn.")
async def confidential_open(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await _balances(ctx, "p.confidential<>'' AND p.kind='payable'")
    by_due: dict[str, int] = defaultdict(int)
    for r in rows:
        if r["left_vnd"] > 0:
            by_due[r["due_on"].isoformat() if r["due_on"] else ""] += int(r["left_vnd"])
    pending = int(await ctx.val("SELECT coalesce(sum(amount_vnd), 0)::bigint FROM acct.payouts WHERE confidential<>''"
                                " AND status IN ('pending_transfer','unknown')") or 0)
    return {"owed": sum(by_due.values()), "by_due": dict(by_due), "pending_payouts": pending}


# Human accounting workflow: external parties use the same approval/settlement path.
class RequestPayment(Input):
    payable_id: UUID = field("Nghĩa vụ", hidden=True)
    amount_vnd: Money = field("Số tiền chi", gt=0, example="10tr", help="Không vượt phần còn lại của nghĩa vụ")
    recipient_instruction: str = field(
        "Tài khoản nhận", kind="textarea", max_length=300, example="0101000300 — Ngân hàng mẫu — NGUYEN VAN A", help="Chụp lại trên lệnh chi"
    )
    reason: str = field("Lý do chi", max_length=300, example="Chi đợt 1")


@m.command("request_payment", resource="acct", title="Đề nghị chi nghĩa vụ", agent="propose")
async def request_payment(ctx: Ctx, inp: RequestPayment) -> dict[str, Any]:
    if not ctx.p.has_scope("acct", "all") or ctx.p.user_id is None:
        raise Denied("Cần quyền kế toán cả công ty")
    p = await ctx.one("SELECT * FROM acct.payables WHERE id=%s AND kind='payable'", (inp.payable_id,))
    if not p:
        raise NotFound("Không thấy nghĩa vụ")
    if (
        inp.amount_vnd <= 0
        or inp.amount_vnd > await _left(ctx, p["id"])
        or not inp.reason.strip()
        or not inp.recipient_instruction.strip()
    ):
        raise Invalid("Cần số tiền trong hạn mức, bên nhận và lý do")
    from barista.modules.acct.bank import flags_for_request
    warn = await flags_for_request(ctx, [p["id"]])   # 0151: gói nguồn cảnh báo (vd giao dịch chưa xác nhận) ⇒ phiếu duyệt ghi rõ, công ty bật chặn ⇒ từ chối
    data = {
        "source": p["source"].split(".")[0] + ".payment",
        "party_id": str(p["party_id"]),
        "entity_id": str(p["entity_id"]),
        "amount_vnd": inp.amount_vnd,
        "label": inp.reason,
        "recipient_instruction": inp.recipient_instruction,
        "payable_ids": [str(p["id"])],
    }
    rid = await ctx.val(
        "INSERT INTO acct.payment_requests(requested_by,payload,confidential) VALUES(core.me(),%s,%s) RETURNING id", (Jsonb(data), p["confidential"])
    )
    aid = await ctx.request_approval(
        chain="settlement_payment",
        command="acct.execute_payment",
        input={"request_id": rid},
        summary=f"Đề nghị chi {inp.amount_vnd:,} đồng: {inp.reason}" + (f" — CẢNH BÁO: {'; '.join(warn[:3])}" if warn else ""),
        on_reject="acct.reject_payment",
    )
    await ctx.exec("UPDATE acct.payment_requests SET approval_id=%s WHERE id=%s", (aid, rid))
    return {"request_id": rid, "approval_id": aid}


class PaymentRequestRef(Input):
    request_id: UUID


@m.command("execute_payment", resource="acct", title="Lập chi nghĩa vụ sau duyệt", internal=True, agent="deny")
async def execute_payment(ctx: Ctx, inp: PaymentRequestRef) -> dict[str, Any]:
    row = await ctx.one("SELECT * FROM acct.payment_requests WHERE id=%s FOR UPDATE", (inp.request_id,))
    if not row:
        raise NotFound("Không thấy đề nghị")
    if row["status"] != "requested":
        raise Invalid("Đề nghị đã xử lý")
    from barista.modules.acct.bank import flags_for_request
    await flags_for_request(ctx, [UUID(x) for x in row["payload"]["payable_ids"]])   # công ty bật chặn: cảnh báo mới phát sinh sau lúc xin ⇒ không lập chi
    result = await prepare_payout(ctx, Prepare(**row["payload"], source_ref=str(row["id"])))
    await ctx.exec("UPDATE acct.payment_requests SET status='executed' WHERE id=%s", (row["id"],))
    return result


@m.command("reject_payment", resource="acct", title="Từ chối chi nghĩa vụ", internal=True, agent="deny")
async def reject_payment(ctx: Ctx, inp: PaymentRequestRef) -> None:
    await ctx.exec("UPDATE acct.payment_requests SET status='rejected' WHERE id=%s AND status='requested'", (inp.request_id,))


class PaymentBatch(Input):
    payable_ids: list[UUID] = field("Nghĩa vụ", hidden=True)
    recipient_instruction: str = field(
        "Tài khoản nhận", kind="textarea", max_length=300, example="Theo bảng kê tài khoản của kỳ lương 10/2026, đã kiểm",
        help="Chụp lại trên từng lệnh chi của lô"
    )
    reason: str = field("Lý do chi", max_length=300, example="Chi kỳ lương 10/2026")


@m.command("request_payment_batch", resource="acct", title="Đề nghị chi một lô nghĩa vụ", agent="deny", risk="money")
async def request_payment_batch(ctx: Ctx, inp: PaymentBatch) -> dict[str, Any]:
    """MỘT phiếu duyệt cho cả lô (0150 §7), mỗi nghĩa vụ vẫn một lệnh chi riêng, qua chuỗi chi nghĩa vụ đang có. Số chi = phần còn lại của từng
    nghĩa vụ lúc xin; lúc duyệt mà phần đó đã đổi ⇒ lô không lập chi (báo lỗi trên phiếu)."""
    if not ctx.p.has_scope("acct", "all") or ctx.p.user_id is None:
        raise Denied("Cần quyền kế toán cả công ty")
    ids = list(dict.fromkeys(inp.payable_ids))
    if not ids or not inp.reason.strip() or not inp.recipient_instruction.strip():
        raise Invalid("Cần nghĩa vụ, bên nhận và lý do")
    rows = {r["id"]: r for r in await _balances(ctx, "p.id=ANY(%s) AND p.kind='payable'", (ids,))}
    if missing := [str(i) for i in ids if i not in rows or rows[i]["left_vnd"] <= 0]:
        raise Invalid(f"{len(missing)} nghĩa vụ không còn gì để chi hoặc không thấy")
    batch = [{"source": rows[i]["source"].split(".")[0] + ".payment", "party_id": str(rows[i]["party_id"]), "entity_id": str(rows[i]["entity_id"]),
              "amount_vnd": int(rows[i]["left_vnd"]), "payable_ids": [str(i)]} for i in ids]
    total = sum(b["amount_vnd"] for b in batch)
    marks = {rows[i]["confidential"] for i in ids}
    if len(marks) != 1:
        raise Invalid("Một lô không gộp nghĩa vụ khác mức mật")
    from barista.modules.acct.bank import flags_for_request
    warn = await flags_for_request(ctx, ids)
    data = {"batch": batch, "label": inp.reason, "recipient_instruction": inp.recipient_instruction}
    rid = await ctx.val("INSERT INTO acct.payment_requests(requested_by,payload,confidential) VALUES(core.me(),%s,%s) RETURNING id",
                        (Jsonb(data), marks.pop()))
    aid = await ctx.request_approval(
        chain="settlement_payment",
        command="acct.execute_payment_batch",
        input={"request_id": rid},
        summary=f"Đề nghị chi {len(batch)} nghĩa vụ, tổng {total:,} đồng: {inp.reason}".replace(",", ".")
                + (f" — CẢNH BÁO: {'; '.join(warn[:3])}" if warn else ""),
        on_reject="acct.reject_payment",
    )
    await ctx.exec("UPDATE acct.payment_requests SET approval_id=%s WHERE id=%s", (aid, rid))
    return {"request_id": rid, "approval_id": aid, "count": len(batch), "total_vnd": total}


@m.command("execute_payment_batch", resource="acct", title="Lập chi một lô sau duyệt", internal=True, agent="deny", risk="money")
async def execute_payment_batch(ctx: Ctx, inp: PaymentRequestRef) -> dict[str, Any]:
    row = await ctx.one("SELECT * FROM acct.payment_requests WHERE id=%s FOR UPDATE", (inp.request_id,))
    if not row or "batch" not in row["payload"]:
        raise NotFound("Không thấy đề nghị chi theo lô")
    if row["status"] != "requested":
        raise Invalid("Đề nghị đã xử lý")
    data = row["payload"]
    out = [await prepare_payout(ctx, Prepare(**b, label=data["label"], recipient_instruction=data["recipient_instruction"],
                                             source_ref=f"{row['id']}:{b['payable_ids'][0]}")) for b in data["batch"]]
    await ctx.exec("UPDATE acct.payment_requests SET status='executed' WHERE id=%s", (row["id"],))
    return {"payouts": len(out), "amount_vnd": sum(o["amount_vnd"] for o in out)}


class PayableIds(Input):
    payable_ids: list[UUID]


@m.query(
    "payable_states", resource="acct", title="Tình trạng chi của nghĩa vụ", internal=True, agent="deny", callers=("pay",), family="settlement",
    why="pay.run hiện 'đã chi chưa' của phiếu đã chốt; gói khai consumes(\"settlement\") (bộ kiểm 'đã chi đủ' của hồ sơ, cảnh báo chi vượt quyền đã mở — "
        "0151): chỉ các nghĩa vụ nguồn đã ghi (payable_id lưu trên phiếu / quyết định mở), chỉ của nguồn caller; trả còn phải "
        "chi / đang chờ chuyển / đã chuyển — không bên nhận, không tài khoản ngân hàng.",
)
async def payable_states(ctx: Ctx, inp: PayableIds) -> dict[str, dict[str, int]]:
    rows = await ctx.all(
        """SELECT p.id,coalesce(sum(a.amount_vnd) FILTER (WHERE x.status='transferred'),0)::bigint AS paid_vnd,
                  coalesce(sum(a.amount_vnd) FILTER (WHERE x.status IN ('pending_transfer','unknown')),0)::bigint AS pending_vnd,
                  -- 0156: nghĩa vụ có khấu trừ / nhiều dòng chi phí / tài khoản nợ gộp: điều chỉnh giảm cần phương án riêng (void_payables từ chối)
                  (p.tax_vnd > 0 OR jsonb_array_length(coalesce(p.payload->'expenses', '[]'::jsonb)) > 1
                   OR coalesce(p.payload->>'liability_account', '') <> '')::int AS complex
           FROM acct.payables p LEFT JOIN acct.payout_allocations a ON a.payable_id=p.id LEFT JOIN acct.payouts x ON x.id=a.payout_id
           WHERE p.id=ANY(%s) AND split_part(p.source,'.',1)=%s GROUP BY p.id""",
        (inp.payable_ids, ctx.caller or ctx.module),
    )
    left = {r["id"]: int(r["left_vnd"]) for r in await _balances(ctx, "p.id=ANY(%s)", (inp.payable_ids,))}
    return {str(r["id"]): {"left_vnd": left.get(r["id"], 0), "pending_vnd": int(r["pending_vnd"]), "paid_vnd": int(r["paid_vnd"]),
                           "complex": int(r["complex"])} for r in rows}


class AdvancesIn(Input):
    party_ids: list[UUID]
    entity_id: UUID | None = None
    sources: list[str]


@m.query(
    "advances_left", resource="acct", title="Tiền ứng còn cấn được", internal=True, agent="deny", callers=("pay",),
    why="pay chụp đầu vào phiếu lương: tổng tiền ứng còn mở theo người, CHỈ các nguồn ứng mà khoản cấn ứng của công ty nêu, một pháp nhân; trả "
        "{party_id: số còn}, không dòng chi tiết, không ngân hàng."
        " [id: module gọi tự có — party_ids là người có dòng trong sổ công của pay (nguồn ghi bằng quyền của nguồn)]",
)
async def advances_left(ctx: Ctx, inp: AdvancesIn) -> dict[str, int]:
    entity = inp.entity_id or await entities.default(ctx)
    rows = await _balances(ctx, "p.kind='advance' AND p.party_id=ANY(%s) AND p.entity_id=%s AND split_part(p.source,'.',1)=ANY(%s)",
                           (inp.party_ids, entity, inp.sources))
    out: dict[str, int] = {}
    for r in rows:
        if r["left_vnd"] > 0:
            out[str(r["party_id"])] = out.get(str(r["party_id"]), 0) + int(r["left_vnd"])
    return out


class UnknownPayout(Input):
    payout_id: UUID = field("Lệnh chi", hidden=True)
    reason: str = field(
        "Vì sao cần tra soát", kind="textarea", max_length=500, example="Ngân hàng báo lỗi lúc gửi lệnh, chưa rõ đã chuyển hay chưa"
    )


@m.command("mark_payout_unknown", resource="acct", action="manage", title="Chờ tra soát lệnh chi", agent="deny")
async def mark_payout_unknown(ctx: Ctx, inp: UnknownPayout) -> dict[str, Any]:
    if not ctx.p.has_scope("acct", "all") or not inp.reason.strip():
        raise Denied("Cần quyền và căn cứ tra soát")
    p = await ctx.one("SELECT * FROM acct.payouts WHERE id=%s", (inp.payout_id,))
    if not p:
        raise NotFound("Không thấy lệnh chi")
    await _lock(ctx, p["party_id"], p["entity_id"])
    n = await ctx.exec(
        "UPDATE acct.payouts SET status='unknown',status_note=%s WHERE id=%s AND status='pending_transfer'", (inp.reason, p["id"])
    )
    if not n:
        raise Invalid("Lệnh không còn chờ chuyển")
    return {"payout_id": p["id"], "status": "unknown"}


class RecoveryReceipt(Input):
    payable_id: UUID = field("Khoản cần thu hồi", hidden=True)
    line_id: UUID = field("Dòng sao kê", kind="select", options_query="acct.recovery_line_options", help="Tiền đã về tài khoản công ty")
    amount_vnd: Money = field("Số tiền hoàn", gt=0, example="3tr")
    reason: str = field("Ghi chú", max_length=300, example="Hoàn một phần theo biên bản")


@m.query("recovery_line_options", resource="acct", title="Dòng sao kê còn tiền chưa phân bổ", min_scope="all")
async def recovery_line_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    rows = await ctx.all(
        "SELECT id,received_on,amount_vnd+deducted_vnd-allocated_vnd AS rest,memo FROM acct.statement_lines WHERE status='open' "
        "AND amount_vnd+deducted_vnd>allocated_vnd ORDER BY received_on DESC LIMIT 200"
    )
    return [{"value": str(r["id"]), "label": f"{r['received_on']:%d/%m/%Y} — còn {money(r['rest'])} — {r['memo'][:60]}"} for r in rows]


@m.command("recover_payment", resource="acct", title="Ghi nhận hoàn tiền ứng / thu hồi", agent="propose")
async def recover_payment(ctx: Ctx, inp: RecoveryReceipt) -> dict[str, Any]:
    from barista.modules.acct import _accept

    if not ctx.p.has_scope("acct", "all") or inp.amount_vnd <= 0 or not inp.reason.strip():
        raise Invalid("Cần quyền, số tiền và lý do hoàn tiền")
    p = await ctx.one("SELECT * FROM acct.payables WHERE id=%s AND kind IN ('advance','recovery')", (inp.payable_id,))
    if not p:
        raise NotFound("Không thấy khoản cần thu hồi")
    await _lock(ctx, p["party_id"], p["entity_id"])
    line = await ctx.one("SELECT * FROM acct.statement_lines WHERE id=%s AND status<>'ignored' FOR UPDATE", (inp.line_id,))
    if not line or line["entity_id"] != p["entity_id"]:
        raise Invalid("Sao kê không tồn tại hoặc khác pháp nhân")
    old = await ctx.one("SELECT * FROM acct.recovery_receipts WHERE payable_id=%s AND line_id=%s", (p["id"], line["id"]))
    if old:
        if old["amount_vnd"] != inp.amount_vnd:
            raise Conflict("IDEMPOTENCY_CONFLICT: tiền hoàn đã đổi")
        return {"recovered_vnd": old["amount_vnd"]}
    if inp.amount_vnd > await _advance_left(ctx, p["id"]) or inp.amount_vnd > line["amount_vnd"] + line["deducted_vnd"] - line["allocated_vnd"]:
        raise Invalid("Tiền hoàn vượt dư cần thu hoặc phần sao kê chưa phân bổ")
    await _accept(ctx, line)
    code = ("advance:" if p["kind"] == "advance" else "recovery:") + str(p["party_id"])
    jid = await ledger.post(
        ctx,
        key=f"acct.recover:{p['id']}:{line['id']}",
        memo=inp.reason,
        source="acct.recovery",
        entity=p["entity_id"],
        lines=[("unapplied:bank", inp.amount_vnd), (code, -inp.amount_vnd)],
        on=line["received_on"],   # ngày tiền hoàn về tài khoản (dòng sao kê)
    )
    await ctx.exec(
        "INSERT INTO acct.recovery_receipts(payable_id,line_id,amount_vnd,journal_id) VALUES(%s,%s,%s,%s)",
        (p["id"], line["id"], inp.amount_vnd, jid),
    )
    allocated = line["allocated_vnd"] + inp.amount_vnd
    await ctx.exec(
        "UPDATE acct.statement_lines SET allocated_vnd=%s,status=CASE WHEN amount_vnd+deducted_vnd=%s THEN 'applied' ELSE 'open' END,decided_by=core.me(),decided_at=now() WHERE id=%s",
        (allocated, allocated, line["id"]),
    )
    return {"recovered_vnd": inp.amount_vnd}


async def _unsettled(ctx: Ctx, owner: str | None = None) -> int:
    rows = await _balances(ctx, "(%s::text IS NULL OR split_part(p.source,'.',1)=%s OR p.source='acct.opening')", (owner, owner))
    return sum(1 for r in rows if r["left_vnd"] > 0)


@m.query(
    "source_settlement_blockers",
    resource="acct",
    title="Nghĩa vụ nguồn chưa xong",
    internal=True,
    agent="deny",
    family="settlement",
    why="Gói khai consumes(\"settlement\") cần chặn tắt gói: đếm nghĩa vụ/ứng/thu hồi của caller và anchor ví cũ, gồm bên không login; không trả PII/số tiền.",
)
async def source_settlement_blockers(ctx: Ctx, inp: NoInput) -> dict[str, int]:
    pending = int(await ctx.val("SELECT count(*) FROM acct.payouts WHERE split_part(source,'.',1)=%s AND status IN ('pending_transfer','unknown')",(ctx.caller,)) or 0)
    return {"n": await _unsettled(ctx, ctx.caller) + pending}


@m.query("payout_states",resource="acct",title="Trạng thái chi của nguồn",internal=True,agent="deny",family="settlement",
         why="Gói khai consumes(\"settlement\") lấy refs từ phiếu người xem thấy qua RLS: chỉ trả source_ref/status để phân biệt đã duyệt/chờ chuyển/đã chuyển; không ngân hàng hay dữ liệu ngoài nguồn caller.")
async def payout_states(ctx: Ctx,inp: SourceRefs) -> dict[str,str]:
    _source(ctx,inp.source)
    return {r["source_ref"]:r["status"] for r in await ctx.all("SELECT source_ref,status FROM acct.payouts WHERE source=%s AND source_ref=ANY(%s)",(inp.source,inp.refs))}
