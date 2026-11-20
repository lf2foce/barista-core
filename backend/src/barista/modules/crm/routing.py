"""Hợp đồng nội bộ cho chia lead (``leadflow``): đặt người phụ trách, tải từng người, tình trạng liên hệ, tóm tắt lead, nhận lead nguồn ngoài."""

from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from barista.api import Ctx, Input, Invalid, NoInput, field, parse_phone
from barista.modules.crm import _set_owner, _stage_event, interest_text, m
from barista.modules.crm.identity import _customer_of, _identifiers
from barista.modules.crm.leads import _campaign, _offering


class PlaceIn(Input):
    lead_id: UUID
    owner_id: UUID | None = None
    team_id: UUID | None = None   # chỉ dùng khi chưa có người nhận (đội thấy hàng chờ)
    reason: str = ""


@m.command("place", resource="crm", title="Đặt người phụ trách lead", internal=True, callers=("leadflow",),
           why="Chia lead (route / give / enforce_sla / drain): người nhận đã lọc là người làm được lead (vai đọc + ghi Khách hàng, đang hoạt "
               "động, không tạm ngừng); give / drain chỉ chạm lead người gọi thấy trong hàng chờ (RLS leadflow). Ghi owner / team của MỘT lead "
               "+ khách của lead (hàng chờ: chỉ đặt đội cho khách chưa có chủ) + lịch sử chặng. Trả {lead_id, owner_id}."
               " [id: người gọi thấy — lead_id từ hàng chờ người gọi thấy (give / drain) hoặc lead vừa chia (route / enforce_sla); owner_id đã lọc là người làm được lead]")
async def place(ctx: Ctx, inp: PlaceIn) -> dict[str, Any]:
    """Cho module chia lead: giao lead cho một người, hoặc đưa về hàng chờ (owner trống). Ghi lịch sử chặng như chuyển tay."""
    await _set_owner(ctx, inp.lead_id, inp.owner_id, team=inp.team_id, reason=inp.reason)
    return {"lead_id": inp.lead_id, "owner_id": inp.owner_id}


@m.query("workload", resource="crm", title="Số lead chưa gọi mỗi người", internal=True, callers=("leadflow",),
         why="Xếp người nhận lead theo trần / tải (_pick, gợi ý hàng chờ, danh sách người nhận): người chia không thấy hết lead của người nhận. "
             "Bên gọi không kiểm thêm gì ngoài quyền thao tác của chính nó. Trả CHỈ SỐ ĐẾM lead chặng Lead chưa chăm sóc theo người phụ "
             "trách ({owner_id: n}, mọi người trong công ty) — không tên khách, không SĐT.")
async def workload(ctx: Ctx, inp: NoInput) -> dict[str, int]:
    """Lead đang ở chặng Lead mà chưa ai chăm sóc — dùng cho trần lead và chia theo tải."""
    rows = await ctx.all("SELECT owner_id, count(*) AS n FROM crm.leads WHERE stage = 'lead' AND last_activity_at IS NULL"
                         " AND owner_id IS NOT NULL GROUP BY owner_id")
    return {str(r["owner_id"]): int(r["n"]) for r in rows}


class LeadIds(Input):
    lead_ids: list[UUID]


class ContactIn(Input):
    lead_ids: list[UUID]
    since: list[datetime | None] = field("Tính liên hệ từ lúc (cùng thứ tự lead_ids)", [])


@m.query("contact_info", resource="crm", title="Tình trạng liên hệ của lead", internal=True, callers=("leadflow",),
         why="enforce_sla (vòng quét hệ thống, không có người gọi): lead_ids lấy từ leadflow.lead_state đang chờ nhận / chờ gọi / đã liên hệ, since = "
             "lúc lead tới tay người đang giữ. Trả mỗi lead {id, stage, owner_id, campaign_id (để báo người phụ trách chiến dịch khi thu hồi — 0214), first_contact_at, last_contact_at, first_call_at, first_connected_at, "
             "first_meeting_at, first_message_at} — chỉ GIỜ của lần đầu từng loại liên hệ (luật chia chọn loại nào tính — 0152; chăm sóc nạp từ hệ khác "
             "chỉ tính lần đầu khi chính người phụ trách đã làm — 0174); không tên khách, "
             "không SĐT, không nội dung chăm sóc."
             " [id: module gọi tự có — lead_ids lấy từ leadflow.lead_state của vòng quét hệ thống, không do người dùng gõ]")
