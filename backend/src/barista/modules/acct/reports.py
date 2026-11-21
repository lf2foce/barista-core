"""BÁO CÁO chỉ đọc của Tài chính: dòng tiền dự phóng (+ tín hiệu bàn việc khi dự phóng âm tiền), số dư sổ cái, xuất bút toán theo mẫu nhập MISA
(+ ánh xạ tài khoản MISA — dữ liệu công ty), số gộp ghi nhận / thực thu / phân tuổi công nợ cho màn điều hành của gói."""

import re
from collections import defaultdict
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from barista.api import (
    Ctx,
    Day,
    Input,
    Invalid,
    NoInput,
    entities,
    field,
    line,
    today,
    ui,
)
from barista.kernel import ledger, registry
from barista.modules.acct import m
from barista.modules.acct.cash_in import fee_earned
from barista.modules.acct.common import _UUID_RE, _misa_account, _vnd


def _month(d: date) -> str:
    return f"{d.month:02d}/{d.year}"



# ── Dòng tiền & sổ ──

class CashflowInput(Input):
    months: int = field("Số tháng", 4, ge=1, le=24)


TRACKED_DAYS = 60   # có dòng sao kê nạp trong ngần này ngày ⇒ coi số tiền ngân hàng trên sổ là đang được đối chiếu (điều kiện để báo âm tiền)


CASHFLOW = "cashflow_lines"   # <module>.cashflow_lines (nội bộ): dòng thu / chi DỰ PHÓNG của gói — {lines: [{key, label, kind, by_month}], kpis, note}
# ``note`` (tuỳ chọn, 0211): một câu gói nói về cách ước dòng của mình (vd "ước theo kỳ đã chốt gần nhất") — nối vào ghi chú của bảng.


async def _projection(ctx: Ctx, n_months: int) -> dict[str, Any]:
    """Phép tính của Dòng tiền dự phóng (xem ``cashflow``) — MỘT nơi cho màn và cho ``acct.cashflow_projection``. Trả tháng, dòng, cột thêm của gói,
    ô số thêm, ghi chú, tiền hiện có, phần không phải của mình, có đang đối chiếu sao kê không, tháng đầu tiên âm tiền."""
    t = today()
    months: list[str] = []
    y, mo = t.year, t.month
    for _ in range(n_months):
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
                out_remit[b] += r["rest"] - (r["fee_vnd"] - fee_earned(r["fee_vnd"], r["amount_vnd"] - r["rest"], r["amount_vnd"]))   # phần phí còn lại, cùng công thức với lúc ghi
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
    note = " ".join(["Dự phóng từ dữ liệu đang có: khoản phải thu theo hạn, nghĩa vụ phải trả theo ngày đến hạn, lệnh chi chờ chuyển và các khoản "
                     "dự kiến của nghiệp vụ khác (vd hoa hồng chờ tiền phí về).", *notes, "Chưa gồm khoản chi chưa có trong hệ thống (thuê văn phòng, thuế doanh nghiệp…).",
                     *([] if tracked else [f"Chưa nạp sao kê trong {TRACKED_DAYS} ngày qua — tiền hiện có chưa đối chiếu ngân hàng, không báo âm tiền."])])
    return {"rows": rows, "extra": extra, "extra_kpis": extra_kpis, "note": note, "opening": opening, "low": low, "not_ours": not_ours, "neg": neg}


@m.query("cashflow_projection", resource="acct", title="Dòng tiền dự phóng — số tóm tắt", min_scope="all", internal=True)
async def cashflow_projection(ctx: Ctx, inp: CashflowInput) -> dict[str, Any]:
    """SỐ TÓM TẮT cho màn tổng hợp (bảng điều hành của gói): số dư cuối từng tháng dự phóng {rows: [{month, closing}], note} — cùng phép tính với màn
    Dòng tiền dự phóng, không dựng bảng. Gọi bằng QUYỀN NGƯỜI XEM (``ctx.call_as_optional(…, ctx.p)``): người không xem được Tài chính cả công ty ⇒
    None; nghĩa vụ mật vẫn vào số dưới dạng tổng như trên màn."""
    p = await _projection(ctx, inp.months)
    return {"rows": [{"month": r["month"], "closing": r["closing"]} for r in p["rows"]], "note": p["note"]}


@m.query("cashflow", resource="acct", title="Dòng tiền dự phóng", min_scope="all")
async def cashflow(ctx: Ctx, inp: CashflowInput) -> dict[str, Any]:
    """Theo tháng: thu = khoản phải thu chưa thu theo hạn (quá hạn dồn tháng này); chi = lệnh chi / chi trả chờ chuyển (tháng này) + nghĩa vụ phải
    trả còn lại theo NGÀY ĐẾN HẠN nguồn nêu (0211 — trống / quá hạn dồn tháng này) + tiền THU HỘ phải trả lại (đang giữ: tháng này; sẽ thu: tháng
    thu) + các dòng DỰ PHÓNG gói khai (``<module>.cashflow_lines``: khoản chi gói biết trước — ngân sách quảng cáo, quỹ lương kỳ chưa chốt…; gói tắt
    ⇒ không có dòng). Nghĩa vụ MẬT người xem không thấy chi tiết (vd lương) vẫn vào số chi dưới dạng tổng theo tháng (``acct.confidential_open``) —
    dòng tiền không thiếu khoản chi chỉ vì quyền xem. Tiền cọc đang giữ không tính chi (hoàn khi hết hợp đồng) — chỉ ghi chú."""
    p = await _projection(ctx, inp.months)
    rows, extra, opening, low, neg = p["rows"], p["extra"], p["opening"], p["low"], p["neg"]
    page = ui.table(rows, [
        ui.col("month", "Tháng"), ui.col("opening", "Đầu kỳ", "money"), ui.col("inflow", "Tiền thu vào", "money"),
        *[ui.col(x["key"], x["label"], "money") for x in extra if x["kind"] == "in"],
        *[ui.col(x["key"], x["label"], "money") for x in extra if x["kind"] == "out"],
        ui.col("out_payout", "Phải trả & lệnh chi chờ", "money"), ui.col("out_remit", "Trả tiền thu hộ", "money"),
        ui.col("net", "Ròng", "money"), ui.col("closing", "Cuối kỳ", "money"),
    ], title="Dòng tiền dự phóng", row_key="id", kpis=[
        ui.kpi("Tiền hiện có", opening, "money", hint=p["not_ours"]),
        ui.kpi("Thấp nhất trong kỳ", low, "money", tone="red" if low < 0 else "green"), *p["extra_kpis"],
    ], note=p["note"])
    # ADD-only (bất biến 7): tháng đầu tiên âm tiền {month, closing} hoặc None — tín hiệu bàn việc + bản tin buổi sáng đọc trường này
    page["alert"] = {"month": neg["month"], "closing": int(neg["closing"])} if neg else None
    return page


def _say_cashflow(o: Any) -> Any:
    """Tín hiệu bàn việc (0211 lát 2): tháng đầu tiên dự phóng âm tiền — code đọc trường ``alert`` của trang, không model."""
    a = (o or {}).get("alert")
    return line("bad", f"Dòng tiền dự phóng âm tháng {a['month']}", f"Cuối tháng còn {_vnd(a['closing'])} — xem khoản thu / chi từng tháng",
                f"Vì sao tháng {a['month']} dự phóng âm tiền, khoản chi nào lớn nhất?") if a else None


SUBLEDGER = {"advance": "Tạm ứng", "recovery": "Phải thu hồi", "payable": "Phải trả"}
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
