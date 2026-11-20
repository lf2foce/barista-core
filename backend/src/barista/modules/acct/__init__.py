"""TÀI CHÍNH dùng chung mọi ngành: khoản phải thu (gói ngành lập — 0057 / 0079), tiền vào (một giao dịch ngân hàng = một dòng) ⇒ phân bổ ⇒
ghi sổ theo bản chất tiền, đối soát sao kê, lệnh chi trong công ty + chi trả thu hộ qua duyệt, dòng tiền dự phóng (gói góp dòng), sổ cái,
xuất bút toán theo mẫu nhập MISA. Module này KHÔNG biết ngành nào: phí CĐT, hoa hồng, tiền thuê… là nguồn (``source``) của khoản phải thu /
lệnh chi; gói nguồn nghe ``acct.payment_received`` / ``acct.payout_transferred`` để phản ứng.

Mọi đồng tiền ghi sổ cái kép (kernel/ledger). Tích hợp MISA / ngân hàng: xuất file (bước 1); đẩy qua API khi khách chốt gói MISA (bước 2).
"""

import logging
import re
import secrets
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Literal, LiteralString
from uuid import UUID

from psycopg import errors as pg

from barista.api import Action as A
from barista.api import (
    Conflict,
    Ctx,
    DateIn,
    Day,
    Denied,
    Desk,
    Dim,
    Event,
    Hints,
    Input,
    Invalid,
    Measure,
    Module,
    Money,
    NoInput,
    NotFound,
    Pack,
    Role,
    Screen,
    SearchIn,
    SearchTerms,
    WorkIn,
    Workspace,
    dong,
    entities,
    field,
    line,
    today,
    ui,
)
from barista.kernel import ledger, registry

log = logging.getLogger("barista.acct")

# Gói 'finance' tự khai (0069) — kernel không biết tên gói; vai / đội / mẫu mặc định xem presets, màn & "cần chú ý" khai ngay dưới.
Pack("finance", "Tài chính",
     "Sổ cái, sao kê & đối soát tiền về, dòng tiền, xuất MISA, việc trợ lý theo dõi — dùng chung mọi ngành",
     roles={
         "accountant": Role("Kế toán", ai_tier="manager",
                            tools=("acct.receivables", "acct.receivable_detail", "acct.cashflow", "acct.reconcile", "acct.unapplied", "acct.cash_brief",
                                   "acct.cash_moves", "acct.payouts", "acct.remittances", "acct.payout_checks", "acct.out_lines", "acct.payables",
                                   "acct.misa_export", "acct.vat_summary", "acct.pnl", "acct.balances", "acct.expenses", "acct.apply_statement",
                                   "acct.apply_line", "acct.record_payment", "acct.confirm_payout", "flow.tasks",
                                   "acct.dossiers", "acct.dossier"),   # 0218; 0223 hồ sơ khoản lệch
                            home=("accounting", ("receivables", "cashflow", "reconcile", "cash_brief", "payout_checks", "flow_board", "payouts")),
                            picker=("Chức năng", "Khoản phải thu, tiền về & đối soát, lệnh chi, dòng tiền, xuất MISA.", "Dòng tiền 4 tháng tới có tháng nào âm không?"),
                            grants=(("ledger", "all", "read write"), ("acct", "all", "read write manage"),
                                    ("kb.common", "all", "read write"),
                                    ("papers", "all", "read write manage"), ("work", "all", "read write"),
                                    ("flow", "all", "read write"), ("flow.templates", "all", "read"),
                                    ("talk", "all", "read write"), ("training", "own", "read write"), ("books", "all", "read write"),
                                    ("books.setup", "all", "read write"),),
                            suggest=(("acct.payouts", "Lệnh chi nào đang chờ chuyển khoản?"),
                                     ("acct.cashflow", "Dòng tiền 4 tháng tới thế nào?"),
                                     ("acct.misa_export", "Xuất bút toán tháng này để nhập MISA"))),
     },
     hints={"director": Hints(suggest=(("acct.cashflow", "Dòng tiền 4 tháng tới có tháng nào âm không?"),))},
     tools={"director": ("acct.cashflow", "acct.receivables", "acct.pnl")},   # 0218
     grants=(("director", "acct", "all", "read"),),   # việc theo dõi ("work") là năng lực nền — quyền của quản trị / giám đốc khai ở gói nền
     teams=(("KT", "Kế toán", "accounting"),),
     chains=(("settlement_payment", "Chi nghĩa vụ", ({"kind": "role", "role": "director"},)),
             ("remittance", "Chi trả tiền thu hộ / hoàn cọc", ({"kind": "role", "role": "director"},)),
             ("period_reopen", "Mở lại kỳ kế toán đã khoá", ({"kind": "role", "role": "director"},)),
             # 0156: sửa khoản phải thu / đảo phiếu thu là tiền đi ngược — người khác duyệt (người xin không tự duyệt)
             ("receivable_correction", "Sửa khoản phải thu / đảo phiếu thu", ({"kind": "role", "role": "director"},)),),
     approval_rules=(("acct.credit_receivable", "receivable_correction"), ("acct.reverse_receipt", "receivable_correction"),
                     # 0165: huỷ / điều chỉnh giảm hoá đơn GTGT cũng là tiền đi ngược — cùng chuỗi
                     ("acct.cancel_vat_invoice", "receivable_correction"), ("acct.adjust_vat_invoice", "receivable_correction"),
                     # 0170: huỷ phiếu chi phí (đảo bút toán chi / phải trả bên bán) — cùng chuỗi sửa tiền
                     ("acct.cancel_expense", "receivable_correction"),
                     # 0183: chuyển pháp nhân của phiếu chi phí = đảo + ghi lại — cùng chuỗi sửa tiền
                     ("acct.transfer_expense_entity", "receivable_correction"),
                     # 0223: xoá phần thiếu của khoản lệch = giảm phải thu qua giấy báo có — cùng chuỗi, người duyệt khác người xin
                     ("acct.write_off_dossier", "receivable_correction")),
     kind="capability", requires=("directory",),
     rules="""- ĐỐI SOÁT TIỀN VỀ: ảnh / PDF SAO KÊ TÀI KHOẢN CỦA CÔNG TY ⇒ ĐỀ XUẤT acct__add_statement_line cho TỪNG dòng tiền VÀO (người xác nhận; source="image"; ngày, số tiền,
  nội dung chuyển khoản, bên chuyển, mã giao dịch chép đúng như ảnh — ô không đọc rõ để trống, KHÔNG đoán), rồi acct__reconcile để code khớp
  với khoản phải thu. Dòng khớp đủ ⇒ đề xuất acct__apply_statement (một lần bấm); dòng trả thiếu / thừa / chưa khớp ⇒ nói số chênh + gợi ý trên
  thẻ, để kế toán chọn từng dòng (acct__apply_line). Ảnh uỷ nhiệm chi / bảng đối soát do BÊN TRẢ gửi KHÔNG phải sao kê của công ty: chỉ đối
  chiếu với khoản phải thu (acct__receivables) và nói khớp / lệch — không nạp, không ghi thu.
- TIỀN THEO BẢN CHẤT: khoản phải thu (acct__receivables) ghi rõ doanh thu / THU HỘ / tiền cọc. Tiền thu hộ (tiền nhà của chủ nhà…) và tiền cọc
  KHÔNG phải doanh thu của công ty — nói doanh thu thì chỉ tính phần phí. Khách hỏi chuyển khoản ⇒ nhắc ghi MÃ THANH TOÁN (TT…) của khoản đó.
""")

# Nhóm màn trên menu Kế toán (Screen.group) — theo nhịp việc kế toán; gói góp màn dùng đúng các chữ này để màn của gói vào cùng nhóm
G_IN, G_AR, G_OUT = "Tiền về & đối soát", "Phải thu", "Chi & phải trả"
G_BOOK, G_REPORT, G_SETUP = "Sổ sách & thuế", "Báo cáo & kiểm soát", "Thiết lập"

m = Module("acct", "Kế toán", pack="finance", resources={"acct": "Kế toán (khoản phải thu, tiền về, chi, dòng tiền)"})
# NGHIỆP VỤ CÔNG TY CÓ DÙNG (api 1.63, ``Screen(feature=)``): công ty không làm việc này thì tắt ⇒ màn không lên menu của vai nào. Mặc định BẬT.
USE_REMITTANCES = m.setting("use_remittances", "Công ty có thu hộ rồi chi trả lại", default=True,
                            help="Bật: Kế toán có màn Chi trả thu hộ (tiền thu hộ bên khác, chờ chuyển trả). Tắt: ẩn với mọi vai (dữ liệu đã có giữ nguyên, "
                                 "bật lại là thấy).")
USE_COST_OBJECTS = m.setting("use_cost_objects", "Công ty tập hợp chi phí theo công trình / hợp đồng", default=True,
                             help="Bật: Kế toán có màn Đối tượng tập hợp chi phí (gom chi phí theo từng công trình, hợp đồng, đơn hàng). Tắt: ẩn với mọi "
                                  "vai (dữ liệu đã có giữ nguyên, bật lại là thấy).")
m.consumes("plug")   # cảnh báo chi (acct.payout_flags) hỏi gói nguồn bằng quyền hệ thống — không trống vì người xin thiếu quyền đọc nguồn
# Tài khoản của Tài chính dùng chung (mọi ngành). Gói ngành khai tài khoản riêng của mình (vd phải thu CĐT / doanh thu phí môi giới — gói Môi
# giới) và chọn khi lập khoản phải thu. held:<người nhận>, deposit:<khoản cọc> tạo theo đối tượng lúc ghi (ledger.ensure_account).
ledger.declare({
    "cash:bank": ("Tiền gửi ngân hàng", "asset"),
    "payout:clearing": ("Phải trả — chờ chuyển khoản", "liability"),
    "revenue:advance_charge": ("Phí ứng theo chính sách", "revenue"),
    "tax:withheld": ("Thuế đã khấu trừ phải nộp", "liability"),
    "unapplied:bank": ("Tiền về chưa phân bổ", "liability"),
    "receivable:customers": ("Phải thu khách hàng", "asset"),
    "revenue:services": ("Doanh thu dịch vụ", "revenue"),
    "equity:opening": ("Số dư đầu kỳ (đối ứng)", "equity"),
    "revenue:opening_adjustment": ("Điều chỉnh giảm phải thu đầu kỳ", "revenue"),
    # 0156: tài khoản chung cho danh mục khoản thu / chi mặc định (công ty khai danh mục riêng, mỗi mục chọn một tài khoản đã khai)
    "revenue:other_income": ("Thu nhập khác", "revenue"),
    "expense:bonus": ("Chi thưởng", "expense"),
    "expense:marketing": ("Chi phí marketing", "expense"),
    "expense:other": ("Chi phí khác", "expense"),   # 0119: huỷ khoản phải thu đầu kỳ ghi vào kỳ hiện tại   # 0119: đối ứng của mọi dòng đầu kỳ; ánh xạ sang hệ thống tài khoản khách: còn mở
}, mechanism=("revenue:advance_charge", "revenue:opening_adjustment"))   # 0189: tài khoản cơ chế — không vào danh mục khoản thu / chi
m.trigger("acct.receivable_overdue", "Khoản phải thu quá hạn", {"code": "Mã thanh toán", "label": "Nội dung", "rest": "Còn phải thu (đồng)"},
          open=("acct.receivables", {}), owner="owner_id")
m.trigger("acct.payment_received", "Có tiền về", {"code": "Mã thanh toán", "payment_vnd": "Số tiền vừa thu (đồng)", "amount_vnd": "Phải thu (đồng)"},
          open=("acct.receivables", {}))

# Nguồn ngày cho luật tự động theo thời gian (ADR 0246): "3 ngày trước hạn khoản phải thu ⇒ nhắc người phụ trách" — cùng trường an toàn như cò quá hạn
@m.date_source("receivable_due", "Khoản phải thu — hạn thu", date_label="hạn thu", resource="acct",
               fields={"code": "Mã thanh toán", "label": "Nội dung", "rest": "Còn phải thu (đồng)"}, ids=("receivable_id",), open=("acct.receivables", {}),
               why="Động cơ luật tự động (flow, ADR 0246) xét luật theo ngày bằng quyền hệ thống: khoản phải thu CHƯA THU ĐỦ (mở / thu một phần) có hạn "
                   "trong khoảng được hỏi — trả mã khoản, hạn, người phụ trách (mã), mã thanh toán, nội dung, số còn phải thu (cùng trường cò "
                   "acct.receivable_overdue); không tên bên trả. Hành động của luật chạy bằng quyền người đặt luật (nạp lại mỗi lần)")
async def receivable_due(ctx: Ctx, inp: DateIn) -> list[dict[str, Any]]:
    return await ctx.all(
        """SELECT id::text AS ref, id AS receivable_id, due_on AS "on", owner_id, code, label, amount_vnd - paid_vnd AS rest
           FROM acct.receivables WHERE status IN ('open', 'partial') AND due_on BETWEEN %s AND %s ORDER BY due_on, id LIMIT 2000""",
        (inp.since, inp.until))


PAYOUT_STATUS = {"pending_transfer": "Chờ chuyển khoản", "transferred": "Đã chuyển", "cancelled": "Đã huỷ", "unknown": "Cần tra soát"}


def _month(d: date) -> str:
    return f"{d.month:02d}/{d.year}"


# ── Kỳ đã khoá (0119): kiểm TRƯỚC ở lệnh có người bấm; việc chạy nền gặp kỳ khoá ⇒ việc cho kế toán, không lặng lẽ mất ──

async def locked_message(ctx: Ctx, entity: UUID | None, on: date) -> str:
    """Câu báo nếu ``on`` thuộc kỳ đã khoá của pháp nhân (trống = mặc định), không thì chuỗi rỗng."""
    lock = await ledger.locked_through(ctx, entity or await entities.default(ctx))
    if lock is not None and on <= lock:
        return f"Ngày {on:%d/%m/%Y} thuộc kỳ đã khoá tới {lock:%d/%m/%Y} — xin mở kỳ hoặc chọn ngày khác"
    return ""


_locked_told: dict[tuple[str, str], date] = {}   # (công ty, việc) → ngày đã giao — vòng quét chạy lại không giao trùng trong ngày


async def locked_task(ctx: Ctx, title: str, notes: str, *, key: str) -> None:
    """Việc cho kế toán (người có quyền ghi Kế toán cả công ty — theo ma trận quyền) khi việc chạy nền không ghi được sổ vì kỳ đã khoá."""
    mark = (str(ctx.p.tenant_id), key)
    if _locked_told.get(mark) == today():
        return
    owners = await ctx.call_system("admin.members_with_grant", resource="acct", action="write", scope="all")
    email = await ctx.val("SELECT email FROM core.users WHERE id = %s", (UUID(owners[0]),)) if owners else None
    if not email:
        return
    # chạy bằng CHÍNH người gọi của chuỗi này (lệnh nội bộ ⇒ quyền hệ thống theo hợp đồng, ghi tên người khởi đầu / người gây sự kiện), GIAO cho kế
    # toán — không mượn danh kế toán (0144: không job_for trong chuỗi do người khởi đầu)
    await ctx.call("flow.create_task", title=title[:300], notes=notes[:4000], due_on=today().isoformat(), assignee_email=email)
    _locked_told[mark] = today()


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


# ── Tiền vào: một giao dịch ngân hàng = một dòng; phiếu thu = phần phân bổ của dòng (quyết định 0057) ──
#
# Mỗi đồng vào tài khoản là MỘT dòng acct.statement_lines — mã giao dịch ngân hàng duy nhất ở ĐÂY, nên cùng một mã không thể thành hai lần thu.
# Xác nhận dòng ⇒ ghi sổ ĐỦ số tiền một lần (tiền gửi / "tiền về chưa phân bổ"); phân bổ từng phần vào khoản phải thu (mọi gói lập qua
# acct.issue_receivable — 0057 / 0079) chuyển tiền từ "chưa phân bổ" sang đúng tài khoản theo bản chất. DB giữ 0 ≤ đã phân bổ ≤ số tiền; dòng chỉ
# "xong" khi phân bổ hết — tiền thừa nằm ở "chưa phân bổ", không biến mất. Phiếu thu nhập tay cũng đi qua đây (dòng nguồn "manual").

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


