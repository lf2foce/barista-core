"""Hợp đồng nội bộ cho gói có thương vụ (họ ``lead_conversion`` — gói khai ``consumes("lead_conversion")``; 0186): gắn khách với giao dịch (khớp bằng quyền người nhập — 0125), đưa lead về tiềm năng khi giao dịch huỷ."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from psycopg import errors as pg

from barista.api import Conflict, Ctx, Denied, Input, NotFound, Principal, field, parse_phone
from barista.modules.crm import _set_owner, _stage_event, m
from barista.modules.crm.identity import _customer_of, _not_yours, _party_of_lead, _seen
from barista.modules.crm.leads import LeadRef


class LinkDeal(Input):
    name: str
    phone: str
    lead_id: UUID | None = None
    viewer_id: UUID   # người NHẬP giao dịch: khách / lead chỉ được khớp nếu CHÍNH HỌ thấy (0125)
    owner_id: UUID
    team_id: UUID | None
    offering_id: UUID
    offering_code: str
    history: bool = False   # 0162: hợp đồng LỊCH SỬ đã huỷ — chỉ tìm / tạo khách, KHÔNG đụng lead nào (không chuyển chặng, không đổi người phụ trách)


@m.command("link_deal", resource="crm", title="Gắn khách với giao dịch", internal=True, family="lead_conversion",
           why="Lệnh tạo thương vụ của gói khai consumes(\"lead_conversion\") (người nhập có quyền ghi thương vụ của gói; người bán là thành viên đang hoạt "
               "động): tìm khách theo SĐT "
               "CHỈ trong số khách người nhập thấy (đọc lại bằng quyền và RLS của họ qua crm.customer_seen / crm.lead_need; SĐT thuộc khách họ không "
               "thấy ⇒ từ chối, không nêu tên người giữ, không sửa gì); chưa có ⇒ tạo, chủ = sale; gắn / tạo lead theo sản phẩm, chuyển lead sang chặng "
               "Giao dịch — ghi crm.customers / leads / lịch sử chặng. RLS của bảng thương vụ ở gói gọi chặn người bán phạm vi hẹp nhập hộ người khác SAU lời gọi (cùng "
               "giao dịch ⇒ huỷ cả). Trả {customer_id, lead_id}."
               " [id: người gọi thấy — viewer_id là chính người nhập; lead_id đã đọc bằng quyền người nhập (crm.lead_need qua call_as); khách khớp theo "
               "SĐT / lead theo sản phẩm chỉ nhận khi viewer thấy (call_as); owner_id là sale do người nhập chọn nên RLS ghi giao dịch kiểm lại trong cùng giao dịch (0125)]")
async def link_deal(ctx: Ctx, inp: LinkDeal) -> dict[str, Any]:
    """Giao dịch nhập từ hợp đồng: tìm khách theo SĐT trong phạm vi NGƯỜI NHẬP thấy (chưa có ⇒ tạo, chủ = sale của giao dịch; có mà họ không thấy ⇒
    từ chối), gắn / tạo lead theo sản phẩm, chuyển lead sang chặng Giao dịch."""
    phone = parse_phone(inp.phone)  # chuẩn hoá giống nhập tay
    viewer = await ctx.principal_of(inp.viewer_id)
    if viewer is None:
        raise Denied("Người nhập giao dịch không còn hoạt động trong công ty")
    if inp.lead_id:   # giao dịch mở từ một lead: đúng khách đó, kể cả khách chưa có số (số vừa nhập thành số chính)
        return await _link_deal_from_lead(ctx, inp, phone, viewer)
    party = await ctx.call_system("parties.ensure_person", name=inp.name, identifiers=[{"kind": "phone", "value": phone}], source="crm")
    found = await _customer_of(ctx, party["party_id"], phone, viewer)
    if found:
        cid = found["id"]
    else:
        try:
            async with ctx.conn.transaction():
                cid = await ctx.val(
                    "INSERT INTO crm.customers (name, phone, owner_id, team_id, party_id) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    (inp.name, phone, inp.owner_id, inp.team_id, party["party_id"]))
        except pg.UniqueViolation as e:   # SĐT đã là khách (vừa tạo xong ở giao dịch khác): không nhận hộ
            raise _not_yours() from e
    if inp.history:
        return {"customer_id": cid, "lead_id": None}
    return await _lead_into_deal(ctx, cid, inp, viewer)


async def _link_deal_from_lead(ctx: Ctx, inp: LinkDeal, phone: str, viewer: Principal) -> dict[str, Any]:
    c = await _party_of_lead(ctx, inp.lead_id)   # type: ignore[arg-type]
    if not c["phone"]:
        got = await ctx.call_system("parties.attach_contact", party_id=c["party_id"], kind="phone", value=phone, source="crm")
        if got.get("conflict"):
            raise Conflict("SĐT này đang thuộc một người khác — đã ghi vào danh sách nghi trùng, kiểm tra trước khi tạo giao dịch")
        try:
            async with ctx.conn.transaction():
                await ctx.exec("UPDATE crm.customers SET phone = %s WHERE id = %s", (phone, c["id"]))
        except pg.UniqueViolation as e:
            raise Conflict("SĐT này đã là khách của người khác trong công ty — báo trưởng nhóm nếu cần chuyển khách") from e
    await ctx.exec("UPDATE crm.leads SET offering_id = %s, offering_code = %s WHERE id = %s AND offering_id IS NULL AND NOT EXISTS ("
                   "SELECT 1 FROM crm.leads x WHERE x.customer_id = %s AND x.offering_id = %s)", (inp.offering_id, inp.offering_code, inp.lead_id, c["id"], inp.offering_id))
    return await _lead_into_deal(ctx, c["id"], inp, viewer)


async def _lead_into_deal(ctx: Ctx, cid: UUID, inp: LinkDeal, viewer: Principal) -> dict[str, Any]:
    lid = await ctx.val("SELECT id FROM crm.leads WHERE customer_id = %s AND offering_id = %s", (cid, inp.offering_id))
    if lid is not None and not await _seen(ctx, viewer, "crm.lead_seen", lead_id=lid):   # lead của người khác (cùng khách, cùng sản phẩm)
        raise _not_yours()
    if lid is None:
        lid = await ctx.val(
            """INSERT INTO crm.leads (customer_id, offering_id, offering_code, stage, source, owner_id, team_id, loaded_at)
               VALUES (%s, %s, %s, 'deal', 'self', %s, %s, CASE WHEN %s THEN now() END) RETURNING id""",
            (cid, inp.offering_id, inp.offering_code, inp.owner_id, inp.team_id, ctx.historical))
        await _stage_event(ctx, lid, "created", None, "deal", data={"source": "deal"})
    else:
        # lead thành giao dịch ⇒ người phụ trách lead = người bán của giao dịch (0153: một câu trả lời cho "ai giữ khách này" từ lead tới giao dịch)
        cur = await ctx.val("SELECT owner_id FROM crm.leads WHERE id = %s", (lid,))
        if cur != inp.owner_id:
            await ctx.call_system("leadflow.follow_owner", lead_id=lid, owner_id=inp.owner_id, by=inp.viewer_id, closing=True)
            await _set_owner(ctx, lid, inp.owner_id, reason="Giao dịch: người bán phụ trách khách")
        before = await ctx.val("SELECT stage FROM crm.leads WHERE id = %s FOR UPDATE", (lid,))
        await ctx.exec("UPDATE crm.leads SET stage = 'deal', updated_at = now() WHERE id = %s", (lid,))
        if before != "deal":
            await _stage_event(ctx, lid, "stage", before, "deal", "Mở giao dịch")
    return {"customer_id": cid, "lead_id": lid}


@m.command("reopen_lead", resource="crm", title="Đưa lead về tiềm năng khi giao dịch huỷ", internal=True, agent="deny", family="lead_conversion",
           why="Bên nghe huỷ thương vụ của gói khai consumes(\"lead_conversion\"): lead_id lấy từ giao dịch vừa huỷ. Chuyển MỘT lead đang ở chặng Giao dịch về Tiềm năng, ghi lịch sử chặng."
               " [id: module gọi tự có — lead_id đọc từ giao dịch vừa huỷ của chính module gọi]")
async def reopen_lead(ctx: Ctx, inp: LeadRef) -> dict[str, Any]:
    n = await ctx.exec("UPDATE crm.leads SET stage = 'prospect', updated_at = now() WHERE id = %s AND stage = 'deal'", (inp.lead_id,))
    if n:
        await _stage_event(ctx, inp.lead_id, "stage", "deal", "prospect", "Giao dịch bị huỷ — khách quay lại tiềm năng")
    return {"lead_id": inp.lead_id, "reopened": bool(n)}


# ── KẾT QUẢ BÁN của một lead (0209): gói ghi kết quả bán (giao dịch KÝ, phí…) báo qua MỘT lệnh của crm ⇒ sự kiện trung lập ``crm.lead_outcome_recorded``.
# Lõi không nghe sự kiện của gói (tên gói); module nào cần "lead này vừa ký" (vd marketing gửi chuyển đổi về kênh quảng cáo) nghe sự kiện của crm.

class LeadOutcome(Input):
    lead_id: UUID
    outcome: Literal["won", "undone"] = field("Kết quả", "won", help="won = vừa có kết quả bán (vd ký hợp đồng); undone = kết quả đó bị huỷ")
    value_vnd: int = field("Giá trị (đồng)", 0, ge=0)
    ref: str = field("Mã kết quả của gói", "", max_length=80, help="Mã giao dịch của gói — cùng mã ⇒ cùng một kết quả (khử trùng)")
    occurred_at: datetime | None = None


@m.command("record_lead_outcome", resource="crm", title="Ghi kết quả bán của lead", internal=True, agent="deny", family="lead_conversion",
           why="Lệnh ký / huỷ thương vụ của gói khai consumes(\"lead_conversion\") (gói ghi kết quả bán — cùng gói cắm crm.lead_outcome): báo MỘT lead vừa có kết "
               "quả bán (hoặc kết quả đó bị huỷ) ⇒ crm phát sự kiện crm.lead_outcome_recorded {lead_id, outcome, value_vnd, ref, occurred_at}. Không sửa "
               "lead, không trả gì của khách. [id: module gọi tự có — lead_id đọc từ thương vụ vừa ký / huỷ của chính module gọi]")
async def record_lead_outcome(ctx: Ctx, inp: LeadOutcome) -> dict[str, Any]:
    if await ctx.val("SELECT 1 FROM crm.leads WHERE id = %s", (inp.lead_id,)) is None:
        raise NotFound("Không thấy lead")
    at = inp.occurred_at or await ctx.val("SELECT now()")
    ctx.emit("crm.lead_outcome_recorded", lead_id=inp.lead_id, outcome=inp.outcome, value_vnd=inp.value_vnd, ref=inp.ref.strip(), occurred_at=at)
    return {"lead_id": inp.lead_id, "outcome": inp.outcome}


class LeadId(Input):
    lead_id: UUID


@m.query("lead_ad_of", resource="crm", title="Chiến dịch / mẫu quảng cáo của một lead", internal=True, agent="deny", callers=("mkt",),
         why="Bên nghe crm.lead_outcome_recorded của mkt (quyền hệ thống, sau khi CÔNG TY bật gửi chuyển đổi về kênh quảng cáo): lead vừa có kết quả bán "
             "đến từ chiến dịch / mẫu quảng cáo nào. Trả {campaign_id, ad_id, captured_at} — mã nội bộ của mkt, không tên, không SĐT. "
             "[id: module gọi tự có — lead_id đọc từ sự kiện crm.lead_outcome_recorded của chính crm]")
async def lead_ad_of(ctx: Ctx, inp: LeadId) -> dict[str, Any] | None:
    return await ctx.one("SELECT campaign_id, ad_id, coalesce(captured_at, created_at) AS captured_at FROM crm.leads WHERE id = %s", (inp.lead_id,))


@m.query("lead_match_keys", resource="crm", title="Dấu băm liên hệ của lead (chuyển đổi quảng cáo)", internal=True, agent="deny", callers=("mkt",),
         why="Vòng gửi chuyển đổi về kênh quảng cáo của mkt (quyền hệ thống; chỉ chạy khi CÔNG TY bật gửi chuyển đổi trong chính sách quảng cáo — mặc định tắt): "
             "kênh khớp người mua bằng dấu băm SHA-256 của SĐT / email theo chuẩn chung. Trả CHỈ dấu băm {email_sha256 (email cắt khoảng trắng, chữ "
             "thường), phone_sha256 (SĐT E.164 chỉ chữ số, không dấu +), phone_e164_sha256 (SĐT E.164 có dấu +)} — '' khi thiếu; không bao giờ SĐT / "
             "email gốc. [id: module gọi tự có — lead_id lấy từ hàng chuyển đổi mkt tạo khi nghe crm.lead_outcome_recorded]")
async def lead_match_keys(ctx: Ctx, inp: LeadId) -> dict[str, str] | None:
    return await ctx.one(
        """SELECT CASE WHEN btrim(c.email) <> '' THEN encode(sha256(convert_to(lower(btrim(c.email)), 'UTF8')), 'hex') ELSE '' END AS email_sha256,
                  CASE WHEN c.phone LIKE '+%%' THEN encode(sha256(convert_to(substr(c.phone, 2), 'UTF8')), 'hex') ELSE '' END AS phone_sha256,
                  CASE WHEN c.phone LIKE '+%%' THEN encode(sha256(convert_to(c.phone, 'UTF8')), 'hex') ELSE '' END AS phone_e164_sha256
           FROM crm.leads l JOIN crm.customers c ON c.id = l.customer_id WHERE l.id = %s""", (inp.lead_id,))
