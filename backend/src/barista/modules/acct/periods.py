"""0119: KHOÁ KỲ theo pháp nhân và SỐ DƯ ĐẦU KỲ theo lô.

Khoá kỳ: mốc ở ``ledger.period_locks`` (sổ cái của lõi — thao tác qua ``kernel/ledger``), chặn ở trigger của DB. ``acct.lock_period`` chỉ TIẾN mốc
(≤ hôm nay); ``acct.reopen_period`` LÙI mốc qua chuỗi duyệt ``period_reopen`` của gói (người xin không tự duyệt, trợ lý không gọi).

Đầu kỳ: dòng nhập (Excel / tay — ``acct.add_opening_line``) vào bản nháp dưới một MÃ LÔ; ``acct.open_balances`` kiểm rồi ghi cả lô một lần, ngày cắt
``D``. Hai loại dòng, hai hàng rào chống ghi đôi:
* KHOẢN CÒN MỞ theo bên (phải thu / phải trả / ứng) — bắt buộc khai MIỀN sở hữu (module có hợp đồng sổ phụ với Tài chính: bên gọi khai trong
  ``callers=`` của ``acct.issue_receivable`` / ``recognize_payable`` / ``prepare_payout``; không bao giờ ``acct``). Thành dòng sổ phụ nguồn
  ``<miền>.opening`` ⇒ chỉ cấn trừ của đúng miền thấy. Rào theo (pháp nhân, miền): đã có đầu kỳ, hoặc miền đã có dòng sổ phụ ngày ≤ D ⇒ từ chối.
* SỐ DƯ TÀI KHOẢN (tiền, tạm ứng chung, giữ hộ…) — rào theo (pháp nhân, tài khoản): đã có bút toán ngày ≤ D, hoặc đã có đầu kỳ ⇒ từ chối.
Mỗi dòng một bút toán nguồn ``acct.opening_batch`` đối ứng ``equity:opening`` (KHÔNG dùng ``acct.opening`` — nguồn đó là mốc ví 0104, mọi module
cấn trừ được). Lô "đủ" phải khớp TỔNG KIỂM khai trong lô (bảng cân đối sổ cũ); lô "một phần" được lệch, kèm lý do. Sửa = mở kỳ (duyệt) → đảo lô →
ghi lại → khoá. Chỉ VND.
"""

import re
import secrets
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from psycopg import Rollback
from psycopg import errors as pg
from psycopg.types.json import Jsonb

from barista.api import Action as A
from barista.api import (
    Conflict,
    Ctx,
    Day,
    Denied,
    Desk,
    Input,
    Invalid,
    Money,
    NoInput,
    NotFound,
    Screen,
    entities,
    field,
    line,
    today,
    ui,
)
from barista.kernel import ledger, registry
from barista.modules.acct import G_BOOK, m
from barista.modules.acct.common import CODE_ALPHABET, _vnd, locked_message

EQUITY = "equity:opening"
SOURCE = "acct.opening_batch"   # nguồn bút toán sổ cái của mọi dòng đầu kỳ (và bút toán đảo lô)
KINDS = {"account": "Số dư tài khoản", "receivable": "Phải thu còn mở", "payable": "Phải trả còn mở", "advance": "Đang ứng còn mở"}
# Hợp đồng sổ phụ: module nào khai gọi các thao tác này là "miền" được có khoản còn mở đầu kỳ của loại đó
DOMAIN_OPS = {"receivable": ("acct.issue_receivable",), "payable": ("acct.recognize_payable", "acct.prepare_payout"),
              "advance": ("acct.recognize_payable", "acct.prepare_payout")}
# Tài khoản có sổ phụ / luồng riêng — số dư đầu kỳ của chúng phải đi qua dòng khoản còn mở (hoặc không có đầu kỳ), không phải dòng số dư
CONTROLLED = ("receivable:", "payable:", "advance:", "recovery:", "wallet:")
CONTROLLED_EXACT = {"unapplied:bank", "payout:clearing", EQUITY}


def domains(kind: str) -> set[str]:
    """Miền hợp lệ cho khoản còn mở loại ``kind``: module đã nạp, khai trong ``callers=`` của hợp đồng sổ phụ tương ứng HOẶC tiêu thụ họ của hợp
    đồng đó (``consumes`` — gói ngoài lõi, vd ``receivable``)."""
    return {c for op in DOMAIN_OPS[kind] if (o := registry.OPS.get(op))
            for c in (*o.callers, *sorted(registry.FAMILY_CALLERS.get(o.family, ()) if o.family else ()))
            if c in registry.MODULES and c != "acct"}


def _account_ok(code: str) -> bool:
    if code in CONTROLLED_EXACT or code.startswith(CONTROLLED):
        return False
    return code in ledger.ACCOUNTS or bool(re.fullmatch(r"(held|deposit):[a-z0-9_-]+", code))


# ── Khoá kỳ ──

class LockPeriod(Input):
    entity_code: str = field("Pháp nhân", "", options_query="admin.entity_options", placeholder="Pháp nhân mặc định", group="Thêm chi tiết")
    locked_through: Day | None = field("Khoá tới hết ngày", None, example="31/10/2026", placeholder="Hết tháng trước",
                                       help="Không ghi / sửa được bút toán có ngày hạch toán từ ngày này trở về trước")
    reason: str = field("Ghi chú", "", max_length=300, example="Đã nộp số liệu tháng 10")