async def _target(ctx: Ctx, ref: str) -> dict[str, Any]:
    """Khoản phải thu theo mã (mã thanh toán TT… hoặc mã gói đặt, vd HD-…). Khoá dòng."""
    no = ref.strip().upper()
    if ar := await ctx.one("SELECT * FROM acct.receivables WHERE code = %s FOR UPDATE", (no,)):
        return ar
    raise NotFound(f"Không có khoản phải thu mã {no}")


def _rest(t: dict[str, Any]) -> int:
    return 0 if t["status"] in ("paid", "void") else t["amount_vnd"] - t["paid_vnd"]


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
        fee_now = round(t["fee_vnd"] * paid / t["amount_vnd"]) - taken   # thu đủ ⇒ đúng bằng phí; thu dần ⇒ phí theo tỷ lệ
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


# ── Dòng tiền & sổ ──

class CashflowInput(Input):
    months: int = field("Số tháng", 4, ge=1, le=24)


TRACKED_DAYS = 60   # có dòng sao kê nạp trong ngần này ngày ⇒ coi số tiền ngân hàng trên sổ là đang được đối chiếu (điều kiện để báo âm tiền)


CASHFLOW = "cashflow_lines"   # <module>.cashflow_lines (nội bộ): dòng thu / chi DỰ PHÓNG của gói — {lines: [{key, label, kind, by_month}], kpis, note}
# ``note`` (tuỳ chọn, 0211): một câu gói nói về cách ước dòng của mình (vd "ước theo kỳ đã chốt gần nhất") — nối vào ghi chú của bảng.


@m.query("cashflow", resource="acct", title="Dòng tiền dự phóng", min_scope="all")
async def cashflow(ctx: Ctx, inp: CashflowInput) -> dict[str, Any]:
    """Theo tháng: thu = khoản phải thu chưa thu theo hạn (quá hạn dồn tháng này); chi = lệnh chi / chi trả chờ chuyển (tháng này) + nghĩa vụ phải
    trả còn lại theo NGÀY ĐẾN HẠN nguồn nêu (0211 — trống / quá hạn dồn tháng này) + tiền THU HỘ phải trả lại (đang giữ: tháng này; sẽ thu: tháng
    thu) + các dòng DỰ PHÓNG gói khai (``<module>.cashflow_lines``: khoản chi gói biết trước — ngân sách quảng cáo, quỹ lương kỳ chưa chốt…; gói tắt
    ⇒ không có dòng). Nghĩa vụ MẬT người xem không thấy chi tiết (vd lương) vẫn vào số chi dưới dạng tổng theo tháng (``acct.confidential_open``) —
    dòng tiền không thiếu khoản chi chỉ vì quyền xem. Tiền cọc đang giữ không tính chi (hoàn khi hết hợp đồng) — chỉ ghi chú."""
    t = today()
    months: list[str] = []
    y, mo = t.year, t.month
    for _ in range(inp.months):
        months.append(f"{mo:02d}/{y}")
        y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)

    def bucket(d: date) -> str | None:
        mth = _month(max(d, t))  # quá hạn ⇒ dồn vào tháng này
        return mth if mth in months else None

    inflow: dict[str, int] = defaultdict(int)
    out_remit: dict[str, int] = defaultdict(int)
    # phần THU HỘ (trừ phí của mình) sẽ phải trả lại người nhận — cùng tháng thu (ước)
    for r in await ctx.all("SELECT due_on, nature, amount_vnd, fee_vnd, amount_vnd - paid_vnd AS rest FROM acct.receivables"
                           " WHERE status IN ('open', 'partial')"):
        if b := bucket(r["due_on"]):
            inflow[b] += r["rest"]
            if r["nature"] == "pass_through":
                out_remit[b] += r["rest"] - round(r["fee_vnd"] * r["rest"] / r["amount_vnd"])
    held_now = -await ledger.prefix_balance(ctx, "held")            # tiền đang giữ hộ người khác — trong số dư ngân hàng nhưng không phải của mình
    deposits = -await ledger.prefix_balance(ctx, "deposit")
    unapplied = -await ledger.balance(ctx, "unapplied:bank")         # tiền đã về nhưng chưa phân bổ vào khoản nào (trả thừa…)
    parts = [f"{k} {_vnd(v)}" for k, v in (("giữ hộ", held_now), ("cọc", deposits), ("chưa phân bổ", unapplied)) if v]
    not_ours = ("Trong đó " + ", ".join(parts)) if parts else ""
    out_payout: dict[str, int] = defaultdict(int)
    # lệnh chi / chi trả đã duyệt, chờ chuyển khoản ⇒ tháng này
    payouts = await ctx.one("""SELECT coalesce(sum(amount_vnd), 0)::bigint AS v, coalesce(sum(amount_vnd) FILTER (WHERE confidential<>''), 0)::bigint AS conf
                               FROM acct.payouts WHERE status IN ('pending_transfer','unknown')""")
    assert payouts is not None
    out_payout[months[0]] += int(payouts["v"])
    out_payout[months[0]] += int(await ctx.val("SELECT coalesce(sum(amount_vnd), 0)::bigint FROM acct.remittances WHERE status = 'pending_transfer'") or 0)
    hidden = await ctx.call_system("acct.confidential_open")
    out_payout[months[0]] += max(0, int(hidden["pending_payouts"]) - int(payouts["conf"]))   # lệnh chi MẬT người xem không thấy (vd lương) — số gộp
    from barista.modules.acct.settlements import _balances
    # nghĩa vụ còn phải trả theo ngày đến hạn (0211). _balances đã trừ phần nằm trong lệnh chi chưa huỷ ⇒ không đếm hai lần với dòng trên
    seen_conf: dict[str, int] = defaultdict(int)
    for r in await _balances(ctx, "p.kind='payable'"):
        if r["left_vnd"] > 0 and (b := bucket(r["due_on"] or t)):
            out_payout[b] += int(r["left_vnd"])
            if r["confidential"]:
                seen_conf[b] += int(r["left_vnd"])
    # nghĩa vụ MẬT người xem không thấy dòng (RLS) ⇒ cộng phần tổng còn thiếu theo tháng đến hạn — số gộp, không người
    conf: dict[str, int] = defaultdict(int)
    for due, v in hidden["by_due"].items():
        if b := bucket(date.fromisoformat(due) if due else t):
            conf[b] += int(v)
    for b, v in conf.items():
        out_payout[b] += max(0, v - seen_conf[b])
    extra: list[dict[str, Any]] = []
    extra_kpis: list[dict[str, Any]] = []
    notes: list[str] = []
    for op in registry.OPS.values():
        if op.name == f"{op.module}.{CASHFLOW}" and (got := await ctx.call_system_optional(op.name, months=months)):
            extra += got["lines"]
            extra_kpis += got.get("kpis", [])
            if got.get("note"):
                notes.append(str(got["note"]))
    opening = await ledger.balance(ctx, "cash:bank")
    rows, bal = [], opening
    for i, mth in enumerate(months):
        row: dict[str, Any] = {"id": mth, "month": mth, "opening": bal, "inflow": inflow[mth], "out_payout": out_payout[mth],
                               "out_remit": out_remit[mth] + (held_now if i == 0 else 0)}
        net = row["inflow"] - row["out_payout"] - row["out_remit"]
        for x in extra:
            v = int(x["by_month"].get(mth, 0))
            row[x["key"]] = v
            net += v if x["kind"] == "in" else -v
        rows.append({**row, "net": net, "closing": bal + net})
        bal += net
    low = min((r["closing"] for r in rows), default=opening)
    # BÁO TRƯỚC (0211 lát 2): tháng đầu tiên cuối kỳ âm tiền — CHỈ khi công ty có nạp sao kê gần đây; không có thì "tiền hiện có" là số sổ chưa đối
    # chiếu ngân hàng (thường 0) ⇒ mọi khoản chi đều thành "âm tiền" giả, báo sẽ thành tiếng ồn
    tracked = bool(await ctx.val("SELECT EXISTS (SELECT 1 FROM acct.statement_lines WHERE received_on >= %s)", (t - timedelta(days=TRACKED_DAYS),)))
    neg = next((r for r in rows if r["closing"] < 0), None) if tracked else None
    page = ui.table(rows, [
        ui.col("month", "Tháng"), ui.col("opening", "Đầu kỳ", "money"), ui.col("inflow", "Tiền thu vào", "money"),
        *[ui.col(x["key"], x["label"], "money") for x in extra if x["kind"] == "in"],
        *[ui.col(x["key"], x["label"], "money") for x in extra if x["kind"] == "out"],
        ui.col("out_payout", "Phải trả & lệnh chi chờ", "money"), ui.col("out_remit", "Trả tiền thu hộ", "money"),
        ui.col("net", "Ròng", "money"), ui.col("closing", "Cuối kỳ", "money"),
    ], title="Dòng tiền dự phóng", row_key="id", kpis=[
        ui.kpi("Tiền hiện có", opening, "money", hint=not_ours),
        ui.kpi("Thấp nhất trong kỳ", low, "money", tone="red" if low < 0 else "green"), *extra_kpis,
    ], note=" ".join(["Dự phóng từ dữ liệu đang có: khoản phải thu theo hạn, nghĩa vụ phải trả theo ngày đến hạn, lệnh chi chờ chuyển và các khoản "
                      "dự kiến của nghiệp vụ khác (vd hoa hồng chờ tiền phí về).", *notes, "Chưa gồm khoản chi chưa có trong hệ thống (thuê văn phòng, thuế doanh nghiệp…).",
                      *([] if tracked else [f"Chưa nạp sao kê trong {TRACKED_DAYS} ngày qua — tiền hiện có chưa đối chiếu ngân hàng, không báo âm tiền."])]))
    # ADD-only (bất biến 7): tháng đầu tiên âm tiền {month, closing} hoặc None — tín hiệu bàn việc + bản tin buổi sáng đọc trường này
    page["alert"] = {"month": neg["month"], "closing": int(neg["closing"])} if neg else None
    return page


def _say_cashflow(o: Any) -> Any:
    """Tín hiệu bàn việc (0211 lát 2): tháng đầu tiên dự phóng âm tiền — code đọc trường ``alert`` của trang, không model."""
    a = (o or {}).get("alert")
    return line("bad", f"Dòng tiền dự phóng âm tháng {a['month']}", f"Cuối tháng còn {_vnd(a['closing'])} — xem khoản thu / chi từng tháng",
                f"Vì sao tháng {a['month']} dự phóng âm tiền, khoản chi nào lớn nhất?") if a else None


SUBLEDGER = {"advance": "Tạm ứng", "recovery": "Phải thu hồi", "payable": "Phải trả"}
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class EntityIn(Input):
    entity_code: str = field("Pháp nhân", "", options_query="admin.entity_options", placeholder="Cả công ty (gộp mọi pháp nhân)")


@m.query("balances", resource="ledger", title="Số dư sổ cái", min_scope="all")
async def balances(ctx: Ctx, inp: EntityIn) -> dict[str, Any]:
    eid = await entities.resolve(ctx, inp.entity_code) if inp.entity_code.strip() else None
    rows = await ledger.balances(ctx, eid)
    kinds = {"asset": "Tài sản", "liability": "Nợ phải trả", "equity": "Vốn", "revenue": "Doanh thu", "expense": "Chi phí"}
    # tài khoản chi tiết theo đối tượng (mã <tiền tố>:<uuid>) — người đọc thấy tên tài khoản cha + tên đối tượng, không thấy uuid (rà vai kế toán
    # 18/11/2026); tài khoản có số dư lên trước, số dư 0 xuống cuối. Chỉ THÊM trường "account"; "code" giữ nguyên cho xuất / đối chiếu.
    rows.sort(key=lambda r: (r["balance"] == 0, list(kinds).index(r["kind"]) if r["kind"] in kinds else 9, r["code"]))
    for i, r in enumerate(rows):
        r["id"] = i
        r["debit"] = r["balance"] if r["balance"] > 0 else 0
        r["credit"] = -r["balance"] if r["balance"] < 0 else 0
        pre, _, rest = r["code"].partition(":")
        r["account"] = f"{SUBLEDGER.get(pre, pre)} — chi tiết đối tượng" if _UUID_RE.fullmatch(rest) else r["code"]
    live = sum(1 for r in rows if r["balance"])
    return ui.table(rows, [
        ui.col("account", "Tài khoản"), ui.col("name", "Tên / đối tượng"), ui.col("kind", "Loại", "badge", labels=kinds),
        ui.col("debit", "Dư Nợ", "money"), ui.col("credit", "Dư Có", "money"),
    ], title="Số dư sổ cái" + (f" · {inp.entity_code.strip().upper()}" if eid else ""), kpis=[ui.kpi("Tổng Nợ", sum(r["debit"] for r in rows), "money"),
                                   ui.kpi("Tổng Có", sum(r["credit"] for r in rows), "money"),
                                   ui.kpi("Tài khoản có số dư", live, hint=f"trên {len(rows)} tài khoản — số dư 0 xếp cuối")],
        note="Tổng Nợ luôn bằng tổng Có — hệ thống kiểm từng bút toán khi ghi.")


class Period(Input):
    month: str = field("Kỳ (tháng)", "", options_query="acct.month_options", example="10/2026", max_length=7, placeholder="Tháng đang kê khai",
                       help="Chọn một tháng, hoặc để trống rồi đặt khoảng ngày ở “Thêm điều kiện”. Tháng đang kê khai là tháng trước tới hạn "
                            "nộp tờ khai, sau đó là tháng này",
                       primary=True)
    date_from: Day | None = field("Từ ngày", None, placeholder="Đầu kỳ kê khai", group="Thêm điều kiện")
    date_to: Day | None = field("Đến ngày", None, placeholder="Cuối kỳ kê khai", group="Thêm điều kiện")
    entity_code: str = field("Pháp nhân", "", options_query="admin.entity_options", primary=True, placeholder="Mọi pháp nhân",
                             help="Mỗi pháp nhân một dữ liệu MISA — chọn pháp nhân để xuất")


def _misa_account(code: str, mapping: list[dict[str, Any]]) -> str:
    best = ""
    acct = "????"
    for mp in mapping:
        pre = mp["ledger_prefix"]
        if (code == pre or code.startswith(pre + ":")) and len(pre) > len(best):
            best, acct = pre, mp["misa_account"]
    return acct


