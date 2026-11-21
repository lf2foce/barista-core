"""Chia lead: ai nhận được (vai, đội / thành viên, tạm ngừng, nghỉ phép, công tắc chỉ thành viên), chọn người (xoay vòng / theo tải, hạng + trọng số),
giao + chụp hạn, hàng chờ, cửa nạp lead, khách quay lại, nhận lead, chuyển tay."""

from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from psycopg.types.json import Jsonb

from barista.api import (
    Conflict,
    Ctx,
    Day,
    Denied,
    Event,
    Hours,
    Input,
    Invalid,
    Money,
    NoInput,
    NotFound,
    OptionalPhone,
    add_working_minutes,
    field,
    today,
    ui,
    zone,
)
from barista.api.lead_labels import SOURCES
from barista.modules import leadflow as lf  # ``lf.clock()`` — test đặt đồng hồ ở MỘT chỗ
from barista.modules.leadflow import (
    DRAINABLE,
    EPOCH,
    HANDOVER,
    OPEN,
    PARKED_LABEL,
    PLANNED,
    QUEUE_REASON,
    SOURCE_LABEL,
    _lead_roles,
    _offering_code,
    _routing_on,
    _rule_for,
    _span,
    _team_id,
    _user_by_email,
    clock_of,
    due_in,
    duty_hours_of,
    hours_of,
    m,
    members_only,
    open_in,
    plan_code_of,
)

RECENT_DAYS = 30   # cửa sổ đếm lead đã nhận khi chia theo trọng số


def open_now(at: datetime, h: Hours) -> bool:
    """``at`` đang trong giờ (đồng hồ hạn đang chạy) — hạn 0 phút làm việc rơi đúng ``at`` chỉ khi đang mở."""
    return add_working_minutes(at, 0, h) == at


async def _lock(ctx: Ctx) -> None:
    """Một công ty chia lead MỘT LẦN MỘT (trần lead + xoay vòng đọc số liệu vừa ghi); khoá tự nhả khi giao dịch xong."""
    await ctx.exec("SELECT pg_advisory_xact_lock(hashtextextended('leadflow:' || core.tenant()::text, 0))")


async def away(ctx: Ctx, user_ids: list[UUID] | set[UUID]) -> dict[str, dict[str, Any]]:
    """Ai đang NGHỈ (đơn nghỉ đã duyệt phủ hôm nay) — hỏi gói cắm vào điểm ``leadflow.away`` (gói chấm công). Hợp đồng điểm cắm: đầu vào
    ``{user_ids: [uuid], on: ngày}``, trả ``{"<user_id>": {"until": "YYYY-MM-DD", "label": "Nghỉ phép"}}`` chỉ cho người đang nghỉ. Không gói nào cắm /
    gói tắt ⇒ {} (như hôm nay: chỉ có tạm ngừng tay)."""
    if not user_ids:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for part in await ctx.plugs("leadflow.away", system=True, user_ids=sorted(user_ids, key=str), on=today()):
        out.update({str(k): dict(v or {}) for k, v in (part or {}).items()})
    return out


async def _eligible(ctx: Ctx, rule: dict[str, Any], offering_code: str = "") -> list[UUID]:
    """Người nhận được: đang hoạt động, thuộc đội nhận (gồm đội con), vai nằm trong luật VÀ vai đó (vẫn) có quyền ghi Khách hàng, không tạm
    ngừng, không nghỉ phép. Luật "theo thành viên" hoặc công tắc chỉ thành viên ⇒ phải là thành viên đang tham gia của [[offering|l]] của lead.
    Quyền bị thu lại sau khi đặt luật ⇒ người đó tự rơi khỏi danh sách (không chia cho người không thấy lead)."""
    strict = rule.get("receivers") == "members"                               # luật chia theo thành viên: ai cũng phải là thành viên
    bound = not strict and bool(offering_code) and await members_only(ctx)    # công tắc: chỉ người phạm vi chia lead "của mình" (như RLS)
    rows = await ctx.all(
        """WITH RECURSIVE tree AS (
               SELECT id FROM core.teams WHERE id = %(team)s
               UNION ALL SELECT t.id FROM core.teams t JOIN tree ON t.parent_id = tree.id)
           SELECT mb.user_id FROM core.memberships mb
           WHERE mb.active AND mb.role = ANY(%(roles)s)
             AND EXISTS (SELECT 1 FROM core.grants g WHERE g.role = mb.role AND g.resource = 'crm'
                          AND 'read' = ANY(g.actions) AND 'write' = ANY(g.actions))
             AND (%(team)s::uuid IS NULL OR EXISTS (SELECT 1 FROM core.team_members tm WHERE tm.user_id = mb.user_id AND tm.team_id IN (SELECT id FROM tree)))
             AND NOT EXISTS (SELECT 1 FROM leadflow.receivers r WHERE r.user_id = mb.user_id AND r.paused_until > now())
             AND (NOT %(strict)s OR EXISTS (SELECT 1 FROM leadflow.members lm WHERE lm.user_id = mb.user_id AND lm.active
                                             AND CASE WHEN %(plan)s <> '' THEN lm.plan_code = %(plan)s   -- luật của kế hoạch: người THAM GIA kế hoạch (0204)
                                                      ELSE lm.plan_code = '' AND (%(off)s = '' OR lm.offering_code = %(off)s) END))
             AND (NOT %(bound)s OR NOT EXISTS (SELECT 1 FROM core.grants g WHERE g.role = mb.role AND g.resource = 'leadflow' AND g.scope = 'own')
                  OR EXISTS (SELECT 1 FROM leadflow.members lm WHERE lm.user_id = mb.user_id AND lm.active AND lm.offering_code = %(off)s))""",
        {"team": rule["team_id"], "roles": list(rule["roles"]), "strict": strict, "bound": bound, "off": offering_code, "plan": rule.get("plan_code") or ""})
    ids = [r["user_id"] for r in rows]
    gone = await away(ctx, ids)
    return [u for u in ids if str(u) not in gone]


async def _lead_capable(ctx: Ctx, offering_code: str = "") -> set[UUID]:
    """Mọi người làm được lead ngay lúc này (vai có quyền ghi Khách hàng, đang hoạt động, không tạm ngừng, không nghỉ phép; công tắc chỉ thành viên
    bật ⇒ thêm: thành viên của [[offering|l]] đó)."""
    need = bool(offering_code) and await members_only(ctx)
    rows = await ctx.all(
        """SELECT mb.user_id FROM core.memberships mb WHERE mb.active
             AND EXISTS (SELECT 1 FROM core.grants g WHERE g.role = mb.role AND g.resource = 'crm'
                          AND 'read' = ANY(g.actions) AND 'write' = ANY(g.actions))
             AND NOT EXISTS (SELECT 1 FROM leadflow.receivers r WHERE r.user_id = mb.user_id AND r.paused_until > now())
             AND (NOT %(need)s OR NOT EXISTS (SELECT 1 FROM core.grants g WHERE g.role = mb.role AND g.resource = 'leadflow' AND g.scope = 'own')
                  OR EXISTS (SELECT 1 FROM leadflow.members lm WHERE lm.user_id = mb.user_id AND lm.active AND lm.offering_code = %(off)s))""",
        {"need": need, "off": offering_code})
    ids = {r["user_id"] for r in rows}
    gone = await away(ctx, ids)
    return {u for u in ids if str(u) not in gone}


@dataclass
class Pick:
    user: UUID | None
    why: str                        # lý do không chọn được ai (no_eligible / cap)
    load: dict[str, int]
    last: dict[UUID, datetime]
    basis: dict[str, Any] = dc_field(default_factory=dict)   # VÌ SAO người này — ghi vào sự kiện "assigned", leadflow.explain đọc lại


async def _members_of(ctx: Ctx, offering_code: str, users: list[UUID], plan_code: str = "") -> dict[UUID, dict[str, int]]:
    """Hạng + trọng số: của người tham gia kế hoạch khi luật là của một kế hoạch (0204), không thì của thành viên [[offering|l]]."""
    rows = await ctx.all("""SELECT user_id, tier, weight FROM leadflow.members WHERE active AND user_id = ANY(%(u)s)
                              AND CASE WHEN %(plan)s <> '' THEN plan_code = %(plan)s ELSE plan_code = '' AND offering_code = %(off)s END""",
                         {"u": users, "plan": plan_code, "off": offering_code})
    return {r["user_id"]: {"tier": r["tier"], "weight": r["weight"]} for r in rows}


async def _recent(ctx: Ctx, offering_code: str, users: list[UUID]) -> dict[UUID, int]:
    """Số lead của [[offering|l]] này mỗi người đã nhận trong ``RECENT_DAYS`` ngày — mẫu số của chia theo trọng số."""
    rows = await ctx.all(
        """SELECT e.user_id, count(DISTINCT e.lead_id) AS n FROM leadflow.sla_events e JOIN leadflow.lead_state s ON s.lead_id = e.lead_id
           WHERE e.kind = 'assigned' AND e.at >= now() - make_interval(days => %s) AND s.offering_code = %s AND e.user_id = ANY(%s) GROUP BY e.user_id""",
        (RECENT_DAYS, offering_code, users))
    return {r["user_id"]: int(r["n"]) for r in rows}


