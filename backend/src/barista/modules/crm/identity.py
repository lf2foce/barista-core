"""Một SĐT = một khách trong công ty; khách gắn định danh ở ``parties`` (0111); ai thấy khách / lead (hỏi bằng quyền người đó — 0125); ⌘K."""

from typing import Any
from uuid import UUID

from psycopg import errors as pg

from barista.api import Conflict, Ctx, Event, Input, NotFound, Principal, SearchIn, SearchTerms, phone_display
from barista.modules.crm import STAGES, m, reveal


def _identifiers(phone: str | None, email: str, contact_kind: str, contact_value: str) -> list[dict[str, str]]:
    out = [{"kind": "phone", "value": phone}] if phone else []
    if email.strip():
        out.append({"kind": "email", "value": email})
    if contact_kind and contact_value.strip():
        out.append({"kind": contact_kind, "value": contact_value})
    return out


async def _customer_of(ctx: Ctx, party_id: UUID, phone: str | None, viewer: Principal | None = None) -> dict[str, Any] | None:
    """Khách của một người (mọi định danh đã gộp) hoặc theo SĐT — trong phạm vi người đang gọi thấy. Khách chưa có số mà lần này có ⇒ thành số chính.
    ``viewer``: chạy bằng quyền hệ thống (``link_deal``) thì phải nêu người nhập — khớp ra khách họ KHÔNG thấy ⇒ từ chối (``_not_yours``), không
    sửa gì trên khách đó (0125)."""
    ids = await ctx.call_system("parties.cluster", party_id=party_id)
    row = await ctx.one("SELECT id, owner_id, phone FROM crm.customers WHERE party_id = ANY(%s) OR phone = %s ORDER BY phone = %s DESC NULLS LAST, created_at LIMIT 1",
                        (ids, phone, phone))
    if row and viewer is not None and not await _seen(ctx, viewer, "crm.customer_seen", customer_id=row["id"]):
        raise _not_yours()
    if row and phone and row["phone"] is None:
        try:
            async with ctx.conn.transaction():
                await ctx.exec("UPDATE crm.customers SET phone = %s WHERE id = %s", (phone, row["id"]))
        except pg.UniqueViolation as e:
            raise Conflict("SĐT này đã là khách của người khác trong công ty — báo trưởng nhóm nếu cần chuyển khách") from e
    return row


class CustomerRef(Input):
    customer_id: UUID


def _not_yours() -> Conflict:
    """Khớp ra khách / lead của người khác mà người nhập không thấy: KHÔNG nêu tên người giữ (cùng lời với ``create_lead``)."""
    return Conflict("SĐT này đã là khách của người khác trong công ty — báo trưởng nhóm nếu cần chuyển khách")


async def _seen(ctx: Ctx, viewer: Principal, op: str, **inp: Any) -> bool:
    """Người ``viewer`` thấy đối tượng này không — hỏi bằng quyền và RLS CỦA HỌ (thiếu quyền Khách hàng ⇒ không thấy)."""
    got = await ctx.call_as_optional(op, viewer, **inp)
    return bool(got and got["seen"])


@m.query("customer_seen", resource="crm", title="Người gọi có thấy khách này không", internal=True, agent="deny")
async def customer_seen(ctx: Ctx, inp: CustomerRef) -> dict[str, Any]:
    """Chỉ chạy bằng ``call_as``: RLS của người gọi quyết định thấy hay không. Không trả tên / SĐT."""
    return {"seen": await ctx.val("SELECT true FROM crm.customers WHERE id = %s", (inp.customer_id,)) is True}


@m.query("customer_party", resource="crm", title="Định danh của một khách", internal=True, agent="deny", family="identity",
         why="Gói khai consumes(\"identity\") cần định danh (party) của khách trên đối tượng của nó — vd đề xuất người hưởng theo mẫu chia (0160): vai "
             "'người giới thiệu' đọc quan hệ giới thiệu của khách ở danh mục. Chỉ trả party_id — không tên, không SĐT, không người phụ trách. "
             "[id: module gọi tự có — customer_id đọc từ đối tượng của gói gọi (thương vụ của khoản đang chia), không do người dùng nêu]")
async def customer_party(ctx: Ctx, inp: CustomerRef) -> dict[str, Any]:
    return {"party_id": await ctx.val("SELECT party_id FROM crm.customers WHERE id = %s", (inp.customer_id,))}


class LeadSeenIn(Input):
    lead_id: UUID


@m.query("lead_seen", resource="crm", title="Người gọi có thấy lead này không", internal=True, agent="deny")
async def lead_seen(ctx: Ctx, inp: LeadSeenIn) -> dict[str, Any]:
    """Chỉ chạy bằng ``call_as``: RLS của người gọi quyết định. Không trả tên / SĐT."""
    return {"seen": await ctx.val("SELECT true FROM crm.leads WHERE id = %s", (inp.lead_id,)) is True}


async def _party_of_lead(ctx: Ctx, lead_id: UUID) -> dict[str, Any]:
    row = await ctx.one("SELECT c.id, c.party_id, c.phone, c.name FROM crm.leads l JOIN crm.customers c ON c.id = l.customer_id WHERE l.id = %s", (lead_id,))
    if row is None:
        raise NotFound("Không thấy lead")
    return row


@m.on("parties.party_renamed")
async def follow_party_name(ctx: Ctx, event: Event) -> None:
    """Danh mục là nơi sửa tên người (0111); khách hiện theo tên đó."""
    await ctx.exec("UPDATE crm.customers SET name = %s WHERE party_id = %s", (event.payload["name"], event.payload["party_id"]))


@m.search("search_customers", "Khách", resource="crm", rank=10, more=("crm.leads", {"stage": ""}))
async def search_customers(ctx: Ctx, inp: SearchIn) -> list[dict[str, Any]]:
    """⌘K (0087): tên (có dấu hay không, từng từ) / SĐT (chỉ khi gõ toàn số) — chỉ khách người tìm được xem (RLS); tên khớp đúng / khớp đầu lên trước."""
    t = SearchTerms(inp.q)
    rows = await ctx.all(
        f"""SELECT l.id, l.stage, l.offering_code, c.name, c.phone FROM crm.leads l JOIN crm.customers c ON c.id = l.customer_id
            WHERE {t.match("c.name", tel="c.phone")} ORDER BY {t.rank("c.name", tel="c.phone")}, l.updated_at DESC LIMIT %(_n)s""", t.params)
    await reveal.mask_rows(ctx, rows)   # 0214: số đã che thì ra dạng che; ``phone_display`` trả nguyên chuỗi không phải E.164
    return [{"title": r["name"], "subtitle": " · ".join(x for x in (phone_display(r["phone"] or ""), r["offering_code"] or "") if x), "badge": STAGES[r["stage"]],
             "tone": "accent", "open": {"query": "crm.journey", "input": {"lead_id": str(r["id"])}}} for r in rows]