@m.query("misa_export", resource="acct", title="Xuất bút toán MISA", min_scope="all")
async def misa_export(ctx: Ctx, inp: Period) -> dict[str, Any]:
    """Mỗi cặp Nợ / Có một dòng — đúng dạng màn "Nhập khẩu chứng từ nghiệp vụ khác" của MISA (cột cần khớp mẫu
    công ty đang dùng; ánh xạ tài khoản sửa ở bảng Ánh xạ tài khoản)."""
    from barista.modules.acct.vat import working_month  # nạp muộn: vat nạp sau gói acct

    t = today()
    start = working_month(t) if inp.date_from is None and inp.date_to is None else t.replace(day=1)
    d_from, d_to = inp.date_from or start, inp.date_to or (t if start == t.replace(day=1) else t.replace(day=1) - timedelta(days=1))
    if inp.month.strip():   # một kỳ MISA = một tháng: tải đúng tháng đó thành file (Xuất Excel của bảng) để nhập vào MISA
        if not (mm := re.fullmatch(r"(\d{1,2})/(\d{4})", inp.month.strip())) or not 1 <= int(mm.group(1)) <= 12:
            raise Invalid("Kỳ ghi dạng MM/YYYY, vd 10/2026")
        d_from = date(int(mm.group(2)), int(mm.group(1)), 1)
        d_to = min((d_from + timedelta(days=32)).replace(day=1) - timedelta(days=1), t)
    if inp.entity_code.strip():
        lock = await ledger.locked_through(ctx, await entities.resolve(ctx, inp.entity_code))
    else:   # mọi pháp nhân: sổ chỉ "khoá tới" ngày của pháp nhân khoá MUỘN NHẤT trong số đó (một pháp nhân chưa khoá ⇒ chưa khoá)
        locks = [await ledger.locked_through(ctx, UUID(e)) for e in await entities.names(ctx)]
        lock = None if not locks or any(x is None for x in locks) else min(x for x in locks if x is not None)
    mapping = await ctx.all("SELECT ledger_prefix, misa_account FROM acct.account_map")
    eid = await entities.resolve(ctx, inp.entity_code) if inp.entity_code.strip() else None
    lines = await ledger.journal_lines(ctx, d_from, d_to, eid)
    ents = await entities.names(ctx)
    owners = {str(r["id"]): r["name"] for r in await ctx.all("SELECT id, name FROM core.users")}
    journals: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for ln in lines:
        journals[ln["journal_id"]].append(ln)
    rows: list[dict[str, Any]] = []
    for jid, ls in journals.items():
        debits = [[ln, ln["amount_vnd"]] for ln in ls if ln["amount_vnd"] > 0]
        credits = [[ln, -ln["amount_vnd"]] for ln in ls if ln["amount_vnd"] < 0]
        di = ci = 0
        while di < len(debits) and ci < len(credits):  # ghép cặp tham lam — tổng nợ = tổng có nên luôn hết
            amt = min(debits[di][1], credits[ci][1])
            dl, cl = debits[di][0], credits[ci][0]
            obj = next((owners.get(x["code"].split(":", 1)[1], "") for x in (dl, cl) if x["code"].startswith("wallet:")), "")
            if not obj:
                obj = next((x["account_name"] for x in (dl,cl) if x["code"].split(":")[0] in {"payable","advance","recovery"}), "")
            rows.append({"id": f"{jid}-{len(rows)}", "day": dl["day"], "voucher": str(jid)[:8].upper(), "memo": dl["memo"],
                         "debit": _misa_account(dl["code"], mapping), "credit": _misa_account(cl["code"], mapping),
                         "amount_vnd": amt, "object": obj, "ref": dl["source_ref"], "entity": ents[str(dl["entity_id"])]["code"]})
            debits[di][1] -= amt
            credits[ci][1] -= amt
            di += debits[di][1] == 0
            ci += credits[ci][1] == 0
    unmapped = sum(1 for r in rows if "????" in (r["debit"], r["credit"]))
    return ui.table(rows, [
        ui.col("day", "Ngày hạch toán", "date"), ui.col("voucher", "Số chứng từ"), ui.col("memo", "Diễn giải", width=280),
        ui.col("debit", "TK Nợ"), ui.col("credit", "TK Có"), ui.col("amount_vnd", "Số tiền", "money"),
        ui.col("object", "Đối tượng"), ui.col("ref", "Tham chiếu"), *([ui.col("entity", "Pháp nhân")] if eid is None and len(ents) > 1 else []),
    ], title=f"Bút toán {d_from:%d/%m/%Y} – {d_to:%d/%m/%Y}" + (f" · {inp.entity_code.strip().upper()}" if eid else ""), kpis=[
        ui.kpi("Số dòng", len(rows)), ui.kpi("Tài khoản chưa ánh xạ", unmapped, tone="red" if unmapped else "green"),
        ui.kpi("Sổ khoá tới", lock, "date", tone="green" if lock is not None and lock >= d_to else "orange",
               hint="kỳ này đã khoá" if lock is not None and lock >= d_to else "kỳ này chưa khoá — số còn đổi được"),
    ], note="Bấm Xuất Excel để tải file nhập MISA của kỳ. Tài khoản “????” = chưa ánh xạ — sửa ở Kế toán › Ánh xạ tài khoản. Nên khoá kỳ trước khi "
            "nhập để số đã nộp không đổi.")


