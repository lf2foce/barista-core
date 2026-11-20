"""VÒNG QUÉT chấm hạn (``enforce_sla``) — MỘT lượt của MỘT công ty, quyền hệ thống, khoá dòng bỏ qua dòng đang bận:

0. người giữ không còn được giữ (nghỉ việc, mất quyền, rời đội / [[offering|l]]) ⇒ chuyển ngay qua luật chia, không tính thu hồi (0153);
1. người giữ lệch người phụ trách bên Khách (chỉ còn khi bản cũ chạy song song — đường chính đi theo trong cùng giao dịch) ⇒ đi theo bên Khách;
2. có BẰNG CHỨNG liên hệ theo luật (``contact_proof``: liên hệ ghi tay theo loại, cuộc gọi tổng đài khách nghe máy ≥ N giây) ⇒ ghi nhận đúng / trễ hạn;
3. người giữ đang nghỉ phép (điểm cắm ``leadflow.away``) ⇒ chuyển ngay, không tính là thu hồi;
4. luật có bước nhận mà quá hạn bấm nhận ⇒ chia người khác (tính như thu hồi);
5. quá hạn gọi ⇒ ghi vi phạm + báo trưởng nhóm (một lần), hết ân hạn ⇒ thu hồi sang người mới / hàng chờ khi hết số lần;
6. sắp tới hạn ⇒ nhắc người giữ (một lần, theo phút làm việc);
7. lead đã liên hệ: nhắc trước / báo khi quá hạn chăm sóc lại (một lần cho mỗi hạn), báo trưởng nhóm;
8. lead nằm hàng chờ quá ``queue_escalate_min`` phút làm việc ⇒ báo người sở hữu hàng chờ, lặp lại tới khi có người nhận — kể cả lead đã hết
   số lần thu hồi: không trạng thái nào là ngõ cụt;
rồi chuyển lead của người đã nghỉ mà chưa từng qua chia lead, và thử chia lại lead kẹt trong hàng chờ. Quét XOAY VÒNG theo ``checked_at``: công ty
có hơn ``SWEEP_LIMIT`` lead đang mở thì lượt sau xét tiếp phần chưa xét, không lead nào đứng mãi sau hàng lead cũ.

Lead NẠP TỪ HỆ KHÁC đang đỗ (``imported_at`` — 0185) không vào vòng quét: không chấm hạn, không nhắc, không thu hồi, không chia lại.
Tin cho TRƯỞNG NHÓM / chủ hàng chờ (bước 5, 7, 8) là BẢN GỘP: một lượt quét gửi mỗi người MỘT tin cho mỗi loại (quá hạn gọi lần đầu / quá hạn chăm sóc lại
/ hàng chờ lâu) kèm số lead + vài cái tên; khoá chống lặp theo người × loại × giờ ⇒ tồn đọng lớn cũng chỉ một tin / loại / giờ / người. Sổ ``sla_events`` vẫn
ghi từng lead. Nhắc NGƯỜI GIỮ ("còn N phút", "quá hạn gọi") vẫn từng lead — gửi cho người phải làm."""

from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import datetime
from typing import Any
from uuid import UUID

from barista.api import Ctx, Hours, NoInput, add_working_minutes, today, zone
from barista.modules import leadflow as lf
from barista.modules.leadflow import (
    OPEN,
    QUEUE_ESCALATE_MIN,
    QUEUE_REASON,
    SWEEP_LIMIT,
    _rule_for,
    _span,
    clock_of,
    due_in,
    hours_of,
    m,
    proof_text,
)
from barista.modules.leadflow.routing import (
    _distribute,
    _drain,
    _event,
    _queue,
    _recalled,
    away,
    follow,
    handover,
    ineligible,
    rehome_departed,
)

# bản chụp luật trên lead chia TRƯỚC 0152 không có các khoá này ⇒ hành vi cũ
LEGACY = {"calendar": "", "contact_proof": ["any"], "min_talk_s": 0, "remind_before_min": 0, "escalate": False, "grace_min": 0, "accept_min": 0, "hide_contact": False,
          "off_hours": "defer", "duty_days": [1, 2, 3, 4, 5, 6, 7], "duty_open_min": 1080, "duty_close_min": 1320, "receivers": "team", "reopen_lost": True,
          "queue_escalate_min": QUEUE_ESCALATE_MIN}
