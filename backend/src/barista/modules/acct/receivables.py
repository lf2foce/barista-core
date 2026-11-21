"""KHOẢN PHẢI THU dùng chung (0057 / 0079): gói ngành lập qua ``acct.issue_receivable`` (chống lặp theo dữ kiện lúc lập — ``_replayed_receivable``),
huỷ khoản chưa thu (``void_receivables`` / ``void_books``), tra khoản theo mã (``_target``), màn / tìm nhanh / ô chọn / hợp đồng đọc cho gói nguồn,
nguồn ngày "hạn thu" cho luật tự động, nhắc nợ quá hạn + việc theo dõi "khách trả tiền", tập dữ liệu khoản phải thu."""

import logging
import re
import secrets
from datetime import date
from typing import Any, Literal, LiteralString
from uuid import UUID

from psycopg import errors as pg

from barista.api import (
    Conflict,
    Ctx,
    DateIn,
    Denied,
    Dim,
    Event,
    Input,
    Invalid,
    Measure,
    NoInput,
    NotFound,
    SearchIn,
    SearchTerms,
    WorkIn,
    dong,
    entities,
    field,
    today,
    ui,
)
from barista.kernel import ledger
from barista.modules.acct import m
from barista.modules.acct.common import CODE_ALPHABET, _vnd, debtor, locked_message, locked_task

log = logging.getLogger("barista.acct")

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



# ── Khoản phải thu dùng chung — phần "sales-to-cash" mọi ngành dùng (quyết định 0057) ──
#
# Gói ngành (cho thuê; sau này học phí, đơn hàng) KHÔNG tự ghi tiền: lập khoản phải thu qua acct.issue_receivable (hợp đồng nội bộ hẹp) và nghe
# acct.payment_received. Tài chính lo phần chung: mã thanh toán, phiếu thu, ghi sổ theo BẢN CHẤT tiền, đối soát sao kê, chi trả tiền thu hộ qua
# duyệt, nhắc nợ quá hạn (việc theo dõi loại "collection" — trừ khoản gói nguồn tự theo dõi: ``followed``).

NATURE = {"revenue": "Doanh thu", "pass_through": "Thu hộ", "deposit": "Tiền cọc", "deferred": "Doanh thu nhận trước"}
NATURE_TONES = {"revenue": "green", "pass_through": "blue", "deposit": "purple", "deferred": "teal"}
AR_STATUS = {"open": "Chưa thu", "partial": "Thu một phần", "paid": "Đã thu", "void": "Đã huỷ"}
AR_TONES = {"open": "orange", "partial": "blue", "paid": "green", "void": "gray"}
def _revenue_accounts() -> set[str]:
    """Tài khoản doanh thu gói ngành dùng được cho khoản phải thu chung = mọi tài khoản doanh thu đã khai (module gói tự khai)."""
    return {c for c, (_, kind) in ledger.ACCOUNTS.items() if kind == "revenue"}


async def _target(ctx: Ctx, ref: str) -> dict[str, Any]:
    """Khoản phải thu theo mã (mã thanh toán TT… hoặc mã gói đặt, vd HD-…). Khoá dòng."""
    no = ref.strip().upper()
    if ar := await ctx.one("SELECT * FROM acct.receivables WHERE code = %s FOR UPDATE", (no,)):
        return ar
    raise NotFound(f"Không có khoản phải thu mã {no}")


def _rest(t: dict[str, Any]) -> int:
    return 0 if t["status"] in ("paid", "void") else t["amount_vnd"] - t["paid_vnd"]



async def deferred_account(ctx: Ctx, entity_id: UUID) -> str:
    """Tài khoản doanh thu nhận trước của MỘT pháp nhân (``deferred:<mã pháp nhân>``, nợ phải trả — 0147). Tạo ở lần ghi đầu; mã pháp nhân đổi tên không
    phát sinh tài khoản mới vì bút toán đã ghi vẫn trỏ tài khoản cũ (mã pháp nhân không đổi sau khi tạo)."""
    row = await ctx.one("SELECT code, name FROM core.legal_entities WHERE id = %s", (entity_id,))
    if row is None:
        raise NotFound("Không thấy pháp nhân")
    code = f"deferred:{row['code'].lower()}"
    await ledger.ensure_account(ctx, code, f"Doanh thu nhận trước — {row['name']}", "liability")
    return code


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


