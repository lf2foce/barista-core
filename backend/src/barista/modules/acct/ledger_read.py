"""Hợp đồng ĐỌC sổ cái cho gói (ADR 0245, họ ``ledger_read``, ``barista.api`` 1.68): gói kế toán / báo cáo lập sổ sách từ MỘT sổ cái của Barista mà
không đi vòng qua màn xuất MISA, không đọc bảng ``ledger.*`` (truy vấn sổ ở ``kernel/ledger.py``: ``journal_page``, ``balances_on``).

* ``acct.ledger_lines`` — dòng bút toán theo NGÀY HẠCH TOÁN (0119) trong kỳ, theo trang ĐỦ BÚT TOÁN (một bút toán không bao giờ bị cắt giữa hai trang ⇒
  mỗi trang tự cân Nợ = Có): pháp nhân, tài khoản (mã, loại, số hiệu đã ánh xạ), đối tượng sổ phụ, số tiền có dấu (dương = Nợ), chứng từ nguồn.
* ``acct.ledger_balances`` — số dư theo (pháp nhân, tài khoản) hết một ngày: số dư đầu kỳ của gói không phải đọc sổ từ đầu.

Số hiệu ``mapped`` lấy từ bảng Ánh xạ tài khoản của Kế toán (tiền tố dài nhất khớp); chưa ánh xạ ⇒ ``""`` (gói tự báo, không đoán). Đối tượng chỉ có ở
tài khoản SỔ PHỤ ``<tiền tố>:<uuid>`` (ví nhân viên, tạm ứng, phải trả người hưởng…): ``{kind, id, name, user_id}``; tài khoản thường ⇒ ``None``.
Đối tượng trên dòng của tài khoản chung (vd khách của một khoản phải thu) chưa có trong sổ cái — phần đó là mở rộng sau (R3), không đoán ở đây.

Cả hai đều ``internal`` (gói gọi bằng ``ctx.call_system`` sau khi khai ``m.consumes("ledger_read")``), ``agent="deny"``. Bên GỌI khai
``min_scope="all"`` (kế toán / giám đốc cả công ty) — đây là sổ của cả công ty; acct không kiểm lại người xem.
"""

from datetime import date
from typing import Any
from uuid import UUID

from barista.api import Ctx, Input, Invalid, entities
from barista.kernel import ledger
from barista.modules.acct import _UUID_RE, _misa_account, m

PAGE = 2000   # số BÚT TOÁN tối đa một trang của acct.ledger_lines (mỗi bút toán thường 2–6 dòng)


def party_of(code: str, name: str, owner: Any) -> dict[str, Any] | None:
    """Hàm thuần: tài khoản sổ phụ ``<tiền tố>:<uuid>`` ⇒ đối tượng ``{kind, id, name, user_id}``; tài khoản khác ⇒ ``None``."""
    pre, sep, rest = code.partition(":")
    if not sep or not _UUID_RE.fullmatch(rest):
        return None
    return {"kind": pre, "id": rest, "name": name, "user_id": str(owner) if owner else None}


def _mapped(code: str, mapping: list[dict[str, Any]]) -> str:
    got = _misa_account(code, mapping)
    return "" if got == "????" else got


def _cursor(after: str) -> tuple[date, UUID] | None:
    if not after.strip():
        return None
    day, _, jid = after.strip().partition("|")
    try:
        return date.fromisoformat(day), UUID(jid)
    except ValueError:
        raise Invalid("Con trỏ trang không đúng dạng — dùng nguyên giá trị next của trang trước") from None


class LedgerLinesIn(Input):
    since: date
    until: date                       # gồm cả ngày này
    entity_code: str = ""             # trống = mọi pháp nhân (dòng vẫn ghi pháp nhân của bút toán)
    after: str = ""                   # con trỏ trang: giá trị ``next`` của trang trước


@m.query("ledger_lines", resource="ledger", title="Dòng bút toán sổ cái theo kỳ", internal=True, agent="deny", family="ledger_read",
         why="Gói lập sổ / báo cáo kế toán khai consumes(\"ledger_read\") (vd kế toán pháp định: nhật ký chung, sổ cái, bảng cân đối tài khoản — ADR 0245; "
             "thao tác gọi khai min_scope=\"all\" — người xem có quyền cả công ty; bên gọi kiểm, acct không kiểm lại) đọc dòng bút toán CHỈ từ sổ cái theo "
             "ngày hạch toán, theo trang đủ bút toán: {basis, from, to, entity, rows: [{journal, posted_on, entity, account, kind, mapped, party, "
             "amount_vnd, memo, source, source_ref}], next, accounts: {mã: {name, kind, mapped}}, entities: {mã: tên}}. amount_vnd dương = Nợ.")