@m.command("lock_period", resource="acct", action="manage", title="Khoá kỳ kế toán", agent="deny")
async def lock_period(ctx: Ctx, inp: LockPeriod) -> dict[str, Any]:
    """Tiến mốc khoá kỳ của một pháp nhân (chỉ tiến, không quá hôm nay). Sau khoá, mọi bút toán có ngày hạch toán ≤ mốc bị DB từ chối — bản xuất
    MISA của kỳ đã khoá không đổi nữa. Lùi mốc: acct.reopen_period (có duyệt)."""
    eid = await entities.resolve(ctx, inp.entity_code)
    through = inp.locked_through or today().replace(day=1) - timedelta(days=1)   # 0225: không hỏi thứ máy đoán được — mặc định khoá tới hết tháng trước
    old = await ledger.lock_through(ctx, entity=eid, through=through, reason=inp.reason)
    return {"entity_id": eid, "locked_through": through, "previous": old,
            "summary": f"Đã khoá kỳ tới hết {through:%d/%m/%Y}"}


class ReopenPeriod(Input):
    entity_code: str = field("Pháp nhân", "", options_query="admin.entity_options", placeholder="Pháp nhân mặc định", group="Thêm chi tiết")
    locked_through: Day | None = field("Mốc khoá mới", None, example="30/09/2026", placeholder="Mở hết", help="Phải trước mốc đang khoá")
    reason: str = field("Lý do mở lại", max_length=500, example="Sửa số dư đầu kỳ ngân hàng nhập sai")


@m.command("reopen_period", resource="acct", action="manage", title="Xin mở lại kỳ đã khoá", agent="deny")
async def reopen_period(ctx: Ctx, inp: ReopenPeriod) -> dict[str, Any]:
    """Lùi mốc khoá: phiếu theo chuỗi duyệt "Mở lại kỳ kế toán đã khoá" (dữ liệu của công ty, mặc định giám đốc). Người xin không tự duyệt;
    duyệt đủ ⇒ mốc lùi, ghi tên người duyệt."""
    if not inp.reason.strip():
        raise Invalid("Cần lý do mở lại kỳ")
    if ctx.p.scope("ledger") != "all":
        raise Denied("Chỉ kế toán sổ cái cả công ty xin mở lại kỳ")
    eid = await entities.resolve(ctx, inp.entity_code)
    cur = await ledger.locked_through(ctx, eid)
    if cur is None:
        raise Invalid("Pháp nhân này chưa khoá kỳ nào")
    if inp.locked_through is not None and inp.locked_through >= cur:
        raise Invalid(f"Mốc mới phải trước mốc đang khoá ({cur:%d/%m/%Y})")
    to = f"{inp.locked_through:%d/%m/%Y}" if inp.locked_through else "mở hết"
    aid = await ctx.request_approval(chain="period_reopen", command="acct.apply_reopen_period",
                                     input={"entity_id": eid, "from_through": cur, "locked_through": inp.locked_through, "reason": inp.reason.strip()},
                                     summary=f"Mở lại kỳ đã khoá tới {cur:%d/%m/%Y} (về {to}) — {inp.reason.strip()}")
    return {"approval_id": aid, "status": "pending"}


class ApplyReopen(Input):
    entity_id: UUID
    from_through: date
    locked_through: date | None = None
    reason: str


@m.command("apply_reopen_period", resource="acct", action="manage", title="Mở lại kỳ sau duyệt", internal=True, agent="deny")
async def apply_reopen_period(ctx: Ctx, inp: ApplyReopen) -> dict[str, Any]:
    """Chạy sau khi duyệt đủ (quyền hệ thống ghi tên người duyệt). Mốc đã đổi từ lúc xin ⇒ dừng, không lùi theo căn cứ cũ."""
    if not ctx.p.approved_by:
        raise Denied("Mở lại kỳ cần phiếu đã được người khác duyệt")
    if await ledger.locked_through(ctx, inp.entity_id) != inp.from_through:
        raise Conflict("Mốc khoá đã đổi từ lúc xin — xin lại theo mốc hiện tại")
    await ledger.reopen_to(ctx, entity=inp.entity_id, through=inp.locked_through, reason=inp.reason)
    return {"entity_id": inp.entity_id, "locked_through": inp.locked_through}


class PeriodIn(Input):
    entity_id: UUID | None = None
    on: date


@m.query("period_open", resource=None, title="Ngày còn trong kỳ mở", internal=True, agent="deny", callers=("pay",), family="ledger_ref",
         why="Gói khai consumes(\"ledger_ref\") kiểm TRƯỚC khi chốt đối tượng của nó (vd ký một thương vụ): ngày ghi nhận (không sau hôm nay) của pháp "
             "nhân đứng tên còn trong kỳ mở không (0119) — "
             "chặn rõ thay vì để bút toán bị khoá chặn lặng lẽ ở bên nghe sự kiện; pay.submit_run kiểm ngày hạch toán của kỳ lương trước khi "
             "gửi duyệt. Trả {open, locked_through, message} — chỉ mốc khoá.")
async def period_open(ctx: Ctx, inp: PeriodIn) -> dict[str, Any]:
    msg = await locked_message(ctx, inp.entity_id, inp.on)
    return {"open": not msg, "locked_through": await ledger.locked_through(ctx, inp.entity_id or await entities.default(ctx)), "message": msg}


# ── Số dư đầu kỳ: dòng nháp ──