async def _pick(ctx: Ctx, rule: dict[str, Any], offering_code: str = "", exclude: tuple[UUID, ...] = (), planned: tuple[UUID, ...] = ()) -> Pick:
    """MỘT cách tính cho cả chia tự động lẫn gợi ý người điều phối: bỏ người đã giữ lead, người chạm trần; luật theo thành viên ⇒ chỉ hạng cao
    nhất còn chỗ, trong hạng chia theo số lead đã nhận 30 ngày / trọng số (hoặc tải / trọng số); không thì xoay vòng (nhận lâu nhất trước) hoặc
    theo tải. ``planned``: người đã được GỢI Ý cho các lead trước trong cùng hàng chờ — tính như vừa nhận, để gợi ý rải đều."""
    cands = [u for u in await _eligible(ctx, rule, offering_code) if u not in exclude]
    if not cands:
        return Pick(None, "no_eligible", {}, {})
    load: dict[str, int] = dict(await ctx.call_system("crm.workload"))
    for u in planned:
        load[str(u)] = load.get(str(u), 0) + 1
    open_ones = [u for u in cands if not rule["cap_uncalled"] or load.get(str(u), 0) < rule["cap_uncalled"]]
    if not open_ones:
        return Pick(None, "cap", load, {})
    plan = rule.get("plan_code") or ""
    mem = await _members_of(ctx, offering_code, open_ones, plan) if rule.get("receivers") == "members" and (offering_code or plan) else {}
    tier = min((mem[u]["tier"] for u in open_ones if u in mem), default=None)
    if tier is not None:
        open_ones = [u for u in open_ones if u in mem and mem[u]["tier"] == tier]
    last = {r["user_id"]: r["at"] for r in await ctx.all(
        "SELECT user_id, max(at) AS at FROM leadflow.sla_events WHERE kind IN ('assigned', 'recalled') AND user_id = ANY(%s) GROUP BY user_id",
        (open_ones,))}
    soon = lf.clock()
    for i, u in enumerate(planned):   # gợi ý trước coi như vừa nhận (thứ tự giữ nguyên)
        last[u] = soon + timedelta(seconds=i + 1)
    least = rule["strategy"] == "least_loaded"
    recent: dict[UUID, int] = {}
    if tier is not None:
        recent = await _recent(ctx, offering_code, open_ones)
        for u in planned:
            recent[u] = recent.get(u, 0) + 1
        chosen = min(open_ones, key=lambda u: ((load.get(str(u), 0) if least else recent.get(u, 0)) / mem[u]["weight"], last.get(u, EPOCH), str(u)))
    else:
        chosen = min(open_ones, key=lambda u: (load.get(str(u), 0) if least else 0, last.get(u, EPOCH), str(u)))
    basis: dict[str, Any] = {"strategy": rule["strategy"], "candidates": len(cands), "open": len(open_ones), "uncalled": load.get(str(chosen), 0)}
    if tier is not None:
        basis |= {"tier": tier, "weight": mem[chosen]["weight"], "recent": recent.get(chosen, 0), "recent_days": RECENT_DAYS}
    if (seen := last.get(chosen)) is not None and seen <= soon:
        basis["last_at"] = seen.isoformat()
    return Pick(chosen, "", load, last, basis)


async def _via_members(ctx: Ctx, rule: dict[str, Any], offering_code: str, user: UUID) -> bool:
    """Người này được chọn VÌ là thành viên [[offering|l]]: luật chia theo thành viên, hoặc công tắc "chỉ thành viên" đang bật và người đó là thành
    viên — rời [[offering|l]] sau này ⇒ chuyển lead (0153). Luật của kế hoạch (0204) chia theo người tham gia ⇒ cũng là "vì thành viên"."""
    if rule.get("receivers") == "members" and (offering_code or rule.get("plan_code")):
        return True
    if not offering_code:
        return False
    return await members_only(ctx) and bool(await ctx.val(
        "SELECT 1 FROM leadflow.members WHERE user_id = %s AND offering_code = %s AND active", (user, offering_code)))


SNAPSHOT = ("first_call_min", "followup_hours", "max_recalls", "work_open_min", "work_close_min", "calendar", "min_talk_s", "remind_before_min",
            "escalate", "grace_min", "accept_min", "hide_contact", "off_hours", "duty_open_min", "duty_close_min", "receivers", "reopen_lost", "queue_escalate_min")


def _snapshot(rule: dict[str, Any]) -> dict[str, Any]:
    """Bản chụp luật trên lead lúc chia: hạn đã phát ra không đổi khi luật đổi."""
    return {"name": rule["name"], "effective_from": str(rule["effective_from"]), "work_days": list(rule["work_days"]),
            "duty_days": list(rule["duty_days"]), "contact_proof": list(rule["contact_proof"]), **{k: rule[k] for k in SNAPSHOT}}


async def _event(ctx: Ctx, lead_id: UUID, kind: str, user_id: UUID | None = None, data: dict[str, Any] | None = None) -> None:
    if ctx.historical:   # sổ việc Barista TỰ làm theo luật (xoay vòng, chấm điểm đọc sổ này) — lần nạp lịch sử không ghi vào (0185; lịch sử hệ cũ ở leadflow.history)
        return
    await ctx.exec("INSERT INTO leadflow.sla_events (lead_id, kind, user_id, data) VALUES (%s, %s, %s, %s)",
                   (lead_id, kind, user_id, Jsonb(data or {})))


async def _recalled(ctx: Ctx, holder: UUID | None, offering_code: str, why: str = "quá hạn gọi lần đầu") -> None:
    """Người bị thu hồi biết ngay (không mở được lead nữa — RLS — nên thông báo không có nút mở)."""
    await ctx.notify([holder], kind="lead_recalled", title=f"Lead bị thu hồi vì {why}{f' ({offering_code})' if offering_code else ''}",
                     push=f"Một lead của anh/chị bị thu hồi ({why})")


async def _queue(ctx: Ctx, lead_id: UUID, offering_code: str, source: str, team_id: UUID | None, why: str,
                 rule: dict[str, Any] | None, holders: tuple[UUID, ...] = (), recalls: int = 0, qualified_by: UUID | None = None) -> None:
    """Đưa lead về hàng chờ của ``team_id`` (đội đó thấy + chia; trống = hàng chờ cả công ty). Đã nằm hàng chờ rồi ⇒ im lặng (vòng quét thử lại
    không báo lặp). Lead nạp từ hệ khác đang ĐỖ (``imported_at`` — 0185) mà Barista đưa vào hàng chờ ⇒ thành lead sống (xoá dấu), tuổi hàng chờ tính từ bây giờ. Ghi trong một lần nạp lịch sử (``ctx.historical``) ⇒ dòng mang dấu đỗ. Hàng chờ có CHỦ (``queue_team_id``) và có TUỔI (``queued_at``): vòng quét báo chủ khi lead nằm quá hạn của luật (0153)."""
    if await ctx.val("SELECT status FROM leadflow.lead_state WHERE lead_id = %s FOR UPDATE", (lead_id,)) == "queued":
        return
    await ctx.call_system("crm.place", lead_id=lead_id, owner_id=None, team_id=team_id, reason=f"Vào hàng chờ: {QUEUE_REASON[why]}")
    await ctx.exec(
        """INSERT INTO leadflow.lead_state (lead_id, offering_code, source, status, holder_id, holders, rule_id, rule, recalls, queue_reason, qualified_by,
                                            queued_at, queue_team_id, imported_at)
           VALUES (%s, %s, %s, 'queued', NULL, %s, %s, %s, %s, %s, %s, %s, %s, CASE WHEN %s THEN now() END)
           ON CONFLICT (lead_id) DO UPDATE SET status = 'queued', holder_id = NULL, rule_id = EXCLUDED.rule_id, rule = EXCLUDED.rule,
               recalls = EXCLUDED.recalls, queue_reason = EXCLUDED.queue_reason, first_call_due = NULL, accept_due = NULL, remind_at = NULL,
               breached_at = NULL, recall_at = NULL, qualified_by = coalesce(EXCLUDED.qualified_by, lead_state.qualified_by), queued_at = EXCLUDED.queued_at,
               queue_team_id = EXCLUDED.queue_team_id, queue_escalated_at = NULL, queue_escalations = 0, via_team_id = NULL, via_members = false,
               imported_at = EXCLUDED.imported_at, updated_at = now()""",
        (lead_id, offering_code, source, list(holders), rule and rule["id"], Jsonb(_snapshot(rule) if rule else {}), recalls, why, qualified_by,
         lf.clock(), team_id, ctx.historical))
    await _event(ctx, lead_id, "queued", data={"reason": why})
    await ctx.notify_team(team_id, kind="lead_queue", title=f"Lead chờ chia{f' ({offering_code})' if offering_code else ''}", body=QUEUE_REASON[why],
                          push="Có lead mới chờ đội chia", open={"query": "leadflow.queue", "input": {}})


