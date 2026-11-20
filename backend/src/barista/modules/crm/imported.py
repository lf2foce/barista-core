"""NẠP CHĂM SÓC TỪ HỆ KHÁC (quyết định 0174): cuộc gọi / gặp / tin nhắn / ghi chú đã xảy ra ở hệ cũ vào ``crm.activities`` GIỮ GIỜ GỐC và người làm.

Chăm sóc là MỘT khái niệm (hạn gọi lead, thẻ buổi sáng, độ nóng khách đọc cùng bảng) nên dòng nạp vào cùng bảng với dòng ghi trong Barista, đánh dấu
``source = 'import'`` + hệ nguồn + mã dòng nguồn (nạp lại không nhân đôi). Khác ``crm.log_activity`` (giờ = lúc ghi, người = người ghi):
* giờ là giờ GỐC (bắt buộc có múi giờ, không nhận giờ tương lai), người là người đã làm ở hệ nguồn (khớp thành viên theo email, không thì giữ mã cũ);
* không hẹn chăm sóc lại, không đổi chặng / người phụ trách, không thông báo, không phát sự kiện; "chăm sóc gần nhất" của lead chỉ tiến tới giờ gốc
  nếu mới hơn (``greatest`` — như cuộc gọi tổng đài);
* chấm hạn chia lead (``crm.contact_info``) tính dòng nạp là bằng chứng liên hệ lần đầu CHỈ khi người làm là người đang giữ lead và giờ gốc từ lúc lead
  tới tay người đó — không bao giờ thành "người đang giữ vừa liên hệ lúc nạp".
Chỉ người quản lý Khách hàng cả công ty nạp; trợ lý không chạy. Nội dung chăm sóc không chép vào nhật ký thao tác (dòng chăm sóc là bản ghi)."""

import re
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import AwareDatetime

from barista.api import Ctx, Denied, Input, Invalid, NotFound, field
from barista.modules.crm import ACTIVITY, OUTCOMES, m

ORIGIN = r"^[a-z][a-z0-9_]{1,39}$"


class ImportActivityIn(Input):
    lead_id: UUID = field("Mã lead (Barista)")
    origin: str = field("Hệ nguồn", max_length=40, example="he_cu", help="Mã ngắn của hệ cũ (chữ thường, số, _) — cùng mã cho mọi dòng")
    ext_ref: str = field("Mã dòng ở hệ nguồn", max_length=120, example="hi-88231", help="Duy nhất trong hệ nguồn — nạp lại cùng mã không nhân đôi")
    kind: Literal["call", "meet", "zalo", "email", "visit", "send_profile", "note"] = field("Hình thức", "call", options=ACTIVITY)
    outcome: Literal["", "connected", "no_answer", "busy", "wrong_number"] = field("Kết quả cuộc gọi", "", options=OUTCOMES)
    at: AwareDatetime = field("Lúc", kind="text", example="2026-05-04T09:15:00+07:00",
                              help="ISO 8601 CÓ múi giờ — lúc hệ nguồn ghi nhận chăm sóc (không phải lúc nạp)")
    author_email: str = field("Người làm (email)", "", max_length=200, help="Người đã nghỉ vẫn nhận nếu từng là thành viên")
    author_ref: str = field("Mã người làm ở hệ nguồn", "", max_length=120, help="Khi không có email khớp thành viên: giữ mã cũ, không bỏ dòng")
    note: str = field("Nội dung", "", kind="textarea", max_length=4000, audit=False,
                      help="Có thể có dữ liệu cá nhân của khách — không chép vào nhật ký thao tác")
    source_label: str = field("Loại / kết quả ở hệ nguồn", "", max_length=120,
                              help="Chữ / mã hệ cũ ghi (vd loại tương tác, trạng thái liên hệ) — giữ nguyên để tra")
    occurred_at: AwareDatetime | None = field("Lúc xảy ra (người làm khai)", None, kind="text",
                                              help="Hệ nguồn cho người làm khai giờ riêng — chỉ lưu để tra, không dùng chấm hạn")


@m.command("import_activity", resource="crm", action="manage", title="Nạp chăm sóc từ hệ khác (giữ giờ gốc)", category="setup", importable=True,
           agent="deny", min_scope="all")
async def import_activity(ctx: Ctx, inp: ImportActivityIn) -> dict[str, Any]:
    """MỘT dòng chăm sóc đã xảy ra ở hệ khác ⇒ ``crm.activities`` (``source = 'import'``) giữ giờ gốc + người làm. Không thông báo, không hẹn, không
    đổi chặng / người phụ trách. Nạp lại cùng (hệ nguồn, mã dòng) ⇒ bỏ qua."""
    if not ctx.p.has_scope("crm", "all"):
        raise Denied("Nạp chăm sóc từ hệ khác là việc của người quản lý Khách hàng cả công ty")
    if not re.fullmatch(ORIGIN, inp.origin):
        raise Invalid("Hệ nguồn: chữ thường không dấu, số, _ (2–40 ký tự), bắt đầu bằng chữ")
    if not inp.ext_ref.strip():
        raise Invalid("Cần Mã dòng ở hệ nguồn")
    if inp.outcome and inp.kind != "call":
        raise Invalid("Kết quả cuộc gọi chỉ dùng cho hình thức Gọi điện")
    now = datetime.now(UTC)
    for v, what in ((inp.at, "Lúc"), (inp.occurred_at, "Lúc xảy ra")):
        if v is not None and v > now:
            raise Invalid(f"{what} ở tương lai — chỉ nạp chăm sóc đã xảy ra")
    if not await ctx.val("SELECT 1 FROM crm.leads WHERE id = %s", (inp.lead_id,)):   # RLS người nạp
        raise NotFound("Không thấy lead")
    author = await ctx.val("SELECT u.id FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant()"
                           " WHERE u.email = %s", (inp.author_email.strip().lower(),)) if inp.author_email.strip() else None
    if inp.author_email.strip() and author is None and not inp.author_ref.strip():
        raise NotFound(f"Không có người {inp.author_email} trong công ty (điền Mã người làm ở hệ nguồn để giữ dòng)")
    data = {k: v for k, v in (("label", inp.source_label.strip()), ("occurred_at", inp.occurred_at and inp.occurred_at.isoformat())) if v}
    aid = await ctx.val(
        """INSERT INTO crm.activities (lead_id, kind, outcome, note, by_user, at, source, origin, ext_ref, author_ref, data, imported_by, imported_at)
           VALUES (%s, %s, %s, %s, %s, %s, 'import', %s, %s, %s, %s, %s, now())
           ON CONFLICT (tenant_id, origin, ext_ref) WHERE source = 'import' DO NOTHING RETURNING id""",
        (inp.lead_id, inp.kind, inp.outcome, inp.note, author, inp.at, inp.origin, inp.ext_ref.strip(), "" if author else inp.author_ref.strip(),
         Jsonb(data), ctx.p.user_id))
    if aid is not None and inp.kind != "note":   # "chăm sóc gần nhất" chỉ tiến tới giờ GỐC nếu mới hơn — không phải lúc nạp, không đổi updated_at
        await ctx.exec("UPDATE crm.leads SET last_activity_at = greatest(last_activity_at, %s) WHERE id = %s", (inp.at, inp.lead_id))
    return {"lead_id": inp.lead_id, "inserted": aid is not None, "matched_author": author is not None}
