"""TIỀN RA: lệnh chi cho người trong công ty (gói nguồn lập, kế toán xác nhận đã chuyển — ``acct.confirm_payout``) và chi trả tiền THU HỘ / hoàn cọc cho
người ngoài qua chuỗi duyệt (``acct.request_remittance`` → duyệt → chờ chuyển → đã chuyển), số đang giữ hộ theo tài khoản của module gọi."""

import re
from typing import Any
from uuid import UUID

from barista.api import (
    Conflict,
    Ctx,
    Day,
    Denied,
    Input,
    Invalid,
    NoInput,
    NotFound,
    field,
    today,
    ui,
)
from barista.kernel import ledger
from barista.modules.acct import m
from barista.modules.acct.common import _vnd

PAYOUT_STATUS = {"pending_transfer": "Chờ chuyển khoản", "transferred": "Đã chuyển", "cancelled": "Đã huỷ", "unknown": "Cần tra soát"}



# ── Chi cho người trong công ty (hoa hồng, hoàn ứng…): gói nguồn lập qua acct.create_payout sau khi đã duyệt + ghi bút toán của mình vào
# "chờ chuyển khoản"; kế toán chuyển khoản rồi xác nhận ⇒ ghi chờ chuyển khoản / tiền gửi. Chi trả cho người NGOÀI (thu hộ, hoàn cọc) là
# acct.request_remittance (có chuỗi duyệt riêng). ──

class ConfirmPayout(Input):
    payout_id: UUID = field("Lệnh chi", hidden=True)
    bank_ref: str = field("Mã giao dịch ngân hàng", max_length=100)
    transferred_on: Day | None = field("Ngày chuyển", None, placeholder="Hôm nay")


@m.command("confirm_payout", resource="acct", title="Xác nhận đã chuyển khoản")
async def confirm_payout(ctx: Ctx, inp: ConfirmPayout) -> dict[str, Any]:
    p = await ctx.one("SELECT * FROM acct.payouts WHERE id = %s", (inp.payout_id,))
    if p:
        from barista.modules.acct.settlements import _lock
        await _lock(ctx, p["party_id"], p["entity_id"])
        p = await ctx.one("SELECT * FROM acct.payouts WHERE id = %s FOR UPDATE", (inp.payout_id,))
    if p is None:
        raise NotFound("Không thấy lệnh chi")
    if not inp.bank_ref.strip():
        raise Invalid("Cần mã giao dịch ngân hàng")
    if p["status"] == "transferred":
        if p["bank_ref"] != inp.bank_ref or (inp.transferred_on and p["transferred_on"] != inp.transferred_on):
            raise Conflict("IDEMPOTENCY_CONFLICT: bằng chứng chuyển khoản đã đổi")
        return {"payout_id": p["id"], "status": "transferred"}
    if p["status"] not in ("pending_transfer", "unknown"):
        raise Invalid("Lệnh chi đã huỷ")
    await ledger.post(ctx, key=f"acct.payout:{p['id']}", memo=f"Chuyển khoản {p['label'].lower()} ({inp.bank_ref})", source="acct.payout",
                      ref=inp.bank_ref, lines=[("payout:clearing", p["amount_vnd"]), ("cash:bank", -p["amount_vnd"])], entity=p["entity_id"],
                      on=inp.transferred_on or today())   # ngày chuyển khoản
    await ctx.exec("UPDATE acct.payouts SET status = 'transferred', bank_ref = %s, transferred_on = %s WHERE id = %s",
                   (inp.bank_ref, inp.transferred_on or today(), p["id"]))
    ctx.emit("acct.payout_transferred", payout_id=p["id"], source=p["source"], source_ref=p["source_ref"], amount_vnd=p["amount_vnd"])
    return {"payout_id": p["id"], "status": "transferred"}