async def contact_info(ctx: Ctx, inp: ContactIn) -> dict[str, dict[str, Any]]:
    """Chặng, người phụ trách, lần liên hệ đầu (từ ``since``) theo TỪNG LOẠI + lần cuối (mọi lúc) — nguồn sự thật để chấm hạn xử lý. Ghi chú không
    phải liên hệ. Gọi: ghi tay hoặc tổng đài (``record_call``); nói chuyện được = cuộc gọi kết quả "nói chuyện được"; gặp = gặp / tham quan;
    nhắn = Zalo / email / gửi tài liệu. Chăm sóc NẠP TỪ HỆ KHÁC (0174, giờ gốc) là bằng chứng lần đầu chỉ khi CHÍNH người đang phụ trách đã làm
    (người khác / người không khớp thành viên thì không — không thành "người giữ vừa liên hệ"); lần cuối tính mọi chăm sóc."""
    since = [*inp.since, *([None] * (len(inp.lead_ids) - len(inp.since)))][: len(inp.lead_ids)]
    rows = await ctx.all(
        """SELECT l.id, l.stage, l.owner_id, l.campaign_id,
                  min(a.at) FILTER (WHERE a.mine) AS first_contact_at, max(a.at) AS last_contact_at,
                  min(a.at) FILTER (WHERE a.mine AND a.kind = 'call') AS first_call_at,
                  min(a.at) FILTER (WHERE a.mine AND a.kind = 'call' AND a.outcome = 'connected') AS first_connected_at,
                  min(a.at) FILTER (WHERE a.mine AND a.kind IN ('meet', 'visit')) AS first_meeting_at,
                  min(a.at) FILTER (WHERE a.mine AND a.kind IN ('zalo', 'email', 'send_profile')) AS first_message_at
           FROM (SELECT x.id, coalesce(x.since, '-infinity'::timestamptz) AS since FROM unnest(%s::uuid[], %s::timestamptz[]) AS x(id, since)) q
           JOIN crm.leads l ON l.id = q.id
           LEFT JOIN LATERAL (SELECT a.at, a.kind, a.outcome,
                                     a.at >= q.since AND (a.source = 'live' OR a.by_user IS NOT DISTINCT FROM l.owner_id) AS mine
                              FROM crm.activities a WHERE a.lead_id = l.id AND a.kind <> 'note') a ON true
           GROUP BY l.id, l.stage, l.owner_id, l.campaign_id""", (inp.lead_ids, since))
    return {str(r["id"]): r for r in rows}


class ReopenIn(Input):
    lead_id: UUID
    reason: str


@m.command("revive_lost", resource="crm", title="Mở lại lead đã mất (khách quay lại)", internal=True, agent="deny", callers=("leadflow",),
           why="leadflow.returned, khi khách đã có lead đã MẤT quay lại qua một kênh (cửa nạp lead của leadflow đã kiểm người nạp) và luật chia cho phép "
               "mở lại: đưa MỘT lead từ chặng Mất về Lead + lịch sử chặng. Không đổi người phụ trách (leadflow chia lại sau). Trả {lead_id, owner_id, "
               "reopened}."
               " [id: module gọi tự có — lead_id do crm.intake_lead trả về (khớp theo liên hệ khách trên toàn công ty), không do người dùng gõ]")
async def revive_lost(ctx: Ctx, inp: ReopenIn) -> dict[str, Any]:
    # lý do của lần mất gần nhất (danh mục + ghi chú) ở lại trên lead — mở lại không xoá "vì sao từng mất" (ADR 0246)
    owner = await ctx.one("UPDATE crm.leads SET stage = 'lead', next_follow_up = core.today(), updated_at = now()"
                          " WHERE id = %s AND stage = 'lost' RETURNING owner_id", (inp.lead_id,))
    if owner is None:
        return {"lead_id": inp.lead_id, "owner_id": None, "reopened": False}
    await _stage_event(ctx, inp.lead_id, "stage", "lost", "lead", inp.reason)
    return {"lead_id": inp.lead_id, "owner_id": owner["owner_id"], "reopened": True}