async def ledger_lines(ctx: Ctx, inp: LedgerLinesIn) -> dict[str, Any]:
    if inp.since > inp.until:
        raise Invalid("Từ ngày sau đến ngày")
    eid = await entities.resolve(ctx, inp.entity_code) if inp.entity_code.strip() else None
    cur = _cursor(inp.after)
    ents = await entities.names(ctx)
    code = {k: v["code"] for k, v in ents.items()}
    mapping = await ctx.all("SELECT ledger_prefix, misa_account FROM acct.account_map")
    rows = await ledger.journal_page(ctx, inp.since, inp.until, eid, cur, PAGE + 1)
    journals = list(dict.fromkeys((r["posted_on"], r["journal_id"]) for r in rows))
    nxt = None
    if len(journals) > PAGE:   # lấy dư một bút toán để biết còn trang sau; trả đúng PAGE bút toán
        keep = set(journals[:PAGE])
        rows = [r for r in rows if (r["posted_on"], r["journal_id"]) in keep]
        last = journals[PAGE - 1]
        nxt = f"{last[0].isoformat()}|{last[1]}"
    accounts: dict[str, dict[str, Any]] = {}
    out = []
    for r in rows:
        mp = _mapped(r["code"], mapping)
        accounts.setdefault(r["code"], {"name": r["name"], "kind": r["kind"], "mapped": mp})
        out.append({"journal": str(r["journal_id"]), "posted_on": r["posted_on"].isoformat(), "entity": code[str(r["entity_id"])],
                    "account": r["code"], "kind": r["kind"], "mapped": mp, "party": party_of(r["code"], r["name"], r["owner_user_id"]),
                    "amount_vnd": int(r["amount_vnd"]), "memo": r["memo"], "source": r["source"], "source_ref": r["source_ref"] or ""})
    return {"basis": "ledger", "from": inp.since.isoformat(), "to": inp.until.isoformat(), "entity": code[str(eid)] if eid else "", "rows": out,
            "next": nxt, "accounts": accounts, "entities": {v["code"]: v["name"] for v in ents.values()}}


class LedgerBalancesIn(Input):
    on: date                          # số dư HẾT ngày này (gồm mọi bút toán có ngày hạch toán ≤ on)
    entity_code: str = ""             # trống = mọi pháp nhân (dòng vẫn theo từng pháp nhân)


@m.query("ledger_balances", resource="ledger", title="Số dư sổ cái tại một ngày", internal=True, agent="deny", family="ledger_read",
         why="Gói lập sổ / báo cáo kế toán khai consumes(\"ledger_read\") (ADR 0245; thao tác gọi khai min_scope=\"all\"; bên gọi kiểm, acct không kiểm "
             "lại) lấy số dư đầu kỳ / cuối kỳ CHỈ từ sổ cái theo ngày hạch toán, không phải đọc lại sổ từ đầu: {basis, on, entity, rows: [{entity, "
             "account, kind, mapped, party, balance_vnd}], accounts, entities}. Chỉ dòng có số dư khác 0; balance_vnd dương = dư Nợ.")
async def ledger_balances(ctx: Ctx, inp: LedgerBalancesIn) -> dict[str, Any]:
    eid = await entities.resolve(ctx, inp.entity_code) if inp.entity_code.strip() else None
    ents = await entities.names(ctx)
    code = {k: v["code"] for k, v in ents.items()}
    mapping = await ctx.all("SELECT ledger_prefix, misa_account FROM acct.account_map")
    rows = await ledger.balances_on(ctx, inp.on, eid)
    accounts: dict[str, dict[str, Any]] = {}
    out = []
    for r in rows:
        mp = _mapped(r["code"], mapping)
        accounts.setdefault(r["code"], {"name": r["name"], "kind": r["kind"], "mapped": mp})
        out.append({"entity": code[str(r["entity_id"])], "account": r["code"], "kind": r["kind"], "mapped": mp,
                    "party": party_of(r["code"], r["name"], r["owner_user_id"]), "balance_vnd": int(r["balance"])})
    return {"basis": "ledger", "on": inp.on.isoformat(), "entity": code[str(eid)] if eid else "", "rows": out, "accounts": accounts,
            "entities": {v["code"]: v["name"] for v in ents.values()}}