class OpeningLine(Input):
    batch: str = field("Mã lô", "", max_length=40, example="DK-2026-10", aliases=("Lô số dư đầu kỳ",), group="Thêm chi tiết",
                       placeholder="Lô nháp đang có (hoặc lô mới của hôm nay)", help="Các dòng cùng mã lô được ghi sổ cùng một lần")
    kind: str = field("Loại dòng", options=KINDS, example="Số dư tài khoản")
    account: str = field("Tài khoản sổ cái", "", max_length=80, example="cash:bank", group="Thêm chi tiết",
                         help="Dòng số dư: mã tài khoản. Phải thu: tài khoản phải thu (trống = phải thu khách hàng). Phải trả / ứng: để trống")
    domain: str = field("Miền sở hữu", "", max_length=40, example="", group="Thêm chi tiết",
                        help="Khoản còn mở: module sở hữu khoản (module đó mới cấn trừ / thu được). Dòng số dư tài khoản: để trống")
    party_id: UUID | None = field("Bên nhận", None, options_query="parties.options", help="Phải trả / đang ứng: cá nhân / tổ chức trong danh mục")
    party: str = field("Bên trả", "", max_length=300, help="Phải thu: tên bên còn nợ (chọn bên trong danh mục ở ô “Bên nhận” nếu đã có; chỉ có tên thì hệ thống tìm / tạo bên theo tên)")
    label: str = field("Diễn giải", max_length=300, example="Số dư tiền gửi ngân hàng")
    ref: str = field("Số chứng từ gốc", "", max_length=60, group="Thêm chi tiết", help="Phải thu: dùng làm mã khoản nếu đúng dạng (chữ in hoa / số, 4–41 ký tự)")
    debit_vnd: Money | None = field("Dư Nợ", None, example="120.000.000", help="Tài sản / phải thu / đang ứng")
    credit_vnd: Money | None = field("Dư Có", None, help="Nợ phải trả / vốn")
    currency: str = field("Loại tiền", "VND", max_length=3, group="Thêm chi tiết", help="Chỉ VND — dòng tiền tệ khác bị từ chối, không tự quy đổi")
    issued_on: Day | None = field("Ngày phát sinh", None, help="Khoản còn mở: ngày phát sinh gốc (bắt buộc)")
    due_on: Day | None = field("Hạn", None, help="Phải thu: hạn gốc (bắt buộc)")


@m.command("add_opening_line",
           purpose="Nhập số dư đầu kỳ vào lô nháp để sổ cái bắt đầu từ số dư đúng — chưa ghi sổ.", reversible="Bỏ bản nháp lô đầu kỳ rồi nhập lại (dòng chưa ghi sổ)",
           resource="acct", action="manage", title="Nhập dòng số dư đầu kỳ", importable=True, agent="propose")
async def add_opening_line(ctx: Ctx, inp: OpeningLine) -> dict[str, Any]:
    """Thêm một dòng vào bản nháp của lô đầu kỳ (chưa ghi sổ). Nhập lại cùng file không thêm trùng (khoá theo nội dung dòng)."""
    batch = await _batch_code(ctx, inp.batch)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", batch):
        raise Invalid("Mã lô chỉ gồm chữ không dấu, số, dấu chấm / gạch")
    if inp.kind not in KINDS:
        raise Invalid(f"Loại dòng lạ: {inp.kind}")
    if (inp.currency or "VND").strip().upper() != "VND":
        raise Invalid(f"Chỉ nhận số dư VND (dòng {inp.label}: {inp.currency}) — không tự quy đổi")
    debit, credit = inp.debit_vnd or 0, inp.credit_vnd or 0
    if debit < 0 or credit < 0 or (debit > 0) == (credit > 0):
        raise Invalid("Mỗi dòng ghi đúng một bên: Dư Nợ hoặc Dư Có (số dương)")
    account, domain = inp.account.strip(), inp.domain.strip().lower()
    if inp.kind == "account":
        if domain:
            raise Invalid("Dòng số dư tài khoản không khai miền — miền chỉ dành cho khoản còn mở theo bên")
        if not _account_ok(account):
            raise Invalid(f"Tài khoản {account or '(trống)'} không nhận số dư đầu kỳ trực tiếp — tài khoản chưa khai, hoặc có sổ phụ "
                          "(phải thu / phải trả / ứng: nhập thành khoản còn mở theo bên)")
    else:
        if not domain:
            raise Invalid("Khoản còn mở phải khai miền sở hữu (module cấn trừ / thu khoản này)")
        if domain == "acct" or domain not in domains(inp.kind):
            raise Invalid(f"Miền “{domain}” không nhận {KINDS[inp.kind].lower()} — chọn trong: {', '.join(sorted(domains(inp.kind))) or '(chưa có)'}")
        if inp.issued_on is None:
            raise Invalid("Khoản còn mở cần ngày phát sinh gốc")
        if inp.kind == "receivable":
            account = account or "receivable:customers"
            if not account.startswith("receivable:") or ledger.ACCOUNTS.get(account, ("", ""))[1] != "asset":
                raise Invalid(f"Tài khoản phải thu lạ: {account}")
            if debit <= 0 or inp.due_on is None or not (inp.party.strip() or inp.label.strip()):
                raise Invalid("Phải thu: số ở cột Dư Nợ, cần hạn gốc và bên trả")
            if inp.party_id is not None:   # 0177: bên nợ chọn trong danh mục — id người dùng nêu: kiểm bằng quyền người nhập (0125)
                await ctx.call_as("parties.resolve_for_payment", ctx.p, party_id=inp.party_id)
        else:
            if account:
                raise Invalid("Phải trả / đang ứng: để trống tài khoản (sổ phụ theo bên tự chọn)")
            if (inp.kind == "payable") != (credit > 0):
                raise Invalid("Phải trả ở cột Dư Có, đang ứng ở cột Dư Nợ")
            if inp.party_id is None:
                raise Invalid("Phải trả / đang ứng cần bên nhận trong danh mục")
            await ctx.call_as("parties.resolve_for_payment", ctx.p, party_id=inp.party_id)   # id người dùng nêu: kiểm bằng quyền người nhập (0125)
    lid = await ctx.val(
        """INSERT INTO acct.opening_lines (batch, kind, account, domain, party_id, party, label, ref, debit_vnd, credit_vnd, issued_on, due_on,
                                          created_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, core.me()) RETURNING id""",
        (batch, inp.kind, account, domain, inp.party_id, inp.party.strip(), inp.label.strip(), inp.ref.strip(), debit, credit, inp.issued_on,
         inp.due_on))
    return {"line_id": lid, "batch": batch}


