"""BẤT BIẾN TIỀN trên dữ liệu thật: khoản phải thu / dòng sao kê lệch với phiếu thu ⇒ Sức khoẻ dữ liệu + vòng quét báo như lỗi hệ thống; điều kiện
chặn tắt gói Tài chính (tiền còn dở dang)."""

from datetime import date
from typing import Any

from barista.api import (
    Ctx,
    NoInput,
    today,
)
from barista.modules.acct import m
from barista.modules.acct.common import _vnd

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