@m.query("lead_cards", resource="crm", title="Tóm tắt lead", internal=True, callers=("leadflow",),
         why="Hàng chờ / tổng quan chia lead / giao lead: lead_ids là lead người gọi THẤY qua RLS của leadflow.lead_state (người điều phối "
             "chưa chắc thấy khách bên crm). Trả mỗi lead {id, customer (tên khách), offering_code, source, interest, interest_text, created_at} "
             "— không SĐT."
             " [id: người gọi thấy — lead_ids là lead người gọi thấy qua RLS leadflow.lead_state (assign kiểm lead trong hàng chờ của mình trước khi hỏi)]")
async def lead_cards(ctx: Ctx, inp: LeadIds) -> dict[str, dict[str, Any]]:
    """Tên khách, [[offering|l]] quan tâm, nguồn, nhu cầu — cho hàng chờ chia lead (module chia lead chỉ đưa id mà NGƯỜI XEM đã thấy qua RLS của nó).
    Không trả SĐT: người điều phối chia lead, không gọi khách."""
    rows = await ctx.all(
        """SELECT l.id, c.name AS customer, l.offering_code, l.source, l.interest, l.created_at FROM crm.leads l
           JOIN crm.customers c ON c.id = l.customer_id WHERE l.id = ANY(%s)""", (inp.lead_ids,))
    return {str(r["id"]): {**r, "interest_text": interest_text(r["interest"])} for r in rows}


class IntakeLeadIn(Input):
    name: str
    phone: str | None = None
    email: str = ""
    contact_kind: str = ""
    contact_value: str = ""
    offering_code: str = ""
    source: str = "ads"
    campaign_code: str = ""
    ad_code: str = ""   # mẫu quảng cáo nguồn gửi kèm (0210)
    interest: dict[str, Any] = field("Nhu cầu", {})
    note: str = ""
    captured_at: datetime | None = None


@m.command("intake_lead", resource="crm", title="Nhận lead từ nguồn ngoài", internal=True, callers=("leadflow",),
           why="leadflow.intake, sau khi kiểm người nạp có quyền chia lead phạm vi đội trở lên (và chặn trùng mã bên ngoài): tra khách theo liên hệ "
               "trên TOÀN công ty (một SĐT = một khách; 0111), tạo khách + lead không người phụ trách (hoặc giữ chủ cũ). Ghi crm.customers / leads / "
               "lịch sử chặng. Trả {lead_id, party_id, created, keep_owner (người đang chăm khách cũ), offering_code, stage (chặng lead đã có — khách quay lại, 0152), plan_code (kế hoạch của chiến dịch, lead mới — 0204)} — không tên / SĐT khách cũ "
               "(party_id: người ứng với liên hệ BÊN GỌI gửi vào — bên gửi đã có liên hệ đó).")