ALWAYS = Hours((1, 2, 3, 4, 5, 6, 7), 0, 1440)   # hàng chờ chưa có luật: tuổi tính theo đồng hồ thường
PROOF_COL = {"any": "first_contact_at", "call": "first_call_at", "connected": "first_connected_at", "meeting": "first_meeting_at",
             "message": "first_message_at"}


async def _pbx(ctx: Ctx, states: list[dict[str, Any]]) -> dict[str, datetime] | None:
    """Cuộc gọi tổng đài khách nghe máy đầu tiên từ lúc lead tới tay người giữ — hỏi gói cắm vào ``leadflow.contact_proof``. Hợp đồng điểm cắm:
    đầu vào ``{leads: [{lead_id, since, min_talk_s, user_id}]}`` (user_id = người đang giữ — chỉ cuộc gọi của chính người đó), trả ``{"<lead_id>": giờ gọi}``. None = không gói nào cắm (tổng đài tắt)."""
    if not await ctx.plugged("leadflow.contact_proof"):
        return None
    if not states:
        return {}
    items = [{"lead_id": str(s["lead_id"]), "since": s["assigned_at"].isoformat(), "min_talk_s": int(s["rule"]["min_talk_s"]),
              "user_id": str(s["holder_id"]) if s["holder_id"] else None} for s in states]
    out: dict[str, datetime] = {}
    for part in await ctx.plugs("leadflow.contact_proof", system=True, leads=items):
        for k, v in (part or {}).items():
            at = datetime.fromisoformat(v) if isinstance(v, str) else v
            if at is not None and (k not in out or at < out[k]):
                out[k] = at
    return out


def _proof_at(rule: dict[str, Any], i: dict[str, Any], pbx_at: datetime | None, pbx_on: bool) -> tuple[datetime | None, str]:
    """Lần liên hệ đầu ĐẠT luật (sớm nhất trong các loại luật chấp nhận) + loại. Luật đòi cuộc gọi tổng đài mà gói tổng đài đang tắt ⇒ tạm tính cuộc
    gọi nói chuyện được (ghi tay) — câu luật có cảnh báo, sự kiện ghi ``fallback``."""
    hits: list[tuple[datetime, str]] = []
    for k in rule["contact_proof"] or ["any"]:
        if k == "pbx":
            v, label = (pbx_at, "pbx") if pbx_on else (i["first_connected_at"], "connected_fallback")
        else:
            v, label = i[PROOF_COL[k]], k
        if v is not None:
            hits.append((v, label))
    return min(hits) if hits else (None, "")


async def _leads_of(ctx: Ctx, user: UUID | None) -> list[UUID]:
    """Trưởng đội GẦN nhất của một người: trưởng đội chính (trừ chính người đó), không có thì trưởng đội cha — người nhận tin "quá hạn"."""
    team = await ctx.home_team(user) if user else None
    return await _team_leads(ctx, team, user)


async def _team_leads(ctx: Ctx, team: UUID | None, user: UUID | None = None) -> list[UUID]:
    """Trưởng đội GẦN nhất của ``team`` (đang hoạt động, trừ ``user``), không có thì trưởng đội cha."""
    if team is None:
        return []
    rows = await ctx.all(
        """WITH RECURSIVE up AS (SELECT id, parent_id, 0 AS depth FROM core.teams WHERE id = %s
                                 UNION ALL SELECT t.id, t.parent_id, up.depth + 1 FROM core.teams t JOIN up ON t.id = up.parent_id)
           SELECT tm.user_id, up.depth FROM up JOIN core.team_members tm ON tm.team_id = up.id AND tm.is_lead
           JOIN core.memberships mb ON mb.user_id = tm.user_id AND mb.active WHERE tm.user_id IS DISTINCT FROM %s::uuid ORDER BY up.depth""", (team, user))
    near = rows[0]["depth"] if rows else None
    return list(dict.fromkeys(r["user_id"] for r in rows if r["depth"] == near))