async def _batch_code(ctx: Ctx, given: str) -> str:
    """Lô nháp: người chọn thì dùng; để trống ⇒ lô nháp DUY NHẤT đang có, không có thì lô mới theo ngày hôm nay (0225 — không bắt nghĩ ra mã lô)."""
    if given.strip():
        return given.strip()
    drafts = [r["batch"] for r in await ctx.all("SELECT DISTINCT batch FROM acct.opening_lines WHERE batch_id IS NULL ORDER BY batch")]
    if len(drafts) > 1:
        raise Invalid("Đang có nhiều lô nháp — chọn lô: " + ", ".join(drafts))
    return drafts[0] if drafts else f"DK-{today():%Y-%m-%d}"


@m.query("opening_draft_options", resource="acct", title="Lô số dư đầu kỳ còn nháp")
async def opening_draft_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return [{"value": r["batch"], "label": f"{r['batch']} — {r['n']} dòng nháp"} for r in await ctx.all(
        "SELECT batch, count(*) AS n FROM acct.opening_lines WHERE batch_id IS NULL GROUP BY batch ORDER BY batch")]


@m.query("opening_posted_options", resource="acct", title="Lô số dư đầu kỳ đã ghi sổ")
async def opening_posted_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return [{"value": r["code"], "label": f"{r['code']} — cắt ngày {r['cut_on']:%d/%m/%Y}"} for r in await ctx.all(
        "SELECT code, cut_on FROM acct.opening_batches WHERE status = 'posted' ORDER BY cut_on DESC, code")]


class BatchCode(Input):
    batch: str = field("Lô số dư đầu kỳ", options_query="acct.opening_draft_options", max_length=40)


@m.command("discard_opening_lines", resource="acct", action="manage", title="Bỏ bản nháp lô đầu kỳ", agent="propose")
async def discard_opening_lines(ctx: Ctx, inp: BatchCode) -> dict[str, Any]:
    """Xoá các dòng NHÁP (chưa ghi sổ) của một mã lô — sửa file rồi nhập lại. Dòng đã ghi sổ không xoá được (sửa = đảo lô)."""
    n = await ctx.exec("DELETE FROM acct.opening_lines WHERE batch = %s AND batch_id IS NULL", (inp.batch.strip(),))
    return {"discarded": n}


# ── Số dư đầu kỳ: ghi lô ──

class OpenBalances(Input):
    batch: str = field("Lô số dư đầu kỳ", "", options_query="acct.opening_draft_options", max_length=40, example="DK-2026-10",
                       placeholder="Lô nháp duy nhất đang có")
    entity_code: str = field("Pháp nhân", "", options_query="admin.entity_options", placeholder="Pháp nhân mặc định", group="Thêm chi tiết")
    cut_on: Day = field("Ngày cắt (số dư tại hết ngày)", example="31/10/2026")
    completeness: str = field("Lô này", options={"full": "Đủ — Barista là sổ chính", "partial": "Một phần — sổ chính ở phần mềm khác"})
    control_debit_vnd: Money | None = field("Tổng kiểm: tổng Dư Nợ", None, help="Từ bảng cân đối của sổ cũ", group="Kiểm tổng với sổ cũ")
    control_credit_vnd: Money | None = field("Tổng kiểm: tổng Dư Có", None, group="Kiểm tổng với sổ cũ")
    control_equity_vnd: Money | None = field("Tổng kiểm: chênh Nợ − Có (vốn chủ sở hữu)", None, help="Khai thay cho cặp tổng Nợ / Có", group="Kiểm tổng với sổ cũ")
    difference_reason: str = field("Lý do chênh lệch", "", max_length=500, help="Lô một phần lệch tổng kiểm: bắt buộc", group="Kiểm tổng với sổ cũ")
    more_history_later: bool = field("Còn miền sẽ nạp lịch sử sau", False, group="Thêm chi tiết",
                                     help="Có ⇒ mặc định KHÔNG khoá kỳ sau khi ghi (miền nạp sau có ngày gốc trước D)")
    lock_after: bool | None = field("Khoá kỳ tới ngày cắt sau khi ghi", None, group="Thêm chi tiết", help="Để trống = khoá, trừ khi còn miền nạp lịch sử sau")
    dry_run: bool = field("Chạy thử", False, group="Thêm chi tiết", help="Kiểm đủ mọi luật rồi bỏ, không ghi gì")