async def intake_lead(ctx: Ctx, inp: IntakeLeadIn) -> dict[str, Any]:
    """Tạo khách + lead KHÔNG người phụ trách (module chia lead giao sau). Khách đã có ⇒ dùng lại; khách đã có lead cho sản phẩm
    này ⇒ không tạo thêm (``created`` = False)."""
    phone = parse_phone(inp.phone)
    contacts = _identifiers(phone, inp.email, inp.contact_kind, inp.contact_value)
    if not contacts:
        raise Invalid("Cần ít nhất một cách liên hệ: SĐT, email hoặc kênh khác")
    offering = await _offering(ctx, inp.offering_code)
    campaign = await _campaign(ctx, inp.campaign_code, inp.ad_code)
    party = await ctx.call_system("parties.ensure_person", name=inp.name, identifiers=contacts, source=inp.source)
    row = await _customer_of(ctx, party["party_id"], phone)
    if row is None:
        cid = await ctx.val("INSERT INTO crm.customers (name, phone, email, note, party_id) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                            (inp.name, phone, inp.email, inp.note, party["party_id"]))
        keep_owner = None
    else:
        cid, keep_owner = row["id"], row["owner_id"]
    pid = offering and offering["id"]
    code = offering["code"] if offering else ""
    existing = await ctx.one("""SELECT id, owner_id, stage FROM crm.leads WHERE customer_id = %s AND offering_id IS NOT DISTINCT FROM %s
                                AND (offering_id IS NOT NULL OR offering_code = %s)""", (cid, pid, code))
    if existing:
        return {"lead_id": existing["id"], "party_id": party["party_id"], "created": False, "keep_owner": existing["owner_id"],
                "offering_code": code or inp.offering_code, "stage": existing["stage"]}
    team = await ctx.home_team(keep_owner) if keep_owner else None
    lead_id = await ctx.val(
        """INSERT INTO crm.leads (customer_id, offering_id, offering_code, source, campaign_id, ad_id, interest, owner_id, team_id, next_follow_up, loaded_at,
                                  captured_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CASE WHEN %s THEN NULL ELSE core.today() END, CASE WHEN %s THEN now() END, %s) RETURNING id""",
        (cid, pid, offering["code"] if offering else "", inp.source, campaign and campaign["id"], campaign and campaign.get("ad_id"), Jsonb(inp.interest),
         keep_owner, team, ctx.historical, ctx.historical, inp.captured_at))   # nạp lịch sử (0185): lead tham chiếu — không hẹn "gọi lại hôm nay"
    await _stage_event(ctx, lead_id, "created", None, "lead", data={"source": inp.source})
    ctx.emit("crm.lead_created", lead_id=lead_id, campaign_id=campaign and campaign["id"], source=inp.source,
             offering_code=offering["code"] if offering else "")
    return {"lead_id": lead_id, "party_id": party["party_id"], "created": True, "keep_owner": keep_owner,
            "offering_code": offering["code"] if offering else "", "stage": "lead",
            "plan_code": (campaign or {}).get("plan_code") or ""}   # kế hoạch của chiến dịch (gói có chiến dịch trả — 0204) ⇒ leadflow chọn luật của kế hoạch


class DepartedIn(Input):
    user_ids: list[UUID] = field("Chỉ lead của những người này", [])
    skip_loaded: bool = field("Bỏ lead nạp từ hệ khác", False)


@m.query("departed_leads", resource="crm", title="Lead đang mở của người không còn làm được lead", internal=True, agent="deny", callers=("leadflow",),
         why="leadflow (vòng quét hệ thống; bên nghe khoá tài khoản; rời [[offering|l]]): lead chặng Lead / Tiềm năng mà người phụ trách KHÔNG còn là thành "
             "viên đang hoạt động hoặc vai đã mất quyền xem + ghi Khách hàng — để chuyển qua luật chia (0153), kể cả lead tự tạo chưa qua chia lead. Trả "
             "tối đa 200 dòng {lead_id, owner_id, offering_code, source, why} — không tên / SĐT khách. skip_loaded (vòng quét): bỏ lead nạp từ hệ khác "
             "(crm.leads.loaded_at — 0185: dữ liệu tham chiếu, vòng quét không tự chuyển; sự kiện khoá tài khoản thật thì vẫn chuyển)."
             " [id: module gọi tự có — user_ids lấy từ sự kiện khoá tài khoản của admin / thành viên vừa rời do quản lý đặt, không do người dùng gõ]")
async def departed_leads(ctx: Ctx, inp: DepartedIn) -> list[dict[str, Any]]:
    return await ctx.all(
        """SELECT l.id AS lead_id, l.owner_id, l.offering_code, l.source,
                  CASE WHEN mb.user_id IS NULL OR NOT mb.active THEN 'deactivated' ELSE 'lost_access' END AS why
           FROM crm.leads l LEFT JOIN core.memberships mb ON mb.user_id = l.owner_id AND mb.tenant_id = core.tenant()
           WHERE l.stage IN ('lead', 'prospect') AND l.owner_id IS NOT NULL
             AND (cardinality(%(u)s::uuid[]) = 0 OR l.owner_id = ANY(%(u)s::uuid[]))
             AND (NOT %(skip)s OR l.loaded_at IS NULL)
             AND (mb.user_id IS NULL OR NOT mb.active OR NOT EXISTS (SELECT 1 FROM core.grants g WHERE g.role = mb.role AND g.resource = 'crm'
                                                                      AND 'read' = ANY(g.actions) AND 'write' = ANY(g.actions)))
           ORDER BY l.updated_at LIMIT 200""", {"u": inp.user_ids, "skip": inp.skip_loaded})