async def _assign_to(ctx: Ctx, lead_id: UUID, offering_code: str, source: str, chosen: UUID, rule: dict[str, Any] | None, *,
                     holders: tuple[UUID, ...] = (), recalls: int = 0, reason: str = "Chia tự động",
                     data: dict[str, Any] | None = None, duty: bool = False, qualified_by: UUID | None = None,
                     via_team: UUID | None = None, via_members: bool = False, place: bool = True) -> UUID:
    """Giao lead + CHỤP hạn theo luật (hạn bấm nhận, nhắc, hạn gọi — tính từ BÂY GIỜ trong giờ của người nhận: giao lúc đêm ⇒ hạn đủ phút từ giờ mở
    cửa, không ai nhận lead "đã trễ"; lead đội trực ⇒ giờ trực ∪ giờ làm việc). Không có luật ⇒ giao nhưng không có hạn.
    ``place=False``: người phụ trách bên Khách đã đổi trong cùng giao dịch (``crm.assign`` → ``follow_owner``) — chỉ ghi phần chia lead.
    ``via_team`` / ``via_members``: người này được chọn VÌ thuộc đội / là thành viên — rời đội / rời [[offering|l]] ⇒ chuyển lead (0153).
    Lead nạp từ hệ khác đang ĐỖ (``imported_at`` — 0185) ⇒ từ đây là lead SỐNG theo luật Barista (xoá dấu), hạn tính từ bây giờ. Ngược lại,
    chia trong một lần nạp lịch sử (``ctx.historical`` — vd nạp lead cũ qua cửa nạp lead) ⇒ dòng mang dấu đỗ ngay: mọi trạng thái chia ghi lúc nạp là tham chiếu."""
    now = lf.clock()
    if place:
        await ctx.call_system("crm.place", lead_id=lead_id, owner_id=chosen, reason=reason)
    due = remind = accept = None
    quiet = False
    if rule:
        hs = clock_of(rule, duty)
        due = due_in(now, rule["first_call_min"], hs)
        if 0 < rule["remind_before_min"] < rule["first_call_min"]:
            remind = due_in(now, rule["first_call_min"] - rule["remind_before_min"], hs)
        if rule["accept_min"]:
            accept = due_in(now, min(rule["accept_min"], rule["first_call_min"]), hs)
        quiet = not open_in(now, hs)   # ngoài giờ của người nhận: báo trong app, không rung máy — hạn tính từ giờ mở cửa
    status = ("offered" if accept else "waiting") if rule else "closed"
    await ctx.exec(
        """INSERT INTO leadflow.lead_state (lead_id, offering_code, source, status, holder_id, holders, rule_id, rule, assigned_at,
                                            first_call_due, recalls, queue_reason, accept_due, remind_at, duty, qualified_by, via_team_id, via_members,
                                            imported_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '', %s, %s, %s, %s, %s, %s, CASE WHEN %s THEN now() END)
           ON CONFLICT (lead_id) DO UPDATE SET status = EXCLUDED.status, holder_id = EXCLUDED.holder_id, holders = EXCLUDED.holders,
               rule_id = EXCLUDED.rule_id, rule = EXCLUDED.rule, assigned_at = EXCLUDED.assigned_at, first_call_due = EXCLUDED.first_call_due,
               first_call_at = NULL, first_call_ok = NULL, recalls = EXCLUDED.recalls, queue_reason = '', accept_due = EXCLUDED.accept_due,
               accepted_at = NULL, remind_at = EXCLUDED.remind_at, reminded_at = NULL, breached_at = NULL, recall_at = NULL, duty = EXCLUDED.duty,
               qualified_by = coalesce(EXCLUDED.qualified_by, lead_state.qualified_by), via_team_id = EXCLUDED.via_team_id,
               via_members = EXCLUDED.via_members, queued_at = NULL, queue_team_id = NULL, queue_escalated_at = NULL, queue_escalations = 0,
               imported_at = EXCLUDED.imported_at, updated_at = now()""",
        (lead_id, offering_code, source, status, chosen, list(dict.fromkeys([*holders, chosen])), rule and rule["id"],
         Jsonb(_snapshot(rule) if rule else {}), now, due, recalls, accept, remind, duty, qualified_by, via_team, via_members, ctx.historical))
    await _event(ctx, lead_id, "assigned", chosen, {"rule": rule["name"] if rule else None, "due": due.isoformat() if due else None,
                                                    **({"accept_due": accept.isoformat()} if accept else {}), **({"duty": True} if duty else {}),
                                                    "reason": reason, **(data or {})})
    if accept:
        title = f"Lead mới{f' ({offering_code})' if offering_code else ''} — bấm Nhận trước {accept.astimezone(zone()):%H:%M %d/%m}"
        push, opened = f"Anh/chị có lead mới — bấm Nhận trước {accept.astimezone(zone()):%H:%M}", {"query": "leadflow.mine", "input": {}}
    else:
        when = f" — gọi trước {due.astimezone(zone()):%H:%M %d/%m}" if due else ""
        title, push = f"Lead mới{f' ({offering_code})' if offering_code else ''}{when}", f"Anh/chị có lead mới{when}"
        opened = {"query": "crm.journey", "input": {"lead_id": str(lead_id)}}
    await ctx.notify([chosen], kind="lead", title=title, body=reason, push="" if quiet else push, open=opened)
    return chosen


async def _distribute(ctx: Ctx, lead_id: UUID, offering_code: str, source: str, *, keep_owner: UUID | None = None,
                      to_team: UUID | None = None, exclude: tuple[UUID, ...] = (), holders: tuple[UUID, ...] = (), recalls: int = 0,
                      reason: str = "Chia tự động", qualified_by: UUID | None = None, data: dict[str, Any] | None = None,
                      plan_code: str = "") -> UUID | None:
    """Chia (hoặc chia lại) MỘT lead. Thứ tự: giữ người cũ / người được chỉ định → đội được chỉ định (hàng chờ của đội đó) → luật
    (điều phối ⇒ hàng chờ đội điều phối; ngoài giờ + luật có đội trực + đang giờ trực ⇒ đội trực; tự chia ⇒ chọn người). Trả người nhận, hoặc
    None nếu vào hàng chờ."""
    await _lock(ctx)
    rule = await _rule_for(ctx, offering_code, source, today(), plan_code)
    extra = dict(data or {})
    if keep_owner and keep_owner not in await _lead_capable(ctx, offering_code):
        keep_owner = None   # người chăm khách cũ đã nghỉ / bị khoá / tạm ngừng / nghỉ phép / không thuộc [[offering|l]] ⇒ đi theo luật như khách mới
    if to_team is None and not await _routing_on(ctx, offering_code):
        # [[offering|l]] tạm ngừng chia: cả khách cũ quay lại cũng chờ người quyết; người chỉ định đội thì vẫn theo người
        await _queue(ctx, lead_id, offering_code, source, None, "paused", rule, holders, recalls, qualified_by)
        return None
    if keep_owner:
        return await _assign_to(ctx, lead_id, offering_code, source, keep_owner, rule, holders=holders, recalls=recalls,
                                reason=reason, data={"kept_owner": True, **extra}, qualified_by=qualified_by)
    if to_team:
        await _queue(ctx, lead_id, offering_code, source, to_team, "team", rule, holders, recalls, qualified_by)
        return None
    if rule is None:
        await _queue(ctx, lead_id, offering_code, source, None, "no_rule", None, holders, recalls, qualified_by)
        return None
    if rule["mode"] == "coordinator":
        await _queue(ctx, lead_id, offering_code, source, rule["coord_team_id"], "coordinator", rule, holders, recalls, qualified_by)
        return None
    now = lf.clock()
    if rule["off_hours"] == "duty" and not open_now(now, hours_of(rule)) and open_now(now, duty_hours_of(rule)):
        pick = await _pick(ctx, {**rule, "team_id": rule["duty_team_id"], "receivers": "team"}, offering_code, exclude)
        if pick.user:   # đội trực không còn ai (nghỉ, chạm trần) ⇒ chia như giờ thường, hạn bắt đầu từ giờ mở cửa
            return await _assign_to(ctx, lead_id, offering_code, source, pick.user, rule, holders=holders, recalls=recalls,
                                    reason=f"{reason} (đội trực ngoài giờ)", data={**pick.basis, **extra}, duty=True, qualified_by=qualified_by,
                                    via_team=rule["duty_team_id"])
    pick = await _pick(ctx, rule, offering_code, exclude)
    if pick.user is None:
        await _queue(ctx, lead_id, offering_code, source, rule["team_id"], pick.why, rule, holders, recalls, qualified_by)
        return None
    return await _assign_to(ctx, lead_id, offering_code, source, pick.user, rule, holders=holders, recalls=recalls, reason=reason,
                            data={**pick.basis, **extra}, qualified_by=qualified_by, via_team=rule["team_id"],
                            via_members=await _via_members(ctx, rule, offering_code, pick.user))


class RouteIn(Input):
    lead_id: UUID
    offering_code: str = ""
    source: str = ""
    keep_owner: UUID | None = None
    to_team: UUID | None = None
    reason: str = "Chia tự động"
    qualified_by: UUID | None = None
    returning: bool = False
    plan_code: str = ""   # kế hoạch của chiến dịch lead (0204) — ghi vào lead_state để mọi lần chia lại theo đúng luật


@m.command("route", resource="leadflow", title="Chia một lead theo luật", internal=True, callers=("leadflow",),
           why="intake, sau khi kiểm người nạp có quyền chia lead phạm vi đội trở lên, người được chỉ định nhận được lead, lead mới thật (không "
               "trùng): chia MỘT lead theo luật — ghi leadflow.lead_state / sla_events, đặt người phụ trách qua crm.place, báo người nhận / đội. "
               "Lead thường rời phạm vi người nạp nên không ghi được bằng quyền họ. Trả {assigned_to}."
               " [id: module gọi tự có — lead_id là lead vừa nạp, người nhận đã kiểm trước, qualified_by là thành viên đang hoạt động tra theo email]")
async def route(ctx: Ctx, inp: RouteIn) -> dict[str, Any]:
    """Ghi sổ chia lead bằng quyền HỆ THỐNG. Lệnh công khai (nạp lead) đã kiểm người gọi được nạp; sau khi giao, lead thường rời
    phạm vi của người nạp (vd team Digital giao cho sale đội khác) nên phần ghi hạn / lịch sử không thể chạy bằng quyền họ."""
    plan = plan_code_of(inp.plan_code)
    chosen = await _distribute(ctx, inp.lead_id, inp.offering_code, inp.source, keep_owner=inp.keep_owner, to_team=inp.to_team, reason=inp.reason,
                               qualified_by=inp.qualified_by, data={"returning_customer": True} if inp.returning else None, plan_code=plan)
    if plan:   # _queue / _assign_to vừa tạo dòng trạng thái (cùng giao dịch) ⇒ ghi kế hoạch để vòng quét chia lại / tính hạn theo đúng luật
        await ctx.exec("UPDATE leadflow.lead_state SET plan_code = %s WHERE lead_id = %s", (plan, inp.lead_id))
    return {"assigned_to": chosen}


class GiveIn(Input):
    lead_id: UUID
    owner_id: UUID
    reason: str
    by: UUID | None = None


