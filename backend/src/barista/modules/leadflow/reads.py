"""Đọc: hạn của lead (thẻ buổi sáng của Khách), tổng quan chia lead, bảng phản hồi theo người, vì sao lead tới tay ai, lead của tôi, gợi ý buổi sáng
cho quản lý (chuyển lead sắp trễ — thẻ đề xuất người bấm), sức khoẻ nguồn lead ngoài."""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from barista.api import Ctx, Input, NoInput, NotFound, field, today, ui, zone
from barista.modules.leadflow import (
    EVENT_LABEL,
    OPEN,
    PARKED_LABEL,
    PLANNED,
    PROOFS,
    QUEUE_REASON,
    SOURCE_LABEL,
    _lead_roles,
    _rule_for,
    _span,
    m,
    proof_text,
)
from barista.modules.leadflow.routing import _pick, away


class LeadIdsIn(Input):
    lead_ids: list[UUID]


@m.query("lead_sla", resource="leadflow", title="Hạn xử lý của lead", internal=True, callers=("crm",),
         why="crm.brief (thẻ buổi sáng): lead_ids là lead CỦA người xem (owner = mình) chưa được chăm sóc; bên gọi không kiểm thêm gì — "
             "sale chưa chắc có quyền đọc Chia lead. Trả mỗi lead {lead_id, status, first_call_due, followup_due, recalls, accept_due} — không người giữ "
             "cũ, không lịch sử."
             " [id: chính người gọi — lead_ids là lead CỦA người xem (owner = mình)]")
async def lead_sla(ctx: Ctx, inp: LeadIdsIn) -> dict[str, dict[str, Any]]:
    """Cho thẻ buổi sáng / hành trình của module Khách: lead nào đang chờ gọi lần đầu, hạn lúc nào."""
    rows = await ctx.all("SELECT lead_id, status, first_call_due, followup_due, recalls, accept_due FROM leadflow.lead_state WHERE lead_id = ANY(%s)"
                         " AND imported_at IS NULL", (inp.lead_ids,))   # lead đỗ (nạp từ hệ cũ — 0185) không có hạn đang chạy
    return {str(r["lead_id"]): r for r in rows}


@m.query("contact_gate", resource="leadflow", title="Lead đang che liên hệ", internal=True, agent="deny", callers=("crm",),
         why="Che liên hệ tới khi bấm nhận (0214): crm hỏi trước khi đưa SĐT / liên hệ khách ra màn, trợ lý, app ngoài. lead_ids là lead người xem đã "
             "THẤY qua RLS crm.leads. Trả CHỈ danh sách lead đang chờ bấm nhận theo luật có bật che {gated: [lead_id]} — không người giữ, không hạn."
             " [id: người gọi thấy — lead_ids là dòng crm.leads người xem đọc được qua RLS ngay trước đó]")
async def contact_gate(ctx: Ctx, inp: LeadIdsIn) -> dict[str, list[str]]:
    """Lead nào đang CHE liên hệ: chờ bấm nhận (``offered``) và bản chụp luật lúc chia có bật ``hide_contact``. Nhận rồi / thu hồi sang người mới mà luật
    không che / về hàng chờ ⇒ không che (hàng chờ: người điều phối không mở được thẻ khách — 0153)."""
    rows = await ctx.all("SELECT lead_id FROM leadflow.lead_state WHERE lead_id = ANY(%s) AND status = 'offered' AND (rule ->> 'hide_contact')::boolean",
                         (inp.lead_ids,))
    return {"gated": [str(r["lead_id"]) for r in rows]}


async def _figures(ctx: Ctx, since: datetime) -> dict[str, int]:
    """Số đếm của Chia lead & hạn xử lý trên CẢ tập trong phạm vi người xem (không phải danh sách đang hiện — danh sách có trần). MỘT định nghĩa cho ô
    số của màn và cho ``leadflow.summary``. Lead ĐỖ (nạp từ hệ cũ — 0185) không phải "kẹt" / "quá hạn": đếm riêng."""
    r = await ctx.one(
        """WITH e AS (SELECT lead_id, kind, (data->>'on_time')::bool AS on_time FROM leadflow.sla_events WHERE at >= %(since)s),
                bad AS (SELECT DISTINCT lead_id FROM e WHERE kind = 'first_call_breached'),
                s AS (SELECT status, queue_reason, breached_at, imported_at FROM leadflow.lead_state
                      WHERE status IN ('offered', 'waiting', 'contacted', 'queued') OR status = ANY(%(open)s))
           SELECT (SELECT count(DISTINCT lead_id) FROM e WHERE kind IN ('first_call_met', 'first_call_breached')) AS judged,
                  (SELECT count(DISTINCT lead_id) FROM e WHERE kind = 'first_call_met' AND on_time
                      AND lead_id NOT IN (SELECT lead_id FROM bad)) AS on_time,
                  (SELECT count(DISTINCT lead_id) FROM e WHERE kind = 'recalled') AS recalled,
                  (SELECT count(DISTINCT lead_id) FROM e WHERE kind = 'returned') AS returned,
                  (SELECT count(*) FROM s WHERE status = 'queued' AND imported_at IS NULL AND NOT coalesce(queue_reason, '') = ANY(%(planned)s)) AS stuck,
                  (SELECT count(*) FROM s WHERE status = 'queued' AND imported_at IS NULL AND queue_reason = ANY(%(planned)s)) AS planned,
                  (SELECT count(*) FROM s WHERE status = ANY(%(open)s) AND imported_at IS NULL) AS waiting,
                  (SELECT count(*) FROM s WHERE status = ANY(%(open)s) AND imported_at IS NULL AND breached_at IS NOT NULL) AS grace,
                  (SELECT count(*) FROM s WHERE status IN ('offered', 'waiting', 'contacted', 'queued') AND imported_at IS NOT NULL) AS parked""",
        {"since": since, "open": list(OPEN), "planned": list(PLANNED)})
    return {k: int(v) for k, v in (r or {}).items()}


