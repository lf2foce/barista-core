"""Phần dùng chung của Tài chính: định dạng số tiền, kỳ đã khoá, bên nợ, mã thanh toán, ánh xạ tài khoản MISA. Các file của package import từ đây;
file này KHÔNG import file nào của package (không vòng)."""

import re
from datetime import date
from typing import Any
from uuid import UUID

from barista.api import (
    Ctx,
    NotFound,
    entities,
    today,
)
from barista.kernel import ledger

# ── Kỳ đã khoá (0119): kiểm TRƯỚC ở lệnh có người bấm; việc chạy nền gặp kỳ khoá ⇒ việc cho kế toán, không lặng lẽ mất ──

async def locked_message(ctx: Ctx, entity: UUID | None, on: date) -> str:
    """Câu báo nếu ``on`` thuộc kỳ đã khoá của pháp nhân (trống = mặc định), không thì chuỗi rỗng."""
    lock = await ledger.locked_through(ctx, entity or await entities.default(ctx))
    if lock is not None and on <= lock:
        return f"Ngày {on:%d/%m/%Y} thuộc kỳ đã khoá tới {lock:%d/%m/%Y} — xin mở kỳ hoặc chọn ngày khác"
    return ""


def receivable_subject(t: dict[str, Any]) -> dict[str, Any]:
    """Hồ sơ cho bản chạy thử chuẩn (ADR 0253): bên trả · diễn giải (mã) của MỘT khoản phải thu đã nạp — không thêm truy vấn."""
    label = " · ".join(x for x in (t.get("party"), t.get("label")) if x) or t["code"]
    return {"label": label if t["code"] in label else f"{label} ({t['code']})", "open": None}


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


def _vnd(n: int) -> str:
    """Chỉ đổi dấu phân cách của CON SỐ — đừng .replace(",", ".") cả câu (ăn mất dấu phẩy của chữ)."""
    return f"{n:,}".replace(",", ".") + " đ"


CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"   # không 0/O, 1/I — khách đọc / gõ lại không nhầm


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


_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _misa_account(code: str, mapping: list[dict[str, Any]]) -> str:
    best = ""
    acct = "????"
    for mp in mapping:
        pre = mp["ledger_prefix"]
        if (code == pre or code.startswith(pre + ":")) and len(pre) > len(best):
            best, acct = pre, mp["misa_account"]
    return acct