# BẢN GỘP tin báo trưởng nhóm / chủ hàng chờ của MỘT lượt quét (0185): loại → (tên loại, màn mở khi bấm, câu thân)
DIGEST = {"first_call": ("Quá hạn gọi lần đầu", "leadflow.late", "Chưa có liên hệ đạt luật — xem danh sách, chuyển tay ngay nếu cần"),
          "followup": ("Quá hạn chăm sóc lại", "leadflow.scorecard", "Khách chưa được chăm sóc lại đúng hạn của luật — xem theo từng người"),
          "queue": ("Lead chờ chia quá lâu", "leadflow.queue", "Giao cho người nhận ở Lead chờ chia"),
          "campaign_recalled": ("Lead của chiến dịch bị thu hồi", "mkt.campaigns",
                                "Người được chia không nhận / không gọi kịp — lead đã tự chuyển người khác hoặc về hàng chờ chia lại. Lặp nhiều: xem lại "
                                "chất lượng lead của chiến dịch hoặc báo trưởng nhóm sale")}
DIGEST_NAMES = 3


@dataclass
class Digest:
    """Gom thứ lẽ ra gửi từng lead: người nhận × loại → các dòng (nhãn, mốc cũ nhất). Gửi một lần cuối lượt quét."""
    items: dict[tuple[UUID, str], list[tuple[str, datetime | None]]] = dc_field(default_factory=dict)
    recalled: list[tuple[str | None, bool]] = dc_field(default_factory=list)   # (chiến dịch của lead, đã sang người mới) — báo người phụ trách chiến dịch

    def add(self, to: list[UUID], kind: str, label: str, since: datetime | None) -> None:
        for u in to:
            self.items.setdefault((u, kind), []).append((label, since))

    async def send(self, ctx: Ctx, now: datetime) -> None:
        hour = f"{now.astimezone(zone()):%Y%m%d%H}"
        for (user, kind), rows in self.items.items():
            title, screen, body = DIGEST[kind]
            names = list(dict.fromkeys(r[0] for r in rows if r[0]))
            oldest = min((r[1] for r in rows if r[1] is not None), default=None)
            more = f" +{len(names) - DIGEST_NAMES}" if len(names) > DIGEST_NAMES else ""
            detail = (f"{', '.join(names[:DIGEST_NAMES])}{more}" if names else "") + (f" · lâu nhất {_span(oldest, now)}" if oldest and oldest < now else "")
            await ctx.notify([user], kind="lead_escalated", title=f"{title}: {len(rows)} lead", body=f"{detail}. {body}" if detail else body,
                             push=f"{title}: {len(rows)} lead", open={"query": screen, "input": {}}, key=f"leadflow.digest:{kind}:{hour}")


async def _escalate(ctx: Ctx, st: dict[str, Any], kind: str, since: datetime, data: dict[str, Any], digest: Digest) -> int:
    """Báo trưởng nhóm gần nhất của người giữ: sổ ghi TỪNG lead (``escalated``), tin gửi đi là bản gộp cuối lượt quét."""
    to = await _leads_of(ctx, st["holder_id"])
    if not to:
        return 0
    name = await ctx.val("SELECT name FROM core.users WHERE id = %s", (st["holder_id"],))
    digest.add(to, kind, f"{name or 'người giữ'}{f' ({st['offering_code']})' if st['offering_code'] else ''}", since)
    await _event(ctx, st["lead_id"], "escalated", st["holder_id"], {**data, "to": [str(u) for u in to]})
    return 1


async def _recall(ctx: Ctx, st: dict[str, Any], rule: dict[str, Any], why: str, out: dict[str, int], digest: Digest | None = None,
                  campaign: Any = None) -> None:
    """Thu hồi: sang người MỚI (không ai từng giữ), quá số lần ⇒ hàng chờ lý do 'đã thu hồi hết số lần'. Người bị thu hồi được báo, mất quyền xem."""
    lid, holders = st["lead_id"], tuple(st["holders"])
    if st["recalls"] < rule["max_recalls"]:
        to = await _distribute(ctx, lid, st["offering_code"], st["source"], exclude=holders, holders=holders,
                               recalls=st["recalls"] + 1, reason=f"Thu hồi: {why}", plan_code=st.get("plan_code") or "")
        await _event(ctx, lid, "recalled", st["holder_id"], {"to": str(to) if to else None, "why": why})
        out["recalled" if to else "queued"] += 1
        moved = to is not None
    else:
        r_now = await _rule_for(ctx, st["offering_code"], st["source"], today(), st.get("plan_code") or "")
        await _queue(ctx, lid, st["offering_code"], st["source"], r_now["team_id"] if r_now else None, "recall_limit", r_now, holders, st["recalls"])
        await _event(ctx, lid, "recalled", st["holder_id"], {"to": None, "limit": True, "why": why})
        out["queued"] += 1
        moved = False
    await _recalled(ctx, st["holder_id"], st["offering_code"], why)
    if digest is not None:
        digest.recalled.append((str(campaign) if campaign else None, moved))