async def _check_fences(ctx: Ctx, rows: list[dict[str, Any]], eid: UUID, cut: date) -> None:
    accounts = [r["account"] for r in rows if r["kind"] == "account"]
    if dup := sorted({a for a in accounts if accounts.count(a) > 1}):
        raise Invalid(f"Một tài khoản chỉ một dòng đầu kỳ: {', '.join(dup)}")
    if accounts:
        had = {r["account"] for r in await ctx.all(
            """SELECT DISTINCT l.account FROM acct.opening_lines l JOIN acct.opening_batches b ON b.id = l.batch_id
               WHERE b.status = 'posted' AND b.entity_id = %s AND l.kind = 'account' AND l.account = ANY(%s)""", (eid, accounts))}
        if had:
            raise Invalid(f"Tài khoản đã có số dư đầu kỳ: {', '.join(sorted(had))}")
        if active := await ledger.accounts_active_through(ctx, accounts, eid, cut):
            raise Invalid(f"Tài khoản đã có bút toán đến ngày {cut:%d/%m/%Y}: {', '.join(sorted(active))} — số đầu kỳ sẽ cộng trùng")
    for domain in sorted({r["domain"] for r in rows if r["kind"] != "account"}):
        if domain not in set().union(*(domains(k) for k in {r["kind"] for r in rows if r["domain"] == domain})):
            raise Invalid(f"Miền “{domain}” không còn hợp lệ")
        if await ctx.val("""SELECT 1 FROM acct.opening_lines l JOIN acct.opening_batches b ON b.id = l.batch_id
                            WHERE b.status = 'posted' AND b.entity_id = %s AND l.domain = %s LIMIT 1""", (eid, domain)):
            raise Invalid(f"Miền “{domain}” của pháp nhân này đã có số dư đầu kỳ — sửa bằng đảo lô cũ")
        opening = f"{domain}.opening"
        hist = await ctx.val(
            """SELECT 1 FROM acct.receivables WHERE split_part(source, '.', 1) = %(d)s AND source <> %(o)s AND entity_id = %(e)s
                      AND issued_on <= %(c)s AND status <> 'void'
               UNION ALL
               SELECT 1 FROM acct.payables WHERE split_part(source, '.', 1) = %(d)s AND source <> %(o)s AND entity_id = %(e)s
                      AND (created_at AT TIME ZONE core.tz())::date <= %(c)s LIMIT 1""",
            {"d": domain, "o": opening, "e": eid, "c": cut})
        if hist:
            raise Invalid(f"Miền “{domain}” đã có khoản ngày đến {cut:%d/%m/%Y} trong sổ — miền mang lịch sử thì không ghi đầu kỳ (ghi đôi)")


def _control(inp: OpenBalances, debit: int, credit: int) -> int:
    """Chênh giữa tổng kiểm khai báo và tổng các dòng (0 = khớp). Lô đủ: thiếu tổng kiểm / lệch ⇒ lỗi; lô một phần: lệch cần lý do."""
    cd, cc, ce = inp.control_debit_vnd, inp.control_credit_vnd, inp.control_equity_vnd
    declared = (cd is not None and cc is not None) or ce is not None
    if (cd is None) != (cc is None):
        raise Invalid("Tổng kiểm Nợ / Có phải khai đủ cả hai")
    if inp.completeness == "full" and not declared:
        raise Invalid("Lô đủ cần tổng kiểm từ bảng cân đối sổ cũ (tổng Dư Nợ và Dư Có, hoặc chênh Nợ − Có)")
    if not declared:
        return 0
    diffs = []
    if cd is not None and cc is not None and (cd != debit or cc != credit):
        diffs.append(f"tổng Nợ {_vnd(debit)} / khai {_vnd(cd)}, tổng Có {_vnd(credit)} / khai {_vnd(cc)}")
    if ce is not None and ce != debit - credit:
        diffs.append(f"chênh Nợ − Có {_vnd(debit - credit)} / khai {_vnd(ce)}")
    if not diffs:
        return 0
    if inp.completeness == "full":
        raise Invalid("Lô đủ lệch tổng kiểm — không ghi dòng nào: " + "; ".join(diffs))
    if not inp.difference_reason.strip():
        raise Invalid("Lô một phần lệch tổng kiểm — cần lý do chênh lệch: " + "; ".join(diffs))
    return (ce if ce is not None else (cd or 0) - (cc or 0)) - (debit - credit)


async def _receivable_code(ctx: Ctx, ref: str) -> str:
    want = ref.strip().upper()
    if re.fullmatch(r"[0-9A-Z][0-9A-Z-]{3,40}", want) and not await ctx.val("SELECT 1 FROM acct.receivables WHERE code = %s", (want,)):
        return want
    for _ in range(6):
        code = "DK" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        if not await ctx.val("SELECT 1 FROM acct.receivables WHERE code = %s", (code,)):
            return code
    raise Conflict("Không cấp được mã khoản phải thu — thử lại")