async def _replayed_receivable(ctx: Ctx, source: str, ref: str, want: dict[str, Any], *, entity: UUID | None,
                               issued_on: date | None) -> dict[str, Any] | None:
    """MỘT nơi quyết định "gọi lại" của khoản phải thu (nguồn, tham chiếu) — cả khi gọi lại tuần tự lẫn khi thua yêu cầu song song: so với DỮ KIỆN LÚC
    LẬP (số lúc lập không đổi khi có giấy báo có — finance/077); đã có mà KHÁC nội dung ảnh hưởng hạch toán (bản chất, số tiền, phí, tài khoản, mục, người nhận; pháp nhân / ngày lập khi bên gọi NÊU RÕ) ⇒ Conflict — đổi số là
    nghiệp vụ điều chỉnh có dấu vết (giấy báo có rồi lập lại), không bao giờ "đã lập" với số cũ (rà Codex 20/11 lõi tiền)."""
    old = await ctx.one("""SELECT id, code, entity_id, issued_on, nature, issued_amount_vnd AS amount_vnd, issued_fee_vnd AS fee_vnd, revenue_account,
                                  receivable_account, category, payee_key
                           FROM acct.receivables WHERE source = %s AND source_ref = %s""", (source, ref))   # số LÚC LẬP (finance/077), không số đã điều chỉnh
    if old is None:
        return None
    if diff := [k for k, v in want.items() if old[k] != v]:
        raise Conflict(f"IDEMPOTENCY_CONFLICT: {old['code']} đã lập với {', '.join(diff)} khác — sửa bằng giấy báo có rồi lập lại, không đổi ngầm")
    if entity is not None and old["entity_id"] != entity:
        raise Conflict(f"IDEMPOTENCY_CONFLICT: {old['code']} đã lập cho pháp nhân khác")
    if issued_on is not None and old["issued_on"] != issued_on:
        raise Conflict(f"IDEMPOTENCY_CONFLICT: {old['code']} đã lập ngày {old['issued_on']:%d/%m/%Y}")
    return {"receivable_id": old["id"], "code": old["code"], "created": False}


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
    want_row = {"nature": inp.nature, "amount_vnd": inp.amount_vnd, "fee_vnd": fee, "revenue_account": rev, "receivable_account": inp.receivable_account,
                "category": category, "payee_key": inp.payee_key if inp.nature in ("pass_through", "deposit") else ""}
    if old := await _replayed_receivable(ctx, inp.source, inp.source_ref, want_row, entity=inp.entity_id, issued_on=inp.issued_on):
        return old
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
        except pg.UniqueViolation:   # song song cùng nguồn ⇒ cùng một phép so như lần gọi lại tuần tự
            if old := await _replayed_receivable(ctx, inp.source, inp.source_ref, want_row, entity=inp.entity_id, issued_on=inp.issued_on):
                return old
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


class TotalsFor(Input):
    source_prefix: str
    refs: list[str]


@m.query("receivable_totals", resource="acct", title="Tổng khoản phải thu theo đối tượng nguồn", internal=True, agent="deny", family="receivable",
         why="Gói khai consumes(\"receivable\") cần Ô TỔNG trên cả tập đối tượng của mình (vd ô phí đã ghi nhận / đã thu của một màn danh sách lọc theo kỳ — "
             "mọi giao dịch trong tập, không phải trang đang hiện), không cần từng khoản. refs = mã đối tượng GỐC người xem đã thấy qua RLS của gói (lần lập lại "
             "<mã>#NN gộp vào mã gốc). Chỉ nguồn của CHÍNH module gọi. Trả {mã gốc: {amount_vnd, paid_vnd, n}} của các khoản chưa huỷ — không mã khoản, "
             "không bên trả, không hạn.")
async def receivable_totals(ctx: Ctx, inp: TotalsFor) -> dict[str, dict[str, int]]:
    """Tổng số phải thu / đã thu theo đối tượng nguồn (gom các lần lập ``<mã>#NN`` về mã gốc) — một câu GROUP BY, khớp mã gốc chính xác (không ``LIKE``
    theo tiền tố: với vài nghìn mã, khớp tiền tố là vài trăm ms)."""
    ctx.owned_by(inp.source_prefix.split(".")[0], f"Nguồn {inp.source_prefix}")
    if not inp.refs:
        return {}
    rows = await ctx.all("""SELECT split_part(source_ref, '#', 1) AS ref, coalesce(sum(amount_vnd), 0)::bigint AS amount_vnd,
                                   coalesce(sum(paid_vnd), 0)::bigint AS paid_vnd, count(*)::int AS n
                            FROM acct.receivables WHERE starts_with(source, %s) AND status <> 'void' AND split_part(source_ref, '#', 1) = ANY(%s)
                            GROUP BY 1""", (inp.source_prefix, inp.refs))
    return {r["ref"]: {"amount_vnd": int(r["amount_vnd"]), "paid_vnd": int(r["paid_vnd"]), "n": r["n"]} for r in rows}


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