@m.command("give", resource="leadflow", title="Giao một lead đang chờ", internal=True, callers=("leadflow",),
           why="assign, sau khi kiểm lead nằm trong hàng chờ NGƯỜI GỌI thấy (RLS leadflow.lead_state) và người nhận làm được lead: giao MỘT lead "
               "đang chờ — ghi lead_state / sla_events, đặt người phụ trách qua crm.place, báo người nhận. Sau khi giao lead rời hàng chờ của "
               "người điều phối nên không ghi được bằng quyền họ. Trả {assigned_to}."
               " [id: người gọi thấy — lead_id nằm trong hàng chờ người gọi thấy (RLS) và owner_id đã lọc là người nhận lead được]")
async def give(ctx: Ctx, inp: GiveIn) -> dict[str, Any]:
    st = await ctx.one("SELECT * FROM leadflow.lead_state WHERE lead_id = %s AND status = 'queued' FOR UPDATE", (inp.lead_id,))
    if st is None:
        raise NotFound("Lead không còn trong hàng chờ")
    await _lock(ctx)
    rule = await _rule_for(ctx, st["offering_code"], st["source"], today(), st.get("plan_code") or "")
    await _assign_to(ctx, inp.lead_id, st["offering_code"], st["source"], inp.owner_id, rule, holders=tuple(st["holders"]),
                     recalls=st["recalls"], reason=inp.reason, data={"by": str(inp.by) if inp.by else None})
    return {"assigned_to": inp.owner_id}


# ── Cửa vào ──

class Intake(Input):
    name: str = field("Họ tên khách", example="Nguyễn Thị Lan", max_length=120)
    phone: OptionalPhone = field("SĐT", None, example="0100000123", help="Không có số thì điền email hoặc kênh khác")
    offering_code: str = field("[[offering]] quan tâm", "", options_query="crm.offering_options", example="HSR")
    source: Literal["ads", "telesales", "self", "referral", "event", "import"] = field("Nguồn", "ads", options=SOURCES)
    note: str = field("Ghi chú", "", kind="textarea", max_length=4000)
    # rà form nhóm B 18/11/2026: 18 ô ⇒ 5 ô; chiến dịch / mẫu quảng cáo chọn theo [[offering|l]]; giao lead, mã nhà cung cấp, liên hệ phụ gấp lại
    campaign_code: str = field("Chiến dịch", "", example="FB-HSR-T10", group="Lead từ quảng cáo", aliases=("Mã chiến dịch",),
                               options_query="mkt.campaign_options", options_with=("offering_code",))
    ad_code: str = field("Mẫu quảng cáo", "", max_length=40, example="120201234567890", group="Lead từ quảng cáo", aliases=("Mã mẫu quảng cáo",),
                         help="Mẫu quảng cáo khách bấm (vd ad id của Meta) — để biết mẫu nào ra khách thật. Mã lạ không chặn lead",
                         options_query="mkt.ad_options", options_with=("campaign_code",))
    team_code: str = field("Giao cho đội", "", options_query="admin.team_options", group="Giao lead",
                           help="Lead về hàng chờ của đội này để đội tự chia. Trống = theo luật chia")
    owner_email: str = field("Giao thẳng cho", "", options_query="leadflow.receiver_options", help="Trống = theo luật chia", group="Giao lead")
    qualified_by_email: str = field("Người đã lọc lead", "", options_query="admin.member_options", group="Giao lead",
                                    help="Telesale / digital đã gọi lọc khách này (ghi công, tính KPI sau) — trống nếu không có")
    email: str = field("Email", "", kind="email", max_length=200, group="Thêm chi tiết")
    contact_kind: Literal["", "zalo", "facebook", "other"] = field("Kênh liên hệ khác", "", group="Thêm chi tiết",
                                                                    options={"": "—", "zalo": "Zalo", "facebook": "Facebook", "other": "Khác"})
    contact_value: str = field("Mã trên kênh đó", "", max_length=200, help="Mã / số của khách trên kênh, không dùng tên hiển thị", group="Thêm chi tiết")
    budget_vnd: Money | None = field("Ngân sách", None, ge=0, group="Thêm chi tiết")
    wishes: str = field("Mong muốn", "", max_length=300, group="Thêm chi tiết")
    captured_on: Day | None = field("Ngày khách để lại thông tin", None, help="Lead nhập muộn: ghi ngày thật", group="Thêm chi tiết", placeholder="Hôm nay")
    provider: str = field("Nhà cung cấp", "", help="meta, web, callio… — cùng mã ngoài chỉ vào hệ một lần", max_length=40, group="Nguồn tự động (kỹ thuật)")
    external_ref: str = field("Mã bên ngoài", "", help="vd leadgen_id của Meta", max_length=200, group="Nguồn tự động (kỹ thuật)")


@m.command("intake",
           purpose="Nạp lead mới từ quảng cáo, biểu mẫu hoặc nhà cung cấp và chia cho người gọi, để khách được liên hệ nhanh.", irreversible=True, effect="Lead được tạo và chia cho người nhận ngay, người nhận được báo; không xoá được, chỉ chuyển sang chặng Mất.",
           resource="leadflow", title="Nạp lead", importable=True, agent="deny", apps=True)   # app nguồn lead (0140) — trợ lý thì không