async def _post_line(ctx: Ctx, batch: dict[str, Any], n: int, r: dict[str, Any]) -> str:
    from barista.modules.acct.settlements import _accounts

    key = f"{SOURCE}:{batch['id']}:{n}"
    memo = f"Đầu kỳ {batch['code']}: {r['label']}"
    amount = r["debit_vnd"] - r["credit_vnd"]   # dương = Nợ
    pid: UUID | None = None
    if r["kind"] == "account":
        if r["account"] not in ledger.ACCOUNTS:   # held:… / deposit:… theo đối tượng
            await ledger.ensure_account(ctx, r["account"], r["label"], "liability")
        control = r["account"]
    elif r["kind"] == "receivable":
        code = await _receivable_code(ctx, r["ref"])
        from barista.modules.acct.common import debtor

        # bên nợ (0177): mã người nếu dòng nháp có (đã kiểm lúc nhập), không thì TÊN ⇒ tìm / tạo định danh; tên hiện lấy từ định danh
        party_id, party_name = await debtor(ctx, r["party_id"], r["party"])
        # khoản doanh thu đã ghi ở sổ cũ: cột tài khoản doanh thu chỉ để thoả ràng buộc của bảng — đầu kỳ không ghi doanh thu nào
        await ctx.exec(
            """INSERT INTO acct.receivables (code, source, source_ref, label, party, party_id, nature, amount_vnd, fee_vnd, revenue_account, issued_on, due_on,
                                             created_by, receivable_account, entity_id, recognized_key, match_keys, followed_at)
               VALUES (%s, %s, %s, %s, %s, %s, 'revenue', %s, %s, 'revenue:services', %s, %s, core.me(), %s, %s, %s, %s,
                       CASE WHEN %s THEN now() END)""",
            (code, f"{r['domain']}.opening", f"{batch['id']}:{n}", r["label"], party_name, party_id, amount, amount, r["issued_on"], r["due_on"],
             r["account"], batch["entity_id"], key, [r["ref"]] if len(r["ref"]) >= 4 else [],
             ctx.historical))   # nạp lịch sử (0185): khoản đầu kỳ đã quá hạn không mở việc nhắc
        control = r["account"]
    else:
        party = await ctx.call_as("parties.resolve_for_payment", ctx.p, party_id=r["party_id"])
        liability, advance, _ = await _accounts(ctx, party)
        control = liability if r["kind"] == "payable" else advance
        pid = await ctx.val(
            """INSERT INTO acct.payables (party_id, owner_id, entity_id, source, source_ref, kind, label, amount_vnd, payload)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (r["party_id"], party["user_id"], batch["entity_id"], f"{r['domain']}.opening", f"{batch['id']}:{n}", r["kind"], r["label"],
             abs(amount), Jsonb({"opening_batch": str(batch["id"]), "ref": r["ref"], "issued_on": str(r["issued_on"]),
                                 "due_on": str(r["due_on"]) if r["due_on"] else None})))
    # ngày hạch toán = ngày cắt D của lô (trigger khoá kỳ kiểm D > mốc)
    jid = (await ledger.post(ctx, key=key, memo=memo, source=SOURCE, ref=batch["code"], entity=batch["entity_id"], on=batch["cut_on"],
                            lines=[(control, amount), (EQUITY, -amount)])).journal_id
    if pid is not None:
        await ctx.exec("UPDATE acct.payables SET journal_id = %s WHERE id = %s", (jid, pid))
    await ctx.exec("UPDATE acct.opening_lines SET batch_id = %s, journal_key = %s WHERE id = %s", (batch["id"], key, r["id"]))
    return key


@m.command("open_balances", resource="acct", action="manage", title="Ghi số dư đầu kỳ", agent="deny")
async def open_balances(ctx: Ctx, inp: OpenBalances) -> dict[str, Any]:
    """Ghi cả lô đầu kỳ (bản nháp cùng mã lô) vào ngày cắt D của một pháp nhân — hoặc chạy thử. D ≤ hôm nay và sau mốc khoá. Hàng rào chống ghi
    đôi theo (pháp nhân, miền) cho khoản còn mở và theo (pháp nhân, tài khoản) cho số dư. Lô đủ phải khớp tổng kiểm; lệch thì không ghi gì.
    Khoá kỳ tới D sau khi ghi: mặc định bật, trừ khi khai còn miền nạp lịch sử sau (khi đó khoá tay bằng acct.lock_period)."""
    code = await _batch_code(ctx, inp.batch)
    if inp.completeness not in ("full", "partial"):
        raise Invalid("Lô phải là đủ hoặc một phần")
    eid = await entities.resolve(ctx, inp.entity_code)
    if old := await ctx.one("SELECT * FROM acct.opening_batches WHERE code = %s AND status = 'posted'", (code,)):
        if old["entity_id"] != eid or old["cut_on"] != inp.cut_on:
            raise Conflict(f"Lô {code} đã ghi cho pháp nhân / ngày cắt khác")
        if n := await ctx.val("SELECT count(*) FROM acct.opening_lines WHERE batch = %s AND batch_id IS NULL", (code,)):
            raise Conflict(f"Lô {code} đã ghi trước đó nhưng còn {n} dòng nháp mới cùng mã — dòng nháp KHÔNG được ghi: đặt mã lô khác cho "
                           "chúng (bỏ bản nháp rồi nhập lại), hoặc đảo lô cũ rồi ghi lại cả lô")
        return {"batch_id": old["id"], "replayed": True, "summary": f"Lô {code} đã ghi trước đó — không ghi lại"}
    if inp.cut_on > today():
        raise Invalid(f"Ngày cắt {inp.cut_on:%d/%m/%Y} sau hôm nay")
    await ledger.hold_period(ctx, eid)   # khoá riêng theo pháp nhân: mốc không đổi giữa lúc kiểm và lúc ghi, hai lô cùng pháp nhân không chồng
    lock = await ledger.locked_through(ctx, eid)
    if lock is not None and inp.cut_on <= lock:
        raise Invalid(f"Kỳ đã khoá tới {lock:%d/%m/%Y} — ngày cắt phải sau mốc khoá (hoặc xin mở lại kỳ)")
    rows = await ctx.all("SELECT * FROM acct.opening_lines WHERE batch = %s AND batch_id IS NULL ORDER BY created_at, id FOR UPDATE", (code,))
    if not rows:
        raise NotFound(f"Lô {code} không có dòng nháp nào — nhập dòng bằng “Nhập dòng số dư đầu kỳ”")
    if late := [r["label"] for r in rows if r["issued_on"] and r["issued_on"] > inp.cut_on]:
        raise Invalid(f"Khoản phát sinh sau ngày cắt không phải số dư đầu kỳ: {', '.join(late[:5])}")
    await _check_fences(ctx, rows, eid, inp.cut_on)
    debit, credit = sum(r["debit_vnd"] for r in rows), sum(r["credit_vnd"] for r in rows)
    diff = _control(inp, debit, credit)
    lock_after = inp.lock_after if inp.lock_after is not None else not inp.more_history_later
    out: dict[str, Any] = {}
    try:
        async with ctx.conn.transaction():
            bid = await ctx.val(
                """INSERT INTO acct.opening_batches (code, entity_id, cut_on, completeness, control_debit_vnd, control_credit_vnd, control_equity_vnd,
                                                     debit_vnd, credit_vnd, difference_vnd, difference_reason, more_history_later, locked, created_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, core.me()) RETURNING id""",
                (code, eid, inp.cut_on, inp.completeness, inp.control_debit_vnd, inp.control_credit_vnd, inp.control_equity_vnd, debit, credit, diff,
                 inp.difference_reason.strip() if diff else "", inp.more_history_later, lock_after))
            batch = {"id": bid, "code": code, "entity_id": eid, "cut_on": inp.cut_on}
            for n, r in enumerate(rows, 1):
                await _post_line(ctx, batch, n, r)
            if lock_after:
                await ledger.lock_through(ctx, entity=eid, through=inp.cut_on, reason=f"Số dư đầu kỳ lô {code}")
            out = {"batch_id": None if inp.dry_run else bid, "lines": len(rows), "debit_vnd": debit, "credit_vnd": credit,
                   "equity_vnd": debit - credit, "difference_vnd": diff, "locked_through": inp.cut_on if lock_after else lock,
                   "dry_run": inp.dry_run}
            if inp.dry_run:
                raise Rollback()
    except pg.UniqueViolation as e:
        raise Conflict(f"Lô {code} vừa được ghi ở nơi khác") from e
    head = "Chạy thử: ghi được" if inp.dry_run else "Đã ghi"
    out["summary"] = (f"{head} {len(rows)} dòng đầu kỳ lô {code} tại {inp.cut_on:%d/%m/%Y} (Nợ {_vnd(debit)}, Có {_vnd(credit)})"
                      + (f", chênh tổng kiểm {_vnd(diff)}" if diff else "")
                      + (f" · khoá kỳ tới {inp.cut_on:%d/%m/%Y}" if lock_after else " · CHƯA khoá kỳ — khoá tay khi các miền của ngày này xong"))
    return out


class ReverseBatch(Input):
    batch: str = field("Lô đã ghi sổ", options_query="acct.opening_posted_options", max_length=40)
    reason: str = field("Lý do", max_length=500, example="Số dư ngân hàng nhập sai")


@m.command("reverse_opening_batch", resource="acct", action="manage", title="Đảo lô số dư đầu kỳ", agent="deny")
async def reverse_opening_batch(ctx: Ctx, inp: ReverseBatch) -> dict[str, Any]:
    """Sửa đầu kỳ: đảo cả lô VÀO NGÀY CẮT của lô (kỳ đó phải đang mở — mở lại kỳ có duyệt trước), rồi nhập + ghi lại, rồi khoá. Khoản còn mở đã
    có thu / chi / cấn trừ ⇒ không đảo được (xử lý bằng nghiệp vụ)."""
    from barista.modules.acct.settlements import _balances

    if not inp.reason.strip():
        raise Invalid("Cần lý do đảo lô")
    b = await ctx.one("SELECT * FROM acct.opening_batches WHERE code = %s AND status = 'posted' FOR UPDATE", (inp.batch.strip(),))
    if b is None:
        raise NotFound(f"Không có lô đầu kỳ đã ghi mã {inp.batch}")
    await ledger.hold_period(ctx, b["entity_id"])
    lock = await ledger.locked_through(ctx, b["entity_id"])
    if lock is not None and b["cut_on"] <= lock:
        raise Invalid(f"Kỳ đã khoá tới {lock:%d/%m/%Y} — xin mở lại kỳ về trước {b['cut_on']:%d/%m/%Y} rồi mới đảo lô")
    rows = await ctx.all("SELECT * FROM acct.opening_lines WHERE batch_id = %s ORDER BY journal_key", (b["id"],))
    for r in rows:
        ref = r["journal_key"].rsplit(":", 1)[1]
        source_ref = f"{b['id']}:{ref}"
        if r["kind"] == "receivable":
            ar = await ctx.one("SELECT id, code, paid_vnd, status FROM acct.receivables WHERE source = %s AND source_ref = %s FOR UPDATE",
                               (f"{r['domain']}.opening", source_ref))
            if ar is None or ar["paid_vnd"] or ar["status"] != "open":
                raise Invalid(f"Khoản phải thu đầu kỳ {ar['code'] if ar else ref} đã thu / đổi — không đảo lô được")
            await ctx.exec("UPDATE acct.receivables SET status = 'void' WHERE id = %s", (ar["id"],))
        elif r["kind"] in ("payable", "advance"):
            p = (await _balances(ctx, "p.source=%s AND p.source_ref=%s", (f"{r['domain']}.opening", source_ref)) or [None])[0]
            if p is None or p["left_vnd"] != p["amount_vnd"]:
                raise Invalid(f"Khoản {r['label']} đã chi / cấn trừ — không đảo lô được")
        undo = await ledger.reverse(ctx, original_key=r["journal_key"], key=f"{SOURCE}.reverse:{b['id']}:{ref}", memo=f"Đảo đầu kỳ {b['code']}: {r['label']}",
                                    source=SOURCE, on=b["cut_on"])
        jid = undo.journal_id if undo else None
        if r["kind"] in ("payable", "advance"):
            await ctx.exec("""INSERT INTO acct.payable_adjustments (payable_id, amount_vnd, action_key, reason, journal_id)
                              SELECT id, amount_vnd, %s, %s, %s FROM acct.payables WHERE source = %s AND source_ref = %s""",
                           (f"opening_reverse:{b['id']}", inp.reason.strip(), jid, f"{r['domain']}.opening", source_ref))
    await ctx.exec("""UPDATE acct.opening_batches SET status = 'reversed', reversed_by = core.me(), reversed_at = now(), reverse_reason = %s
                      WHERE id = %s""", (inp.reason.strip(), b["id"]))
    return {"batch_id": b["id"], "reversed": len(rows), "summary": f"Đã đảo {len(rows)} dòng của lô {b['code']} tại {b['cut_on']:%d/%m/%Y}"}


# ── Màn: khoá kỳ theo pháp nhân + lô đầu kỳ ──

@m.query("periods", resource="acct", title="Khoá kỳ & số dư đầu kỳ", min_scope="all")
async def periods(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Mỗi pháp nhân: mốc khoá hiện hành, lô đầu kỳ đã ghi mà kỳ CHƯA khoá tới ngày cắt (nhắc khoá tay — 0119)."""
    ents = await entities.names(ctx)
    batches = await ctx.all("SELECT entity_id, code, cut_on FROM acct.opening_batches WHERE status = 'posted' ORDER BY cut_on")
    rows = []
    for eid, e in ents.items():
        lock = await ledger.locked_through(ctx, UUID(eid))
        mine = [b for b in batches if str(b["entity_id"]) == eid]
        open_ = [b for b in mine if lock is None or b["cut_on"] > lock]
        rows.append({"id": eid, "entity_code": e["code"], "entity": e["name"], "locked_through": lock, "batches": len(mine),
                     "unlocked": ", ".join(f"{b['code']} ({b['cut_on']:%d/%m/%Y})" for b in open_), "flag": "unlocked" if open_ else ""})
    waiting = sum(1 for r in rows if r["flag"])
    return ui.table(rows, [
        ui.col("entity_code", "Mã"), ui.col("entity", "Pháp nhân", width=220), ui.col("locked_through", "Khoá tới hết ngày", "date"),
        ui.col("batches", "Lô đầu kỳ", "number"), ui.col("unlocked", "Lô đầu kỳ chưa khoá kỳ", width=260),
    ], title="Khoá kỳ & số dư đầu kỳ", kpis=[ui.kpi("Pháp nhân có đầu kỳ chưa khoá", waiting, tone="orange" if waiting else None)],
        note="Kỳ đã khoá: không ghi / sửa bút toán có ngày hạch toán từ mốc trở về trước. Mở lại cần duyệt.")


def _say(out: Any) -> Any:
    n = sum(1 for r in (out or {}).get("rows") or [] if r.get("flag") == "unlocked")
    if not n:
        return None
    return line("warn", f"{n} pháp nhân có số dư đầu kỳ chưa khoá kỳ",
                "Khi các miền của ngày cắt đã xong, khoá kỳ tới ngày đó để không ai ghi lùi vào số đầu kỳ")


@m.query("opening_batches", resource="acct", title="Lô số dư đầu kỳ", min_scope="all")
async def opening_batches(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    ents = await entities.names(ctx)
    rows = await ctx.all("""SELECT id, code, entity_id, cut_on, completeness, debit_vnd, credit_vnd, debit_vnd - credit_vnd AS equity_vnd,
                                   difference_vnd, difference_reason, locked, status, created_at FROM acct.opening_batches ORDER BY created_at DESC LIMIT 500""")
    drafts = await ctx.all("""SELECT batch, count(*) AS n, sum(debit_vnd)::bigint AS d, sum(credit_vnd)::bigint AS c FROM acct.opening_lines
                              WHERE batch_id IS NULL GROUP BY batch ORDER BY batch""")
    for r in rows:
        r["entity"] = ents.get(str(r["entity_id"]), {}).get("code", "")
    return ui.table(rows, [
        ui.col("code", "Mã lô"), ui.col("entity", "Pháp nhân"), ui.col("cut_on", "Ngày cắt", "date"),
        ui.col("completeness", "Lô", "badge", labels={"full": "Đủ", "partial": "Một phần"}),
        ui.col("debit_vnd", "Tổng Dư Nợ", "money"), ui.col("credit_vnd", "Tổng Dư Có", "money"), ui.col("equity_vnd", "Đối ứng đầu kỳ", "money"),
        ui.col("difference_vnd", "Chênh lệch đầu kỳ", "money"), ui.col("difference_reason", "Lý do chênh", width=220),
        ui.col("status", "Trạng thái", "badge", labels={"posted": "Đã ghi", "reversed": "Đã đảo"}, tones={"posted": "green", "reversed": "gray"}),
    ], title="Lô số dư đầu kỳ", kpis=[ui.kpi("Bản nháp chờ ghi", len(drafts),
                                          hint=", ".join(f"{d['batch']}: {d['n']} dòng" for d in drafts[:5]))],
        empty="Chưa có lô đầu kỳ — nhập dòng (Excel) rồi “Ghi số dư đầu kỳ”")


m.screens("accounting", (
    Screen("periods", "Khoá kỳ", "lock.fill", "acct.periods", min_scope="all", group=G_BOOK, desk=Desk("do", say=_say, menu=True, rank=60, doers_only=True),
           actions=(A("acct.lock_period", "Khoá kỳ", style="primary"), A("acct.reopen_period", "Xin mở lại kỳ")),
           row_actions=(A("acct.lock_period", "Khoá kỳ", bind={"entity_code": "entity_code"}, style="primary"),
                        A("acct.reopen_period", "Xin mở lại", bind={"entity_code": "entity_code"}))),
    Screen("opening_batches", "Số dư đầu kỳ", "tray.and.arrow.down", "acct.opening_batches", min_scope="all", group=G_BOOK,
           actions=(A("acct.add_opening_line", "Nhập dòng đầu kỳ", style="primary"), A("acct.open_balances", "Ghi lô đầu kỳ"),
                    A("acct.discard_opening_lines", "Bỏ bản nháp")),
           row_actions=(A("acct.reverse_opening_batch", "Đảo lô", bind={"batch": "code"}, when={"status": ["posted"]}, style="destructive"),)),
), rank=45)