async def _tell_campaign_owners(ctx: Ctx, digest: Digest) -> None:
    """Người phụ trách CHIẾN DỊCH (marketing) biết lead của chiến dịch mình bị thu hồi (0214): tên chiến dịch + số lead, KHÔNG tên / SĐT khách
    (marketing thường không có quyền xem khách). Một bản gộp mỗi lượt quét như tin cho trưởng nhóm. Gói Marketing tắt / lead không có chiến dịch ⇒ thôi."""
    camps = sorted({c for c, _ in digest.recalled if c})
    if not camps:
        return
    owners = await ctx.call_system_optional("mkt.campaign_owners", campaign_ids=camps) or {}
    for c, moved in digest.recalled:
        if (o := owners.get(c or "")) and o["owner_id"]:
            digest.add([o["owner_id"]], "campaign_recalled", f"{o['name']} ({'chuyển người khác' if moved else 'về hàng chờ'})", None)


async def _queue_owners(ctx: Ctx, team: UUID | None, *, wide: bool) -> list[UUID]:
    """Ai SỞ HỮU một hàng chờ: trưởng đội của đội đó (gần nhất lên trên). Lần báo thứ hai trở đi, hoặc hàng chờ cả công ty / đội không có trưởng ⇒
    thêm người quản lý chia lead cả công ty; công ty không có ai như vậy ⇒ chủ tài khoản (luôn có — 0029). Không bao giờ rỗng khi công ty còn người."""
    to = await _team_leads(ctx, team)
    if wide or not to:
        to += [r["user_id"] for r in await ctx.all(
            """SELECT DISTINCT mb.user_id FROM core.memberships mb JOIN core.grants g ON g.role = mb.role AND g.resource = 'leadflow' AND g.scope = 'all'
               WHERE mb.active AND mb.tenant_id = core.tenant()""")]
    if not to:
        to = [r["user_id"] for r in await ctx.all("SELECT user_id FROM core.memberships WHERE owner AND active AND tenant_id = core.tenant()")]
    return list(dict.fromkeys(to))


async def _age_queue(ctx: Ctx, st: dict[str, Any], now: datetime, digest: Digest) -> int:
    """Lead nằm hàng chờ (mọi lý do — kể cả hết số lần thu hồi, chờ điều phối) quá ``queue_escalate_min`` phút làm việc kể từ lúc vào / lần báo
    trước ⇒ báo người sở hữu hàng chờ; lặp lại mỗi chừng ấy phút, từ lần hai báo rộng lên quản lý cả công ty."""
    rule = st["rule"]
    hs = clock_of(rule, False) if "work_days" in rule else (ALWAYS,)
    since = st["queue_escalated_at"] or st["queued_at"] or st["updated_at"]
    every = int(rule.get("queue_escalate_min") or QUEUE_ESCALATE_MIN)
    if now < due_in(since, every, hs):
        return 0
    n = st["queue_escalations"] + 1
    to = await _queue_owners(ctx, st["queue_team_id"], wide=n > 1)
    waited = _span(st["queued_at"] or st["updated_at"], now)
    digest.add(to, "queue", f"{QUEUE_REASON.get(st['queue_reason'], 'đang chờ')}{f' ({st['offering_code']})' if st['offering_code'] else ''}",
               st["queued_at"] or st["updated_at"])
    await ctx.exec("UPDATE leadflow.lead_state SET queue_escalated_at = %s, queue_escalations = %s WHERE lead_id = %s", (now, n, st["lead_id"]))
    await _event(ctx, st["lead_id"], "escalated", None, {"queue": True, "n": n, "reason": st["queue_reason"], "waited": waited,
                                                         "to": [str(u) for u in to]})
    return 1


@m.command("enforce_sla", resource="leadflow", title="Chấm hạn xử lý lead", internal=True,
           sweep="SELECT DISTINCT tenant_id FROM leadflow.lead_state WHERE status IN ('offered', 'waiting', 'contacted', 'queued') AND imported_at IS NULL")