@m.query("payouts", resource="acct", title="Lệnh chi", min_scope="all")
async def payouts(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Xuất Excel bảng này = file chuyển khoản lô (số tài khoản lấy từ hồ sơ nhân sự — bước sau)."""
    rows = await ctx.all(
        """SELECT p.id, coalesce(u.name,p.recipient_name) AS person, u.phone, p.label, p.amount_vnd, p.status, p.created_at, p.bank_ref, p.transferred_on, p.status_note
           FROM acct.payouts p LEFT JOIN core.users u ON u.id = p.user_id ORDER BY p.status, p.created_at DESC LIMIT 2000""")
    pending = [r for r in rows if r["status"] == "pending_transfer"]
    return ui.table(rows, [
        ui.col("person", "Người nhận"), ui.col("label", "Chi cho"), ui.col("amount_vnd", "Số tiền", "money"),
        ui.col("status", "Trạng thái", "badge", labels=PAYOUT_STATUS, tones={"pending_transfer": "orange", "transferred": "green", "unknown": "red"}),
        ui.col("created_at", "Lập lúc", "datetime"), ui.col("bank_ref", "Mã CK"), ui.col("transferred_on", "Ngày chuyển", "date"),
        ui.col("status_note", "Ghi chú tra soát / huỷ", width=240),
    ], title="Lệnh chi", kpis=[ui.kpi("Chờ chuyển khoản", sum(r["amount_vnd"] for r in pending), "money", tone="orange" if pending else None),
                               ui.kpi("Số lệnh chờ", len(pending))])


class SourceIn(Input):
    source: str


@m.query("payouts_open", resource="acct", title="Lệnh chi chờ chuyển theo nguồn", internal=True, agent="deny", family="settlement",
         why="Điều kiện chặn tắt gói của gói khai consumes(\"settlement\") (bên gọi không kiểm thêm): đếm lệnh chi CHỜ CHUYỂN KHOẢN của đúng nguồn được "
             "hỏi — nguồn phải mang tên module gọi (ctx.owned_by) — mọi người nhận. Trả CHỈ {n, amount_vnd} — không người nhận, không từng lệnh.")
async def payouts_open(ctx: Ctx, inp: SourceIn) -> dict[str, int]:
    """Cho điều kiện chặn tắt gói nguồn: lệnh chi của nguồn này còn chờ chuyển khoản."""
    ctx.owned_by(inp.source.split(".")[0], f"Nguồn {inp.source}")   # 0186: họ mở cho gói ngoài lõi ⇒ gói chỉ đếm lệnh chi của nguồn mình
    r = await ctx.one("""SELECT count(*) AS n, coalesce(sum(amount_vnd), 0)::bigint AS v FROM acct.payouts
                         WHERE source = %s AND status IN ('pending_transfer','unknown')""", (inp.source,))
    return {"n": int(r["n"]) if r else 0, "amount_vnd": int(r["v"]) if r else 0}


REMIT_STATUS = {"requested": "Chờ duyệt", "pending_transfer": "Chờ chuyển khoản", "transferred": "Đã chuyển", "rejected": "Từ chối"}
REMIT_TONES = {"requested": "orange", "pending_transfer": "blue", "transferred": "green", "rejected": "gray"}


class Accounts(Input):
    accounts: list[str]


async def _owned_accounts(ctx: Ctx, accounts: list[str]) -> set[str]:
    """Tài khoản giữ hộ / cọc (``held:<khoá>`` / ``deposit:<khoá>``) mà module gọi ĐƯỢC đụng: nguồn (module) của MỌI khoản thu hộ / cọc mang khoá đó phải
    là chính module gọi (``ctx.owned_by`` — nguồn là ``<module>.<…>``). Họ ``remittance`` mở cho gói ngoài lõi: gói không rút / không xem tiền giữ hộ của
    nguồn khác. Tài khoản chưa có khoản thu nào (chưa từng nhận tiền hộ) ⇒ không ai sở hữu: trả khỏi tập này (không có số để đọc / để chi). Module gọi
    không rõ (lõi tự gọi, không qua hợp đồng) ⇒ ``owned_by`` bỏ qua như mọi chỗ khác."""
    owned: set[str] = set()
    for acc in dict.fromkeys(accounts):
        kind, _, key = acc.partition(":")
        owners = [r["m"] for r in await ctx.all(
            "SELECT DISTINCT split_part(source, '.', 1) AS m FROM acct.receivables WHERE payee_key = %s AND nature = %s",
            (key, "pass_through" if kind == "held" else "deposit"))]
        for owner in owners:
            ctx.owned_by(owner, f"Tiền giữ hộ {acc}")
        if owners:
            owned.add(acc)
    return owned


@m.query("balances_for", resource="acct", title="Tiền đang giữ theo tài khoản phải trả", internal=True, agent="deny", family="remittance",
         why="Gói khai consumes(\"remittance\") (thu hộ / giữ hộ — vd cho thuê: held:<chủ nhà> / deposit:<hợp đồng>) dựng tài khoản từ dòng người xem thấy qua RLS "
             "của gói (màn, hoàn cọc, xin chi trả) hoặc mọi dòng (pack_blockers). acct chỉ nhận tài khoản held:… / deposit:… (két, ví, doanh thu ⇒ "
             "Invalid) — đọc số dư sổ cái + acct.remittances của đúng các tài khoản được hỏi. Trả {tài khoản: {held, requested, pending, paid}} — chỉ số tiền.")
async def balances_for(ctx: Ctx, inp: Accounts) -> dict[str, dict[str, int]]:
    """{tài khoản held:… / deposit:…: {held: đang giữ (sau các lệnh chi đã duyệt — sổ cái là nguồn), requested: đang xin chờ duyệt, pending: chờ
    chuyển khoản, paid: đã chuyển}}. Mỗi người nhận / mỗi khoản cọc một tài khoản ⇒ không có chuyện khoản này chi bằng tiền của khoản khác."""
    if bad := [a for a in inp.accounts if not re.fullmatch(r"(held|deposit):[a-z0-9_-]+", a)]:
        raise Invalid(f"Chỉ hỏi được tài khoản giữ hộ / cọc (held:… / deposit:…), không phải {bad[0]}")   # không đọc két, ví của người khác
    out: dict[str, dict[str, int]] = {}
    owned = await _owned_accounts(ctx, inp.accounts)
    for acc in inp.accounts:
        if acc not in owned:   # chưa ai lập khoản thu hộ / cọc cho khoá này ⇒ không có tiền giữ — không đọc sổ cái của tài khoản không rõ chủ
            out[acc] = {"held": 0, "requested": 0, "pending": 0, "paid": 0}
            continue
        rem = {r["status"]: int(r["v"]) for r in await ctx.all(
            "SELECT status, coalesce(sum(amount_vnd), 0)::bigint AS v FROM acct.remittances WHERE account = %s GROUP BY status", (acc,))}
        out[acc] = {"held": -await ledger.balance(ctx, acc), "requested": rem.get("requested", 0), "pending": rem.get("pending_transfer", 0),
                    "paid": rem.get("transferred", 0)}
    return out


class RequestRemittance(Input):
    account: str
    payee_name: str
    payee_bank: str = ""
    amount_vnd: int
    reason: str = ""
    source: str = ""
    source_ref: str = ""


@m.command("request_remittance", resource="acct", title="Xin chi trả tiền thu hộ", internal=True, family="remittance",
           why="Gói khai consumes(\"remittance\") — lệnh hoàn cọc / chi trả người nhận của gói (quyền ghi của gói; đối tượng đọc qua RLS người xin): ghi một dòng "
               "acct.remittances (người xin = người gọi) + mở chuỗi duyệt remittance — tiền chưa đi, người duyệt quyết; không vượt số đang giữ "
               "trừ phần đang xin (kiểm dưới khoá tài khoản). Trả {remittance_id, approval_id}.")
async def request_remittance(ctx: Ctx, inp: RequestRemittance) -> dict[str, Any]:
    """Hợp đồng nội bộ cho gói ngành: xin chi trả từ tiền ĐANG GIỮ (thu hộ của một người nhận / tiền cọc) ⇒ chuỗi duyệt "remittance". Không xin
    vượt số đang giữ trừ phần đang xin; lúc duyệt sổ cái kiểm lại dưới khoá (không chi âm)."""
    if not re.fullmatch(r"(held|deposit):[a-z0-9_-]+", inp.account) or inp.amount_vnd <= 0 or not inp.payee_name.strip():
        raise Invalid("Lệnh chi không hợp lệ")
    if inp.account not in await _owned_accounts(ctx, [inp.account]) and ctx.caller:
        raise Denied(f"Tài khoản {inp.account} chưa có khoản thu hộ / cọc nào của module {ctx.caller} — không xin chi được")
    # khoá theo tài khoản: hai yêu cầu cùng lúc không cùng đọc "đang giữ / đang xin" rồi cùng lọt (lúc duyệt sổ cái kiểm lại dưới khoá)
    await ctx.exec("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"acct.remit:{inp.account}",))
    held = -await ledger.balance(ctx, inp.account)
    asked = int(await ctx.val("SELECT coalesce(sum(amount_vnd), 0)::bigint FROM acct.remittances WHERE account = %s AND status = 'requested'",
                              (inp.account,)) or 0)
    if inp.amount_vnd > held - asked:
        raise Invalid(f"Số đang giữ chi được: {_vnd(max(0, held - asked))}" + (f" (đã trừ {_vnd(asked)} đang chờ duyệt)" if asked else ""))
    rid = await ctx.val(
        """INSERT INTO acct.remittances (account, payee_name, payee_bank, amount_vnd, reason, source, source_ref, requested_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, core.me()) RETURNING id""",
        (inp.account, inp.payee_name, inp.payee_bank, inp.amount_vnd, inp.reason, inp.source, inp.source_ref))
    aid = await ctx.request_approval(chain="remittance", command="acct.approve_remittance", input={"remittance_id": rid},
                                     summary=f"Chi trả {_vnd(inp.amount_vnd)} cho {inp.payee_name}" + (f" — {inp.reason}" if inp.reason else ""),
                                     on_reject="acct.reject_remittance")
    await ctx.exec("UPDATE acct.remittances SET approval_id = %s WHERE id = %s", (aid, rid))
    return {"remittance_id": rid, "approval_id": aid}


class RemittanceRef(Input):
    remittance_id: UUID = field("Lệnh chi", hidden=True)


@m.command("approve_remittance", resource="acct", title="Duyệt chi trả tiền thu hộ", internal=True)
async def approve_remittance(ctx: Ctx, inp: RemittanceRef) -> dict[str, Any]:
    """Chạy sau khi duyệt đủ: giảm nợ phải trả người nhận ⇒ chờ chuyển khoản. Sổ cái chặn chi quá số đang giữ (hai phiếu duyệt cùng lúc)."""
    r = await ctx.one("SELECT * FROM acct.remittances WHERE id = %s FOR UPDATE", (inp.remittance_id,))
    if r is None:
        raise NotFound("Không thấy lệnh chi")
    if r["status"] != "requested":
        raise Invalid("Lệnh chi đã xử lý")
    await ledger.post(ctx, key=f"acct.remit:{r['id']}", memo=f"Chi trả {r['payee_name']}", source="acct.remittance", ref=str(r["id"]),
                      lines=[(r["account"], r["amount_vnd"]), ("payout:clearing", -r["amount_vnd"])], limits={r["account"]: (None, 0)},
                      entity=r["entity_id"], on=today())   # ngày duyệt chi — nghiệp vụ không có ngày chứng từ riêng
    await ctx.exec("UPDATE acct.remittances SET status = 'pending_transfer', decided_at = now() WHERE id = %s", (r["id"],))
    ctx.emit("acct.remittance_changed", remittance_id=r["id"], source=r["source"], source_ref=r["source_ref"], status="pending_transfer")
    return {"remittance_id": r["id"], "status": "pending_transfer"}


@m.command("reject_remittance", resource="acct", title="Từ chối chi trả tiền thu hộ", internal=True)
async def reject_remittance(ctx: Ctx, inp: RemittanceRef) -> dict[str, Any]:
    await ctx.exec("UPDATE acct.remittances SET status = 'rejected', decided_at = now() WHERE id = %s AND status = 'requested'",
                   (inp.remittance_id,))
    return {"remittance_id": inp.remittance_id, "status": "rejected"}


class ConfirmRemittance(Input):
    remittance_id: UUID = field("Lệnh chi", hidden=True)
    bank_ref: str = field("Mã giao dịch ngân hàng", max_length=100)
    transferred_on: Day | None = field("Ngày chuyển", None, placeholder="Hôm nay")


@m.command("confirm_remittance", resource="acct", title="Xác nhận đã chuyển trả")
async def confirm_remittance(ctx: Ctx, inp: ConfirmRemittance) -> dict[str, Any]:
    r = await ctx.one("SELECT * FROM acct.remittances WHERE id = %s FOR UPDATE", (inp.remittance_id,))
    if r is None:
        raise NotFound("Không thấy lệnh chi")
    if r["status"] != "pending_transfer":
        raise Invalid(f"Lệnh chi đang “{REMIT_STATUS[r['status']]}”")
    await ledger.post(ctx, key=f"acct.remit_paid:{r['id']}", memo=f"Chuyển trả {r['payee_name']} ({inp.bank_ref})", source="acct.remittance_paid",
                      ref=inp.bank_ref, lines=[("payout:clearing", r["amount_vnd"]), ("cash:bank", -r["amount_vnd"])], entity=r["entity_id"],
                      on=inp.transferred_on or today())   # ngày chuyển khoản
    await ctx.exec("UPDATE acct.remittances SET status = 'transferred', bank_ref = %s, transferred_on = %s WHERE id = %s",
                   (inp.bank_ref, inp.transferred_on or today(), r["id"]))
    ctx.emit("acct.remittance_changed", remittance_id=r["id"], source=r["source"], source_ref=r["source_ref"], status="transferred")
    return {"remittance_id": r["id"], "status": "transferred", "summary": f"Đã chuyển {_vnd(r['amount_vnd'])} cho {r['payee_name']}"}


@m.query("remittances", resource="acct", title="Chi trả tiền thu hộ", min_scope="all")
async def remittances(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await ctx.all(
        """SELECT r.id, r.payee_name, r.payee_bank, r.amount_vnd, r.reason, r.status, r.created_at, r.bank_ref, r.transferred_on,
                  u.name AS requested_by FROM acct.remittances r LEFT JOIN core.users u ON u.id = r.requested_by
           ORDER BY (r.status = 'pending_transfer') DESC, r.created_at DESC LIMIT 1000""")
    pending = [r for r in rows if r["status"] == "pending_transfer"]
    return ui.table(rows, [
        ui.col("status", "Trạng thái", "badge", labels=REMIT_STATUS, tones=REMIT_TONES), ui.col("payee_name", "Người nhận"),
        ui.col("amount_vnd", "Số tiền", "money"), ui.col("reason", "Lý do", width=240), ui.col("payee_bank", "Tài khoản nhận", width=200),
        ui.col("requested_by", "Người xin"), ui.col("created_at", "Lập lúc", "datetime"), ui.col("bank_ref", "Mã CK"),
        ui.col("transferred_on", "Ngày chuyển", "date"),
    ], title="Chi trả tiền thu hộ", kpis=[
        ui.kpi("Chờ chuyển khoản", sum(r["amount_vnd"] for r in pending), "money", tone="orange" if pending else None),
        ui.kpi("Chờ duyệt", sum(1 for r in rows if r["status"] == "requested")),
    ], empty="Chưa có lệnh chi trả tiền thu hộ — lệnh được lập khi công ty thu tiền hộ người khác rồi xin chi trả lại cho họ")
