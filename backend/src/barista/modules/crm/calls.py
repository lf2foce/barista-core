"""Hợp đồng nội bộ cho gói tổng đài (họ ``call_log`` — 0149; gói khai ``consumes("call_log")``): khớp SĐT khách → lead, ghi cuộc gọi thật thành chăm sóc "Gọi điện".

Cuộc gọi qua tổng đài là liên hệ THẬT ⇒ đi vào ``crm.activities`` như chăm sóc ghi tay: một định nghĩa "đã liên hệ" cho hạn gọi lead (0034), thẻ
buổi sáng, độ nóng khách. Chỉ thêm, không sửa nhu cầu / chặng / hẹn của lead.
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from barista.api import Ctx, Input, NotFound, parse_phone
from barista.modules.crm import m


def _phone(v: str) -> str | None:
    try:
        return parse_phone(v)
    except ValueError:
        return None


class PhoneBy(Input):
    phone: str
    user_id: UUID | None = None


class PhonesIn(Input):
    calls: list[PhoneBy]


@m.query("leads_for_phones", resource="crm", title="Lead theo SĐT (tổng đài)", internal=True, agent="deny", family="call_log",
         why="Vòng quét đồng bộ của gói tổng đài (quyền hệ thống) / lệnh đồng bộ ngay của quản trị kết nối: SĐT lấy từ nhật ký cuộc gọi của nhà cung cấp tổng đài, "
             "không do người dùng gõ. Mỗi cặp (SĐT, người gọi) trả MỘT lead: ưu tiên lead của chính người gọi (một khách nhiều lead, mỗi lead "
             "một sale — review 09/11), rồi lead đang mở cập nhật gần nhất {lead_id, party_id, created_at} "
             "— không tên, không người phụ trách, không nhu cầu; cuộc gọi chỉ hiện cho người thấy khách (RLS của bảng cuộc gọi)."
             " [id: module gọi tự có — user_id là nhân viên đã khớp trên dòng nhật ký tổng đài lúc đồng bộ (theo máy lẻ / email của nhà cung cấp),"
             " không do người dùng gõ; chỉ dùng để xếp thứ tự chọn lead, không trả lại]")
async def leads_for_phones(ctx: Ctx, inp: PhonesIn) -> dict[str, dict[str, Any]]:
    asks = sorted({(c.phone, p, str(c.user_id) if c.user_id else "") for c in inp.calls[:500] if (p := _phone(c.phone))})
    if not asks:
        return {}
    rows = await ctx.all(
        """SELECT q.raw, q.by, h.lead_id, h.party_id, h.created_at
           FROM unnest(%s::text[], %s::text[], %s::text[]) AS q(raw, phone, by)
           CROSS JOIN LATERAL (
             SELECT l.id AS lead_id, c.party_id, l.created_at FROM crm.customers c JOIN crm.leads l ON l.customer_id = c.id
             WHERE c.phone = q.phone
             ORDER BY (l.owner_id::text = q.by) DESC, (l.stage IN ('lead', 'prospect')) DESC, l.updated_at DESC LIMIT 1) h""",
        ([a[0] for a in asks], [a[1] for a in asks], [a[2] for a in asks]))
    # khoá = SĐT ĐÚNG như người gọi gửi + "|" + người gọi — bên gọi không phải chuẩn hoá SĐT giống crm
    return {f"{r['raw']}|{r['by']}": {"lead_id": r["lead_id"], "party_id": r["party_id"], "created_at": r["created_at"]} for r in rows}


class RecordCallIn(Input):
    lead_id: UUID
    user_id: UUID | None = None
    at: datetime
    outcome: Literal["", "connected", "no_answer", "busy", "wrong_number"] = ""
    note: str = ""
    callback: bool = False   # cuộc gọi RA không được nghe ⇒ hẹn gọi lại hôm nay như khi sale ghi tay (0178)
    missed_in: bool = False  # khách GỌI VÀO mà không ai nghe: ghi chú (KHÔNG phải liên hệ của sale) + hẹn gọi lại hôm nay (0178)


@m.command("record_call", resource="crm", title="Ghi cuộc gọi tổng đài thành chăm sóc", internal=True, agent="deny", family="call_log",
           why="Lượt đồng bộ của gói tổng đài: MỘT lần cho mỗi cuộc gọi mới đã kết thúc (bảng cuộc gọi chống lặp theo mã của nhà cung cấp), chỉ khi lead "
               "có TRƯỚC lúc gọi. Thêm một dòng crm.activities kind 'call' đúng giờ gọi, kết quả chuẩn, tên nhân viên đã gọi; đẩy last_activity_at "
               "nếu mới hơn; callback=true (gọi ra không nghe máy) ⇒ hẹn gọi lại hôm nay nếu lead chưa có hẹn nào sắp tới; missed_in=true (khách gọi vào chưa ai nghe) ⇒ ghi CHÚ (không phải liên hệ của sale, không đẩy last_activity_at) + cùng hẹn gọi lại. Không đổi chặng / người phụ trách. Trả {activity_id}."
               " [id: module gọi tự có — lead_id lấy từ crm.leads_for_phones theo SĐT trong nhật ký cuộc gọi, user_id là nhân viên khớp theo email"
               " của nhà cung cấp trong thành viên công ty]")
async def record_call(ctx: Ctx, inp: RecordCallIn) -> dict[str, Any]:
    if inp.missed_in:   # khách gọi mà sale chưa nghe không phải "sale đã liên hệ khách": ghi chú (không đẩy last_activity_at, không là bằng chứng liên hệ của chia lead) + nợ khách một cuộc gọi lại
        aid = await ctx.val("INSERT INTO crm.activities (lead_id, kind, outcome, note, by_user, at, data) SELECT id, 'note', '', %s, %s, %s, '{\"auto\": \"call_log\"}'::jsonb"
                            " FROM crm.leads WHERE id = %s RETURNING id", (inp.note, inp.user_id, inp.at, inp.lead_id))
    else:
        aid = await ctx.val("INSERT INTO crm.activities (lead_id, kind, outcome, note, by_user, at, data) SELECT id, 'call', %s, %s, %s, %s, '{\"auto\": \"call_log\"}'::jsonb"
                            " FROM crm.leads WHERE id = %s RETURNING id", (inp.outcome, inp.note, inp.user_id, inp.at, inp.lead_id))   # data.auto: dòng máy ghi, để biết sale CHƯA ghi gì thêm
    if aid is None:
        raise NotFound("Không thấy lead")
    if not inp.missed_in:
        await ctx.exec("UPDATE crm.leads SET last_activity_at = greatest(last_activity_at, %s), updated_at = now() WHERE id = %s", (inp.at, inp.lead_id))
    if inp.callback or inp.missed_in:   # cùng luật với crm.log_activity: không nghe máy / máy bận mà chưa hẹn ⇒ hẹn gọi lại hôm nay — sale không phải gõ để khỏi quên khách
        await ctx.exec("UPDATE crm.leads SET next_follow_up = core.today(), updated_at = now() WHERE id = %s AND stage IN ('lead', 'prospect')"
                       " AND (next_follow_up IS NULL OR next_follow_up < core.today())", (inp.lead_id,))
    return {"activity_id": aid}


class Since(Input):
    lead_id: UUID
    since: datetime


class CareIn(Input):
    leads: list[Since]


@m.query("call_targets", resource="crm", title="Khách của các cuộc gọi vừa xong + đã ghi chú chưa", internal=True, agent="deny")
async def call_targets(ctx: Ctx, inp: CareIn) -> dict[str, dict[str, Any]]:
    """Gọi bằng ``call_as`` (quyền người gọi — lead không thấy ⇒ không có trong kết quả) từ thẻ "cuộc gọi vừa xong" của gói tổng đài, lead lấy từ cuộc gọi của chính người gọi
    (0178). Mỗi lead trả {customer (tên khách), cared (đã có chăm sóc DO NGƯỜI ghi từ lúc gọi — ghi chú, hẹn, nhu cầu; dòng tổng đài tự ghi không tính)} —
    không SĐT, không nội dung ghi chú."""
    asks = {str(a.lead_id): a.since for a in inp.leads[:50]}
    rows = await ctx.all(
        """SELECT l.id, c.name, EXISTS (SELECT 1 FROM crm.activities a WHERE a.lead_id = l.id AND a.data->>'auto' IS NULL
                                          AND a.at >= q.since) AS cared
           FROM unnest(%s::uuid[], %s::timestamptz[]) AS q(lead_id, since)
           JOIN crm.leads l ON l.id = q.lead_id JOIN crm.customers c ON c.id = l.customer_id""",
        ([UUID(k) for k in asks], list(asks.values())))
    return {str(r["id"]): {"customer": r["name"], "cared": r["cared"]} for r in rows}