async def intake(ctx: Ctx, inp: Intake) -> dict[str, Any]:
    """Ai nạp được: người có quyền chia lead phạm vi đội trở lên (team digital / marketing được cấp, trưởng nhóm, quản lý).
    Khách cũ đã có người chăm ⇒ giữ người đó (một SĐT = một khách), kể cả khi người nạp chỉ định khác. Khách đã có lead [[offering|l]] này quay
    lại ⇒ không tạo trùng, nhưng là SỰ KIỆN: báo người đang chăm, lead đã mất mở lại theo luật (``returned``)."""
    if not ctx.p.has_scope("leadflow", "team"):
        raise Denied("Nạp lead cần quyền chia lead phạm vi đội trở lên")
    if bool(inp.provider) != bool(inp.external_ref):
        raise Invalid("Nhà cung cấp và mã bên ngoài phải đi cùng nhau")
    if inp.team_code and inp.owner_email:
        raise Invalid("Chỉ chọn một: giao cho đội HOẶC giao thẳng cho người")
    owner = await _user_by_email(ctx, inp.owner_email) if inp.owner_email else None
    qualified = await _user_by_email(ctx, inp.qualified_by_email) if inp.qualified_by_email else None
    if owner and owner not in await _lead_capable(ctx, await _offering_code(ctx, inp.offering_code)):
        raise Invalid(f"{inp.owner_email} không nhận lead được (đã khoá, đang tạm ngừng / nghỉ, vai chưa có quyền xem + ghi Khách hàng, "
                      "hoặc không là thành viên [[offering|l]] này)")
    if ctx.p.via == "app" and not inp.provider:
        raise Invalid("Ứng dụng nạp lead phải gửi nhà cung cấp + mã bên ngoài (chống lặp khi gửi lại — 0140)")
    team = await _team_id(ctx, inp.team_code)
    # app từ xa (0140, review 08/11): mã ngoài thuộc RIÊNG app gửi — app khác gửi cùng (nhà cung cấp, mã) không lấy được lead của nguồn kia
    provider = f"{inp.provider}@app-{ctx.p.app_id}" if ctx.p.app_id and inp.provider else inp.provider
    if provider:
        # cùng (nhà cung cấp, mã ngoài) tới cùng lúc (hai lần gửi lại song song, khác khoá chống lặp) ⇒ lần sau chờ lần trước rồi thấy mã đã có
        await ctx.exec("SELECT pg_advisory_xact_lock(hashtextextended('leadflow-ext:' || core.tenant()::text || %s || ':' || %s, 0))",
                       (provider, inp.external_ref))
        prior = await ctx.one("SELECT lead_id, party_id FROM leadflow.external_refs WHERE provider = %s AND ref = %s", (provider, inp.external_ref))
        if prior:
            return await _answer(ctx, prior["lead_id"], prior["party_id"], duplicate=True, assigned_to=None, own=True)
    interest = {k: v for k, v in {"budget_vnd": inp.budget_vnd, "wishes": inp.wishes.strip()}.items() if v not in (None, "")}
    captured = datetime.combine(inp.captured_on, datetime.min.time().replace(hour=12), tzinfo=zone()) if inp.captured_on else None
    got = await ctx.call_system("crm.intake_lead", name=inp.name, phone=inp.phone, email=inp.email, contact_kind=inp.contact_kind, contact_value=inp.contact_value, offering_code=inp.offering_code, source=inp.source,
                                campaign_code=inp.campaign_code, ad_code=inp.ad_code, interest=interest, note=inp.note, captured_at=captured)
    if provider:
        await ctx.exec("INSERT INTO leadflow.external_refs (provider, ref, lead_id, party_id) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                       (provider, inp.external_ref, got["lead_id"], got["party_id"]))
    if not got["created"]:   # khách đã có lead [[offering|l]] này — không tạo trùng, không chia lại; KHÔNG nuốt im: báo / mở lại theo luật
        await ctx.call_system("leadflow.returned", lead_id=got["lead_id"], source=inp.source, campaign_code=inp.campaign_code,
                              provider=inp.provider, qualified_by=qualified)
        return await _answer(ctx, got["lead_id"], got["party_id"], duplicate=True, assigned_to=None)
    old = got["keep_owner"]
    routed = await ctx.call_system("leadflow.route", lead_id=got["lead_id"], offering_code=got["offering_code"], source=inp.source,
                                   plan_code=got.get("plan_code") or "",
                                   keep_owner=old or owner, to_team=team, qualified_by=qualified, returning=bool(old),
                                   reason="Khách cũ quay lại — giữ người đang chăm" if old else ("Người nạp chỉ định" if owner else "Chia tự động"))
    return await _answer(ctx, got["lead_id"], got["party_id"], duplicate=False, assigned_to=routed["assigned_to"])


async def _answer(ctx: Ctx, lead_id: UUID, party_id: UUID | None, *, duplicate: bool, assigned_to: UUID | None,
                  own: bool = False) -> dict[str, Any]:
    """Kết quả nạp lead. App từ xa (review 08/11): chỉ nhận mã lead / người khi XEM được lead đó bằng quyền của nó (khách đã có của người khác thì
    không lộ mã) — không xem được ⇒ câu trả lời trung tính "đã nhận" (mã ngoài vẫn được ghi để chống lặp)."""
    if ctx.p.via == "app":
        # ``duplicate`` cho app chỉ nói "CHÍNH app đã gửi mã ngoài này" — không nói SĐT / email đã thuộc một khách có sẵn (re-review 08/11)
        try:
            await ctx.call("crm.lead_status", lead_id=lead_id)
        except (NotFound, Denied):
            return {"lead_id": None, "party_id": None, "duplicate": own, "assigned_to": None, "accepted": True}
        return {"lead_id": lead_id, "party_id": party_id, "duplicate": own, "assigned_to": assigned_to}
    return {"lead_id": lead_id, "party_id": party_id, "duplicate": duplicate, "assigned_to": assigned_to}


class ReturnedIn(Input):
    lead_id: UUID
    source: str = ""
    campaign_code: str = ""
    provider: str = ""
    qualified_by: UUID | None = None


@m.command("returned", resource="leadflow", title="Khách quay lại qua kênh khác", internal=True, agent="deny", callers=("leadflow",),
           why="intake, sau khi kiểm người nạp có quyền chia lead phạm vi đội trở lên và crm.intake_lead báo khách ĐÃ có lead [[offering|l]] này: ghi sự "
               "kiện 'returned' (nguồn, chiến dịch) lên lead, báo người đang chăm, lead đã MẤT thì mở lại (crm.revive_lost) và chia lại (giữ người cũ nếu "
               "còn nhận được) theo luật. Lead của người khác — người nạp không thấy nên không ghi được bằng quyền họ. Trả {reopened, assigned_to}."
               " [id: module gọi tự có — lead_id do crm.intake_lead trả về theo liên hệ khách trên toàn công ty, qualified_by là thành viên đang hoạt"
               " động tra theo email]")
async def returned(ctx: Ctx, inp: ReturnedIn) -> dict[str, Any]:
    lid = inp.lead_id
    info = (await ctx.call_system("crm.contact_info", lead_ids=[lid])).get(str(lid))
    if info is None:
        raise NotFound("Không thấy lead")
    st = await ctx.one("SELECT offering_code, source, plan_code FROM leadflow.lead_state WHERE lead_id = %s FOR UPDATE", (lid,))
    card = (await ctx.call_system("crm.lead_cards", lead_ids=[lid])).get(str(lid), {})
    offering = (st or {}).get("offering_code") or card.get("offering_code") or ""
    channel = SOURCE_LABEL.get(inp.source, inp.source or "kênh khác") + (f" · chiến dịch {inp.campaign_code}" if inp.campaign_code else "")
    owner = info["owner_id"]
    data = {"source": inp.source, "campaign": inp.campaign_code, "provider": inp.provider, "stage": info["stage"]}
    await _event(ctx, lid, "returned", owner, data)
    await ctx.exec("UPDATE leadflow.lead_state SET returns = returns + 1, returned_at = now(), qualified_by = coalesce(qualified_by, %s),"
                   " updated_at = now() WHERE lead_id = %s", (inp.qualified_by, lid))
    if info["stage"] == "lost":
        rule = await _rule_for(ctx, offering, inp.source, today(), (st or {}).get("plan_code") or "")
        if rule is None or rule["reopen_lost"]:
            got = await ctx.call_system("crm.revive_lost", lead_id=lid, reason=f"Khách quay lại qua {channel}")
            if got["reopened"]:
                await _event(ctx, lid, "reopened", owner, data)
                to = await _distribute(ctx, lid, offering, inp.source, keep_owner=owner, holders=(), reason=f"Khách quay lại qua {channel}",
                                       qualified_by=inp.qualified_by, data={"returned": True}, plan_code=(st or {}).get("plan_code") or "")
                return {"reopened": True, "assigned_to": to}
    if owner:
        await ctx.notify([owner], kind="lead_returned", title=f"Khách quay lại: {card.get('customer', 'khách')}{f' · {offering}' if offering else ''}",
                         body=f"Khách vừa để lại thông tin lần nữa qua {channel} — gọi lại khi khách còn nóng",
                         push=f"Khách của anh/chị quay lại qua {channel}", open={"query": "crm.journey", "input": {"lead_id": str(lid)}})
    return {"reopened": False, "assigned_to": owner}


async def _drain(ctx: Ctx) -> int:
    """Thử chia lại lead KẸT (chưa có luật / không ai đủ điều kiện / chạm trần). Lead chờ điều phối hay giao cho đội thì để người chia; lead nạp từ hệ
    khác đang đỗ (``imported_at`` — 0185) không tự chia — người giao tay ở Lead chờ chia."""
    n = 0
    await _lock(ctx)   # khoá TRƯỚC khi đọc tải / chọn người: lượt nạp lead chạy song song phải chờ, không vượt trần
    routing: dict[str, bool] = {}
    for q in await ctx.all("SELECT lead_id, offering_code, source, holders, recalls, plan_code FROM leadflow.lead_state"
                           " WHERE status = 'queued' AND queue_reason = ANY(%s) AND imported_at IS NULL ORDER BY updated_at LIMIT 200 FOR UPDATE SKIP LOCKED",
                           (list(DRAINABLE),)):
        if q["offering_code"] not in routing:
            routing[q["offering_code"]] = await _routing_on(ctx, q["offering_code"])
        if not routing[q["offering_code"]]:
            continue   # còn tạm ngừng chia ⇒ để yên trong hàng chờ
        rule = await _rule_for(ctx, q["offering_code"], q["source"], today(), q.get("plan_code") or "")
        if rule is None or rule["mode"] == "coordinator":
            continue
        pick = await _pick(ctx, rule, q["offering_code"], tuple(q["holders"]))
        if pick.user:
            await _assign_to(ctx, q["lead_id"], q["offering_code"], q["source"], pick.user, rule, holders=tuple(q["holders"]),
                             recalls=q["recalls"], reason="Chia từ hàng chờ", data=pick.basis, via_team=rule["team_id"],
                             via_members=await _via_members(ctx, rule, q["offering_code"], pick.user))
            n += 1
    return n


@m.command("drain",
           purpose="Chia lại các lead đang chờ trong hàng chờ cho những người đủ điều kiện nhận.", irreversible=True, effect="Chia ngay các lead đang chờ cho người nhận theo luật và báo từng người; muốn đổi phải chuyển tay từng lead.",
           resource="leadflow", action="manage", title="Chia lại hàng chờ", agent="deny")
async def drain(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    return {"assigned": await _drain(ctx)}


class AssignIn(Input):
    lead_id: UUID = field("Lead", hidden=True)
    owner_email: str = field("Giao cho", options_query="leadflow.receiver_options")


@m.command("assign", resource="leadflow", title="Giao lead từ hàng chờ")
async def assign(ctx: Ctx, inp: AssignIn) -> dict[str, Any]:
    """Người điều phối giao một lead đang chờ. Chỉ thấy (nên chỉ giao được) lead trong hàng chờ của đội mình — RLS của lead.
    Trợ lý được SOẠN đề xuất giao (người bấm mới giao)."""
    # quyết định bằng quyền NGƯỜI GỌI: chỉ thấy (nên chỉ giao được) lead trong hàng chờ của đội mình
    st = await ctx.one("SELECT offering_code FROM leadflow.lead_state WHERE lead_id = %s AND status = 'queued'", (inp.lead_id,))
    if st is None:
        raise NotFound("Không thấy lead này trong hàng chờ bạn được chia")
    uid = await _user_by_email(ctx, inp.owner_email)
    if uid not in await _lead_capable(ctx, st["offering_code"]):
        raise Invalid(f"{inp.owner_email} không nhận lead được (đã khoá, đang tạm ngừng / nghỉ, vai chưa có quyền xem + ghi Khách hàng, "
                      "hoặc không là thành viên [[offering|l]] này)")
    card = (await ctx.call_system("crm.lead_cards", lead_ids=[inp.lead_id])).get(str(inp.lead_id), {})
    who = await ctx.val("SELECT name FROM core.users WHERE id = %s", (uid,))
    await ctx.call_system("leadflow.give", lead_id=inp.lead_id, owner_id=uid, reason=f"Điều phối: {ctx.p.name or 'người điều phối'} giao",
                          by=ctx.p.user_id)
    return {"lead_id": inp.lead_id, "assigned_to": uid,
            "summary": f"Giao {card.get('customer', 'lead')}{' · ' + card['offering_code'] if card.get('offering_code') else ''} cho {who}"}


@m.query("receiver_options", resource="leadflow", title="Người nhận lead")
async def receiver_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    capable = await _lead_capable(ctx)
    load: dict[str, int] = await ctx.call_system("crm.workload")
    rows = await ctx.all(
        """SELECT u.id, u.email, u.name, r.label AS role, t.name AS team FROM core.memberships mb JOIN core.users u ON u.id = mb.user_id
           JOIN core.roles r ON r.key = mb.role LEFT JOIN core.team_members tm ON tm.user_id = u.id AND tm.is_home LEFT JOIN core.teams t ON t.id = tm.team_id
           WHERE mb.active ORDER BY t.name NULLS LAST, u.name""")
    return [{"value": r["email"], "label": f"{r['name']} ({r['role']}{' · ' + r['team'] if r['team'] else ''}) — {load.get(str(r['id']), 0)} lead chưa gọi"}
            for r in rows if r["id"] in capable]


@m.query("queue", resource="leadflow", title="Lead chờ chia")
async def queue(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Hàng chờ người xem được chia (RLS: đội mình / cả công ty). Mỗi lead có GỢI Ý người nhận + lý do — bấm là giao."""
    now = lf.clock()
    states = await ctx.all("SELECT * FROM leadflow.lead_state WHERE status = 'queued' ORDER BY updated_at LIMIT 60")
    actions = [ui.act("leadflow.intake", "Nạp lead", style="primary")]
    if not states:
        return ui.card("Lead chờ chia", [ui.b_callout("ok", "Không còn lead nào chờ chia", "Lead mới nạp vào hàng chờ của đội sẽ hiện ở đây.")],
                       icon="tray.and.arrow.down", actions=actions)
    cards = await ctx.call_system("crm.lead_cards", lead_ids=[s_["lead_id"] for s_ in states])
    people = {r["id"]: r for r in await ctx.all("SELECT id, name, email FROM core.users")}
    lead_roles = sorted(await _lead_roles(ctx))
    rules_cache: dict[tuple[str, str, date, str], dict[str, Any] | None] = {}
    blocks: list[dict[str, Any]] = [ui.b_kpis([
        ui.kpi("Chờ chia", len(states), tone="orange"),
        ui.kpi("Chờ lâu nhất", _span(states[0]["updated_at"], now), "text")])]
    rest: list[dict[str, Any]] = []
    planned: list[UUID] = []
    for i, st in enumerate(states):
        c = cards.get(str(st["lead_id"]), {})
        title = f"{c.get('customer', '—')} · {st['offering_code'] or 'chưa chọn [[offering|l]]'} · {SOURCE_LABEL.get(st['source'], st['source'])}"
        ago = "vừa về" if now - st["updated_at"] < timedelta(minutes=1) else f"về {_span(st['updated_at'], now)} trước"
        waited = f"{ago[0].upper()}{ago[1:]} · {QUEUE_REASON.get(st['queue_reason'], 'đang chờ')}" + (f" · {PARKED_LABEL}" if st["imported_at"] else "")
        if i >= 8:
            rest.append({"title": title, "subtitle": waited, "tone": "warn", "open": {"query": "crm.journey", "input": {"lead_id": str(st["lead_id"])}}})
            continue
        key = (st["offering_code"], st["source"], today(), st.get("plan_code") or "")
        if key not in rules_cache:
            rules_cache[key] = await _rule_for(ctx, *key)
        rule = rules_cache[key] or {"team_id": None, "roles": lead_roles, "cap_uncalled": 0, "strategy": "least_loaded"}
        pick = await _pick(ctx, rule, st["offering_code"], tuple(st["holders"]), tuple(planned))
        if pick.user:
            planned.append(pick.user)
        facts = [f"Khách cần: {c['interest_text']}"] if c.get("interest_text") else []
        acts = []
        if pick.user and (who := people.get(pick.user)):
            seen = pick.last.get(pick.user)
            n_planned = planned.count(pick.user) - 1
            facts.append(f"Gợi ý: {who['name']} — {pick.load.get(str(pick.user), 0) - n_planned} lead chưa gọi, "
                         + ("vừa được gợi ý lead ở trên" if n_planned else
                            f"nhận lead gần nhất {_span(seen, now)} trước" if seen and seen <= now else "chưa nhận lead nào gần đây")
                         + (f" · hạng {pick.basis['tier']}, trọng số {pick.basis['weight']}" if "tier" in pick.basis else ""))
            acts.append(ui.act("leadflow.assign", f"Giao cho {who['name'].split()[-1]}",
                               {"lead_id": str(st["lead_id"]), "owner_email": who["email"]}, "primary"))
        else:
            facts.append(f"Chưa gợi ý được: {QUEUE_REASON.get(pick.why, pick.why)}")
        acts.append(ui.act("leadflow.assign", "Chọn người khác", {"lead_id": str(st["lead_id"])}))
        tone = "accent" if st["queue_reason"] in PLANNED else "bad"
        blocks.append(ui.b_callout(tone, title, waited, facts=facts, actions=acts))
    if rest:
        blocks.append(ui.b_list(rest, f"Còn {len(rest)} lead khác"))
    return ui.card("Lead chờ chia", blocks, subtitle=f"{len(states)} lead · gợi ý theo đúng cách chia tự động (công bằng, trần lead)",
                   icon="tray.and.arrow.down", actions=actions, ask={"title": "Nhờ trợ lý chia hộ", "prompt": "Chia giúp tôi các lead đang chờ: "})


# ── Bước nhận lead (luật có hạn bấm nhận) và chuyển tay lead đang chờ gọi ──

class LeadRef(Input):
    lead_id: UUID = field("Lead", hidden=True)


@m.command("accept",
           subject=("crm.lead", "lead_id"), purpose="Người được chia xác nhận nhận lead, để hạn gọi lần đầu bắt đầu tính và lead không bị thu hồi.", irreversible=True, effect="Lead thành của bạn và hạn gọi lần đầu bắt đầu chạy; không trả lại được, chỉ chuyển tay qua trưởng nhóm.",
           resource="leadflow", title="Nhận lead", agent="propose", risk="low")
async def accept(ctx: Ctx, inp: LeadRef) -> dict[str, Any]:
    """Người được chia bấm NHẬN (chỉ khi luật có bước nhận): chính người đang giữ, lead còn chờ nhận. Hạn gọi lần đầu giữ nguyên như lúc chia.
    Trợ lý chỉ soạn thẻ — bấm nhận là cam kết của chính người đó."""
    # khoá dòng rồi KIỂM LẠI người giữ + hạn (0153): bấm nhận chạy đua với vòng thu hồi ⇒ một trong hai thắng, bên thua thấy trạng thái mới
    st = await ctx.one("SELECT holder_id, status, first_call_due, accept_due FROM leadflow.lead_state WHERE lead_id = %s FOR UPDATE", (inp.lead_id,))
    if st is None or st["holder_id"] != ctx.p.user_id:
        raise NotFound("Không thấy lead đang chờ anh/chị nhận")
    if st["status"] != "offered":
        return {"lead_id": inp.lead_id, "summary": "Lead này đã nhận rồi"}
    if st["accept_due"] is not None and lf.clock() > st["accept_due"]:
        raise Conflict("Đã quá hạn bấm nhận — lead đang được chia cho người khác")
    await ctx.exec("UPDATE leadflow.lead_state SET status = 'waiting', accepted_at = now(), updated_at = now() WHERE lead_id = %s", (inp.lead_id,))
    await _event(ctx, inp.lead_id, "accepted", ctx.p.user_id)
    due = st["first_call_due"]
    return {"lead_id": inp.lead_id, "summary": "Đã nhận lead" + (f" — gọi trước {due.astimezone(zone()):%H:%M %d/%m}" if due else "")}


class ReassignIn(Input):
    lead_id: UUID = field("Lead", hidden=True)
    owner_email: str = field("Chuyển cho", options_query="leadflow.receiver_options")
    reason: str = field("Lý do", "", max_length=300, example="Người giữ đang nghỉ, khách cần gọi ngay")


@m.command("reassign",
           subject=("crm.lead", "lead_id"), purpose="Chuyển lead chưa liên hệ sang người khác khi người giữ không kịp gọi, để khách không bị bỏ chờ.", irreversible=True, effect="Lead sang người mới với hạn gọi tính lại từ đầu, người giữ cũ được báo ngay; muốn trả lại phải chuyển tay lần nữa.",
           resource="leadflow", title="Chuyển lead đang chờ gọi sang người khác", agent="propose", risk="authority", min_scope="team")
async def reassign(ctx: Ctx, inp: ReassignIn) -> dict[str, Any]:
    """Trưởng nhóm / quản lý chuyển một lead CHƯA được liên hệ (đang chờ nhận / chờ gọi) sang người khác — người mới có đủ hạn từ đầu, người cũ
    được báo, lịch sử ghi rõ ai chuyển vì sao. Trợ lý được ĐỀ XUẤT (gợi ý buổi sáng, câu hỏi "lead nào sắp trễ") — người quản lý bấm mới chuyển.
    Quy trình: mã lead lấy ở ``leadflow.late`` (lead quá hạn gọi của đội), người nhận gợi ý lấy ở ``leadflow.explain``; lead đã được liên hệ thì không
    chuyển ở đây (đổi người phụ trách khách)."""
    if not ctx.p.has_scope("leadflow", "team"):
        raise Denied("Chuyển lead cần quyền chia lead phạm vi đội trở lên")
    st = await ctx.one("SELECT lead_id, holder_id, offering_code FROM leadflow.lead_state WHERE lead_id = %s AND status = ANY(%s)",
                       (inp.lead_id, list(OPEN)))   # quyền NGƯỜI GỌI (RLS): chỉ lead người gọi thấy
    if st is None:
        raise NotFound("Không thấy lead đang chờ gọi trong phạm vi của bạn (lead đã liên hệ thì chuyển người phụ trách ở thẻ khách)")
    uid = await _user_by_email(ctx, inp.owner_email)
    if uid == st["holder_id"]:
        raise Invalid("Người này đang giữ lead")
    if not ctx.p.has_scope("leadflow", "all"):   # phạm vi đội: chỉ chuyển cho người trong đội mình quản lý, không tự chuyển cho mình (review 10/11)
        mine = await ctx.val("SELECT EXISTS (SELECT 1 FROM core.team_members WHERE user_id = %s AND team_id = ANY(%s))", (uid, list(ctx.p.managed_teams)))
        if uid == ctx.p.user_id or not mine:
            raise Invalid("Chỉ chuyển cho người trong đội anh/chị quản lý (không tự nhận) — chuyển ra ngoài đội cần quyền chia lead cả công ty")
    if uid not in await _lead_capable(ctx, st["offering_code"]):
        raise Invalid(f"{inp.owner_email} không nhận lead được (đã khoá, đang tạm ngừng / nghỉ, vai chưa có quyền xem + ghi Khách hàng, "
                      "hoặc không là thành viên [[offering|l]] này)")
    who = await ctx.val("SELECT name FROM core.users WHERE id = %s", (uid,))
    await ctx.call_system("leadflow.move", lead_id=st["lead_id"], owner_id=uid, reason=inp.reason.strip() or "quản lý chuyển", by=ctx.p.user_id)
    return {"lead_id": st["lead_id"], "assigned_to": uid, "summary": f"Đã chuyển lead cho {who}"}


class MoveIn(Input):
    lead_id: UUID
    owner_id: UUID
    reason: str
    by: UUID | None = None


@m.command("move", resource="leadflow", title="Chuyển lead đang chờ gọi", internal=True, agent="deny", callers=("leadflow",),
           why="reassign, sau khi kiểm người gọi có quyền chia lead phạm vi đội trở lên, THẤY lead đang chờ gọi (RLS leadflow.lead_state) và người nhận "
               "làm được lead: đổi người giữ MỘT lead (crm.place), ghi lại hạn mới + sự kiện chuyển, báo hai người. Sau khi chuyển lead có thể rời "
               "phạm vi người chuyển. Trả {assigned_to}."
               " [id: người gọi thấy — lead_id người gọi đọc được qua RLS ở dòng trước, owner_id đã lọc là người nhận lead được, by = chính người gọi]")
async def move(ctx: Ctx, inp: MoveIn) -> dict[str, Any]:
    st = await ctx.one("SELECT * FROM leadflow.lead_state WHERE lead_id = %s AND status = ANY(%s) FOR UPDATE", (inp.lead_id, list(OPEN)))
    if st is None:
        raise NotFound("Lead không còn chờ gọi")
    await _lock(ctx)
    rule = await _rule_for(ctx, st["offering_code"], st["source"], today(), st.get("plan_code") or "")
    await _event(ctx, inp.lead_id, "handed_over", st["holder_id"],
                 {"to": str(inp.owner_id), "why": "manager", "by": str(inp.by) if inp.by else None, "note": inp.reason})
    await _assign_to(ctx, inp.lead_id, st["offering_code"], st["source"], inp.owner_id, rule, holders=tuple(st["holders"]), recalls=st["recalls"],
                     reason=f"Chuyển tay: {inp.reason}", data={"manual": True, "by": str(inp.by) if inp.by else None})
    await ctx.notify([st["holder_id"]], kind="lead_recalled", title=f"Lead đã chuyển sang người khác{f' ({st['offering_code']})' if st['offering_code'] else ''}",
                     body=inp.reason, push="Một lead của anh/chị đã được chuyển cho người khác")
    return {"assigned_to": inp.owner_id}


class ReceiverCheckIn(Input):
    user_id: UUID
    offering_code: str = ""


@m.query("receiver_check", resource="leadflow", title="Người này giữ được lead của [[offering|l]] này không", internal=True, agent="deny",
         callers=("crm",),
         why="crm.assign (người chuyển đã qua quyền quản lý Khách hàng; người nhận tra theo email trong thành viên đang hoạt động): công tắc công ty "
             "'chỉ thành viên' bật thì người nhận (phạm vi chia lead của mình) phải là thành viên [[offering|l]] của lead. Trả {why} — chuỗi rỗng = được; "
             "không trả danh sách thành viên."
             " [id: người gọi thấy — user_id là người nhận crm.assign vừa tra theo email trong thành viên đang hoạt động; chỉ trả được / không]")
async def receiver_check(ctx: Ctx, inp: ReceiverCheckIn) -> dict[str, str]:
    if not inp.offering_code or not await members_only(ctx):
        return {"why": ""}
    row = await ctx.one(
        """SELECT u.name,
                  EXISTS (SELECT 1 FROM core.memberships mb JOIN core.grants g ON g.role = mb.role AND g.resource = 'leadflow' AND g.scope = 'own'
                          WHERE mb.user_id = u.id AND mb.active) AS bound,
                  EXISTS (SELECT 1 FROM leadflow.members lm WHERE lm.user_id = u.id AND lm.active AND lm.offering_code = %s) AS member
           FROM core.users u WHERE u.id = %s""", (inp.offering_code, inp.user_id))
    if row is None or not row["bound"] or row["member"]:
        return {"why": ""}
    return {"why": f"{row['name']} không phải thành viên [[offering|l]] {inp.offering_code} — công ty đang bật “chỉ thành viên” (thêm ở Thành viên "
                   "[[offering|l]] trước)"}


# ── Không lead nào mồ côi (0153): người giữ nghỉ việc / mất quyền / rời đội / rời [[offering|l]] ⇒ chuyển NGAY qua luật chia, không tính thu hồi ──

HELD = (*OPEN, "contacted")   # trạng thái có người giữ đang chịu trách nhiệm


async def handover(ctx: Ctx, st: dict[str, Any], why: str, *, data: dict[str, Any] | None = None) -> UUID | None:
    """Chuyển MỘT lead khỏi người giữ hiện tại qua đúng luật chia (người đã từng giữ không nhận lại), KHÔNG tăng số lần thu hồi; sự kiện
    ``handed_over`` (lý do ``why`` ∈ ``HANDOVER``) vào sổ của lead. Không ai nhận được ⇒ hàng chờ (có chủ, có tuổi — vòng quét báo)."""
    lid, gone = st["lead_id"], st["holder_id"]
    holders: tuple[UUID, ...] = tuple(dict.fromkeys([*st["holders"], *([gone] if gone else [])]))
    to = await _distribute(ctx, lid, st["offering_code"], st["source"], exclude=holders, holders=holders, recalls=st["recalls"],
                           reason=f"Chuyển: {HANDOVER[why]}", data={"handover": why}, plan_code=st.get("plan_code") or "")
    await _event(ctx, lid, "handed_over", gone, {"to": str(to) if to else None, "why": why, **(data or {})})
    if gone and why not in ("deactivated", "lost_access"):   # người đã nghỉ / mất quyền không còn đọc được thông báo về khách
        await ctx.notify([gone], kind="lead_recalled", title=f"Lead đã chuyển cho đồng nghiệp{f' ({st['offering_code']})' if st['offering_code'] else ''}",
                         body=f"Lý do: {HANDOVER[why]}", push="" if why == "away" else "Một lead của anh/chị đã chuyển cho đồng nghiệp")
    return to


async def ineligible(ctx: Ctx, states: list[dict[str, Any]]) -> dict[UUID, str]:
    """Lead nào có người giữ KHÔNG còn được giữ — một câu SQL cho cả lô: tài khoản khoá / hết thành viên ⇒ ``deactivated``; vai mất quyền xem + ghi
    Khách hàng ⇒ ``lost_access``; được chọn vì thuộc đội mà đã rời đội (gồm đội con) ⇒ ``left_team``; được chọn vì là thành viên [[offering|l]] (luật
    theo thành viên, hoặc công tắc "chỉ thành viên" lúc chia) mà đã rời [[offering|l]] ⇒ ``left_offering``. BẬT công tắc không chuyển lead đang giữ
    (0152 — chỉ chặn giao / chia mới). Tạm ngừng / nghỉ phép KHÔNG ở đây (tạm thời — nghỉ phép chuyển lead chưa liên hệ ở vòng quét)."""
    rows = [s for s in states if s.get("holder_id")]
    if not rows:
        return {}
    got = await ctx.all(
        """WITH RECURSIVE q AS (SELECT * FROM unnest(%(l)s::uuid[], %(u)s::uuid[], %(t)s::uuid[], %(m)s::bool[], %(o)s::text[])
                                 AS x(lead_id, user_id, via_team, via_members, offering)),
           tree AS (SELECT id AS root, id FROM core.teams WHERE id IN (SELECT via_team FROM q WHERE via_team IS NOT NULL)
                    UNION ALL SELECT tree.root, t.id FROM core.teams t JOIN tree ON t.parent_id = tree.id)
           SELECT q.lead_id, CASE
               WHEN mb.user_id IS NULL OR NOT mb.active THEN 'deactivated'
               WHEN NOT EXISTS (SELECT 1 FROM core.grants g WHERE g.role = mb.role AND g.resource = 'crm'
                                AND 'read' = ANY(g.actions) AND 'write' = ANY(g.actions)) THEN 'lost_access'
               WHEN q.via_team IS NOT NULL AND NOT EXISTS (SELECT 1 FROM core.team_members tm JOIN tree ON tree.id = tm.team_id AND tree.root = q.via_team
                                                         WHERE tm.user_id = q.user_id) THEN 'left_team'
               WHEN q.offering <> '' AND q.via_members
                    AND NOT EXISTS (SELECT 1 FROM leadflow.members lm WHERE lm.user_id = q.user_id AND lm.active AND lm.offering_code = q.offering)
                    THEN 'left_offering'
           END AS why
           FROM q LEFT JOIN core.memberships mb ON mb.user_id = q.user_id AND mb.tenant_id = core.tenant()""",
        {"l": [s["lead_id"] for s in rows], "u": [s["holder_id"] for s in rows], "t": [s.get("via_team_id") for s in rows],
         "m": [bool(s.get("via_members")) for s in rows], "o": [s["offering_code"] for s in rows]})
    return {r["lead_id"]: r["why"] for r in got if r["why"]}


async def rehome_departed(ctx: Ctx, user_ids: list[UUID] | None = None, *, live_only: bool = False) -> int:
    """Lead đang mở mà người phụ trách bên Khách không còn làm được lead (nghỉ việc / mất quyền) — kể cả lead tự tạo, chưa từng qua chia lead.
    Có trạng thái chia đang giữ ⇒ ``handover``; chưa có / đã đóng ⇒ chia như lead mới, người cũ không nhận lại.
    ``live_only`` (vòng quét): bỏ lead nạp từ hệ khác đang ĐỖ (0185) — trạng thái chia mang ``imported_at``, hoặc chưa có trạng thái chia mà lead bên
    Khách mang dấu nạp. Sự kiện khoá tài khoản THẬT thì chuyển cả lead đỗ (việc của hôm nay — lead thành lead sống)."""
    n = 0
    for r in await ctx.call_system("crm.departed_leads", user_ids=user_ids or [], skip_loaded=live_only):
        st = await ctx.one("SELECT * FROM leadflow.lead_state WHERE lead_id = %s FOR UPDATE", (r["lead_id"],))
        if live_only and st is not None and st["imported_at"] is not None:
            continue
        if st is not None and st["status"] in HELD:
            if st["holder_id"] == r["owner_id"]:
                await handover(ctx, st, r["why"])
                n += 1
            continue   # lệch người giữ ⇒ vòng quét đồng bộ trước (follow), lượt sau xét lại
        if st is not None and st["status"] == "queued":
            continue
        owner = r["owner_id"]
        to = await _distribute(ctx, r["lead_id"], r["offering_code"], r["source"], exclude=(owner,), holders=(owner,),
                               reason=f"Chuyển: {HANDOVER[r['why']]}", data={"handover": r["why"]}, plan_code=(st or {}).get("plan_code") or "")
        await _event(ctx, r["lead_id"], "handed_over", owner, {"to": str(to) if to else None, "why": r["why"]})
        n += 1
    return n


async def recheck(ctx: Ctx, *, user_ids: list[UUID] | None = None, team_bound: bool = False) -> int:
    """Xét lại người giữ của lead đang có người giữ (của ``user_ids``, hoặc mọi lead được chọn theo đội khi ``team_bound``) và chuyển lead của
    người không còn được giữ. Dùng NGAY khi có thay đổi (khoá tài khoản, đổi đội, rời [[offering|l]]); vòng quét làm lại mỗi lượt (lưới an toàn)."""
    rows = await ctx.all(
        """SELECT * FROM leadflow.lead_state WHERE status = ANY(%s) AND holder_id IS NOT NULL AND (%s::uuid[] IS NULL OR holder_id = ANY(%s::uuid[]))
             AND (NOT %s OR via_team_id IS NOT NULL) ORDER BY assigned_at LIMIT 500 FOR UPDATE SKIP LOCKED""",
        (list(HELD), user_ids, user_ids, team_bound))
    bad = await ineligible(ctx, rows)
    n = 0
    for st in rows:
        if why := bad.get(st["lead_id"]):
            await handover(ctx, st, why)
            n += 1
    if user_ids:
        n += await rehome_departed(ctx, user_ids)
    return n


@m.on("admin.member_deactivated")
async def holder_left(ctx: Ctx, event: Event) -> None:
    """Khoá tài khoản (nghỉ việc) ⇒ mọi lead đang mở người đó giữ chuyển NGAY qua luật chia — không đợi quá hạn, không tính thu hồi."""
    if ctx.historical:   # khoá tài khoản của người đã nghỉ Ở HỆ CŨ trong lúc nạp (0185): lead của họ là tham chiếu, không chia lại
        return
    await recheck(ctx, user_ids=[UUID(str(event.payload["user_id"]))])


@m.on("admin.team_members_changed")
async def team_changed(ctx: Ctx, event: Event) -> None:
    """Đổi thành viên đội ⇒ lead được chia vì người giữ thuộc đội nhận mà người đó đã rời đội ⇒ chuyển ngay. Lead giao tay / khách cũ giữ người
    cũ thì không (người đó không giữ vì là thành viên đội)."""
    if ctx.historical:
        return
    await recheck(ctx, team_bound=True)


# ── MỘT nguồn "ai giữ lead" (0153): người phụ trách ở crm.leads quyết; lead_state.holder_id đi theo trong CÙNG giao dịch ──

async def follow(ctx: Ctx, st: dict[str, Any], owner: UUID | None, *, by: UUID | None = None, closing: bool = False) -> bool:
    """Người phụ trách bên Khách đã đổi (``crm.assign`` / tạo giao dịch) ⇒ trạng thái chia đi theo. Chờ nhận / chờ gọi / hàng chờ ⇒ người mới có
    đủ hạn từ đầu; đã liên hệ ⇒ người mới tiếp hạn chăm sóc lại; lead đóng ⇒ chỉ ghi người. Cùng người ⇒ không làm gì (lưu lại KHÔNG đặt lại
    đồng hồ — lỗi hệ cũ: mỗi lần lưu xếp lại hẹn giờ thu hồi)."""
    if st["holder_id"] == owner and not (closing and st["status"] != "closed"):
        return False
    lid, prev = st["lead_id"], st["holder_id"]
    holders: list[UUID] = list(dict.fromkeys([*st["holders"], *([owner] if owner else [])]))
    if closing or st["status"] == "closed":
        await ctx.exec("UPDATE leadflow.lead_state SET holder_id = %s, holders = %s, status = CASE WHEN %s THEN 'closed' ELSE status END,"
                       " updated_at = now() WHERE lead_id = %s", (owner, holders, closing, lid))
        return True
    rule = await _rule_for(ctx, st["offering_code"], st["source"], today(), st.get("plan_code") or "")
    if owner is None:   # bên Khách bỏ người phụ trách ngoài chia lead (bản cũ đang chạy song song) ⇒ hàng chờ có chủ, có tuổi
        await _queue(ctx, lid, st["offering_code"], st["source"], rule["team_id"] if rule else None, "no_eligible", rule, tuple(holders), st["recalls"])
        return True
    if prev:
        await _event(ctx, lid, "handed_over", prev, {"to": str(owner), "why": "manager", "by": str(by) if by else None})
    if st["status"] == "contacted":
        await ctx.exec("UPDATE leadflow.lead_state SET holder_id = %s, holders = %s, via_team_id = NULL, via_members = false, followup_reminded = NULL,"
                       " followup_flagged = NULL, updated_at = now() WHERE lead_id = %s", (owner, holders, lid))
        await ctx.notify([owner], kind="lead", title="Anh/chị nhận chăm sóc tiếp một khách", push="Anh/chị nhận chăm sóc tiếp một khách",
                         open={"query": "crm.journey", "input": {"lead_id": str(lid)}})
        return True
    await _assign_to(ctx, lid, st["offering_code"], st["source"], owner, rule, holders=tuple(st["holders"]), recalls=st["recalls"],
                     reason="Chuyển tay ở thẻ khách", data={"manual": True, "by": str(by) if by else None}, place=False)
    return True


class FollowIn(Input):
    lead_id: UUID
    owner_id: UUID | None
    by: UUID | None = None
    closing: bool = False


@m.command("follow_owner", resource="leadflow", title="Trạng thái chia đi theo người phụ trách", internal=True, agent="deny", callers=("crm",),
           why="crm.assign (người chuyển có quyền quản lý Khách hàng, ghi người phụ trách bằng quyền + RLS của họ) và crm.link_deal (lead thành giao "
               "dịch, người phụ trách = người bán): CÙNG giao dịch, trước khi crm ghi người phụ trách — khoá dòng leadflow.lead_state rồi cho người "
               "giữ đi theo (hạn mới / tiếp chăm sóc / đóng), ghi sự kiện chuyển tay, báo người nhận. Lead có thể rời phạm vi người chuyển. Trả {followed}."
               " [id: người gọi thấy — lead_id người chuyển đọc được qua RLS crm.leads; owner_id là thành viên đang hoạt động crm tra theo email / "
               "sale của giao dịch, ghi lại bằng quyền người gọi (WITH CHECK) trong cùng giao dịch; by = chính người gọi]")
async def follow_owner(ctx: Ctx, inp: FollowIn) -> dict[str, Any]:
    st = await ctx.one("SELECT * FROM leadflow.lead_state WHERE lead_id = %s FOR UPDATE", (inp.lead_id,))
    return {"followed": st is not None and await follow(ctx, st, inp.owner_id, by=inp.by, closing=inp.closing)}


async def put_imported(ctx: Ctx, v: dict[str, Any]) -> None:
    """Trạng thái chia NẠP TỪ HỆ KHÁC (0168, ``history.import_state``) — chỗ ghi người giữ vẫn chỉ ở file này (0153): ``holder_id`` là người phụ
    trách lead bên Khách mà bên gọi vừa đọc bằng quyền người nạp (hàng chờ ⇒ trống, đã bỏ người phụ trách bên Khách trong cùng giao dịch).
    Không ghi sự kiện, không thông báo; ``imported_at`` đánh dấu lead ĐỖ (0185): tham chiếu, vòng quét không chấm hạn / nhắc / thu hồi / chia lại cho
    tới khi Barista tự chia lead đó (``_assign_to`` / ``_queue`` xoá dấu)."""
    await ctx.exec(
        """INSERT INTO leadflow.lead_state (lead_id, offering_code, source, status, holder_id, holders, rule_id, rule, assigned_at, first_call_due,
                                            first_call_at, first_call_ok, recalls, followup_due, queue_reason, queued_at, queue_team_id, imported_at)
           VALUES (%(lead_id)s, %(offering_code)s, %(source)s, %(status)s, %(holder_id)s, %(holders)s, %(rule_id)s, %(rule)s, %(assigned_at)s,
                   %(first_call_due)s, %(first_call_at)s, %(first_call_ok)s, %(recalls)s, %(followup_due)s, %(queue_reason)s, %(queued_at)s,
                   %(queue_team_id)s, now())
           ON CONFLICT (lead_id) DO UPDATE SET status = EXCLUDED.status, holder_id = EXCLUDED.holder_id, holders = EXCLUDED.holders,
               rule_id = EXCLUDED.rule_id, rule = EXCLUDED.rule, assigned_at = EXCLUDED.assigned_at, first_call_due = EXCLUDED.first_call_due,
               first_call_at = EXCLUDED.first_call_at, first_call_ok = EXCLUDED.first_call_ok, recalls = EXCLUDED.recalls,
               followup_due = EXCLUDED.followup_due, queue_reason = EXCLUDED.queue_reason, queued_at = EXCLUDED.queued_at,
               queue_team_id = EXCLUDED.queue_team_id, imported_at = now(), updated_at = now()""", v)