async def enforce_sla(ctx: Ctx, inp: NoInput) -> dict[str, int]:
    """Một vòng quét của MỘT công ty: người giữ còn được giữ không, đồng bộ người giữ, liên hệ đầu, nghỉ phép, bấm nhận, chấm hạn, nhắc, báo trưởng
    nhóm, thu hồi, tuổi hàng chờ, lead của người đã nghỉ, rồi chia lại hàng chờ."""
    out = {"met": 0, "breached": 0, "recalled": 0, "queued": 0, "followup": 0, "closed": 0, "reminded": 0, "escalated": 0, "handed_over": 0,
           "accept_missed": 0, "resynced": 0, "queue_escalated": 0}
    now = lf.clock()
    digest = Digest()
    picked = await ctx.all("SELECT * FROM leadflow.lead_state WHERE status IN ('offered', 'waiting', 'contacted', 'queued') AND imported_at IS NULL"
                           " ORDER BY checked_at NULLS FIRST, assigned_at LIMIT %s FOR UPDATE SKIP LOCKED", (SWEEP_LIMIT,))
    if picked:
        await ctx.exec("UPDATE leadflow.lead_state SET checked_at = now() WHERE lead_id = ANY(%s)", ([s["lead_id"] for s in picked],))
    for st in picked:
        st["rule"] = {**LEGACY, **st["rule"]}
    states = [s for s in picked if s["status"] != "queued"]
    first = [s for s in states if s["status"] in OPEN]
    info = await ctx.call_system("crm.contact_info", lead_ids=[s["lead_id"] for s in states],
                                 since=[s["assigned_at"] if s["status"] in OPEN else None for s in states]) if states else {}
    pbx = await _pbx(ctx, [s for s in first if "pbx" in s["rule"]["contact_proof"]])
    gone = await away(ctx, {s["holder_id"] for s in first if s["holder_id"]})
    unfit = await ineligible(ctx, states)
    for st in states:
        i = info.get(str(st["lead_id"]))
        lid, rule = st["lead_id"], st["rule"]
        if i is None or i["stage"] not in ("lead", "prospect"):
            await ctx.exec("UPDATE leadflow.lead_state SET status = 'closed', updated_at = now() WHERE lead_id = %s", (lid,))
            out["closed"] += 1
            continue
        if i["owner_id"] != st["holder_id"]:   # MỘT nguồn: người phụ trách bên Khách quyết — trạng thái chia đi theo (0153)
            await follow(ctx, st, i["owner_id"])
            out["resynced"] += 1
            continue
        if (why := unfit.get(lid)) is not None:   # nghỉ việc / mất quyền / rời đội / rời [[offering|l]]: chuyển NGAY, không tính thu hồi
            await handover(ctx, st, why)
            out["handed_over"] += 1
            continue
        hours = hours_of(rule)
        hs = clock_of(rule, st["duty"])   # lead chia đội trực: giờ trực ∪ giờ làm việc
        if st["status"] in OPEN:
            proof_at, kind = _proof_at(rule, i, (pbx or {}).get(str(lid)), pbx is not None)
            if proof_at is not None:
                on_time = proof_at <= st["first_call_due"]
                last_due = add_working_minutes(i["last_contact_at"] or proof_at, rule["followup_hours"] * 60, hours)
                await ctx.exec("UPDATE leadflow.lead_state SET status = 'contacted', first_call_at = %s, first_call_ok = %s, followup_due = %s,"
                               " accepted_at = coalesce(accepted_at, %s), updated_at = now() WHERE lead_id = %s",
                               (proof_at, on_time, last_due, proof_at, lid))
                minutes = max(0, int((proof_at - st["assigned_at"]).total_seconds() // 60))
                await _event(ctx, lid, "first_call_met", st["holder_id"], {"on_time": on_time, "at": proof_at.isoformat(), "proof": kind,
                                                                          "minutes": minutes})
                out["met"] += 1
                continue
            if (off := gone.get(str(st["holder_id"]))) is not None:   # nghỉ phép đã duyệt: chuyển NGAY, không chờ quá hạn, không tính thu hồi
                await handover(ctx, st, "away", data={"until": off.get("until")})
                out["handed_over"] += 1
                continue
            if st["status"] == "offered" and st["accept_due"] is not None and now > st["accept_due"]:
                await _event(ctx, lid, "accept_missed", st["holder_id"], {"due": st["accept_due"].isoformat()})
                out["accept_missed"] += 1
                await _recall(ctx, st, rule, "không bấm nhận kịp", out, digest, i.get("campaign_id"))
                continue
            if now > st["first_call_due"]:
                recall_at = st["recall_at"]
                if st["breached_at"] is None:
                    await _event(ctx, lid, "first_call_breached", st["holder_id"], {"due": st["first_call_due"].isoformat()})
                    out["breached"] += 1
                    recall_at = due_in(st["first_call_due"], rule["grace_min"], hs) if rule["grace_min"] else now
                    await ctx.exec("UPDATE leadflow.lead_state SET breached_at = %s, recall_at = %s, updated_at = now() WHERE lead_id = %s",
                                   (now, recall_at, lid))
                    if rule["escalate"]:
                        out["escalated"] += await _escalate(ctx, st, "first_call", st["first_call_due"], {"due": st["first_call_due"].isoformat()}, digest)
                    if rule["grace_min"]:
                        await ctx.notify([st["holder_id"]], kind="lead", title=f"Quá hạn gọi — còn {rule['grace_min']} phút trước khi thu hồi",
                                         push="Lead quá hạn gọi — gọi ngay", open={"query": "crm.journey", "input": {"lead_id": str(lid)}})
                if recall_at is None or now >= recall_at:
                    await _recall(ctx, st, rule, "quá hạn gọi lần đầu", out, digest, i.get("campaign_id"))
                continue
            if st["remind_at"] is not None and st["reminded_at"] is None and now >= st["remind_at"]:
                left = max(1, int((st["first_call_due"] - now).total_seconds() // 60))
                await ctx.notify([st["holder_id"]], kind="lead", title=f"Còn {left} phút để liên hệ lead{f' ({st['offering_code']})' if st['offering_code'] else ''}",
                                 body=f"Tính là đã liên hệ khi có {proof_text(rule)}", push=f"Còn {left} phút để gọi lead",
                                 open={"query": "crm.journey", "input": {"lead_id": str(lid)}})
                await ctx.exec("UPDATE leadflow.lead_state SET reminded_at = now() WHERE lead_id = %s", (lid,))
                await _event(ctx, lid, "reminded", st["holder_id"], {"due": st["first_call_due"].isoformat()})
                out["reminded"] += 1
        else:   # contacted: theo dõi hạn chăm sóc lại (nhắc + báo, không thu hồi — lead đã có người làm)
            if i["last_contact_at"] is None:
                continue
            span = rule["followup_hours"] * 60
            fdue = add_working_minutes(i["last_contact_at"], span, hours)
            if fdue != st["followup_due"]:
                await ctx.exec("UPDATE leadflow.lead_state SET followup_due = %s, updated_at = now() WHERE lead_id = %s", (fdue, lid))
            rb = rule["remind_before_min"]
            if (0 < rb < span and now <= fdue and st["followup_reminded"] != fdue
                    and now >= add_working_minutes(i["last_contact_at"], span - rb, hours)):
                await ctx.notify([st["holder_id"]], kind="lead", title="Sắp tới hạn chăm sóc lại khách", push="Sắp tới hạn chăm sóc lại khách",
                                 open={"query": "crm.journey", "input": {"lead_id": str(lid)}})
                await ctx.exec("UPDATE leadflow.lead_state SET followup_reminded = %s WHERE lead_id = %s", (fdue, lid))
                await _event(ctx, lid, "reminded", st["holder_id"], {"followup": True, "due": fdue.isoformat()})
                out["reminded"] += 1
            if now > fdue and st["followup_flagged"] != fdue:
                await ctx.exec("UPDATE leadflow.lead_state SET followup_flagged = %s WHERE lead_id = %s", (fdue, lid))
                await _event(ctx, lid, "followup_breached", st["holder_id"], {"due": fdue.isoformat()})
                out["followup"] += 1
                if rule["escalate"]:
                    out["escalated"] += await _escalate(ctx, st, "followup", fdue, {"followup": True, "due": fdue.isoformat()}, digest)
    for st in picked:
        if st["status"] == "queued":
            out["queue_escalated"] += await _age_queue(ctx, st, now, digest)
    await _tell_campaign_owners(ctx, digest)
    await digest.send(ctx, now)
    out["handed_over"] += await rehome_departed(ctx, live_only=True)   # lead mở của người đã nghỉ mà chưa từng qua chia lead (lead tự tạo)
    await _drain(ctx)
    return out