@m.query("account_map", resource="acct", title="Ánh xạ tài khoản MISA", min_scope="all")
async def account_map(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await ctx.all("SELECT ledger_prefix AS id, ledger_prefix, misa_account, label FROM acct.account_map ORDER BY ledger_prefix")
    return ui.table(rows, [ui.col("ledger_prefix", "Tài khoản sổ cái"), ui.col("misa_account", "Tài khoản MISA"),
                           ui.col("label", "Ghi chú", width=300)], title="Ánh xạ tài khoản MISA",
                    note="Một tiền tố (vd “wallet”) áp cho mọi tài khoản con cùng tiền tố. Giá trị mặc định là GIẢ ĐỊNH — kế toán xác nhận theo hệ thống "
                         "tài khoản đang dùng.")


class SetMap(Input):
    ledger_prefix: str = field("Tài khoản trong Barista", options_query="acct.ledger_account_options", example="expense:marketing", max_length=60,
                               aliases=("Tài khoản sổ cái",))
    misa_account: str = field("Số tài khoản bên MISA", example="641", max_length=8, aliases=("Tài khoản MISA",))
    label: str = field("Ghi chú", "", max_length=200)


@m.query("ledger_account_options", resource="acct", title="Tài khoản sổ cái")
async def ledger_account_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    """Mọi tài khoản sổ cái đã khai + tài khoản chi phí riêng của công ty (theo mục khoản chi), bằng tên người đọc; mã chỉ là giá trị."""
    names = {c: n for c, (n, _) in ledger.ACCOUNTS.items()}
    for r in await ctx.all("SELECT account, label FROM acct.categories WHERE active"):
        names.setdefault(r["account"], r["label"])
    for pre in sorted({c.split(":")[0] for c in list(names) if ":" in c}):   # ánh xạ theo nhóm (tiền tố) — vd mọi tài khoản “payable:…”
        names.setdefault(pre, f"Cả nhóm {pre}")
    dup = {n for n in names.values() if list(names.values()).count(n) > 1}
    return [{"value": c, "label": f"{n} ({c})" if n in dup else n} for c, n in sorted(names.items(), key=lambda kv: (kv[1], kv[0]))]


@m.command("set_account_map", resource="acct", action="manage", title="Đặt ánh xạ tài khoản", importable=True)
async def set_account_map(ctx: Ctx, inp: SetMap) -> dict[str, Any]:
    await ctx.exec(
        """INSERT INTO acct.account_map (ledger_prefix, misa_account, label) VALUES (%s, %s, %s)
           ON CONFLICT (tenant_id, ledger_prefix) DO UPDATE SET misa_account = EXCLUDED.misa_account, label = EXCLUDED.label""",
        (inp.ledger_prefix, inp.misa_account, inp.label))
    return {"ledger_prefix": inp.ledger_prefix}


class CollectIn(Input):
    since: date
    until: date
    source_prefix: str = ""


@m.query("collections", resource="acct", title="Ghi nhận và thực thu theo kỳ", internal=True, agent="deny", family="company_figures",
         why="Màn điều hành / trang báo cáo quản trị của gói khai consumes(\"company_figures\") (thao tác gọi khai min_scope=\"all\" — người xem có "
             "quyền trên màn đó cả công ty; bên gọi kiểm, acct không kiểm lại) xem công nợ của MỘT tiền tố nguồn <module>.<loại> (bắt buộc — không gộp mọi "
             "nguồn) của CẢ công ty. "
             "Chỉ SỐ GỘP: {recognized, received: [{m, v}] theo tháng, aging: {not_due, d30, d60, d60p, total}, top_parties: 5 [{name, rest}]} "
             "— không từng khoản, không mã.")
async def collections(ctx: Ctx, inp: CollectIn) -> dict[str, Any]:
    """Cho màn điều hành của gói (lọc theo nguồn khoản phải thu của gói): khoản GHI NHẬN theo ngày lập và TIỀN THỰC THU theo ngày thu từng tháng + công
    nợ đang mở phân tuổi theo hạn + bên trả nợ nhiều nhất. Hai số đầu KHÔNG cùng một thứ — màn điều hành ghi rõ, không gộp."""
    if ctx.caller and not re.fullmatch(r"[a-z_]+\.[a-z_.]*", inp.source_prefix):   # 0186: qua hợp đồng hệ thống thì phải nêu nguồn — không "mọi khoản phải thu"
        raise Invalid("Cần tiền tố nguồn dạng <module>.<loại>")
    prm = {"a": inp.since, "b": inp.until, "s": inp.source_prefix}
    recognized = await ctx.all("""SELECT to_char(issued_on, 'YYYY-MM') AS m, coalesce(sum(amount_vnd), 0)::bigint AS v FROM acct.receivables
                                  WHERE starts_with(source, %(s)s) AND status <> 'void' AND issued_on >= %(a)s AND issued_on < %(b)s
                                  GROUP BY 1 ORDER BY 1""", prm)
    received = await ctx.all("""SELECT to_char(p.received_on, 'YYYY-MM') AS m, coalesce(sum(p.amount_vnd), 0)::bigint AS v FROM acct.payments p
                                JOIN acct.receivables r ON r.id = p.receivable_id
                                WHERE starts_with(r.source, %(s)s) AND p.received_on >= %(a)s AND p.received_on < %(b)s GROUP BY 1 ORDER BY 1""", prm)
    ag = await ctx.one(
        """SELECT coalesce(sum(rest) FILTER (WHERE due_on >= core.today()), 0)::bigint AS not_due,
                  coalesce(sum(rest) FILTER (WHERE due_on < core.today() AND due_on >= core.today() - 30), 0)::bigint AS d30,
                  coalesce(sum(rest) FILTER (WHERE due_on < core.today() - 30 AND due_on >= core.today() - 60), 0)::bigint AS d60,
                  coalesce(sum(rest) FILTER (WHERE due_on < core.today() - 60), 0)::bigint AS d60p,
                  coalesce(sum(rest), 0)::bigint AS total
           FROM (SELECT due_on, amount_vnd - paid_vnd AS rest FROM acct.receivables
                 WHERE starts_with(source, %(s)s) AND status IN ('open', 'partial')) x""", prm)
    top = await ctx.all("""SELECT max(party) AS name, sum(amount_vnd - paid_vnd)::bigint AS rest FROM acct.receivables
                           WHERE starts_with(source, %(s)s) AND status IN ('open', 'partial')
                           GROUP BY coalesce(party_id::text, party) ORDER BY rest DESC LIMIT 5""", prm)   # 0177: cùng tên khác mã ⇒ hai dòng
    return {"recognized": recognized, "received": received, "aging": ag, "top_parties": top}


# ── Đối soát sao kê (họp khách 31/10 — docs/clients/<khách>, F02/F03 — quyết định 0055) ──
#
# Dòng tiền VÀO (sao kê ngân hàng, bảng đối soát của bên trả, ảnh trợ lý đọc) nạp vào acct.statement_lines — chưa đụng sổ cái. CODE khớp từng
# dòng với KHOẢN PHẢI THU: mã khoản / mã riêng phụ (số giao dịch, hợp đồng) / chữ gợi ý (mã căn) trong nội dung chuyển khoản + so số tiền với số
# còn phải thu. Người bấm "ghi" ⇒ phân bổ (sổ cái, sự kiện cho gói nguồn). Chênh lệch KHÔNG tự xoá nợ: ghi phần đã nhận + một việc cho kế
# toán kèm gợi ý nguyên nhân (luật hạch toán phần chênh chờ khách chốt).

LINE_STATUS = {"open": "Chưa đối soát", "applied": "Đã phân bổ", "ignored": "Bỏ qua"}
MATCH = {"full": "Khớp đủ", "short": "Trả thiếu", "over": "Trả thừa", "amount_only": "Khớp theo số tiền", "unmatched": "Chưa khớp",
         "settled": "Khoản đã thu đủ"}
MATCH_TONE = {"full": "green", "short": "orange", "over": "orange", "amount_only": "blue", "unmatched": "red", "settled": "gray"}
FEE_TOLERANCE = 50_000   # chênh ≤ mức này ⇒ nhiều khả năng phí chuyển khoản ngân hàng
DUE_SOON_DAYS = 10       # khoản phải thu chung tới hạn trong ngần này ngày mới được khớp theo mã căn / số tiền


def _vnd(n: int) -> str:
    """Chỉ đổi dấu phân cách của CON SỐ — đừng .replace(",", ".") cả câu (ăn mất dấu phẩy của chữ)."""
    return f"{n:,}".replace(",", ".") + " đ"


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


@m.command("add_statement_line", resource="acct", title="Nạp dòng sao kê", importable=True, agent="propose", import_rows=_statement_rows)
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


def _hint(remaining: int, amount: int, payer: str) -> str:
    """Gợi ý nguyên nhân chênh (trả thiếu) — chỉ là GỢI Ý để kế toán hỏi lại bên trả, không tự hạch toán: phí ngân hàng, giữ lại 10% (bảo lưu),
    chuyển số trước VAT, chia đợt."""
    diff = remaining - amount
    if diff <= FEE_TOLERANCE:
        return "Chênh nhỏ — có thể là phí chuyển khoản ngân hàng"
    if abs(diff - round(remaining * 0.1)) <= max(1000, remaining // 1000):
        return f"Thiếu đúng 10% — có thể {payer} giữ lại 10% (bảo lưu / chờ đủ hồ sơ)"
    if abs(diff - round(remaining / 11)) <= max(1000, remaining // 1000):
        return f"Thiếu đúng phần VAT 10% — có thể {payer} chuyển số trước thuế"
    if abs(diff - round(remaining * 0.5)) <= max(1000, remaining // 1000):
        return f"Trả một nửa — có thể {payer} chia đợt"
    return f"Hỏi {payer} lý do (phạt, khấu trừ, chia đợt, chuyển nhầm số…)"


def _targets(receivables: list[dict[str, Any]], horizon: date | None) -> list[dict[str, Any]]:
    """``invoice`` = phần số của số hoá đơn đã xuất (0151). Mọi khoản phải thu một dòng tiền vào có thể trả cho, cùng một dạng để khớp. ``exact`` = mã khoản — thấy là nhận; ``unique`` = mã riêng
    phụ (số giao dịch, hợp đồng) — nhận khi đúng MỘT khoản trúng; ``hints`` = mã gợi ý (vd mã đơn vị / phòng) — nhận khi mọi khoản trúng thuộc CÙNG một bên
    trả (cùng ``group``) thì lấy khoản tới hạn sớm nhất còn nợ. ``soon`` = khoản đến hạn trong tầm (kỳ sau chưa tới không "ăn" tiền kỳ này khi
    khớp theo gợi ý / số tiền)."""
    from barista.api import name_key
    from barista.modules.acct.bank import invoice_number

    out: list[dict[str, Any]] = []
    for r in receivables:
        if r["status"] == "void":
            continue
        out.append({"id": r["id"], "ref": r["code"], "amount_vnd": r["amount_vnd"], "paid_vnd": r["paid_vnd"], "due_on": r["due_on"],
                    "payer": r.get("party") or "khách",
                    "group": (r.get("party") or "", tuple(sorted(r.get("match_hints") or [])), tuple(sorted(r.get("match_keys") or []))),   # cùng bên trả + cùng gợi ý (cọc lẫn tiền nhà)
                    "soon": horizon is None or r["due_on"] <= horizon, "exact": [(name_key(r["code"]), "mã khoản")],
                    "invoice": invoice_number(r.get("invoice_no") or ""),
                    "unique": [(k, "mã riêng") for x in r.get("match_keys") or [] if len(k := name_key(x)) >= 4],
                    "hints": [(k, "mã gợi ý") for h in r.get("match_hints") or [] if len(k := name_key(h)) >= 4]})
    return out


class _Keys:
    """Chỉ mục "mã nào nằm trong nội dung": thay vì thử từng mã của mọi khoản cho mỗi dòng (O(dòng × khoản) — 1.500 × 2.000 mất ~3 giây, N11), cắt
    nội dung thành mọi đoạn con có độ dài bằng độ dài một mã rồi tra từ điển. ``hits(key)`` = các (thứ tự khoản, thứ tự mã trong khoản) có mã
    nằm trong ``key`` — cùng tập với phép ``k in key`` cũ, bên gọi tự xếp theo thứ tự cũ."""

    def __init__(self, entries: list[list[str]]) -> None:
        self.by_key: dict[str, list[tuple[int, int]]] = {}
        for i, keys in enumerate(entries):
            for j, k in enumerate(keys):
                self.by_key.setdefault(k, []).append((i, j))
        self.lengths = sorted({len(k) for k in self.by_key})

    def hits(self, key: str) -> list[tuple[int, int]]:
        found: set[str] = set()
        for n in self.lengths:
            if n > len(key):
                break
            found.update(s for s in (key[p:p + n] for p in range(len(key) - n + 1)) if s in self.by_key)
        return sorted(h for k in found for h in self.by_key[k])


class _Owed:
    """Khoản đến hạn trong tầm còn nợ, theo SỐ CÒN PHẢI THU (đổi dần khi các dòng trước trả vào): tra "đúng một khoản bằng số tiền" và "ba khoản
    gần số tiền nhất" không phải duyệt mọi khoản cho mỗi dòng. Hoà khoảng cách ⇒ khoản đứng trước trong danh sách (như sắp xếp ổn định cũ)."""

    def __init__(self, left: list[int], soon: list[bool]) -> None:
        import bisect

        self._bisect = bisect
        self.soon = soon
        self.sorted = sorted((v, i) for i, v in enumerate(left) if soon[i] and v > 0)
        self.by_left: dict[int, set[int]] = defaultdict(set)
        for v, i in self.sorted:
            self.by_left[v].add(i)

    def set(self, i: int, old: int, new: int) -> None:
        if not self.soon[i]:
            return
        if old > 0:
            del self.sorted[self._bisect.bisect_left(self.sorted, (old, i))]
            self.by_left[old].discard(i)
        if new > 0:
            self._bisect.insort(self.sorted, (new, i))
            self.by_left[new].add(i)

    def equal(self, amount: int) -> set[int]:
        return self.by_left.get(amount, set())

    def nearest(self, amount: int, n: int = 3) -> list[int]:
        arr = self.sorted
        lo = self._bisect.bisect_left(arr, (amount, -1)) - 1
        hi = lo + 1
        cand: list[tuple[int, int]] = []
        while lo >= 0 or hi < len(arr):
            dl = amount - arr[lo][0] if lo >= 0 else None
            dh = arr[hi][0] - amount if hi < len(arr) else None
            d = dl if dh is None or (dl is not None and dl <= dh) else dh
            assert d is not None
            if len(cand) >= n and d > cand[-1][0]:
                break
            if d == dl and lo >= 0:
                cand.append((d, arr[lo][1]))
                lo -= 1
            else:
                cand.append((d, arr[hi][1]))
                hi += 1
        return [i for _, i in sorted(cand)[:n]]


def match_lines(lines: list[dict[str, Any]], receivables: list[dict[str, Any]], horizon: date | None = None) -> list[dict[str, Any]]:
    """Khớp bằng CODE (hàm thuần, test thẳng). Ưu tiên: mã khoản > mã riêng phụ (đúng một khoản) > SỐ HOÁ ĐƠN đã xuất ngay sau chữ "hoá đơn /
    HĐĐT / invoice" (đúng một khoản mang số đó, số tiền không vượt phần còn phải thu — 0151; ghi hàng loạt KHÔNG nhận, người xem từng dòng) > gợi ý (đúng một bên trả; khoản thu định kỳ ⇒
    kỳ tới hạn sớm nhất còn nợ) trong nội dung đã bỏ dấu / ký tự; không thấy mã ⇒ đúng MỘT khoản đến hạn có số còn phải thu bằng số tiền ⇒
    "khớp theo số tiền" (người xác nhận từng dòng). Hai dòng cùng trỏ một khoản ⇒ tính dồn theo ngày. Khi nhiều khoản cùng trúng, khoản đứng
    trước trong ``receivables`` thắng."""
    from barista.api import name_key
    from barista.modules.acct.bank import invoice_numbers_in

    targets = _targets(receivables, horizon)
    by_invoice: dict[str, list[int]] = defaultdict(list)
    for i, t in enumerate(targets):
        if t["invoice"]:
            by_invoice[t["invoice"]].append(i)
    left = {t["id"]: t["amount_vnd"] - t["paid_vnd"] for t in targets}
    pos = {id(t): i for i, t in enumerate(targets)}
    exact, unique = _Keys([[k for k, _ in t["exact"]] for t in targets]), _Keys([[k for k, _ in t["unique"]] for t in targets])
    hints = _Keys([[k for k, _ in t["hints"]] if t["soon"] else [] for t in targets])
    owed = _Owed([left[t["id"]] for t in targets], [t["soon"] for t in targets])
    out = []
    for ln in sorted(lines, key=lambda x: (x["received_on"], str(x["id"]))):
        key = name_key(f"{ln['memo']} {ln['payer']}")
        r: dict[str, Any] = {**ln, "match": "unmatched", "code": "", "remaining": None, "diff": None, "hint": "", "how": ""}
        t, how = None, ""
        if hit := exact.hits(key):
            i, j = hit[0]
            t, how = targets[i], targets[i]["exact"][j][1]
        if t is None:
            hits = {i: j for i, j in unique.hits(key)}
            if len(hits) == 1:
                i, j = next(iter(hits.items()))
                t, how = targets[i], targets[i]["unique"][j][1]
        if t is None and by_invoice:   # số hoá đơn SAU mã riêng; chỉ nhận khi đúng một khoản và số tiền không vượt phần còn phải thu
            cand = {i for no in invoice_numbers_in(key) for i in by_invoice.get(no, [])}
            if len(cand) == 1 and 0 < ln["amount_vnd"] <= left[targets[(c := next(iter(cand)))]["id"]]:
                t, how = targets[c], "số hoá đơn"
        if t is None:
            hits_h = hints.hits(key)
            if hits_h and len({targets[i]["group"] for i, _ in hits_h}) == 1:
                last = {i: j for i, j in hits_h}   # một khoản trúng nhiều mã gợi ý ⇒ nhãn của mã sau cùng (như dict cũ)
                ordered = sorted(((targets[i], targets[i]["hints"][j][1]) for i, j in last.items()), key=lambda h: h[0]["due_on"])
                t, how = next((h for h in ordered if left[h[0]["id"]] > 0), ordered[0])
        if t is None:
            same = owed.equal(ln["amount_vnd"])
            if len(same) == 1:
                t, how = targets[next(iter(same))], "số tiền"
        if t is None:
            near = [targets[i] for i in owed.nearest(ln["amount_vnd"])]
            r["hint"] = "Không thấy mã khoản / mã riêng / mã gợi ý trong nội dung" + (
                " — gần số tiền nhất: " + ", ".join(x["ref"] for x in near) if near else "")
            out.append(r)
            continue
        rem = left[t["id"]]
        r.update(code=t["ref"], remaining=rem, how=how)
        if rem <= 0:
            r.update(match="settled", hint=f"{t['ref']} đã thu đủ — có thể trả trùng, hỏi lại {t['payer']}")
        elif how == "số tiền":
            r.update(match="amount_only", diff=0, hint="Không có mã trong nội dung — chỉ khớp theo số tiền, kiểm trước khi ghi")
        elif ln["amount_vnd"] == rem:
            r.update(match="full", diff=0)
        elif ln["amount_vnd"] < rem:
            r.update(match="short", diff=rem - ln["amount_vnd"], hint=_hint(rem, ln["amount_vnd"], t["payer"]))
        else:
            r.update(match="over", diff=ln["amount_vnd"] - rem, hint=f"{t['payer']} chuyển thừa — ghi đủ số còn phải thu, phần thừa hỏi lại")
        if r["match"] in ("full", "short", "over", "amount_only"):
            left[t["id"]] = max(0, rem - ln["amount_vnd"])
            owed.set(pos[id(t)], rem, left[t["id"]])
        out.append(r)
    return out


async def _matched(ctx: Ctx, line_ids: list[UUID] | None = None) -> list[dict[str, Any]]:
    # dòng đã ghi một phần (tiền thừa sau lần phân bổ trước) chỉ khớp tiếp phần CHƯA phân bổ
    lines = await ctx.all("""SELECT id, received_on, amount_vnd + deducted_vnd - allocated_vnd AS amount_vnd, amount_vnd AS line_total, allocated_vnd, memo, payer,
                                    bank_ref, source, entity_id FROM acct.statement_lines
                             WHERE status = 'open' AND (%s::uuid[] IS NULL OR id = ANY(%s)) ORDER BY received_on, id""", (line_ids, line_ids))
    receivables = await ctx.all("""SELECT id, code, source, party, amount_vnd, paid_vnd, due_on, status, match_hints, match_keys, entity_id, invoice_no
                                   FROM acct.receivables WHERE status <> 'void'""")
    # 0099: tiền về tài khoản của pháp nhân nào chỉ khớp khoản phải thu CỦA pháp nhân đó (thu hộ giữa pháp nhân chưa hỗ trợ — không đề xuất)
    out: dict[Any, dict[str, Any]] = {}
    for eid in {ln["entity_id"] for ln in lines}:
        for r in match_lines([ln for ln in lines if ln["entity_id"] == eid], [t for t in receivables if t["entity_id"] == eid],
                             today() + timedelta(days=DUE_SOON_DAYS)):
            out[r["id"]] = r
    await _other_entity_hints(ctx, [r for r in out.values() if r["match"] == "unmatched"], receivables)
    return [out[ln["id"]] for ln in lines if ln["id"] in out]


async def _other_entity_hints(ctx: Ctx, unmatched: list[dict[str, Any]], receivables: list[dict[str, Any]]) -> None:
    """Dòng chưa khớp mà nội dung có MÃ KHOẢN của pháp nhân KHÁC ⇒ nói rõ (code biết, không để người / trợ lý đoán): tiền về nhầm tài khoản
    pháp nhân. Vẫn không tự ghép — thu hộ giữa pháp nhân chưa hỗ trợ (0099); người quyết hoàn lại / chuyển nội bộ."""
    if not unmatched:
        return
    from barista.api import name_key

    names = await entities.names(ctx)
    owing = [t for t in receivables if t["paid_vnd"] < t["amount_vnd"]]
    codes = _Keys([[name_key(t["code"])] for t in owing])
    for r in unmatched:
        key = name_key(f"{r['memo']} {r['payer']}")
        hit = next((owing[i] for i, _ in codes.hits(key) if owing[i]["entity_id"] != r["entity_id"]), None)
        if hit is None:
            continue
        own, other = names.get(str(hit["entity_id"]), {}), names.get(str(r["entity_id"]), {})
        r["hint"] = (f"Mã {hit['code']} là khoản của pháp nhân {own.get('code', '?')} ({own.get('name', '')}) nhưng tiền về tài khoản pháp nhân "
                     f"{other.get('code', '?')} — có thể bên trả chuyển nhầm tài khoản. Không ghép tự động (thu hộ giữa pháp nhân chưa hỗ trợ): "
                     f"hỏi {hit['party'] or 'bên trả'} chuyển lại đúng tài khoản, hoặc chuyển nội bộ giữa hai pháp nhân rồi ghi thu bên {own.get('code', '?')}")


RECONCILE_VIEW = {"": "Tất cả", "full": "Khớp đủ — ghi được ngay", "diff": "Lệch số tiền", "review": "Cần người xem"}
_VIEW_MATCH = {"full": ("full",), "diff": ("short", "over"), "review": ("amount_only", "unmatched", "settled")}
# thứ tự việc của kế toán: ghi dòng khớp đủ → xử lý lệch → xem dòng chưa rõ (rà vai kế toán 18/11/2026: "trả thiếu" từng chìm giữa dòng lẻ)
_MATCH_ORDER = {"full": 0, "short": 1, "over": 2, "amount_only": 3, "unmatched": 4, "settled": 5}


class ReconcileIn(Input):
    view: str = field("Xem", "", options=RECONCILE_VIEW)


@m.query("reconcile", resource="acct", title="Đối soát sao kê", min_scope="all")
async def reconcile(ctx: Ctx, inp: ReconcileIn) -> dict[str, Any]:
    """Dòng sao kê chưa đối soát, mỗi dòng code đã khớp với khoản phải thu nào (theo mã trong nội dung chuyển khoản + số tiền): khớp đủ / trả thiếu /
    trả thừa / chỉ khớp theo số tiền / chưa khớp, kèm gợi ý nguyên nhân chênh. Ghi hàng loạt = acct.apply_statement (chỉ dòng khớp đủ);
    từng dòng = acct.apply_line."""
    rows = await _matched(ctx)
    total = lambda k: sum(r["amount_vnd"] for r in rows if r["match"] == k)  # noqa: E731
    full = [r for r in rows if r["match"] == "full"]
    for r in rows:
        r["state"] = r["match"]
    kpi_rows = rows   # ô số luôn tính trên mọi dòng chờ đối soát, không theo bộ lọc xem
    if inp.view in _VIEW_MATCH:
        rows = [r for r in rows if r["match"] in _VIEW_MATCH[inp.view]]
    rows.sort(key=lambda r: (_MATCH_ORDER.get(r["match"], 9), str(r.get("received_on") or "")))
    return ui.table(rows, [
        # cột quyết định (khớp đâu, chênh bao nhiêu, vì sao) đứng trước — màn hẹp vẫn thấy không phải cuộn ngang
        ui.col("state", "Kết quả", "badge", labels=MATCH, tones=MATCH_TONE), ui.col("amount_vnd", "Số tiền chưa ghi", "money"),
        ui.col("code", "Khoản phải thu"), ui.col("diff", "Chênh lệch", "money"), ui.col("hint", "Gợi ý", width=260),
        ui.col("memo", "Nội dung chuyển khoản", width=280), ui.col("received_on", "Ngày", "date"), ui.col("how", "Khớp theo"),
        ui.col("remaining", "Còn phải thu", "money"), ui.col("payer", "Bên chuyển"), ui.col("bank_ref", "Số tham chiếu NH"),
        ui.col("allocated_vnd", "Đã ghi trước đó", "money"),
    ], title="Đối soát sao kê", empty="Không còn dòng nào chờ đối soát — nạp sao kê (Excel) hoặc gửi ảnh bảng đối soát cho trợ lý",
        kpis=[ui.kpi("Khớp đủ", len(full), tone="green" if full else None, hint=_vnd(total("full")) if full else ""),
              ui.kpi("Lệch số tiền", sum(1 for r in kpi_rows if r["match"] in ("short", "over")), tone="orange"),
              ui.kpi("Cần người xem", sum(1 for r in kpi_rows if r["match"] in ("amount_only", "unmatched", "settled")), tone="red",
                     hint="Chưa khớp mã / chỉ khớp số tiền / khoản đã thu đủ (có thể trả trùng)"),
              ui.kpi("Tổng tiền chờ đối soát", sum(r["amount_vnd"] for r in kpi_rows), "money")],
        note="Khớp bằng mã trong nội dung chuyển khoản + số tiền (code tính, không phải AI đoán). Chênh lệch không tự xoá nợ: ghi phần đã nhận + "
             "giao việc cho kế toán.")


async def _apply(ctx: Ctx, r: dict[str, Any], ref: str = "", confirm_payer: bool = False) -> dict[str, Any]:
    """Một dòng sao kê → phân bổ vào khoản code đã khớp (hoặc khoản người chọn): số ghi = min(phần chưa phân bổ của dòng, số còn phải thu).
    Trả thiếu ⇒ khoản còn nợ + việc cho kế toán; trả thừa ⇒ phần thừa NẰM LẠI ở dòng (tiền về chưa phân bổ, dòng chưa xong) + việc."""
    no = (ref or r["code"]).strip().upper()
    if not no:
        raise Invalid("Dòng này chưa khớp khoản nào — chọn khoản phải thu")
    line = await ctx.one("SELECT * FROM acct.statement_lines WHERE id = %s AND status = 'open' FOR UPDATE", (r["id"],))
    if line is None:
        raise NotFound("Không thấy dòng sao kê đang chờ đối soát")
    t = await _target(ctx, no)
    if ref and no != r["code"]:   # người CHỌN khoản khác khoản code đã khớp ⇒ kiểm bên chuyển (0156)
        _payer_gate(line, t, confirm_payer)
    left, rest = line["amount_vnd"] + line["deducted_vnd"] - line["allocated_vnd"], _rest(t)
    if rest <= 0:
        raise Invalid(f"{no} đã thu đủ / đã huỷ — không ghi thêm")
    out = await _allocate(ctx, line, no, min(left, rest))
    who = t["party"] or "khách"
    diff = rest - left
    task = None
    if diff != 0:
        over = diff < 0
        title = f"{who} chuyển thừa {_vnd(-diff)} — {no}" if over else f"Chênh {_vnd(diff)} — {no} ({who} trả thiếu)"
        notes = (f"Dòng sao kê {line['received_on']:%d/%m/%Y}: {_vnd(left)}, nội dung “{line['memo']}”. "
                 + (f"Đã ghi đủ số còn phải thu; phần thừa {_vnd(-diff)} vẫn nằm ở dòng sao kê (tiền về chưa phân bổ) — phân bổ vào khoản khác "
                    f"hoặc hỏi lại {who}." if over
                    else f"Đã ghi phần đã nhận. Gợi ý: {_hint(rest, left, who)}. Hỏi {who} rồi xử lý phần còn lại — chưa xoá nợ."))
        task = (await ctx.call("flow.create_task", title=title[:300], notes=notes, due_on=(today()).isoformat()))["task_id"]
    return {"line_id": line["id"], "code": no, "payment_id": out["payment_id"], "amount_vnd": out["amount_vnd"], "diff": diff,
            "line_left": out["line_left"], "task_id": task}


class ApplyLine(Input):
    line_id: UUID = field("Dòng sao kê", hidden=True)
    code: str = field("Ghi vào", "", options_query="acct.collect_options",
                      placeholder="Khoản đã khớp", help="Dòng chưa khớp thì chọn khoản phải thu")
    confirm_payer: bool = field("Đúng bên trả", False, help="Chỉ khi tên bên chuyển trên sao kê khác bên trả của khoản đã chọn mà đúng là tiền của họ", group="Thêm chi tiết")


@m.command("apply_line", resource="acct", title="Ghi phiếu thu từ dòng sao kê")
async def apply_line(ctx: Ctx, inp: ApplyLine) -> dict[str, Any]:
    """Một dòng sao kê → phiếu thu (mặc định vào khoản code đã khớp; dòng chưa khớp / khớp theo số tiền thì người chọn). Trả thiếu ⇒ ghi phần
    đã nhận + việc "chênh lệch" cho kế toán; trả thừa ⇒ ghi đủ số còn phải thu + việc hỏi lại phần thừa."""
    rows = await _matched(ctx, [inp.line_id])
    if not rows:
        raise NotFound("Không thấy dòng sao kê đang chờ đối soát")
    res = await _apply(ctx, rows[0], inp.code, inp.confirm_payer)
    res["summary"] = f"Ghi {_vnd(res['amount_vnd'])} vào {res['code']}" + (
        " · đã giao việc xử lý chênh lệch" if res["task_id"] else "")
    return res


@m.command("apply_statement", resource="acct", title="Ghi phiếu thu các dòng khớp đủ")
async def apply_statement(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Ghi hàng loạt CHỈ các dòng khớp ĐỦ theo mã (khớp lại lúc bấm — không tin danh sách cũ). Mỗi dòng phân bổ vào khoản của nó ⇒ gói nguồn
    phản ứng (ghi nhận của gói, việc theo dõi tự đóng, hồ sơ chuyển chặng). Dòng lệch / chưa khớp để lại cho người xem từng dòng."""
    # khớp chỉ nhờ số hoá đơn (số ngắn, có thể trùng ngẫu nhiên) không ghi hàng loạt — kế toán xem từng dòng (acct.apply_line)
    done = [await _apply(ctx, r) for r in await _matched(ctx) if r["match"] == "full" and r["how"] != "số hoá đơn"]
    if not done:
        raise Invalid("Không có dòng nào khớp đủ để ghi")
    total = sum(d["amount_vnd"] for d in done)
    return {"applied": len(done), "total_vnd": total, "codes": [d["code"] for d in done],
            "summary": f"Ghi {len(done)} phiếu thu, tổng {_vnd(total)}"}


class IgnoreLine(Input):
    line_id: UUID = field("Dòng sao kê", hidden=True)
    note: str = field("Lý do", max_length=500, example="Tiền nội bộ chuyển giữa hai tài khoản công ty")


@m.command("ignore_line", resource="acct", title="Bỏ qua dòng sao kê")
async def ignore_line(ctx: Ctx, inp: IgnoreLine) -> dict[str, Any]:
    line = await ctx.one("SELECT allocated_vnd, amount_vnd, accepted_at FROM acct.statement_lines WHERE id = %s AND status = 'open' FOR UPDATE",
                         (inp.line_id,))
    if line is None:
        raise NotFound("Không thấy dòng sao kê đang chờ đối soát")
    if line["allocated_vnd"] or line["accepted_at"] is not None:   # 0156: đã vào sổ (kể cả khi phiếu thu đã đảo hết) ⇒ không bỏ qua được
        # tiền đã vào sổ ⇒ không "bỏ qua" được phần còn lại: phân bổ vào khoản khác; hoàn / ghi khác chờ khách chốt luật (G17)
        raise Invalid(f"Dòng đã ghi {_vnd(line['allocated_vnd'])} — phần còn lại {_vnd(line['amount_vnd'] - line['allocated_vnd'])} là tiền về "
                      "chưa phân bổ: phân bổ vào khoản khác (Ghi phiếu thu, chọn khoản)")
    await ctx.exec("""UPDATE acct.statement_lines SET status = 'ignored', note = %s, decided_by = core.me(), decided_at = now()
                      WHERE id = %s""", (inp.note, inp.line_id))
    return {"line_id": inp.line_id, "summary": "Đã bỏ qua dòng sao kê"}


# ── Khoản phải thu dùng chung — phần "sales-to-cash" mọi ngành dùng (quyết định 0057) ──
#
# Gói ngành (cho thuê; sau này học phí, đơn hàng) KHÔNG tự ghi tiền: lập khoản phải thu qua acct.issue_receivable (hợp đồng nội bộ hẹp) và nghe
# acct.payment_received. Tài chính lo phần chung: mã thanh toán, phiếu thu, ghi sổ theo BẢN CHẤT tiền, đối soát sao kê, chi trả tiền thu hộ qua
# duyệt, nhắc nợ quá hạn (việc theo dõi loại "collection" — trừ khoản gói nguồn tự theo dõi: ``followed``).

NATURE = {"revenue": "Doanh thu", "pass_through": "Thu hộ", "deposit": "Tiền cọc", "deferred": "Doanh thu nhận trước"}
NATURE_TONES = {"revenue": "green", "pass_through": "blue", "deposit": "purple", "deferred": "teal"}
AR_STATUS = {"open": "Chưa thu", "partial": "Thu một phần", "paid": "Đã thu", "void": "Đã huỷ"}
AR_TONES = {"open": "orange", "partial": "blue", "paid": "green", "void": "gray"}
REMIT_STATUS = {"requested": "Chờ duyệt", "pending_transfer": "Chờ chuyển khoản", "transferred": "Đã chuyển", "rejected": "Từ chối"}
REMIT_TONES = {"requested": "orange", "pending_transfer": "blue", "transferred": "green", "rejected": "gray"}
def _revenue_accounts() -> set[str]:
    """Tài khoản doanh thu gói ngành dùng được cho khoản phải thu chung = mọi tài khoản doanh thu đã khai (module gói tự khai)."""
    return {c for c, (_, kind) in ledger.ACCOUNTS.items() if kind == "revenue"}
CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"   # không 0/O, 1/I — khách đọc / gõ lại không nhầm


async def deferred_account(ctx: Ctx, entity_id: UUID) -> str:
    """Tài khoản doanh thu nhận trước của MỘT pháp nhân (``deferred:<mã pháp nhân>``, nợ phải trả — 0147). Tạo ở lần ghi đầu; mã pháp nhân đổi tên không
    phát sinh tài khoản mới vì bút toán đã ghi vẫn trỏ tài khoản cũ (mã pháp nhân không đổi sau khi tạo)."""
    row = await ctx.one("SELECT code, name FROM core.legal_entities WHERE id = %s", (entity_id,))
    if row is None:
        raise NotFound("Không thấy pháp nhân")
    code = f"deferred:{row['code'].lower()}"
    await ledger.ensure_account(ctx, code, f"Doanh thu nhận trước — {row['name']}", "liability")
    return code


async def debtor(ctx: Ctx, party_id: UUID | None, name: str = "", *, kind: str = "organization", tax_code: str = "") -> tuple[UUID | None, str]:
    """BÊN NỢ của khoản phải thu (0177) — MỘT nơi quyết cả mã lẫn tên hiện: có ``party_id`` ⇒ tên hiện lấy từ ``parties.names`` (không tin chữ gõ kèm);
    chỉ có TÊN ⇒ ``parties.ensure_counterparty`` (mã số thuế, rồi tên chuẩn hoá; chưa có ⇒ tạo); không có gì ⇒ (None, ""). Hai bên trùng tên nhưng
    khác mã (hai chủ nhà cùng tên) vẫn là hai dòng khác nhau vì mã đi trước tên."""
    if party_id is not None:
        got = (await ctx.call_system("parties.names", party_ids=[party_id])).get(str(party_id))
        if got is None:
            raise NotFound("Không thấy bên nợ trong danh mục cá nhân / tổ chức của công ty")
        return party_id, got["name"]
    if name.strip():
        got2 = await ctx.call_system("parties.ensure_counterparty", name=name, kind=kind, tax_code=tax_code)
        return UUID(str(got2["party_id"])), got2["name"]
    return None, ""


class IssueReceivable(Input):
    source: str
    source_ref: str
    label: str
    party_id: UUID | None = None                      # 0177: bên nợ = định danh trong parties (gói nguồn đã có mã) — nguồn sự thật, ưu tiên hơn ``party``
    party: str = ""                                   # chỉ TÊN (file nhập, gói ngoài lõi): acct tìm / tạo bên qua parties.ensure_counterparty rồi ghi tên hiện từ định danh
    party_kind: Literal["person", "organization"] = "organization"   # loại bên được TẠO khi chỉ có tên (cá nhân không khớp theo tên)
    party_tax_code: str = ""                          # mã số thuế của bên (nếu có) — khớp bên cũ theo mã trước tên
    nature: Literal["revenue", "pass_through", "deposit", "deferred"]
    amount_vnd: int
    fee_vnd: int = 0
    revenue_account: str = ""
    payee_key: str = ""
    payee_name: str = ""
    match_hints: tuple[str, ...] = ()
    match_keys: tuple[str, ...] = ()
    due_on: date
    issued_on: date | None = None
    owner_id: UUID | None = None
    team_id: UUID | None = None
    code: str = ""                                    # mã gói tự đặt (in trên giấy tờ, bên trả ghi vào nội dung CK); trống ⇒ cấp TT…
    receivable_account: str = "receivable:customers"  # tài khoản phải thu (gói khai, vd phải thu CĐT)
    followed: bool = False                            # gói nguồn tự mở việc theo dõi ⇒ vòng nhắc nợ chung không mở thêm
    entity_id: UUID | None = None                     # pháp nhân ghi doanh thu / nhận tiền (0099) — gói suy từ đối tượng (vd dự án); trống = mặc định
    category: str = ""                                # 0156: mục khoản thu của danh mục công ty (acct.categories) — trống = không phân loại
    when_locked: Literal["refuse", "task"] = "refuse"  # 0119: ngày ghi nhận thuộc kỳ đã khoá — lỗi rõ (có người bấm) / việc cho kế toán (sự kiện, vòng quét)


@m.command("issue_receivable", resource="acct", title="Lập khoản phải thu", internal=True, family="receivable",
           why="Nguồn phải mang tên module gọi (ctx.owned_by). Gói nguồn lập khoản phải thu của đối tượng mình: "
               "gói khai consumes(\"receivable\") (vd phí của một thương vụ khi bên nghe sự kiện ký chạy — không người bấm; cọc của khách ghép vào hàng đang "
               "giữ theo thoả thuận với đối tác — 0182, người có quyền ghi thoả thuận: nature=deposit, khoá cọc theo đối tượng, tiền khách về thì gói ghép vào "
               "tiền đặt tại đối tác; hợp đồng thuê khi ký: đối tượng khoá qua RLS người ký; phí dịch vụ tháng: vòng quét / "
               "quyền quản lý của gói; gói bán trả nhiều đợt (0147 lát A, người bán bấm bán: nature=deferred, một khoản mỗi đợt đóng, tham chiếu <mã gói>#NN, "
               "số tiền / hạn tính từ mẫu chia đợt của công ty, không từ ô người dùng gõ)). Ghi acct.receivables "
               "(+ bút toán doanh thu nếu nature=revenue; nature=deferred: Nợ phải thu / Có deferred:<pháp nhân>, một tài khoản mỗi pháp nhân); acct chỉ kiểm dạng nguồn / tài khoản, không kiểm nguồn thuộc module gọi. "
               "Trả {receivable_id, code, created}."
               " Bên nợ (0177): party_id của gói nguồn (người trả của gói bán, chủ nhà ký gửi), hoặc chỉ TÊN ⇒ acct tìm / tạo định danh qua parties.ensure_counterparty; "
               "cột party (chữ) chỉ là tên hiện, acct ghi từ định danh."
               " [id: module gọi tự có — owner_id lấy từ đối tượng của chính module gọi (thương vụ vừa ký; đối tượng của gói người gọi thấy qua RLS / vòng quét), không từ ô người dùng gõ; "
               "party_id là người trả / chủ nhà của đối tượng chính module gọi (người trả của gói bán, chủ nhà trên thoả thuận ký gửi), kiểm tồn tại trong công ty bằng parties.names]")
async def issue_receivable(ctx: Ctx, inp: IssueReceivable) -> dict[str, Any]:
    """Hợp đồng nội bộ cho gói ngành: lập một khoản phải thu + cấp MÃ THANH TOÁN (hoặc mã gói đặt). Chống lặp theo (nguồn, tham chiếu): gọi lại
    trả khoản cũ. Doanh thu ⇒ ghi sổ ngay (phải thu / doanh thu, tài khoản do gói chọn trong số đã khai); thu hộ / cọc ⇒ chưa ghi sổ."""
    fee, rev = {"revenue": (inp.amount_vnd, inp.revenue_account or "revenue:services"),
                "pass_through": (inp.fee_vnd, (inp.revenue_account or "revenue:services") if inp.fee_vnd else ""),
                "deposit": (0, ""), "deferred": (0, "")}[inp.nature]
    if not re.fullmatch(r"[a-z_]+\.[a-z_]+", inp.source) or not re.fullmatch(r"[a-z0-9_-]*", inp.payee_key):
        raise Invalid("Nguồn / người nhận không đúng dạng")
    ctx.owned_by(inp.source.split(".")[0], f"Nguồn {inp.source}")   # gói chỉ lập khoản phải thu cho nguồn của chính mình
    if inp.amount_vnd <= 0 or not 0 <= fee <= inp.amount_vnd:
        raise Invalid("Số tiền / phần phí không hợp lệ")
    if rev and rev not in _revenue_accounts():
        raise Invalid(f"Tài khoản doanh thu lạ: {rev}")
    if not inp.receivable_account.startswith("receivable:") or ledger.ACCOUNTS.get(inp.receivable_account, ("", ""))[1] != "asset":
        raise Invalid(f"Tài khoản phải thu lạ: {inp.receivable_account}")
    if ctx.caller not in (None, "", "acct") and not all(ledger.usable_by(c, ctx.caller or "") for c in (rev, inp.receivable_account) if c):   # 0188: tài khoản của chính gói gọi hoặc sơ đồ nền
        raise Denied(f"Tài khoản {rev or inp.receivable_account} do gói khác khai — module {ctx.caller} không được ghi vào qua họ receivable (0188)")
    want = inp.code.strip().upper()
    if want and not re.fullmatch(r"[0-9A-Z][0-9A-Z-]{3,40}", want):
        raise Invalid(f"Mã khoản phải thu không đúng dạng: {want}")
    if inp.nature in ("pass_through", "deposit") and not inp.payee_key:
        raise Invalid("Tiền thu hộ / tiền cọc cần khoá người nhận (người được trả / được hoàn)")
    if inp.nature == "deferred" and (inp.fee_vnd or inp.revenue_account or inp.payee_key or inp.category):
        raise Invalid("Khoản doanh thu nhận trước không mang phí / tài khoản doanh thu / người nhận / mục khoản thu — doanh thu ghi nhận khi dịch vụ được cung cấp")
    category = ""
    if inp.category:   # 0156: mục của danh mục công ty ⇒ tài khoản doanh thu theo mục (gói không tự chọn khác)
        from barista.modules.acct.corrections import category_of
        cat = await category_of(ctx, inp.category, "in")
        if inp.nature != "revenue" or (inp.revenue_account and inp.revenue_account != cat["account"]):
            raise Invalid(f"Mục {cat['label']} là doanh thu ghi vào {cat['account']}")
        category, rev = cat["code"], cat["account"]
    find = "SELECT id, code, category FROM acct.receivables WHERE source = %s AND source_ref = %s"
    if old := await ctx.one(find, (inp.source, inp.source_ref)):
        if old["category"] != category:
            raise Conflict(f"{old['code']} đã lập với mục khác — sửa bằng giấy báo có rồi lập lại, không đổi mục ngầm")
        return {"receivable_id": old["id"], "code": old["code"], "created": False}
    if inp.nature in ("revenue", "deferred"):   # 0119: kiểm kỳ TRƯỚC khi lập — không để khoản đã lập mà bút toán bị khoá chặn ở bước sau
        eid = inp.entity_id or await entities.default(ctx)
        if msg := await locked_message(ctx, eid, min(inp.issued_on or today(), today())):
            if inp.when_locked == "refuse":
                raise Invalid(msg)
            await locked_task(ctx, f"Chưa lập được: {inp.label}", f"{msg}. Khoản {inp.label} ({_vnd(inp.amount_vnd)}, nguồn {inp.source} "
                              f"{inp.source_ref}) chưa vào sổ. Mở lại kỳ (có duyệt) rồi chạy lại việc lập khoản này, hoặc ghi tay.",
                              key=f"{inp.source}:{inp.source_ref}")
            return {"receivable_id": None, "code": None, "created": False, "blocked": msg}
    party_id, party_name = await debtor(ctx, inp.party_id, inp.party, kind=inp.party_kind, tax_code=inp.party_tax_code)
    rid = code = None
    for _ in range(1 if want else 6):
        code = want or "TT" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        try:
            async with ctx.conn.transaction():
                rid = await ctx.val(
                    """INSERT INTO acct.receivables (code, source, source_ref, label, party, party_id, nature, amount_vnd, fee_vnd, revenue_account, payee_key,
                                                     payee_name, match_hints, match_keys, issued_on, due_on, owner_id, team_id, created_by,
                                                     receivable_account, followed_at, entity_id, category)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, coalesce(%s, core.today()), %s, %s, %s, core.me(), %s,
                               CASE WHEN %s THEN now() END, %s, %s) RETURNING id""",
                    (code, inp.source, inp.source_ref, inp.label, party_name, party_id, inp.nature, inp.amount_vnd, fee, rev,
                     inp.payee_key if inp.nature in ("pass_through", "deposit") else "", inp.payee_name, list(inp.match_hints), list(inp.match_keys), inp.issued_on,
                     inp.due_on, inp.owner_id, inp.team_id, inp.receivable_account, inp.followed or ctx.historical,   # nạp lịch sử (0185): không nhắc
                     inp.entity_id, category))
            break
        except pg.UniqueViolation:
            if old := await ctx.one(find, (inp.source, inp.source_ref)):   # song song cùng nguồn
                return {"receivable_id": old["id"], "code": old["code"], "created": False}
    if rid is None:
        raise Conflict(f"Mã {want} đã dùng cho khoản khác" if want else "Không cấp được mã thanh toán — thử lại")
    if inp.nature in ("revenue", "deferred"):
        key = f"acct.ar:{rid}"
        row = await ctx.one("SELECT entity_id, issued_on FROM acct.receivables WHERE id = %s", (rid,))
        assert row is not None
        credit = rev
        if inp.nature == "deferred":
            # 0147: GIẢ ĐỊNH CHỜ KẾ TOÁN — ghi nợ phải thu / có doanh thu nhận trước NGAY LÚC LẬP khoản (không đợi lúc thu) để thấy công nợ; một tài khoản
            # mỗi pháp nhân (không mỗi gói — số dư theo gói tính từ sổ phụ). Doanh thu chỉ ghi nhận ở lát B (theo buổi đã xác nhận).
            credit = await deferred_account(ctx, row["entity_id"])
        await ledger.post(ctx, key=key, memo=inp.label, source="acct.receivable", ref=code or "",
                          lines=[(inp.receivable_account, inp.amount_vnd), (credit, -inp.amount_vnd)],
                          # ngày phát sinh của khoản; gói nguồn ghi ngày tương lai (ký HĐMB đề ngày sau) ⇒ ghi nhận hôm nay, sổ không ghi trước kỳ tới
                          entity=row["entity_id"], on=min(row["issued_on"], today()))
        await ctx.exec("UPDATE acct.receivables SET recognized_key = %s WHERE id = %s", (key, rid))   # với deferred: khoá bút toán LẬP khoản (để đảo khi huỷ)
    return {"receivable_id": rid, "code": code, "created": True}


LINK_PARTY_NAMES = 200   # số tên bên nợ khác nhau chạy bù mỗi lượt quét / công ty (lượt sau làm tiếp) — không giữ giao dịch lâu


@m.command("link_receivable_parties", resource="acct", title="Gắn bên nợ (định danh) cho khoản phải thu cũ", internal=True, agent="deny",
           sweep="SELECT DISTINCT tenant_id FROM acct.receivables WHERE party_id IS NULL AND party <> ''")
async def link_receivable_parties(ctx: Ctx, inp: NoInput) -> dict[str, int]:
    """Chạy bù (0177): khoản phải thu cũ chỉ có TÊN bên nợ ⇒ khớp tên chuẩn hoá với tổ chức đã có trong danh mục, chưa có thì TẠO qua
    ``parties.ensure_counterparty`` (không ghi thẳng bảng parties); ghi ``party_id`` + tên hiện của định danh (tên viết khác hoa / dấu cùng một bên).
    Trả số khoản: ``matched`` (gắn vào bên đã có), ``created`` (gắn vào bên vừa tạo), ``parties_created``, ``unmatched`` (không gắn được — còn lại
    chờ lượt sau / xem log), ``left`` (còn chưa gắn sau lượt này). Chạy lại an toàn: chỉ đụng dòng ``party_id`` trống."""
    names = await ctx.all("""SELECT party, count(*)::int AS n FROM acct.receivables WHERE party_id IS NULL AND party <> ''
                             GROUP BY party ORDER BY party LIMIT %s""", (LINK_PARTY_NAMES,))
    matched = created = made = unmatched = 0
    for r in names:
        try:
            async with ctx.conn.transaction():
                got = await ctx.call_system("parties.ensure_counterparty", name=r["party"], kind="organization")
                n = await ctx.exec("UPDATE acct.receivables SET party_id = %s, party = %s WHERE party_id IS NULL AND party = %s",
                                   (got["party_id"], got["name"], r["party"]))
        except Exception:   # một tên hỏng không chặn các tên khác; dòng còn trống, lượt sau thử lại
            log.exception("chạy bù bên nợ cho “%s” lỗi", r["party"])
            unmatched += r["n"]
            continue
        if got["created"]:
            made += 1
            created += n
        else:
            matched += n
    left = await ctx.val("SELECT count(*) FROM acct.receivables WHERE party_id IS NULL AND party <> ''")
    return {"names": len(names), "matched": matched, "created": created, "parties_created": made, "unmatched": unmatched, "left": int(left or 0)}


async def void_books(ctx: Ctx, r: dict[str, Any], on: date | None = None) -> None:
    """Bút toán huỷ một khoản phải thu CHƯA còn tiền thu (``r`` cần id, code, recognized_key, amount_vnd, receivable_account, revenue_account,
    credited_vnd, entity_id). Huỷ ghi vào hôm nay — kỳ gốc có thể đã khoá; ``on``: ngày huỷ thật bên gọi nêu (nạp lịch sử, api 1.56)."""
    on = on or today()
    if r["credited_vnd"] and r["recognized_key"]:
        # 0156: đã có giấy báo có ⇒ đảo nguyên bút toán ghi nhận sẽ giảm doanh thu hai lần; chỉ huỷ phần CÒN LẠI của khoản
        await ledger.post(ctx, key=f"acct.ar_void:{r['id']}", memo=f"Huỷ khoản phải thu {r['code']}", source="acct.receivable_void", ref=r["code"],
                          on=on, entity=r["entity_id"], lines=[(r["revenue_account"], r["amount_vnd"]), (r["receivable_account"], -r["amount_vnd"])])
    elif r["recognized_key"].startswith("acct.opening_batch:"):
        # 0119: khoản ĐẦU KỲ — đảo về equity:opening hôm nay sẽ dời số đầu kỳ sang sau D. Ghi điều chỉnh giảm vào kỳ hiện tại (doanh thu
        # điều chỉnh), số đầu kỳ tại D giữ nguyên; sửa chính số đầu kỳ = mở kỳ + đảo lô.
        await ledger.post(ctx, key=f"acct.ar_void:{r['id']}", memo=f"Huỷ khoản phải thu đầu kỳ {r['code']}", source="acct.receivable_void",
                          ref=r["code"], on=on, entity=r["entity_id"],
                          lines=[("revenue:opening_adjustment", r["amount_vnd"]), (r["receivable_account"], -r["amount_vnd"])])
    elif r["recognized_key"]:
        await ledger.reverse(ctx, original_key=r["recognized_key"], key=f"acct.ar_void:{r['id']}", memo=f"Huỷ khoản phải thu {r['code']}",
                             source="acct.receivable_void", on=on)


class VoidReceivables(Input):
    source: str
    ref_prefix: str
    due_after: date
    on: date | None = None   # ngày huỷ (api 1.56 — nạp lịch sử: ngày huỷ thật của đối tượng nguồn); trống = hôm nay; không sau hôm nay


@m.command("void_receivables", resource="acct", title="Huỷ khoản phải thu chưa tới kỳ", internal=True, family="receivable",
           why="Nguồn phải mang tên module gọi (ctx.owned_by). Gói khai consumes(\"receivable\") khi bên nghe sự kiện huỷ đối tượng nguồn chạy (không người bấm), khi đối tượng "
               "nguồn chấm dứt sớm (quyền quản lý của gói, đối tượng khoá qua RLS) hoặc sau phiếu duyệt huỷ một gói bán CHƯA thu đồng nào (0147 lát A — bên gọi "
               "tự kiểm; tiền tố <mã gói>#). "
               "Huỷ khoản CHƯA THU đồng nào của đúng nguồn + tiền tố tham chiếu, hạn sau due_after (đảo bút toán doanh thu). "
               "Trả {voided, kept}: chỉ MÃ khoản.")
async def void_receivables(ctx: Ctx, inp: VoidReceivables) -> dict[str, Any]:
    """Nguồn chấm dứt sớm (trả nhà trước hạn…) ⇒ huỷ các khoản CHƯA THU đồng nào có hạn sau ``due_after``. Khoản đã thu một phần giữ lại cho kế
    toán xử lý tay (trả về trong ``kept``) — không tự xoá tiền đã nhận."""
    ctx.owned_by(inp.source.split(".")[0], f"Nguồn {inp.source}")   # gói chỉ huỷ khoản của nguồn mình
    if inp.on is not None and inp.on > today():
        raise Invalid("Ngày huỷ sau hôm nay")
    rows = await ctx.all("""SELECT id, code, nature, paid_vnd, recognized_key, amount_vnd, receivable_account, revenue_account, credited_vnd, entity_id
                            FROM acct.receivables
                            WHERE source = %s AND starts_with(source_ref, %s)
                            AND due_on > %s AND status IN ('open', 'partial') FOR UPDATE""", (inp.source, inp.ref_prefix, inp.due_after))
    voided, kept = [], []
    from barista.modules.acct.vat import has_active_invoice
    for r in rows:
        if r["paid_vnd"] > 0 or await has_active_invoice(ctx, r["id"]):   # 0165: đã có hoá đơn GTGT ⇒ kế toán huỷ / điều chỉnh hoá đơn, không huỷ ngầm
            kept.append(r["code"])
            continue
        await ctx.exec("UPDATE acct.receivables SET status = 'void' WHERE id = %s", (r["id"],))
        await void_books(ctx, r, inp.on)
        voided.append(r["code"])
    return {"voided": voided, "kept": kept}


class Payment(Input):
    code: str = field("Khoản phải thu", options_query="acct.receivable_options", example="TT4K7Q2M", aliases=("Mã thanh toán", "Mã khoản phải thu"))
    amount_vnd: Money = field("Số tiền thu", ge=1, example="15tr")
    received_on: Day | None = field("Ngày nhận", None, example="05/11/2026", placeholder="Hôm nay")
    bank_ref: str = field("Mã giao dịch ngân hàng", "", max_length=100, help="Có mã ⇒ nhập lại sao kê không bị ghi hai lần", group="Thêm chi tiết")
    confirm_payer: bool = field("Đúng bên trả", False, help="Chỉ khi dòng tiền đã nạp (theo mã giao dịch) ghi bên chuyển khác bên trả của khoản mà đúng là tiền của họ", group="Thêm chi tiết")
    keep_excess: bool = field("Giữ phần thừa chưa phân bổ", False, aliases=("Thu thừa", "Phần thừa"),
                              help="Bên trả chuyển nhiều hơn số còn phải thu: ghi đủ số còn phải thu, phần thừa nằm ở tiền về chưa phân bổ. "
                                   "Không chọn ⇒ số vượt bị từ chối (chặn gõ nhầm)", group="Thêm chi tiết")


@m.command("record_payment", resource="acct", title="Ghi tiền khách trả", importable=True)
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
    out = await _allocate(ctx, line, t["code"], take) if take > 0 else None
    excess = inp.amount_vnd - take
    if excess:
        await _accept(ctx, line)   # tiền đã vào tài khoản: cả dòng vào sổ, phần thừa nằm ở "tiền về chưa phân bổ"
    return {"payment_id": out["payment_id"] if out else None, "status": out["status"] if out else t["status"], "fee_vnd": out["fee_vnd"] if out else 0,
            "applied_vnd": take, "unapplied_vnd": excess, "line_id": line["id"],
            "summary": (f"Ghi {_vnd(take)} vào {t['code']}" if take else f"{t['code']} đã thu đủ")
                       + (f" · thừa {_vnd(excess)} nằm ở tiền về chưa phân bổ (Đối soát sao kê)" if excess else "")}


class ARFilter(Input):
    status: str = field("Trạng thái", "open", options={"open": "Chưa thu xong", "late": "Quá hạn", "": "Tất cả", **AR_STATUS})
    q: str = field("Tìm", "", max_length=80, example="HSR",
                   help="Lọc theo chữ trong mã khoản, nội dung, tên bên trả hoặc mã chứng từ gốc (ví dụ mã giao dịch / mã dự án trong mã đó)")


@m.query("receivables", resource="acct", title="Khoản phải thu")
async def receivables(ctx: Ctx, inp: ARFilter) -> dict[str, Any]:
    """Khoản phải thu chung của mọi gói ngành (tiền nhà, cọc…) — kèm MÃ THANH TOÁN khách ghi vào nội dung chuyển khoản. Người phạm vi hẹp thấy
    khoản mình phụ trách. Cột "bản chất" cho biết tiền này là doanh thu, thu hộ phải trả lại, hay tiền cọc. Ô "Tìm" khớp như ⌘K (từng từ, có dấu hay không)."""
    find = SearchTerms(inp.q)
    rows = await ctx.all(
        """SELECT r.id, r.code, r.label, r.party, r.nature, r.amount_vnd, r.paid_vnd, r.amount_vnd - r.paid_vnd AS remaining_vnd, r.due_on,
                  r.status, r.payee_name, e.code AS entity, r.invoice_no, r.category, r.credited_vnd,
                  (r.status IN ('open', 'partial') AND r.due_on < core.today()) AS overdue, core.today() - r.due_on AS late_days
           FROM acct.receivables r JOIN core.legal_entities e ON e.id = r.entity_id
           WHERE (%(s)s = '' OR (%(s)s IN ('open', 'late') AND r.status IN ('open', 'partial')) OR r.status = %(s)s)
             AND (%(s)s <> 'late' OR r.due_on < core.today())
             AND (%(q)s = '' OR """ + find.match(*AR_FIND) + """)
           ORDER BY r.due_on, r.code LIMIT 2000""", {**find.params, "s": inp.status, "q": find.q})
    many = await entities.many(ctx)
    from barista.api import company_settings
    from barista.modules.acct.vat import VAT_ON
    vat_on = await company_settings.enabled(ctx.conn, ctx.p.tenant_id, VAT_ON)
    invoiced = {r["receivable_id"] for r in await ctx.all(
        "SELECT receivable_id FROM acct.vat_invoices WHERE kind = 'invoice' AND cancelled_on IS NULL AND receivable_id = ANY(%s)",
        ([r["id"] for r in rows],))} if vat_on and rows else set()
    for r in rows:
        r["late"] = f"Quá hạn {r['late_days']} ngày" if r.pop("overdue") else ""
        # 0165: chỉ THÊM trường — nút "Ghi hoá đơn GTGT" hiện khi công ty bật thuế và khoản chưa có hoá đơn còn hiệu lực
        r["vat"] = "" if not vat_on or r["status"] == "void" else "issued" if r["id"] in invoiced else "ready"
    open_rows = [r for r in rows if r["status"] in ("open", "partial")]
    late = [r for r in open_rows if r["late"]]
    return ui.table(rows, [
        ui.col("late", "", "badge", tones={r["late"]: "red" for r in late}), ui.col("code", "Mã thanh toán"), ui.col("label", "Nội dung", width=240),
        ui.col("party", "Bên trả"), ui.col("nature", "Bản chất", "badge", labels=NATURE, tones=NATURE_TONES),
        ui.col("remaining_vnd", "Còn lại", "money"), ui.col("amount_vnd", "Phải thu", "money"), ui.col("paid_vnd", "Đã thu", "money"),
        ui.col("due_on", "Hạn", "date"), ui.col("status", "Trạng thái", "badge", labels=AR_STATUS, tones=AR_TONES),
        ui.col("invoice_no", "Số hoá đơn"), ui.col("category", "Mục khoản thu"), ui.col("credited_vnd", "Đã giảm (giấy báo có)", "money"),
        ui.col("payee_name", "Thu hộ cho"), *([ui.col("entity", "Pháp nhân")] if many else []),
        *([ui.col("vat", "Hoá đơn GTGT", "badge", labels={"ready": "Chưa ghi", "issued": "Đã ghi"}, tones={"ready": "orange", "issued": "green"})] if vat_on else []),
    ], title="Khoản phải thu", limit=2000, kpis=[
        ui.kpi("Còn phải thu", sum(r["remaining_vnd"] for r in open_rows), "money", hint="Mọi khoản phải thu (mọi nguồn, mọi bản chất), trừ khoản đã huỷ",
               drill=ui.drill("acct.receivables", {"status": "open", **({"q": inp.q.strip()} if inp.q.strip() else {})}, sum="remaining_vnd")),
        ui.kpi("Quá hạn", sum(r["remaining_vnd"] for r in late), "money", tone="red" if late else None,
               drill=ui.drill("acct.receivables", {"status": "late", **({"q": inp.q.strip()} if inp.q.strip() else {})}, sum="remaining_vnd"),
               hint=f"{len(late)} khoản" if late else ""),
        ui.kpi("Trong đó thu hộ", sum(r["remaining_vnd"] for r in open_rows if r["nature"] == "pass_through"), "money",
               hint="Thu về là nợ phải trả người nhận, không phải doanh thu"),
    ], empty="Chưa có khoản phải thu — khoản được lập tự động khi ký hợp đồng / lập đợt thu, hoặc ghi tay",
        note="Khách ghi MÃ THANH TOÁN vào nội dung chuyển khoản ⇒ đối soát sao kê tự khớp.",
        # chỉ XEM: trạng thái đổi theo phiếu thu (tiền), không kéo thẻ được
        views=ui.views(board=ui.board_view("status", "label", "party"), calendar=ui.calendar_view("due_on", "label", "party")))


# trường ô "Tìm" của Khoản phải thu và nguồn ⌘K cùng khớp (một chỗ khai)
AR_FIND: tuple[LiteralString, ...] = ("r.code", "r.label", "r.party", "r.source_ref", "r.invoice_no")


@m.search("search_receivables", "Khoản phải thu", resource="acct", rank=35, more=("acct.receivables", {"status": ""}))
async def search_receivables(ctx: Ctx, inp: SearchIn) -> list[dict[str, Any]]:
    """⌘K (0087): mã thanh toán (khách ghi trong nội dung chuyển khoản), số hoá đơn, mã chứng từ gốc, bên trả, nội dung — trong phạm vi người tìm
    xem được (RLS: người phạm vi hẹp thấy khoản mình phụ trách). Đúng mã lên trước, rồi khoản chưa thu xong. Mở Khoản phải thu đã lọc theo mã."""
    t = SearchTerms(inp.q)
    rows = await ctx.all(
        f"""SELECT r.code, r.label, r.party, r.status, r.amount_vnd - r.paid_vnd AS remaining_vnd, r.due_on FROM acct.receivables r
            WHERE {t.match(*AR_FIND)}
            ORDER BY {t.rank("r.code", "r.invoice_no", "r.source_ref")}, (r.status IN ('open', 'partial')) DESC, r.due_on DESC LIMIT %(_n)s""", t.params)
    return [{"title": f"{r['code']} — {r['label']}",
             "subtitle": " · ".join(x for x in (r["party"], f"còn {dong(r['remaining_vnd'])}" if r["status"] in ("open", "partial") else "",
                                                f"hạn {r['due_on']:%d/%m/%Y}" if r["due_on"] else "") if x),
             "badge": AR_STATUS.get(r["status"], r["status"]), "tone": "accent" if r["status"] in ("open", "partial") else "muted",
             "open": {"query": "acct.receivables", "input": {"status": "", "q": r["code"]}}} for r in rows]


@m.query("receivable_options", resource="acct", title="Khoản phải thu chưa thu xong")
async def receivable_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return ui.options(await ctx.all(
        "SELECT code AS value, code || ' — ' || label || ' — còn ' || to_char(amount_vnd - paid_vnd, 'FM999G999G999G999') AS label"
        " FROM acct.receivables WHERE status IN ('open', 'partial') ORDER BY due_on"))


@m.query("receivable_any_options", resource="acct", title="Khoản phải thu gần đây (mọi trạng thái)")
async def receivable_any_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    """Cho ô chọn khoản phải thu ở thao tác SỬA / HUỶ / ĐẢO (hoá đơn, giấy báo có, đảo thu) — khoản đã thu xong vẫn phải chọn được (0225: không bắt gõ mã)."""
    return ui.options(await ctx.all(
        "SELECT code AS value, code || ' — ' || label || ' — ' || CASE status WHEN 'paid' THEN 'đã thu' WHEN 'partial' THEN 'thu một phần' ELSE status END AS label"
        " FROM acct.receivables ORDER BY created_at DESC LIMIT 300"))


@m.query("collect_options", resource="acct", title="Khoản đang chờ thu")
async def collect_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    """Cho ô "Ghi vào" của đối soát: mọi khoản phải thu chưa thu xong."""
    return ui.options(await ctx.all(
        """SELECT code AS value, code || ' — ' || label || ' — còn ' || to_char(amount_vnd - paid_vnd, 'FM999G999G999G999') AS label
           FROM acct.receivables WHERE status IN ('open', 'partial') ORDER BY due_on"""))


class Code(Input):
    code: str = field("Khoản phải thu", options_query="acct.receivable_options", aliases=("Mã thanh toán",))


@m.query("receivable_detail", resource="acct", title="Một khoản phải thu", agent="deny")
async def receivable_detail(ctx: Ctx, inp: Code) -> dict[str, Any]:
    """Nguồn cho việc theo dõi "khách trả tiền" (đọc bằng quyền người phụ trách) — dữ liệu thô, không phải trang."""
    r = await ctx.one("SELECT * FROM acct.receivables WHERE code = %s", (inp.code.strip().upper(),))
    if r is None:
        raise NotFound(f"Không có khoản phải thu mã {inp.code}")
    return r


class ForSource(Input):
    source_prefix: str
    ref_prefixes: list[str]


@m.query("receivables_for", resource="acct", title="Khoản phải thu theo đối tượng nguồn", internal=True, agent="deny",
         family="receivable",
         why="Gói khai consumes(\"receivable\") hỏi đối tượng người xem đã thấy (RLS của gói) hoặc, ở báo cáo min_scope=\"all\", các kỳ đã lập phí của nguồn mình. Vd màn "
             "Còn nợ của một gói bán trả nhiều đợt (0147 lát A): tiền tố <mã gói>#NN của các gói người xem THẤY qua RLS của gói nguồn (của mình / cơ sở mình / cả "
             "công ty) — số tiền, hạn, đã thu của đúng các đợt đó, không bên khác; thẻ đối tượng và báo cáo min_scope=\"all\" của gói nguồn — 0164: cùng cách, "
             "chỉ mã người xem thấy qua RLS của gói khi công ty bật công tắc; trước khi huỷ gói bán: đợt đã thu của gói sắp huỷ. Gói nguồn cũng "
             "hỏi MỌI khoản phí của nguồn mình trong công ty (tham chiếu \"\") — màn / ô chọn khoản phí (quyền Kế toán), đối chiếu phí (chỉ kiểm acct read), bộ "
             "kiểm hồ sơ, sức khoẻ, dòng tiền, chặn tắt gói. Trả từng khoản: id, code, source, source_ref, label, party, nature, số tiền "
             "(amount / fee / paid), issued_on, due_on, status, created_at, last_paid_at, invoice_no / invoice_on (số hoá đơn đã xuất — 0151), category (mục khoản thu — api 1.54).")
async def receivables_for(ctx: Ctx, inp: ForSource) -> list[dict[str, Any]]:
    """Cho gói nguồn hiện lịch thu trên hồ sơ của mình (hợp đồng thuê…). Gói nguồn chỉ hỏi đối tượng NGƯỜI XEM đã thấy (RLS bên nguồn) — số
    tiền ở đây là của chính đối tượng đó."""
    ctx.owned_by(inp.source_prefix.split(".")[0], f"Nguồn {inp.source_prefix}")   # gói chỉ đọc khoản phải thu của nguồn CỦA MÌNH ("" / tiền tố chung ⇒ Denied)
    if not inp.ref_prefixes:
        return []
    return await ctx.all(
        """SELECT r.id, r.code, r.source, r.source_ref, r.label, r.party, r.nature, r.amount_vnd, r.fee_vnd, r.paid_vnd, r.issued_on, r.due_on,
                  r.status, r.created_at, r.invoice_no, r.invoice_on, r.category, (SELECT max(p.created_at) FROM acct.payments p WHERE p.receivable_id = r.id) AS last_paid_at
           FROM acct.receivables r WHERE starts_with(r.source, %s) AND r.source_ref LIKE ANY(%s) ORDER BY r.due_on, r.source_ref""",
        (inp.source_prefix, [r.replace("%", "").replace("_", r"\_") + "%" for r in inp.ref_prefixes]))


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


# ── BẤT BIẾN TIỀN trên dữ liệu thật (ROADMAP A8 — cùng bộ kiểm tests/test_money_stress.py): khoản phải thu = tổng phiếu thu, trạng thái khớp
# số; dòng sao kê phân bổ = tổng phiếu thu từ dòng, không vượt số tiền. Lệch = lỗi phần mềm (không phải sai dữ liệu người nhập) ⇒ Sức khoẻ
# dữ liệu hiện ra + vòng quét báo như lỗi hệ thống (nhật ký, quản trị, nhà vận hành). Sổ cái cân do DB chặn (ràng buộc trì hoãn), không cần ở đây. ──

# 0156: phiếu thu đã đảo (acct.payment_reversals) trừ khỏi số đã thu / đã phân bổ; giấy báo có cộng đúng bằng credited_vnd.
# THEO TẬP (0185): mỗi bảng con gộp MỘT lần (GROUP BY) rồi nối — không truy vấn con theo từng dòng (bản cũ quét cả bảng đảo phiếu / giấy báo có cho MỖI khoản phải
# thu: 4,6 giây ở 3.500 khoản, chạy mỗi phút trong câu chọn công ty của vòng quét). Cùng ngữ nghĩa từng điều kiện; trả các cột của ``r`` / ``l`` bên gọi cần.
_NET_PAID = """SELECT p.{key} AS id, sum(p.amount_vnd) - coalesce(sum(x.reversed_vnd), 0) AS net_vnd
               FROM acct.payments p LEFT JOIN (SELECT payment_id, sum(amount_vnd) AS reversed_vnd FROM acct.payment_reversals GROUP BY payment_id) x
                 ON x.payment_id = p.id
               WHERE p.{key} IS NOT NULL GROUP BY p.{key}"""
_AR_DRIFT = ("""SELECT {cols} FROM acct.receivables r
                LEFT JOIN (""" + _NET_PAID.format(key="receivable_id") + """) pay ON pay.id = r.id
                LEFT JOIN (SELECT receivable_id AS id, sum(amount_vnd) AS credited_vnd FROM acct.receivable_credits GROUP BY receivable_id) cr ON cr.id = r.id
                WHERE r.paid_vnd <> coalesce(pay.net_vnd, 0) OR r.credited_vnd <> coalesce(cr.credited_vnd, 0)
                   OR r.paid_vnd > r.amount_vnd OR (r.status = 'paid' AND r.paid_vnd <> r.amount_vnd)
                   OR (r.status = 'partial' AND NOT r.paid_vnd BETWEEN 1 AND r.amount_vnd - 1) OR (r.status = 'open' AND r.paid_vnd <> 0)""")
_LINE_DRIFT = ("""SELECT {cols} FROM acct.statement_lines l
                  LEFT JOIN (""" + _NET_PAID.format(key="line_id") + """) pay ON pay.id = l.id
                  LEFT JOIN (SELECT line_id AS id, sum(amount_vnd) AS v FROM acct.recovery_receipts GROUP BY line_id) rec ON rec.id = l.id
                  LEFT JOIN (SELECT line_id AS id, sum(amount_vnd) AS v FROM acct.placement_receipts GROUP BY line_id) plc ON plc.id = l.id   -- 0182: tiền đặt hoàn về
                  LEFT JOIN (SELECT line_id AS id, sum(amount_vnd) AS v FROM acct.line_deductions GROUP BY line_id) ded ON ded.id = l.id   -- 0217: khấu trừ của bên trả
                  WHERE l.allocated_vnd > l.amount_vnd + l.deducted_vnd OR l.deducted_vnd <> coalesce(ded.v, 0)
                     OR l.allocated_vnd <> coalesce(pay.net_vnd, 0) + coalesce(rec.v, 0) + coalesce(plc.v, 0)""")


async def _drift(ctx: Ctx) -> tuple[list[str], list[str]]:
    return ([r["code"] for r in await ctx.all(_AR_DRIFT.format(cols="r.code") + " ORDER BY r.code LIMIT 20")],
            [r["ref"] for r in await ctx.all(_LINE_DRIFT.format(cols="coalesce(nullif(l.bank_ref, ''), '(không mã)') AS ref, l.received_on")
                                             + " ORDER BY l.received_on LIMIT 20")])


@m.query("health_report", resource="acct", title="Sức khoẻ dữ liệu kế toán", internal=True, agent="deny", callers=("health",),
         why="health._collect (Sức khoẻ dữ liệu — health.overview, min_scope all): bất biến tiền của cả công ty. Trả {rank, issues, notes, "
             "fresh} — số khoản / dòng lệch + tối đa vài mã để tra; không số tiền, không bên trả.")
async def health_report(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    ar, lines = await _drift(ctx)
    return {"rank": 5, "fresh": [], "notes": [], "issues": [
        {"key": "money_drift", "n": len(ar) + len(lines), "title": "khoản phải thu / dòng sao kê lệch với phiếu thu (lỗi phần mềm)",
         "subtitle": ", ".join([*ar, *lines][:6]), "open": "acct.receivables",
         "why": "{n} chỗ số đã thu không khớp tổng phiếu thu ⇒ báo ngay cho bên vận hành phần mềm; đừng sửa tay số dư"}]}


_drift_alerted: dict[str, date] = {}   # công ty → ngày đã báo (mỗi tiến trình) — vòng quét chạy liên tục, báo tối đa một lần / ngày


@m.command("check_money", resource=None, title="Kiểm bất biến tiền", internal=True, agent="deny",
           sweep=_AR_DRIFT.format(cols="r.tenant_id") + " UNION " + _LINE_DRIFT.format(cols="l.tenant_id"))
async def check_money(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Vòng quét: công ty có số tiền lệch ⇒ LỖI HỆ THỐNG (ghi nhật ký kèm mã lệch, quản trị nhận thông báo, nhà vận hành nhận tin) — mỗi công
    ty một lần / ngày; ngày sau còn lệch thì báo lại. Hết lệch ⇒ vòng quét không chọn công ty này nữa."""
    ar, lines = await _drift(ctx)
    key = str(ctx.p.tenant_id)
    if (ar or lines) and _drift_alerted.get(key) != today():
        _drift_alerted[key] = today()
        raise RuntimeError(f"Lệch tiền: {len(ar)} khoản phải thu ({', '.join(ar[:5])}), {len(lines)} dòng sao kê ({', '.join(lines[:5])})")
    return {"receivables": len(ar), "lines": len(lines)}


@m.command("follow_overdue", resource="acct", title="Mở việc nhắc khoản phải thu quá hạn", internal=True,
           sweep="""SELECT DISTINCT tenant_id FROM acct.receivables WHERE status IN ('open', 'partial') AND followed_at IS NULL
                    AND due_on < core.today_of(tenant_id)""")
async def follow_overdue(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Vòng quét: khoản phải thu chung QUÁ HẠN chưa thu đủ, chưa mở việc ⇒ đánh dấu + phát ``acct.receivable_overdue`` (Việc theo dõi mở việc
    "khách trả tiền" cho trợ lý người phụ trách). Mỗi khoản một lần — khoản trả đúng hạn không sinh việc."""
    rows = await ctx.all("""UPDATE acct.receivables SET followed_at = now()
                            WHERE status IN ('open', 'partial') AND followed_at IS NULL AND due_on < core.today()
                            RETURNING id, code, label, party, owner_id, amount_vnd - paid_vnd AS rest, due_on""")
    for r in rows:
        ctx.emit("acct.receivable_overdue", code=r["code"], label=r["label"], party=r["party"], owner_id=r["owner_id"], rest=r["rest"],
                 due_on=r["due_on"])
    return {"opened": len(rows)}


@m.pack_blockers()
async def pack_blockers(ctx: Ctx, inp: NoInput) -> list[str]:
    """Không tắt Tài chính khi còn: dòng tiền về chưa đối soát / chưa phân bổ hết, khoản phải thu chưa thu xong, lệnh chi trả đang chờ."""
    from barista.modules.acct.settlements import _unsettled
    out = []
    if n := await ctx.val("SELECT count(*) FROM acct.payouts WHERE status IN ('pending_transfer','unknown')"):
        out.append(f"{n} lệnh chi chưa hoàn tất / đang tra soát")
    if n := await _unsettled(ctx):
        out.append(f"{n} nghĩa vụ / khoản ứng / thu hồi chưa tất toán")
    r = await ctx.one("SELECT count(*) AS n, coalesce(sum(amount_vnd + deducted_vnd - allocated_vnd), 0) AS v FROM acct.statement_lines WHERE status = 'open'")
    if r and r["n"]:
        out.append(f"{r['n']} dòng tiền về chưa đối soát / chưa phân bổ hết ({_vnd(int(r['v']))})")
    r = await ctx.one("SELECT count(*) AS n, coalesce(sum(amount_vnd - paid_vnd), 0) AS v FROM acct.receivables WHERE status IN ('open', 'partial')")
    if r and r["n"]:
        out.append(f"{r['n']} khoản phải thu chưa thu xong ({_vnd(int(r['v']))})")
    if n := await ctx.val("SELECT count(*) FROM acct.remittances WHERE status IN ('requested', 'pending_transfer')"):
        out.append(f"{n} lệnh chi trả đang chờ duyệt / chuyển khoản")
    return out


# ── Việc theo dõi "khách trả tiền" (0021 / 0076): loại CHUNG cho mọi khoản phải thu của Tài chính (0057), không cần gói ngành ──

@m.work_kind("collection", "Khách trả tiền", subject_help="Mã thanh toán của khoản phải thu (TT…)", tools=("kb.search",))
async def work_collection(ctx: Ctx, inp: WorkIn) -> dict[str, Any]:
    """Bằng quyền người phụ trách: khoản phải thu — xong khi thu đủ (code), huỷ khi khoản huỷ."""
    ar = await ctx.call("acct.receivable_detail", code=inp.subject)   # không thấy ⇒ NotFound ⇒ việc dừng vì quyền
    due: date = ar["due_on"]
    rest = ar["amount_vnd"] - ar["paid_vnd"]
    late = max(0, (today() - due).days)
    party = ar["party"] or "khách"
    f = {"code": ar["code"], "label": ar["label"], "party": party, "amount": ar["amount_vnd"], "paid": ar["paid_vnd"], "rest": rest,
         "due": due.isoformat(), "late_days": late}
    state = "cancelled" if ar["status"] == "void" else "done" if rest <= 0 or ar["status"] == "paid" else "open"
    return {
        "ref": ar["code"], "title": f"{ar['party'] or 'Khách'} trả {ar['label']}", "state": state, "facts": f,
        "summary": f"{party} còn phải trả {dong(rest)} ({ar['label']}, mã {ar['code']}), hạn {due:%d/%m/%Y}" + (f" — quá hạn {late} ngày." if late else "."),
        "closing": f"Đã thu đủ {dong(ar['amount_vnd'])} — đóng việc." if state == "done" else "Khoản phải thu đã huỷ — dừng theo dõi.",
        "task": (f"{party} chưa trả đủ khoản {ar['label']} (mã thanh toán {ar['code']}): còn {dong(rest)} / {dong(ar['amount_vnd'])}, hạn "
                 f"{f['due']}, đã quá hạn {late} ngày.\nHãy tìm trong kho tài liệu quy trình / mẫu tin nhắc trả tiền, rồi viết 2–3 câu báo người "
                 f"phụ trách: tình hình và nên nhắc thế nào (nhắc khách ghi đúng mã {ar['code']} vào nội dung chuyển khoản). Không tự liên hệ "
                 "khách, không bịa số."),
        "fallback": (f"{party} quá hạn {late} ngày khoản {dong(rest)} ({ar['label']}). Bạn nhắc khách trả, ghi mã {ar['code']} vào nội dung "
                     "chuyển khoản để đối soát tự khớp."),
    }


@m.on("acct.payment_received")
async def wake_on_payment(ctx: Ctx, event: Event) -> None:
    p = event.payload
    await ctx.call_system("work.wake", kind="collection", subject_ref=p["code"],
                          text=f"Có tiền về — luỹ kế {dong(int(p['paid_vnd']))} / {dong(int(p['amount_vnd']))}.")


@m.on("acct.receivable_overdue")
async def open_collection(ctx: Ctx, event: Event) -> None:
    """Khoản phải thu quá hạn (vòng quét của Tài chính) ⇒ việc "khách trả tiền" cho trợ lý người phụ trách khoản đó; không có người phụ
    trách ⇒ người có quyền ghi Kế toán cả công ty (theo ma trận quyền, không viết cứng vai)."""
    p = event.payload
    owner = p.get("owner_id")
    if owner is None:
        owners = await ctx.call_system("admin.members_with_grant", resource="acct", action="write", scope="all")
        if not owners:
            return
        owner = owners[0]
    await ctx.call_system("work.open", kind="collection", owner_id=str(owner), subject_ref=p["code"], title=f"{p['party'] or 'Khách'} trả {p['label']}",
                          note=f"Mở khi quá hạn: {p['party'] or 'khách'} còn {dong(int(p['rest']))} (mã {p['code']}), "
                               f"hạn {date.fromisoformat(p['due_on']):%d/%m/%Y}.")


# ---- Màn hình (0069): module tự khai phân hệ / góp màn vào phân hệ khác; workspaces.py chỉ ráp và lọc theo quyền
m.workspace(Workspace("accounting", "Kế toán", "banknote.fill", (
    Screen("reconcile", "Đối soát sao kê", "arrow.left.arrow.right.square.fill", "acct.reconcile", tint="purple", min_scope="all", group=G_IN,
           actions=(A("acct.add_statement_line", "Nạp sao kê", style="primary"), A("acct.apply_statement", "Ghi các dòng khớp đủ"),
                    A("acct.record_unapplied", "Tiền về chưa có khoản")),
           row_actions=(A("acct.apply_line", "Ghi phiếu thu", bind={"line_id": "id"}, prefill={"code": "code"},
                          when={"state": ["full", "short", "over", "amount_only", "unmatched"]}, style="primary"),
                        A("acct.ignore_line", "Bỏ qua", bind={"line_id": "id"}))),
    Screen("receivables", "Khoản phải thu", "list.bullet.rectangle.portrait.fill", "acct.receivables", tint="teal", min_scope="all", group=G_AR,
           row_actions=(A("acct.record_payment", "Ghi thu", bind={"code": "code"}, when={"status": ["open", "partial"]}, style="primary"),
                        A("acct.credit_receivable", "Giảm phải thu", bind={"code": "code"}, when={"status": ["open", "partial", "paid"]}),
                        A("acct.reverse_receipt", "Đảo phiếu thu", bind={"code": "code"}, when={"status": ["partial", "paid"]}),
                        A("acct.issue_vat_invoice", "Ghi hoá đơn GTGT", bind={"code": "code"}, when={"vat": ["ready"]}))),
), order=70))
# Menu Kế toán theo nhịp việc (rà vai kế toán 18/11/2026): tiền về & đối soát → phải thu → chi & phải trả → sổ sách & thuế → báo cáo → thiết lập.
# Thứ tự nhóm trên menu = thứ tự màn ĐẦU TIÊN của nhóm (app vẽ theo thứ tự xuất hiện) ⇒ màn thiết lập / báo cáo khai ở hạng cao (90 / 70).
m.screens("accounting", (
    Screen("unapplied", "Tiền về chưa phân bổ", "tray.and.arrow.down", "acct.unapplied", tint="orange", min_scope="all", group=G_IN),
    Screen("payables", "Phải trả & ứng", "banknote", "acct.payables", tint="orange", min_scope="all", group=G_OUT,
           row_actions=(A("acct.request_payment", "Đề nghị chi", bind={"payable_id": "id"}, when={"kind": ["payable"], "open": ["yes"]}, style="primary"),
                        A("acct.recover_payment", "Ghi nhận hoàn tiền", bind={"payable_id": "id"}, when={"kind": ["advance", "recovery"], "open": ["yes"]}))),
    Screen("payouts", "Lệnh chi", "arrow.up.right.square.fill", "acct.payouts", tint="orange", group=G_OUT,
           row_actions=(A("acct.confirm_payout", "Đã chuyển khoản", bind={"payout_id": "id"}, when={"status": ["pending_transfer", "unknown"]},
                          style="primary"),
                        A("acct.mark_payout_unknown", "Cần tra soát", bind={"payout_id": "id"}, when={"status": ["pending_transfer"]}),
                        A("acct.cancel_payout", "Huỷ lệnh", bind={"payout_id": "id"}, when={"status": ["pending_transfer", "unknown"]}, style="destructive"))),
    Screen("remittances", "Chi trả thu hộ", "arrow.uturn.up.square", "acct.remittances", group=G_OUT, feature=USE_REMITTANCES,
           row_actions=(A("acct.confirm_remittance", "Đã chuyển khoản", bind={"remittance_id": "id"}, when={"status": ["pending_transfer"]},
                          style="primary"),)),
), rank=20)
m.screens("accounting", (
    Screen("cashflow", "Dòng tiền dự phóng", "chart.line.uptrend.xyaxis.circle.fill", "acct.cashflow", tint="green", group=G_REPORT,
           desk=Desk("decide", say=_say_cashflow, menu=True, rank=35)),
), rank=70)
m.screens("accounting", (
    Screen("balances", "Số dư sổ cái", "books.vertical", "acct.balances", group=G_BOOK),
    Screen("misa", "Xuất MISA", "square.and.arrow.up", "acct.misa_export", group=G_BOOK),
), rank=40)
m.screens("accounting", (
    Screen("account_map", "Ánh xạ tài khoản", "arrow.left.arrow.right", "acct.account_map", group=G_SETUP,
           actions=(A("acct.set_account_map", style="primary"),)),
    Screen("categories", "Danh mục khoản thu / chi", "list.bullet.indent", "acct.category_list", group=G_SETUP,
           actions=(A("acct.set_category", style="primary"),)),
    Screen("tax_categories", "Loại thuế GTGT", "tag", "acct.tax_category_list", min_scope="all", pinnable=False, group=G_SETUP,
           actions=(A("acct.set_tax_category", style="primary"),)),
), rank=90)
# 0165: thuế GTGT đầu ra — màn có sẵn, nhưng ghi được chỉ khi công ty bật acct.vat_enabled (mặc định tắt)
m.screens("accounting", (
    Screen("vat_invoices", "Hoá đơn GTGT", "doc.text", "acct.vat_invoices", min_scope="all", pinnable=False, group=G_BOOK,
           actions=(A("acct.issue_vat_invoice", "Ghi hoá đơn GTGT", style="primary"),),
           row_actions=(A("acct.adjust_vat_invoice", "Điều chỉnh giảm", bind={"code": "code"}, when={"state": ["active"]}),
                        A("acct.cancel_vat_invoice", "Huỷ hoá đơn", bind={"code": "code"}, when={"state": ["active"]}, style="destructive"))),
    Screen("vat_summary", "Tổng hợp thuế GTGT", "percent", "acct.vat_summary", min_scope="all", pinnable=False, group=G_BOOK),
), rank=45)


# ── Tập dữ liệu (0100): khoản phải thu nhóm theo bản chất, tình trạng thu, bên trả, tháng hạn — khoản đã huỷ không tính ──
m.dataset(
    "receivables", "Khoản phải thu", resource="acct",
    description="Mỗi dòng gốc là một khoản phải thu (không gồm khoản đã huỷ).",
    base="""SELECT status, nature, party, source, category, amount_vnd, paid_vnd, amount_vnd - paid_vnd AS remaining_vnd, issued_on, due_on,
                   CASE WHEN status = 'paid' THEN 'done' WHEN due_on < core.today() THEN 'late' ELSE 'due' END AS collect
            FROM acct.receivables WHERE status <> 'void'""",
    dims={"collect": Dim("Tình trạng thu", "t.collect", labels={"late": "Quá hạn", "due": "Chưa tới hạn", "done": "Đã thu xong"}),
          "status": Dim("Trạng thái", "t.status", labels=AR_STATUS), "nature": Dim("Bản chất", "t.nature", labels=NATURE),
          "party": Dim("Bên trả", "t.party"), "category": Dim("Mục khoản thu", "t.category"), "due_month": Dim("Tháng tới hạn", "to_char(t.due_on, 'YYYY-MM')", month=True),
          "issued_month": Dim("Tháng phát sinh", "to_char(t.issued_on, 'YYYY-MM')", month=True)},
    measures={"count": Measure("Số khoản", "count(*)"), "amount": Measure("Phải thu", "sum(t.amount_vnd)", "money"),
              "paid": Measure("Đã thu", "sum(t.paid_vnd)", "money"), "remaining": Measure("Còn lại", "sum(t.remaining_vnd)", "money")},
    dates={"due_on": "Hạn thu", "issued_on": "Ngày phát sinh"},
)

# Báo cáo quản trị (0166): dòng tiền dự phóng trong menu của ban giám đốc — cùng truy vấn
m.screens("mgmt", (Screen("mgmt_cashflow", "Dòng tiền dự phóng", "chart.line.uptrend.xyaxis", "acct.cashflow", group="Dòng tiền & công nợ", tint="green", min_scope="all"),), rank=20)


from barista.modules.acct import (  # noqa: E402, F401 — registers bank accounts / cash in-out, payables, period locks, corrections, VAT, expenses, money placed with counterparties (0182), goods books (0217), money assurance over discrepancy dossiers (0228 §D)
    assurance,
    bank,
    corrections,
    dossier,
    expenses,
    goods,
    ledger_read,
    periods,
    placements,
    settlements,
    vat,
)