@m.query("summary", resource="leadflow", title="Chia lead — số tóm tắt", min_scope="all", internal=True)
async def summary(ctx: Ctx, inp: NoInput) -> dict[str, int]:
    """SỐ TÓM TẮT cho màn tổng hợp (bảng điều hành của gói): số đếm của màn Chia lead & hạn xử lý {stuck, planned, waiting, grace, judged, on_time,
    recalled, returned, parked} (sự kiện: 7 ngày qua) — không lead nào, không tên. Gọi bằng QUYỀN NGƯỜI XEM (``ctx.call_as_optional(…, ctx.p)``):
    người không xem được Chia lead cả công ty ⇒ None."""
    return await _figures(ctx, datetime.now(zone()) - timedelta(days=7))


@m.query("overview", resource="leadflow", title="Chia lead & hạn xử lý", min_scope="all")
async def overview(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    now = datetime.now(zone())
    fig = await _figures(ctx, now - timedelta(days=7))
    q_states = await ctx.all("SELECT lead_id, offering_code, source, queue_reason, updated_at FROM leadflow.lead_state WHERE status = 'queued'"
                             " AND imported_at IS NULL ORDER BY updated_at LIMIT 60")
    planned = [q for q in q_states if q["queue_reason"] in PLANNED]
    stuck = [q for q in q_states if q["queue_reason"] not in PLANNED]
    waiting = await ctx.all("SELECT lead_id, holder_id, first_call_due, offering_code, breached_at FROM leadflow.lead_state WHERE status = ANY(%s)"
                            " AND imported_at IS NULL ORDER BY first_call_due LIMIT 50", (list(OPEN),))
    names = {r["id"]: r["name"] for r in await ctx.all("SELECT id, name FROM core.users")}
    custs = await ctx.call_system("crm.lead_cards", lead_ids=[q["lead_id"] for q in q_states]) if q_states else {}

    def qtitle(q: dict[str, Any]) -> str:
        c = custs.get(str(q["lead_id"]), {})
        return f"{c.get('customer', '—')} · {q['offering_code'] or 'chưa chọn [[offering|l]]'} · {SOURCE_LABEL.get(q['source'], '')}"

    # ô số: cả tập (``_figures``); danh sách bên dưới: vài chục dòng đầu
    judged, n_stuck, n_planned, grace, parked = fig["judged"], fig["stuck"], fig["planned"], fig["grace"], fig["parked"]
    kpis = [ui.kpi("Kẹt — không ai nhận", n_stuck, tone="red" if n_stuck else None),
            ui.kpi("Chờ điều phối chia", n_planned, tone="orange" if n_planned else None),
            ui.kpi("Đang chờ gọi lần đầu", fig["waiting"]),
            ui.kpi("Gọi đúng hạn (7 ngày)", round(100 * fig["on_time"] / judged, 1) if judged else None, "percent",
                   hint=f"{fig['on_time']} đúng hạn / {judged} lead đã có kết quả" if judged else "Chưa có lead nào có kết quả"),
            ui.kpi("Thu hồi (7 ngày)", fig["recalled"], tone="orange" if fig["recalled"] else None),
            ui.kpi("Quá hạn — trong ân hạn", grace, tone="red" if grace else None, hint="Đã báo trưởng nhóm, chưa thu hồi — chuyển tay ngay được"),
            ui.kpi("Khách quay lại (7 ngày)", fig["returned"], tone="blue" if fig["returned"] else None,
                   hint="Khách đã có lead để lại thông tin lần nữa qua kênh khác — người đang chăm đã được báo")]
    if parked:
        kpis.append(ui.kpi(PARKED_LABEL[0].upper() + PARKED_LABEL[1:], parked,
                           hint="Trạng thái chia nạp từ hệ cũ là tham chiếu: không chấm hạn, không nhắc, không thu hồi. Giao / chuyển lead ở Barista thì lead "
                                "đó thành lead sống, hạn tính từ lúc giao"))
    blocks: list[dict[str, Any]] = [ui.b_kpis(kpis)]
    if stuck:
        blocks.append(ui.b_callout("bad", f"{n_stuck} lead kẹt", "Không có ai đủ điều kiện nhận, chưa có luật, hoặc đã thu hồi hết số lần. "
                                   "Giao tay ở “Lead chờ chia” (có gợi ý người nhận), hoặc chỉnh luật ở “Lead đi đâu”.",
                                   actions=[ui.act("leadflow.drain", "Thử chia lại", style="primary")]))
        blocks.append(ui.b_list([{"title": qtitle(q), "subtitle": f"Chờ {_span(q['updated_at'], now)} — {QUEUE_REASON.get(q['queue_reason'], '')}", "tone": "bad",
                                  "open": {"query": "crm.journey", "input": {"lead_id": str(q["lead_id"])}}} for q in stuck[:15]], "Lead kẹt"))
    if planned:
        blocks.append(ui.b_list([{"title": qtitle(q), "subtitle": f"Chờ {_span(q['updated_at'], now)}" + (" · người nạp giao cho đội" if q["queue_reason"] == "team" else ""),
                                  "tone": "accent",
                                  "open": {"query": "crm.journey", "input": {"lead_id": str(q["lead_id"])}}} for q in planned[:15]],
                                "Đang chờ đội điều phối chia"))
    late = [w for w in waiting if w["first_call_due"] < now]
    soon = [w for w in waiting if w["first_call_due"] >= now][:10]
    if late or soon:
        blocks.append(ui.b_list(
            [{"title": f"{names.get(w['holder_id'], '—')} · {w['offering_code'] or '—'}",
              "subtitle": f"quá hạn {_span(w['first_call_due'], now)}" + (" · trong ân hạn" if w["breached_at"] else "")
              if w["first_call_due"] < now else f"còn {_span(now, w['first_call_due'])}",
              "tone": "bad" if w["first_call_due"] < now else "accent",
              "open": {"query": "leadflow.explain", "input": {"lead_id": str(w["lead_id"])}}} for w in late[:15] + soon],
            "Đang chờ gọi lần đầu"))
    recent = await ctx.all(
        """SELECT e.kind, e.at, e.user_id, e.data, e.lead_id FROM leadflow.sla_events e
           WHERE e.kind IN ('first_call_breached', 'recalled', 'queued', 'followup_breached', 'returned', 'handed_over', 'accept_missed')
             AND NOT (e.kind = 'queued' AND e.data->>'reason' = ANY(%s))   -- vào hàng chờ điều phối là CÓ CHỦ Ý, không phải vi phạm
           ORDER BY e.at DESC LIMIT 15""", (list(PLANNED),))
    if recent:
        blocks.append(ui.b_list([{"title": f"{EVENT_LABEL[e['kind']]} — {names.get(e['user_id'], '—') if e['user_id'] else 'hàng chờ'}",
                                  "subtitle": _event_note(e),
                                  "badge": f"{e['at'].astimezone(zone()):%d/%m %H:%M}", "tone": "accent" if e["kind"] == "returned" else "warn",
                                  "open": {"query": "leadflow.explain", "input": {"lead_id": str(e["lead_id"])}}} for e in recent],
                                "Vi phạm, thu hồi, khách quay lại gần đây"))
    return ui.card("Chia lead & hạn xử lý", blocks, subtitle=f"{now:%d/%m/%Y %H:%M}", icon="arrow.triangle.branch",
                   actions=[ui.act("leadflow.intake", "Nạp lead", style="primary")],
                   open=[{"title": "Lead đi đâu", "query": "leadflow.policy", "input": {}}, {"title": "Lead chờ chia", "query": "leadflow.queue", "input": {}},
                         {"title": "Phản hồi theo người", "query": "leadflow.scorecard", "input": {}}])


def _event_note(e: dict[str, Any]) -> str:
    d = e["data"] or {}
    if e["kind"] == "returned":
        return f"qua {SOURCE_LABEL.get(d.get('source', ''), d.get('source') or 'kênh khác')}" + (f" · {d['campaign']}" if d.get("campaign") else "")
    if e["kind"] == "handed_over":
        return "người giữ đang nghỉ" if d.get("why") == "away" else "quản lý chuyển tay"
    return QUEUE_REASON.get(str(d.get("reason", "")), str(d.get("why", "")))


# ── Bảng phản hồi theo người (chỉ đọc; nguồn cho KPI / thi đua sau này) ──

class ScoreIn(Input):
    days: int = field("Số ngày gần nhất", 30, ge=1, le=365)


@m.query("scorecard", resource="leadflow", title="Phản hồi lead theo người")
async def scorecard(ctx: Ctx, inp: ScoreIn) -> dict[str, Any]:
    """Mỗi người: lead đã nhận, % liên hệ đúng hạn, trung vị phút tới liên hệ đầu, quá hạn, bị thu hồi, không bấm nhận kịp, chuyển vì nghỉ. Ai xem
    được ai theo phạm vi chia lead: của mình / đội mình quản lý / cả công ty. Đo từ sổ sự kiện chỉ thêm — không ai tự khai."""
    scope = ctx.p.scope("leadflow")
    if scope == "all":
        users = [r["user_id"] for r in await ctx.all("SELECT user_id FROM core.memberships WHERE active")]
    elif scope == "team":
        users = [r["user_id"] for r in await ctx.all("SELECT DISTINCT user_id FROM core.team_members WHERE team_id = ANY(%s)",
                                                     (list(ctx.p.managed_teams),))]
        users = list(dict.fromkeys([*users, *([ctx.p.user_id] if ctx.p.user_id else [])]))
    else:
        users = [ctx.p.user_id] if ctx.p.user_id else []
    since = datetime.now(zone()) - timedelta(days=inp.days)
    got = await ctx.call_system("leadflow.score_rows", user_ids=users, since=since) if users else []
    names = {r["id"]: r["name"] for r in await ctx.all("SELECT id, name FROM core.users WHERE id = ANY(%s)", ([g["user_id"] for g in got],))}
    rows = []
    for g in got:
        judged = g["judged"]
        rows.append({"id": str(g["user_id"]), "name": names.get(g["user_id"], "—"), "received": g["received"], "judged": judged,
                     "on_time_pct": round(100 * g["on_time"] / judged, 1) if judged else None,
                     "median_min": round(float(g["median_min"])) if g["median_min"] is not None else None,
                     "breached": g["breached"], "recalled": g["recalled"], "accept_missed": g["accept_missed"], "away": g["away"]})
    rows.sort(key=lambda r: (-r["received"], r["name"]))
    tot_j = sum(r["judged"] for r in rows)
    tot_ok = sum(g["on_time"] for g in got)
    meds = sorted(r["median_min"] for r in rows if r["median_min"] is not None)
    return ui.table(rows, [
        ui.col("name", "Người"), ui.col("received", "Lead nhận", "number"), ui.col("judged", "Đã có kết quả", "number"),
        ui.col("on_time_pct", "Liên hệ đúng hạn", "percent"), ui.col("median_min", "Trung vị phút tới liên hệ đầu", "number"),
        ui.col("breached", "Quá hạn", "number"), ui.col("recalled", "Bị thu hồi", "number"), ui.col("accept_missed", "Không bấm nhận kịp", "number"),
        ui.col("away", "Chuyển vì nghỉ", "number"),
    ], title=f"Phản hồi lead theo người — {inp.days} ngày", kpis=[
        ui.kpi("Liên hệ đúng hạn", round(100 * tot_ok / tot_j, 1) if tot_j else None, "percent",
               hint=f"{tot_ok} / {tot_j} lead đã có kết quả" if tot_j else "Chưa có lead nào có kết quả"),
        ui.kpi("Trung vị phút tới liên hệ đầu", meds[len(meds) // 2] if meds else None, hint="Trung vị của các trung vị từng người"),
        ui.kpi("Bị thu hồi", sum(r["recalled"] for r in rows)),
    ], empty="Chưa có lead nào được chia trong khoảng này",
        note="“Liên hệ” tính theo luật chia của lead (vd cuộc gọi tổng đài khách nghe máy) — không phải điều người giữ tự khai.")


class ScoreRowsIn(Input):
    user_ids: list[UUID]
    since: datetime


@m.query("score_rows", resource="leadflow", title="Số đo phản hồi theo người", internal=True, agent="deny", callers=("leadflow",),
         why="scorecard: user_ids lọc theo phạm vi chia lead của người xem (chính mình / thành viên đội mình quản lý / cả công ty). Sự kiện của lead "
             "đã bị thu hồi khỏi người xem không còn hiện qua RLS nên phải đếm bằng quyền hệ thống. Trả CHỈ SỐ ĐẾM mỗi người {user_id, received, "
             "judged, on_time, breached, recalled, accept_missed, away, median_min} — không mã lead, không tên khách."
             " [id: người gọi thấy — user_ids là chính người xem, thành viên đội người xem quản lý (managed_teams), hoặc mọi thành viên khi phạm vi cả"
             " công ty]")
async def score_rows(ctx: Ctx, inp: ScoreRowsIn) -> list[dict[str, Any]]:
    return await ctx.all(
        """SELECT e.user_id,
                  count(DISTINCT e.lead_id) FILTER (WHERE e.kind = 'assigned') AS received,
                  count(DISTINCT e.lead_id) FILTER (WHERE e.kind IN ('first_call_met', 'first_call_breached')) AS judged,
                  count(DISTINCT e.lead_id) FILTER (WHERE e.kind = 'first_call_met' AND (e.data->>'on_time')::bool) AS on_time,
                  count(DISTINCT e.lead_id) FILTER (WHERE e.kind = 'first_call_breached') AS breached,
                  count(DISTINCT e.lead_id) FILTER (WHERE e.kind = 'recalled') AS recalled,
                  count(DISTINCT e.lead_id) FILTER (WHERE e.kind = 'accept_missed') AS accept_missed,
                  count(DISTINCT e.lead_id) FILTER (WHERE e.kind = 'handed_over' AND e.data->>'why' = 'away') AS away,
                  percentile_cont(0.5) WITHIN GROUP (ORDER BY (e.data->>'minutes')::numeric)
                      FILTER (WHERE e.kind = 'first_call_met' AND e.data ? 'minutes') AS median_min
           FROM leadflow.sla_events e WHERE e.at >= %s AND e.user_id = ANY(%s) GROUP BY e.user_id""", (inp.since, inp.user_ids))


# ── Vì sao lead tới tay ai (trợ lý giải thích từ sổ sự kiện, không đoán) ──

class ExplainIn(Input):
    lead_id: UUID = field("Lead", hidden=True)


def _assigned_text(d: dict[str, Any], who: str) -> str:
    bits = []
    if d.get("kept_owner"):
        bits.append("giữ người đang chăm khách cũ (một người một khách)")
    if d.get("returned") or d.get("returning_customer"):
        bits.append("khách quay lại")
    if d.get("duty"):
        bits.append("lead về ngoài giờ ⇒ đội trực")
    if d.get("manual"):
        bits.append("chuyển tay")
    if d.get("by") and not d.get("manual"):
        bits.append("người điều phối giao")
    if "tier" in d:
        bits.append(f"thành viên hạng {d['tier']}, trọng số {d['weight']}, đã nhận {d.get('recent', 0)} lead / {d.get('recent_days', 30)} ngày")
    if d.get("strategy") == "least_loaded":
        bits.append(f"theo tải: {d.get('uncalled', 0)} lead chưa gọi")
    elif d.get("strategy") == "rotate" and "tier" not in d:
        bits.append("xoay vòng: người nhận lâu nhất" + (f" (lần nhận trước {datetime.fromisoformat(d['last_at']):%H:%M %d/%m})" if d.get("last_at") else ""))
    if d.get("candidates"):
        bits.append(f"{d['candidates']} người đủ điều kiện")
    due = f" · hạn liên hệ {datetime.fromisoformat(d['due']).astimezone(zone()):%H:%M %d/%m}" if d.get("due") else ""
    rule = f" theo luật “{d['rule']}”" if d.get("rule") else ""
    return f"Chia cho {who}{rule}" + (f" — {'; '.join(bits)}" if bits else "") + due


def _event_text(k: str, d: dict[str, Any], person: str) -> str:
    """Một sự kiện chia lead thành câu (đọc từ dữ liệu sự kiện đã ghi — không đoán)."""
    proof = str(d.get("proof", ""))
    match k:
        case "assigned":
            return _assigned_text(d, person)
        case "queued":
            return f"Vào hàng chờ — {QUEUE_REASON.get(d.get('reason', ''), d.get('reason', ''))}"
        case "first_call_met":
            return (f"Liên hệ lần đầu ({PROOFS.get(proof.removesuffix('_fallback'), 'liên hệ')}"
                    f"{', tạm tính thay cuộc gọi tổng đài' if proof.endswith('_fallback') else ''})"
                    + (f" sau {d['minutes']} phút" if d.get("minutes") is not None else "") + (" — đúng hạn" if d.get("on_time") else " — trễ hạn"))
        case "first_call_breached":
            return f"Quá hạn liên hệ lần đầu — {person}"
        case "reminded":
            return f"Nhắc {person} trước hạn" + (" chăm sóc lại" if d.get("followup") else " liên hệ")
        case "escalated":
            return f"Báo {len(d.get('to', []))} trưởng nhóm" + (" (quá hạn chăm sóc lại)" if d.get("followup") else " (quá hạn liên hệ)")
        case "recalled":
            return f"Thu hồi khỏi {person} — {d.get('why', 'quá hạn')}" + (" — hết số lần, về hàng chờ" if d.get("limit") else "")
        case "accepted":
            return f"{person} bấm nhận"
        case "accept_missed":
            return f"{person} không bấm nhận kịp"
        case "returned":
            return (f"Khách quay lại qua {SOURCE_LABEL.get(d.get('source', ''), d.get('source') or 'kênh khác')}"
                    + (f" · chiến dịch {d['campaign']}" if d.get("campaign") else "") + f" — báo {person}")
        case "reopened":
            return "Mở lại lead đã mất (khách quay lại)"
        case "handed_over":
            return f"Chuyển khỏi {person} — " + ("đang nghỉ phép" if d.get("why") == "away" else f"quản lý chuyển: {d.get('note', '')}")
        case "followup_breached":
            return f"Quá hạn chăm sóc lại — {person}"
    return EVENT_LABEL.get(k, k)


@m.query("explain", resource="leadflow", title="Vì sao lead tới tay ai")
async def explain(ctx: Ctx, inp: ExplainIn) -> dict[str, Any]:
    """Lịch sử chia của MỘT lead đọc thành câu: luật nào, vì sao người này (xoay vòng / tải / hạng + trọng số / giữ người cũ / đội trực), hạn, nhắc,
    quá hạn, báo trưởng nhóm, thu hồi, khách quay lại. Chỉ lead người xem thấy (RLS). Người phạm vi của mình không thấy tên người giữ trước.
    Dùng cho "vì sao lead này về người đó", và BẮT BUỘC trước khi chuyển lead trễ hạn cho người khác: thẻ có sẵn người nhận gợi ý cho ``leadflow.reassign``."""
    st = await ctx.one("SELECT * FROM leadflow.lead_state WHERE lead_id = %s", (inp.lead_id,))
    if st is None:
        raise NotFound("Không thấy lead này trong phạm vi chia lead của bạn")
    events = await ctx.all("SELECT kind, at, user_id, data FROM leadflow.sla_events WHERE lead_id = %s ORDER BY id", (inp.lead_id,))
    card = (await ctx.call_system("crm.lead_cards", lead_ids=[st["lead_id"]])).get(str(st["lead_id"]), {})   # mã đọc qua RLS ở trên
    names = {r["id"]: r["name"] for r in await ctx.all("SELECT id, name FROM core.users")}
    named = ctx.p.has_scope("leadflow", "team")

    def who(u: Any) -> str:
        if u is None:
            return "hàng chờ"
        u = UUID(str(u))
        return "anh/chị" if u == ctx.p.user_id else (names.get(u, "—") if named else "đồng nghiệp")

    items = []
    for e in events:
        k = e["kind"]
        items.append({"title": _event_text(k, e["data"] or {}, who(e["user_id"])), "badge": f"{e['at'].astimezone(zone()):%H:%M %d/%m}",
                      "tone": "bad" if k in ("first_call_breached", "recalled", "accept_missed", "followup_breached") else
                      ("accent" if k in ("returned", "reopened") else "")})
    rule = st["rule"] or {}
    facts = []
    if rule:
        facts.append(f"Luật “{rule.get('name', '')}”: liên hệ lần đầu trong {rule.get('first_call_min')} phút làm việc; tính là đã liên hệ khi có "
                     f"{proof_text(rule)}")
    if st["status"] in OPEN and st["first_call_due"]:
        facts.append(f"Hạn liên hệ: {st['first_call_due'].astimezone(zone()):%H:%M %d/%m}" + (" (đội trực)" if st["duty"] else ""))
    if st["qualified_by"]:
        facts.append(f"Người lọc lead: {names.get(st['qualified_by'], '—')}")
    if st["returns"]:
        facts.append(f"Khách đã quay lại {st['returns']} lần")
    if st["imported_at"] is not None:
        facts.append(f"Trạng thái chia {PARKED_LABEL}: không chấm hạn, không nhắc, không thu hồi cho tới khi giao / chuyển lead ở Barista")
    acts = []
    if named and st["status"] in OPEN:
        r = await _rule_for(ctx, st["offering_code"], st["source"], today(), st.get("plan_code") or "")
        pick = await _pick(ctx, r or {"team_id": None, "roles": sorted(await _lead_roles(ctx)), "cap_uncalled": 0, "strategy": "least_loaded"},
                           st["offering_code"], tuple(st["holders"]))
        if pick.user and (email := await ctx.val("SELECT email FROM core.users WHERE id = %s", (pick.user,))):
            acts.append(ui.act("leadflow.reassign", f"Chuyển cho {names.get(pick.user, '').split()[-1] if names.get(pick.user) else 'người được gợi ý'}",
                               {"lead_id": str(inp.lead_id), "owner_email": email, "reason": "Chuyển theo gợi ý — lead sắp / đã quá hạn"}, "primary"))
        acts.append(ui.act("leadflow.reassign", "Chuyển cho người khác", {"lead_id": str(inp.lead_id)}))
    title = f"{card.get('customer', 'Lead')}{' · ' + st['offering_code'] if st['offering_code'] else ''}"
    return ui.card(f"Vì sao lead tới tay ai — {title}", [ui.b_callout("accent" if st["status"] != "queued" else "warn", "Bây giờ",
                                                                      f"Đang ở: {who(st['holder_id'])}", facts=facts, actions=acts or None),
                                                         ui.b_list(items, "Lịch sử chia")],
                   icon="point.topleft.down.to.point.bottomright.curvepath",
                   open=[{"title": "Thẻ khách", "query": "crm.journey", "input": {"lead_id": str(inp.lead_id)}}])


# ── Lead của tôi: chờ bấm nhận, sắp / đã quá hạn liên hệ, khách quay lại ──

@m.query("mine", resource="leadflow", title="Lead của tôi — hạn liên hệ")
async def mine(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Cho người NHẬN lead: lead đang chờ mình bấm nhận / liên hệ lần đầu (hạn, luật tính thế nào là đã liên hệ), khách của mình vừa quay lại qua kênh
    khác. Có nút Nhận lead (luật có bước nhận)."""
    now = datetime.now(zone())
    rows = await ctx.all("SELECT lead_id, status, first_call_due, accept_due, offering_code, breached_at, rule FROM leadflow.lead_state"
                         " WHERE holder_id = %s AND status = ANY(%s) AND imported_at IS NULL ORDER BY first_call_due LIMIT 50", (ctx.p.user_id, list(OPEN)))
    back = await ctx.all("SELECT DISTINCT ON (lead_id) lead_id, at, data FROM leadflow.sla_events WHERE kind = 'returned' AND user_id = %s"
                         " AND at >= now() - interval '7 days' ORDER BY lead_id, at DESC", (ctx.p.user_id,))
    cards = await ctx.call_system("crm.lead_cards", lead_ids=[r["lead_id"] for r in rows] + [b["lead_id"] for b in back]) if rows or back else {}
    offered = [r for r in rows if r["status"] == "offered"]
    late = [r for r in rows if r["first_call_due"] and r["first_call_due"] < now]
    soon = [r for r in rows if r["first_call_due"] and now <= r["first_call_due"] <= now + timedelta(minutes=30)]
    blocks: list[dict[str, Any]] = [ui.b_kpis([
        ui.kpi("Chờ bấm nhận", len(offered), tone="orange" if offered else None),
        ui.kpi("Quá hạn liên hệ", len(late), tone="red" if late else None),
        ui.kpi("Sắp tới hạn (≤ 30 phút)", len(soon), tone="orange" if soon else None),
        ui.kpi("Khách quay lại (7 ngày)", len(back), tone="blue" if back else None)])]
    for r in rows[:12]:
        c = cards.get(str(r["lead_id"]), {})
        due = r["accept_due"] if r["status"] == "offered" else r["first_call_due"]
        what = "bấm nhận" if r["status"] == "offered" else "liên hệ"
        when = (f"quá hạn {what} {_span(due, now)}" if due < now else f"{what} trong {_span(now, due)} ({due.astimezone(zone()):%H:%M})") if due else ""
        acts = [ui.act("leadflow.accept", "Nhận lead", {"lead_id": str(r["lead_id"])}, "primary")] if r["status"] == "offered" else []
        blocks.append(ui.b_callout("bad" if due and due < now else ("warn" if r["status"] == "offered" else "accent"),
                                   f"{c.get('customer', '—')}{' · ' + r['offering_code'] if r['offering_code'] else ''}", when,
                                   facts=[f"Tính là đã liên hệ khi có {proof_text(r['rule'])}"]
                                   if r["rule"] else [], actions=acts or None))
    if back:
        blocks.append(ui.b_list([{"title": f"{cards.get(str(b['lead_id']), {}).get('customer', '—')} quay lại", "badge": f"{b['at'].astimezone(zone()):%H:%M %d/%m}",
                                  "subtitle": f"qua {SOURCE_LABEL.get((b['data'] or {}).get('source', ''), 'kênh khác')}", "tone": "accent",
                                  "open": {"query": "crm.journey", "input": {"lead_id": str(b["lead_id"])}}} for b in back], "Khách quay lại"))
    if len(blocks) == 1:
        blocks.append(ui.b_callout("ok", "Không có lead nào đang chờ anh/chị", "Lead mới được chia sẽ hiện ở đây kèm hạn liên hệ."))
    return ui.card("Lead của tôi — hạn liên hệ", blocks, subtitle=f"{now:%d/%m %H:%M}", icon="timer")


# ── Lead quá hạn gọi lần đầu của đội (trưởng nhóm hỏi "lead nào đang trễ? chuyển giúp") ──

@m.query("late", resource="leadflow", title="Lead quá hạn / sắp quá hạn gọi lần đầu", min_scope="team")
async def late(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Cho trưởng nhóm / quản lý: lead CHƯA được liên hệ mà đã QUÁ HẠN gọi lần đầu (hoặc còn ≤ 30 phút, hoặc người giữ đang nghỉ) trong phạm vi chia lead
    của mình — mã lead, người đang giữ, trễ bao lâu. Bước đầu của "lead nào của đội đang trễ / quá hạn gọi, chuyển giúp": lấy mã lead ở đây, rồi
    ``leadflow.explain`` (vì sao tới người này + người nhận gợi ý), rồi ``leadflow.reassign`` (thẻ đề xuất — quản lý bấm mới chuyển)."""
    rows = await ctx.all("SELECT lead_id, holder_id, status, first_call_due, accept_due, offering_code FROM leadflow.lead_state WHERE status = ANY(%s)"
                         " AND imported_at IS NULL ORDER BY first_call_due LIMIT 200", (list(OPEN),))
    now = datetime.now(zone())
    gone = await away(ctx, {r["holder_id"] for r in rows if r["holder_id"]})
    hot = [r for r in rows if gone.get(str(r["holder_id"])) is not None or r["first_call_due"] <= now + timedelta(minutes=30)][:50]
    cards = await ctx.call_system("crm.lead_cards", lead_ids=[r["lead_id"] for r in hot]) if hot else {}
    names = {r["id"]: r["name"] for r in await ctx.all("SELECT id, name FROM core.users WHERE id = ANY(%s)", ([r["holder_id"] for r in hot],))}
    out = []
    for r in hot:
        due = r["first_call_due"]
        out.append({"id": str(r["lead_id"]), "lead_id": str(r["lead_id"]), "customer": cards.get(str(r["lead_id"]), {}).get("customer", "—"),
                    "offering": r["offering_code"] or "", "holder": names.get(r["holder_id"], "—"),
                    "state": "away" if gone.get(str(r["holder_id"])) is not None and due > now else "late" if due < now else "soon",
                    "when": f"quá hạn {_span(due, now)}" if due < now else f"còn {_span(now, due)}"})
    n_late = sum(1 for r in out if r["state"] == "late")
    return ui.table(out, [
        ui.col("customer", "Khách"), ui.col("offering", "Sản phẩm"), ui.col("holder", "Người đang giữ"), ui.col("when", "Hạn gọi lần đầu"),
        ui.col("state", "Tình trạng", "badge", labels={"late": "Quá hạn", "soon": "Sắp tới hạn", "away": "Người giữ đang nghỉ"},
               tones={"late": "red", "soon": "orange", "away": "purple"}),
        ui.col("lead_id", "Mã lead"),
    ], title="Lead quá hạn / sắp quá hạn gọi lần đầu", kpis=[ui.kpi("Quá hạn", n_late, tone="red" if n_late else "green"), ui.kpi("Cần để mắt", len(out))],
        empty="Không có lead nào quá hạn hay sắp tới hạn gọi lần đầu",
        note="Lead đã được liên hệ không ở đây (đổi người phụ trách khách ở thẻ khách). Chuyển tay: leadflow.explain rồi leadflow.reassign.")


# ── Gợi ý buổi sáng cho quản lý: chuyển lead sắp / đã trễ (thẻ đề xuất người bấm) ──

@m.query("suggestions", resource="leadflow", title="Gợi ý buổi sáng (chia lead)", agent="deny")
async def suggestions(ctx: Ctx, inp: NoInput) -> list[dict[str, Any]]:
    """Nguồn gợi ý buổi sáng (0074 A): lead CHƯA được liên hệ mà người giữ đang nghỉ, hoặc đã quá hạn (trong ân hạn), hoặc còn ≤ 15 phút ⇒ đề xuất
    ``leadflow.reassign`` cho người gợi ý bằng đúng cách chia tự động. Chỉ cho người phạm vi chia lead đội trở lên; quản lý bấm mới chuyển."""
    if not ctx.p.has_scope("leadflow", "team"):
        return []
    rows = await ctx.all("SELECT * FROM leadflow.lead_state WHERE status = ANY(%s) AND imported_at IS NULL ORDER BY first_call_due LIMIT 60", (list(OPEN),))
    gone = await away(ctx, {r["holder_id"] for r in rows if r["holder_id"]})
    now = datetime.now(zone())
    cards = await ctx.call_system("crm.lead_cards", lead_ids=[r["lead_id"] for r in rows]) if rows else {}
    out: list[dict[str, Any]] = []
    for r in rows:
        off = gone.get(str(r["holder_id"]))
        if off is None and r["first_call_due"] >= now + timedelta(minutes=15):
            continue
        rule = await _rule_for(ctx, r["offering_code"], r["source"], today(), r.get("plan_code") or "")
        if rule is None:
            continue
        pick = await _pick(ctx, rule, r["offering_code"], tuple(r["holders"]))
        email = await ctx.val("SELECT email FROM core.users WHERE id = %s", (pick.user,)) if pick.user else None
        if not email:
            continue
        cust = cards.get(str(r["lead_id"]), {}).get("customer", "lead")
        why = ("người giữ đang nghỉ" if off else f"quá hạn liên hệ {_span(r['first_call_due'], now)}" if r["first_call_due"] < now
               else f"còn {_span(now, r['first_call_due'])} mà chưa liên hệ")
        out.append({"command": "leadflow.reassign", "input": {"lead_id": str(r["lead_id"]), "owner_email": email, "reason": f"Gợi ý buổi sáng: {why}"},
                    "reason": f"{cust}{' · ' + r['offering_code'] if r['offering_code'] else ''}: {why}", "key": f"reassign:{r['lead_id']}",
                    "rank": 60 + len(out) * 10})
        if len(out) >= 3:
            break
    return out


m.suggests("leadflow.suggestions", rank=25)


# ── Sức khoẻ dữ liệu (module health gom) ──

EXTERNAL_STALE_H = 24   # nguồn ngoài quá ngần này giờ không đổ lead ⇒ trễ (kiểm kết nối)


@m.query("health_report", resource="leadflow", title="Sức khoẻ nguồn lead ngoài", internal=True, agent="deny", callers=("health",),
         why="health._collect (Sức khoẻ dữ liệu — health.overview, min_scope all): tình trạng dữ liệu của module này cả công ty. Trả {rank, issues, notes, fresh} — câu + số đếm, có thể kèm mã để sửa; không thân dữ liệu nghiệp vụ.")
async def health_report(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Phần của nguồn lead ngoài (form quảng cáo, Phê Nâu…) trong "Sức khoẻ dữ liệu": lần đổ gần nhất + số 7 ngày theo nhà cung cấp."""
    rows = await ctx.all(
        """SELECT provider, max(at) AS last_at, count(*) FILTER (WHERE at >= now() - interval '7 days') AS week
           FROM leadflow.external_refs GROUP BY provider ORDER BY provider""")
    now = datetime.now(zone())
    return {"rank": 20, "notes": [f"Lead nguồn ngoài trễ khi > {EXTERNAL_STALE_H} giờ không đổ lead."], "issues": [], "fresh": [
        {"title": f"Lead nguồn ngoài · {r['provider']}", "subtitle": f"{r['week']} lead / 7 ngày", "at": r["last_at"], "open": "leadflow.queue",
         "stale": r["last_at"] is not None and (now - r["last_at"]).total_seconds() > EXTERNAL_STALE_H * 3600,
         "why": f"Nguồn {r['provider']} quá {EXTERNAL_STALE_H} giờ chưa đổ lead ⇒ kiểm kết nối trước khi đọc số lead"} for r in rows]}


class LeadDaysIn(Input):
    days: int = field("Số ngày gần nhất", 14, ge=1, le=92)


@m.query("external_lead_days", resource=None, title="Lead nguồn ngoài theo nhà cung cấp và ngày", internal=True, agent="deny", family="company_figures",
         why="Gói kênh khai consumes(\"company_figures\") (vd nhận form lead quảng cáo — 0216 luật 4: một công ty một đường nhận lead Facebook): biết công ty "
             "đang nhận lead qua nhà cung cấp nào để từ chối bật đường thứ hai và báo sức khoẻ khi hai đường cùng đổ lead một ngày. Trả CHỈ SỐ ĐẾM "
             "[{provider, day, leads}] của ``days`` ngày gần nhất (ngày theo múi công ty, mỗi lần nhà cung cấp gửi một mã ngoài = một dòng, kể cả khách đã "
             "có) — không mã lead, không tên / SĐT.")
async def external_lead_days(ctx: Ctx, inp: LeadDaysIn) -> list[dict[str, Any]]:
    rows = await ctx.all("""SELECT provider, (at AT TIME ZONE core.tz())::date AS day, count(*) AS leads FROM leadflow.external_refs
                            WHERE at >= (date_trunc('day', now() AT TIME ZONE core.tz()) - make_interval(days => %s - 1)) AT TIME ZONE core.tz()
                            GROUP BY 1, 2 ORDER BY 2, 1""", (inp.days,))
    return [{"provider": r["provider"], "day": r["day"].isoformat(), "leads": int(r["leads"])} for r in rows]
